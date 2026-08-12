from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts import validate_mixamo_character as validator


class ValidateMixamoCharacterTests(unittest.TestCase):
    def test_accepts_normalized_skinned_humanoid_with_animation(self) -> None:
        bone_names = sorted(validator.CORE_BONES | {"Neck", "Spine1", "Spine2", "LeftToeBase", "RightToeBase"})
        nodes = [{"name": f"mixamorig:{name}"} for name in bone_names]
        nodes.append({"name": "CharacterMesh", "mesh": 0, "skin": 0})
        document = {
            "nodes": nodes,
            "skins": [{"joints": list(range(len(bone_names)))}],
            "meshes": [
                {
                    "primitives": [
                        {
                            "attributes": {
                                "POSITION": 0,
                                "NORMAL": 1,
                                "JOINTS_0": 2,
                                "WEIGHTS_0": 3,
                            },
                            "indices": 4,
                        }
                    ]
                }
            ],
            "animations": [
                {
                    "name": "Mixamo_Idle",
                    "samplers": [{"input": 5, "output": 6}],
                    "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}],
                }
            ],
        }
        accessors = {
            0: [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            1: [(0.0, 0.0, 1.0)] * 3,
            2: [(0, 0, 0, 0)] * 3,
            3: [(1.0, 0.0, 0.0, 0.0)] * 3,
            4: [(0,), (1,), (2,)],
            5: [(0.0,), (1.0,)],
            6: [(0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0, 1.0)],
        }
        with (
            patch.object(validator, "read_glb", return_value=(document, bytearray())),
            patch.object(validator, "read_accessor", side_effect=lambda _d, _b, index: accessors[index]),
        ):
            result = validator.validate(Path("character.glb"))
        self.assertEqual(result["triangles"], 1)
        self.assertEqual(result["animations"], 1)
        self.assertEqual(result["weightError"], 0.0)

    def test_rejects_missing_core_bones(self) -> None:
        document = {
            "nodes": [{"name": "mixamorig:Hips"}],
            "skins": [{"joints": [0]}],
        }
        with patch.object(validator, "read_glb", return_value=(document, bytearray())):
            with self.assertRaisesRegex(ValueError, "humanoid skeleton"):
                validator.validate(Path("invalid.glb"))

    def test_rejects_transparent_character_material_by_default(self) -> None:
        document = {"materials": [{"name": "Character", "alphaMode": "BLEND"}]}
        with patch.object(validator, "read_glb", return_value=(document, bytearray())):
            with self.assertRaisesRegex(ValueError, "transparent character materials"):
                validator.validate(Path("transparent.glb"))

    def test_can_allow_intentional_transparent_material(self) -> None:
        document = {"materials": [{"name": "Visor", "alphaMode": "BLEND"}]}
        with patch.object(validator, "read_glb", return_value=(document, bytearray())):
            with self.assertRaisesRegex(ValueError, "Mixamo skin"):
                validator.validate(
                    Path("transparent.glb"),
                    allow_transparent_materials=True,
                )

    def test_rejects_animation_that_does_not_drive_the_skin(self) -> None:
        bone_names = sorted(validator.CORE_BONES | {"Neck", "Spine1", "Spine2", "LeftToeBase", "RightToeBase"})
        nodes = [{"name": f"mixamorig:{name}"} for name in bone_names]
        nodes.extend(
            [
                {"name": "CharacterMesh", "mesh": 0, "skin": 0},
                {"name": "DetachedAnimationTarget"},
            ]
        )
        document = {
            "nodes": nodes,
            "skins": [{"joints": list(range(len(bone_names)))}],
            "meshes": [
                {
                    "primitives": [
                        {
                            "attributes": {
                                "POSITION": 0,
                                "NORMAL": 1,
                                "JOINTS_0": 2,
                                "WEIGHTS_0": 3,
                            },
                            "indices": 4,
                        }
                    ]
                }
            ],
            "animations": [
                {
                    "name": "Detached",
                    "samplers": [{"input": 5, "output": 6}],
                    "channels": [
                        {
                            "sampler": 0,
                            "target": {
                                "node": len(nodes) - 1,
                                "path": "rotation",
                            },
                        }
                    ],
                }
            ],
        }
        accessors = {
            0: [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            1: [(0.0, 0.0, 1.0)] * 3,
            2: [(0, 0, 0, 0)] * 3,
            3: [(1.0, 0.0, 0.0, 0.0)] * 3,
            4: [(0,), (1,), (2,)],
            5: [(0.0,), (1.0,)],
            6: [(0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0, 1.0)],
        }
        with (
            patch.object(validator, "read_glb", return_value=(document, bytearray())),
            patch.object(validator, "read_accessor", side_effect=lambda _d, _b, index: accessors[index]),
        ):
            with self.assertRaisesRegex(ValueError, "does not drive the Mixamo skeleton"):
                validator.validate(Path("detached.glb"))


if __name__ == "__main__":
    unittest.main()
