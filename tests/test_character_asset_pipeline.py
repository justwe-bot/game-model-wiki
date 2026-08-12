from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "character_asset_pipeline.py"
SPEC = importlib.util.spec_from_file_location("character_asset_pipeline", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)

RIG_SCRIPT = REPO_ROOT / "scripts" / "rig_unirig_character.py"
RIG_SPEC = importlib.util.spec_from_file_location("rig_unirig_character_test", RIG_SCRIPT)
assert RIG_SPEC is not None and RIG_SPEC.loader is not None
RIG = importlib.util.module_from_spec(RIG_SPEC)
RIG_SPEC.loader.exec_module(RIG)


class CharacterAssetPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.popbot_path = (
            REPO_ROOT / "references" / "characters" / "popbot-hunyuan-v31-proxy-v1.json"
        )
        self.popbot = json.loads(self.popbot_path.read_text(encoding="utf-8"))
        self.popbot_v2_path = (
            REPO_ROOT / "references" / "characters" / "popbot-hunyuan-v31-proxy-v2.json"
        )
        self.popbot_v2 = json.loads(self.popbot_v2_path.read_text(encoding="utf-8"))

    def test_popbot_manifest_validates_with_existing_inputs(self) -> None:
        self.assertEqual(
            PIPELINE.validate_manifest(self.popbot, REPO_ROOT, check_files=True),
            [],
        )

    def test_popbot_v2_uses_independent_proxy_cache(self) -> None:
        self.assertEqual(
            PIPELINE.validate_manifest(self.popbot_v2, REPO_ROOT, check_files=True),
            [],
        )
        self.assertNotEqual(
            self.popbot["reconstruction"]["output"],
            self.popbot_v2["reconstruction"]["output"],
        )
        self.assertIn("proxy-v2", self.popbot_v2["reconstruction"]["output"])

    def test_plan_contains_current_backends_and_approval_gates(self) -> None:
        actions = PIPELINE.plan_actions(REPO_ROOT, self.popbot)
        descriptions = "\n".join(description for _, description in actions)
        self.assertIn("modal_hunyuan3d_mv.py", descriptions)
        self.assertIn("modal_unirig.py", descriptions)
        self.assertIn("rig_hunyuan_character.py", descriptions)
        self.assertIn("use declared original and low GLB variants", descriptions)
        self.assertTrue(self.popbot["approvals"]["requireProxyVisual"])
        self.assertTrue(self.popbot["approvals"]["requireFinalVisual"])

    def test_template_initialization_has_no_popbot_paths(self) -> None:
        template_path = REPO_ROOT / "references" / "templates" / "character-pipeline.template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        manifest = PIPELINE.initialize_manifest(
            template,
            slug="new-character",
            name="新角色",
            name_en="New Character",
            game="risk-of-rain-2",
            kind="hero",
            tier="英雄",
        )
        serialized = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn("popbot", serialized.lower())
        self.assertNotIn("__SLUG__", serialized)
        self.assertEqual(manifest["identity"]["slug"], "new-character")
        self.assertEqual(PIPELINE.validate_manifest(manifest, REPO_ROOT), [])

        actions = PIPELINE.plan_actions(REPO_ROOT, manifest)
        stages = [stage for stage, _ in actions]
        self.assertIn("skin-final-mesh", stages)
        self.assertIn("generate-rig-profile", stages)
        self.assertEqual(manifest["finalSkin"]["adapter"], "unirig-shared")
        self.assertEqual(manifest["finalRig"]["adapter"], "unirig-template-transfer")

    def test_skip_generation_uses_cached_cloud_outputs(self) -> None:
        stages = PIPELINE.selected_run_stages("all", skip_generation=True)
        self.assertFalse(set(stages) & PIPELINE.CLOUD_GENERATION_STAGES)
        self.assertEqual(
            stages,
            [
                "prepare-views",
                "validate-proxy",
                "generate-rig-profile",
                "rig-final",
                "validate-final",
            ],
        )

    def test_provenance_records_skin_and_profile_adapters(self) -> None:
        template_path = REPO_ROOT / "references" / "templates" / "character-pipeline.template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        manifest = PIPELINE.initialize_manifest(
            template,
            slug="provenance-test",
            name="来源测试",
            name_en="Provenance Test",
            game="sample-game",
            kind="enemy",
            tier="普通敌人",
        )
        manifest["finalRig"]["template"] = ""
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            manifest_path = directory_path / "manifest.json"
            original = directory_path / "original.glb"
            low = directory_path / "low.glb"
            provenance = directory_path / "provenance.json"
            manifest["publish"]["provenanceOutput"] = str(provenance)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            original.write_bytes(b"original")
            low.write_bytes(b"low")
            PIPELINE.write_provenance(
                REPO_ROOT,
                manifest_path,
                manifest,
                {"original": original, "low": low},
                {
                    "original": {"triangles": 1000},
                    "low": {"triangles": 400},
                },
            )
            payload = json.loads(provenance.read_text(encoding="utf-8"))
        self.assertEqual(payload["adapters"]["finalSkin"], "unirig-shared")
        self.assertEqual(payload["adapters"]["rigProfile"], "unirig-auto")

    def test_relaxed_hand_pose_falls_back_for_34_bone_unirig(self) -> None:
        source = {"nodes": [{"name": "unchanged"}]}
        posed, effective = RIG.apply_relaxed_hand_pose(
            source, [f"bone_{index}" for index in range(34)]
        )
        self.assertEqual(effective, "source-fallback-34-bone")
        self.assertEqual(posed, source)
        self.assertIsNot(posed, source)

    def test_relaxed_hand_pose_bakes_a_visible_running_grip(self) -> None:
        source = {
            "nodes": [
                {
                    "name": f"bone_{index}",
                    "rotation": [0.0, 0.0, 0.0, 1.0],
                }
                for index in range(52)
            ]
        }
        posed, effective = RIG.apply_relaxed_hand_pose(
            source, [f"bone_{index}" for index in range(52)]
        )
        self.assertEqual(effective, "relaxed")
        self.assertLess(posed["nodes"][13]["rotation"][0], -0.35)
        self.assertLess(posed["nodes"][14]["rotation"][0], -0.50)
        self.assertEqual(source["nodes"][13]["rotation"], [0.0, 0.0, 0.0, 1.0])

    def test_locomotion_despike_reduces_an_isolated_rotation_jump(self) -> None:
        times = [0.0, 0.25, 0.5, 0.75, 1.0]
        values = [
            RIG.x_rotation(0.0),
            RIG.x_rotation(10.0),
            RIG.x_rotation(70.0),
            RIG.x_rotation(10.0),
            RIG.x_rotation(0.0),
        ]
        converted = RIG.despike_quaternion_track(
            times,
            values,
            threshold_deg=2.0,
            strength=0.75,
            passes=1,
        )
        self.assertLess(
            RIG.rotation_delta_degrees(RIG.x_rotation(10.0), converted[2]),
            RIG.rotation_delta_degrees(RIG.x_rotation(10.0), values[2]),
        )
        self.assertLess(RIG.rotation_delta_degrees(converted[0], converted[-1]), 0.01)

    def test_rigid_shoe_mode_maps_toe_weights_to_foot(self) -> None:
        source_joint_names = [f"bone_{index}" for index in range(52)]
        semantic_mapping = RIG.semantic_weight_mapping(52, finger_mode="rigid")
        target_names = sorted(set(semantic_mapping.values()))
        target_joint_by_name = {
            name: index for index, name in enumerate(target_names)
        }
        transferred, mapping = RIG.remap_weights(
            [((47, 0, 0, 0), (1.0, 0.0, 0.0, 0.0))],
            source_joint_names,
            target_joint_by_name,
            finger_mode="rigid",
            foot_mode="rigid-shoe",
        )
        self.assertEqual(mapping["bone_47"], "foot.r")
        self.assertEqual(
            transferred[0][0][0],
            target_joint_by_name["foot.r"],
        )

    def test_popbot_unirig_command_uses_relaxed_hands_and_rigid_shoes(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "references" / "characters" / "popbot-unirig-template-v1.json").read_text(
                encoding="utf-8"
            )
        )
        command = PIPELINE.final_rig_command(
            REPO_ROOT,
            manifest,
            REPO_ROOT / "generated" / "source.glb",
            REPO_ROOT / "generated" / "output.glb",
        )
        self.assertIsNotNone(command)
        self.assertIn("relaxed", command)
        self.assertIn("rigid-shoe", command)

    def test_enemy_initialization_uses_direct_catalog(self) -> None:
        template_path = REPO_ROOT / "references" / "templates" / "character-pipeline.template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        manifest = PIPELINE.initialize_manifest(
            template,
            slug="new-enemy",
            name="新敌人",
            name_en="New Enemy",
            game="risk-of-rain-2",
            kind="enemy",
            tier="普通敌人",
        )
        self.assertEqual(manifest["publish"]["catalog"]["mode"], "direct")
        self.assertEqual(manifest["publish"]["catalog"]["path"], "catalog.json")
        self.assertNotIn("templateEntry", manifest["publish"]["catalog"])
        self.assertEqual(PIPELINE.validate_manifest(manifest, REPO_ROOT), [])

    def test_other_game_initialization_uses_its_catalog(self) -> None:
        template_path = REPO_ROOT / "references" / "templates" / "character-pipeline.template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        manifest = PIPELINE.initialize_manifest(
            template,
            slug="other-hero",
            name="其他角色",
            name_en="Other Hero",
            game="sample-game",
            kind="hero",
            tier="英雄",
        )
        self.assertEqual(
            manifest["publish"]["catalog"]["path"],
            "games/sample-game/catalog.json",
        )
        self.assertEqual(
            manifest["publish"]["modelsDir"],
            "models/sample-game/characters",
        )

    def test_direct_catalog_entry_includes_runtime_contract(self) -> None:
        template_path = REPO_ROOT / "references" / "templates" / "character-pipeline.template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        manifest = PIPELINE.initialize_manifest(
            template,
            slug="direct-entry",
            name="直接条目",
            name_en="Direct Entry",
            game="sample-game",
            kind="enemy",
            tier="普通敌人",
        )
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.glb"
            low = Path(directory) / "low.glb"
            original.write_bytes(b"original")
            low.write_bytes(b"low")
            entry = PIPELINE.build_catalog_entry(
                REPO_ROOT,
                manifest,
                [],
                {
                    "original": {"triangles": 1000, "animations": 20, "joints": 78},
                    "low": {"triangles": 400, "animations": 20, "joints": 78},
                },
                {"original": original, "low": low},
            )
        self.assertEqual(entry["kind"], "enemy")
        self.assertEqual(entry["animationCount"], 20)
        self.assertEqual(entry["jointCount"], 78)
        self.assertTrue(entry["preserveMaterials"])

    def test_source_template_runtime_contract_can_remove_template_weapons(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "references" / "characters" / "popbot-unirig-template-v1.json").read_text(
                encoding="utf-8"
            )
        )
        template_slug = manifest["publish"]["catalog"]["templateEntry"]
        entries = [
            {
                "slug": template_slug,
                "meshes": ["Body", "Gun", "Bow"],
                "rendererCount": 9,
                "skills": [{"name": "Template weapon"}],
                "skillsAppend": [{"name": "Template heavy weapon"}],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.glb"
            low = Path(directory) / "low.glb"
            original.write_bytes(b"original")
            low.write_bytes(b"low")
            entry = PIPELINE.build_catalog_entry(
                REPO_ROOT,
                manifest,
                entries,
                {
                    "original": {"triangles": 1000, "animations": 20, "joints": 78},
                    "low": {"triangles": 400, "animations": 20, "joints": 78},
                },
                {"original": original, "low": low},
            )
        self.assertEqual(entry["meshes"], ["GeneratedCharacterMesh"])
        self.assertEqual(entry["rendererCount"], 1)
        self.assertEqual(entry["skills"], [])
        self.assertEqual(entry["skillsAppend"], [])

    def test_proxy_approval_is_required_before_final_rig(self) -> None:
        with self.assertRaisesRegex(ValueError, "approve-proxy"):
            PIPELINE.require_proxy_approval(REPO_ROOT, self.popbot, approved=False)

    def test_existing_manifest_is_not_overwritten_without_force(self) -> None:
        template_path = REPO_ROOT / "references" / "templates" / "character-pipeline.template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "character.json"
            destination.write_text("{}", encoding="utf-8")
            self.assertTrue(destination.exists())
            manifest = PIPELINE.initialize_manifest(
                template,
                slug="guard-test",
                name="守卫测试",
                name_en="Guard Test",
                game="risk-of-rain-2",
                kind="hero",
                tier="英雄",
            )
            self.assertEqual(manifest["identity"]["slug"], "guard-test")
            self.assertEqual(destination.read_text(encoding="utf-8"), "{}")

    def test_existing_catalog_entry_requires_replace_flag(self) -> None:
        existing = {
            **self.popbot,
            "identity": {
                **self.popbot["identity"],
                "slug": "popbot-heavy-gunner-v1",
            },
        }
        with self.assertRaisesRegex(ValueError, "replace-entry"):
            PIPELINE.catalog_preflight(REPO_ROOT, existing, replace_entry=False)

    def test_publish_requires_visual_approval(self) -> None:
        with self.assertRaisesRegex(ValueError, "approve-visual"):
            PIPELINE.publish_stage(
                REPO_ROOT,
                self.popbot_path,
                self.popbot,
                force=False,
                approve_visual=False,
                replace_entry=False,
            )


if __name__ == "__main__":
    unittest.main()
