from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PIPELINE = load_module(
    "mixamo_character_pipeline_test",
    REPO_ROOT / "scripts" / "mixamo_character_pipeline.py",
)
MIXAMO = load_module(
    "modal_mixamo_character_test",
    REPO_ROOT / "cloud" / "modal_mixamo_character.py",
)
ATTACHMENT = load_module(
    "modal_character_attachment_test",
    REPO_ROOT / "cloud" / "modal_character_attachment.py",
)
HEAD_REPLACEMENT = load_module(
    "modal_character_head_replacement_test",
    REPO_ROOT / "cloud" / "modal_character_head_replacement.py",
)


class MixamoCharacterPipelineTests(unittest.TestCase):
    def test_clip_names_are_stable_and_prefixed(self) -> None:
        self.assertEqual(MIXAMO.clip_name("left strafe walking.fbx"), "Mixamo_LeftStrafeWalking")
        self.assertEqual(MIXAMO.clip_name("Rifle-Run_Fire.FBX"), "Mixamo_RifleRunFire")

    def test_canonical_bone_name_accepts_mixamo_prefixes(self) -> None:
        self.assertEqual(MIXAMO.canonical_bone_name("mixamorig:Hips"), "Hips")
        self.assertEqual(MIXAMO.canonical_bone_name("Hips"), "Hips")

    def test_action_paths_are_remapped_to_the_base_bone_prefix(self) -> None:
        class Bone:
            def __init__(self, name: str) -> None:
                self.name = name

        class Curve:
            def __init__(self, data_path: str) -> None:
                self.data_path = data_path

        base_armature = type(
            "Armature",
            (),
            {"data": type("Data", (), {"bones": [Bone("mixamorig:Hips"), Bone("mixamorig:Spine")]})()},
        )()
        action = type(
            "Action",
            (),
            {
                "fcurves": [
                    Curve('pose.bones["Hips"].location'),
                    Curve('pose.bones["Spine"].rotation_quaternion'),
                    Curve("location"),
                ]
            },
        )()

        changed = MIXAMO.remap_action_bone_paths(action, base_armature)

        self.assertEqual(changed, 2)
        self.assertEqual(action.fcurves[0].data_path, 'pose.bones["mixamorig:Hips"].location')
        self.assertEqual(
            action.fcurves[1].data_path,
            'pose.bones["mixamorig:Spine"].rotation_quaternion',
        )
        self.assertEqual(action.fcurves[2].data_path, "location")

    def test_clip_name_uses_the_original_archive_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "actions.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("idle.fbx", b"fbx")
            with zipfile.ZipFile(archive_path) as archive:
                member = archive.namelist()[0]
                source = Path(directory) / Path(member).name
            self.assertEqual(MIXAMO.clip_name(source.name), "Mixamo_Idle")

    def test_prepare_command_uses_modal_mesh_pipeline(self) -> None:
        args = argparse.Namespace(
            input=Path("generated/source.glb"),
            output_dir=Path("generated/clean"),
            target_faces=15_000,
            target_height=1.9,
            texture_size=2048,
            symmetry_x=False,
            force=False,
        )
        command = PIPELINE.prepare_command(args)
        self.assertIn("cloud/modal_character_mesh.py", " ".join(command))
        self.assertIn("15000", command)
        self.assertIn("2048", command)

    def test_replace_head_command_runs_before_mixamo(self) -> None:
        args = argparse.Namespace(
            body=Path("generated/body.glb"),
            head=Path("generated/head.glb"),
            output_dir=Path("generated/head-replaced"),
            body_cut_ratio=0.865,
            head_cut_ratio=0.12,
            head_fit_height=0.0,
            neck_overlap=0.018,
            head_offset="0,0,0",
            head_rotation="0,0,0",
            voxel_size=0.0045,
            texture_size=2048,
            force=False,
        )
        command = PIPELINE.replace_head_command(args)
        self.assertIn("cloud/modal_character_head_replacement.py", " ".join(command))
        self.assertIn("--body-cut-ratio", command)
        self.assertIn("--voxel-size", command)

    def test_head_replacement_cut_height_is_ratio_based(self) -> None:
        self.assertAlmostEqual(HEAD_REPLACEMENT.resolve_cut_height(1.0, 3.0, 0.25), 1.5)
        with self.assertRaisesRegex(ValueError, "between"):
            HEAD_REPLACEMENT.resolve_cut_height(0.0, 1.0, 1.0)

    def test_head_replacement_vector_parser_is_strict(self) -> None:
        self.assertEqual(
            HEAD_REPLACEMENT.parse_vector("0.1, -0.2, 0", label="offset"),
            (0.1, -0.2, 0.0),
        )
        with self.assertRaisesRegex(ValueError, "three"):
            HEAD_REPLACEMENT.parse_vector("1,2", label="offset")

    def test_finalize_command_fixes_the_delivery_height(self) -> None:
        args = argparse.Namespace(
            base_fbx=Path("generated/character-with-skin.fbx"),
            output=Path("generated/character.glb"),
            output_fbx=Path("generated/character.fbx"),
            report=Path("generated/character.mixamo-report.json"),
            target_height=1.9,
            stabilize_helmet_weights=False,
            helmet_rigid_threshold=0.35,
            helmet_minimum_head_influence=0.02,
            force_opaque_materials=False,
            force=False,
        )
        command = PIPELINE.finalize_command(args, Path("generated/actions.zip"))
        self.assertIn("--target-height", command)
        self.assertIn("1.9", command)

    def test_finalize_command_can_stabilize_rigid_helmet_weights(self) -> None:
        args = argparse.Namespace(
            base_fbx=Path("generated/character-with-skin.fbx"),
            output=Path("generated/character.glb"),
            output_fbx=Path("generated/character.fbx"),
            report=Path("generated/character.mixamo-report.json"),
            target_height=1.9,
            stabilize_helmet_weights=True,
            helmet_rigid_threshold=0.35,
            helmet_minimum_head_influence=0.02,
            force_opaque_materials=False,
            force=False,
        )
        command = PIPELINE.finalize_command(args, Path("generated/actions.zip"))
        self.assertIn("--stabilize-helmet-weights", command)
        self.assertIn("--helmet-rigid-threshold", command)
        self.assertIn("--helmet-minimum-head-influence", command)

    def test_finalize_command_can_force_opaque_character_materials(self) -> None:
        args = argparse.Namespace(
            base_fbx=Path("generated/character-with-skin.fbx"),
            output=Path("generated/character.glb"),
            output_fbx=Path("generated/character.fbx"),
            report=Path("generated/character.mixamo-report.json"),
            target_height=1.9,
            stabilize_helmet_weights=False,
            helmet_rigid_threshold=0.35,
            helmet_minimum_head_influence=0.02,
            force_opaque_materials=True,
            force=False,
        )
        command = PIPELINE.finalize_command(args, Path("generated/actions.zip"))
        self.assertIn("--force-opaque-materials", command)

    def test_attach_command_targets_a_mixamo_bone(self) -> None:
        args = argparse.Namespace(
            character=Path("generated/character.glb"),
            part=Path("generated/helmet.glb"),
            bone="mixamorig:Head",
            part_name="Helmet",
            part_anchor="bottom",
            bone_anchor="head",
            offset="0,0,0.02",
            rotation="0,0,0",
            scale=1.0,
            fit_height=0.36,
            output=Path("generated/character-equipped.glb"),
            output_fbx=Path("generated/character-equipped.fbx"),
            report=Path("generated/character-equipped.attachment-report.json"),
            force_opaque_materials=True,
            force=False,
        )
        command = PIPELINE.attach_command(args)
        self.assertIn("cloud/modal_character_attachment.py", " ".join(command))
        self.assertIn("mixamorig:Head", command)
        self.assertIn("0.36", command)
        self.assertIn("--force-opaque-materials", command)

    def test_attachment_vector_parser_requires_three_finite_values(self) -> None:
        self.assertEqual(
            ATTACHMENT.parse_vector("1, -2.5, 0.25", label="offset"),
            (1.0, -2.5, 0.25),
        )
        with self.assertRaisesRegex(ValueError, "three"):
            ATTACHMENT.parse_vector("1,2", label="offset")
        with self.assertRaisesRegex(ValueError, "finite"):
            ATTACHMENT.parse_vector("1,nan,3", label="rotation")

    def test_attachment_reads_binary_glb_json(self) -> None:
        document = {"asset": {"version": "2.0"}, "nodes": [{"name": "Head"}]}
        payload = json.dumps(document).encode("utf-8")
        payload += b" " * ((4 - len(payload) % 4) % 4)
        data = (
            struct.pack("<4sII", b"glTF", 2, 20 + len(payload))
            + struct.pack("<II", len(payload), 0x4E4F534A)
            + payload
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.glb"
            path.write_bytes(data)
            self.assertEqual(ATTACHMENT._read_glb_document(path), document)

    def test_action_directory_becomes_a_flat_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "idle.fbx").write_bytes(b"idle")
            (source / "run forward.fbx").write_bytes(b"run")
            (source / "ignore.txt").write_text("ignore", encoding="ascii")
            archive_path = PIPELINE.make_action_archive(source)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.namelist(), ["idle.fbx", "run forward.fbx"])


if __name__ == "__main__":
    unittest.main()
