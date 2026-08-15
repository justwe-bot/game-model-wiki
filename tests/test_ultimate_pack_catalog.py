from __future__ import annotations

import json
import math
import struct
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "robots-01": (15, 292),
    "robots-02": (15, 275),
    "monsters-03": (16, 285),
    "low-poly-10": (3137, 44452),
    "stylized-weapons": (648, 0),
    "sci-fi-civilians": (6, 150),
    "sci-fi-battle-weapons": (35, 0),
}


def read_glb(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise AssertionError(f"Invalid GLB header: {path}")
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if json_type != 0x4E4F534A:
        raise AssertionError(f"Missing GLB JSON chunk: {path}")
    return json.loads(data[20 : 20 + json_length].decode("utf-8"))


def rotate_vector(quaternion: list[float], vector: list[float]) -> list[float]:
    x, y, z, w = quaternion
    vx, vy, vz = vector
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return [
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    ]


class UltimatePackCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.games = json.loads((ROOT / "games.json").read_text(encoding="utf-8"))
        cls.catalog = json.loads(
            (ROOT / "games" / "ultimate-pack" / "catalog.json").read_text(encoding="utf-8")
        )

    def test_category_and_packages_are_registered(self) -> None:
        game = next(item for item in self.games if item["slug"] == "ultimate-pack")
        self.assertEqual(game["catalog"], "games/ultimate-pack/catalog.json")
        self.assertIn("3,872 个模型", game["subtitle"])
        self.assertIn("45,454 组动作", game["subtitle"])
        self.assertIn("人物", game["tiers"])
        self.assertIn("科幻武器", game["tiers"])
        counts = Counter(entry["packageSlug"] for entry in self.catalog)
        actions = Counter()
        for entry in self.catalog:
            actions[entry["packageSlug"]] += entry["animationCount"]
        self.assertEqual({slug: counts[slug] for slug in EXPECTED}, {slug: value[0] for slug, value in EXPECTED.items()})
        self.assertEqual({slug: actions[slug] for slug in EXPECTED}, {slug: value[1] for slug, value in EXPECTED.items()})

    def test_action_specific_equipment_transform_is_applied(self) -> None:
        index_html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("function applyEquipmentTransform(equipment, transform)", index_html)
        self.assertIn("equipment.quaternion.fromArray(transform.rotationQuaternion).normalize()", index_html)
        self.assertIn("texture.flipY = entry.textureFlipY ?? false", index_html)
        self.assertIn(
            "option.actionTransforms?.[animation?.clip]",
            index_html,
        )
        self.assertIn("equipmentTransformForAction(animation)", index_html)
        self.assertIn("function defaultClipForEquipment(entry = activeEntry)", index_html)
        self.assertIn("animation.weaponActionFamily === actionFamily", index_html)

    def test_all_models_and_actions_are_valid(self) -> None:
        self.assertEqual(len(self.catalog), 3872)
        self.assertEqual(len({entry["slug"] for entry in self.catalog}), len(self.catalog))
        inspected: dict[Path, dict] = {}
        for entry in self.catalog:
            with self.subTest(slug=entry["slug"]):
                self.assertTrue(entry["package"])
                self.assertTrue(entry["packageSlug"])
                expected_clips = [item["clip"] for item in entry["animations"]]
                for variant, model in entry["models"].items():
                    model_path = ROOT / model
                    self.assertTrue(model_path.is_file(), model)
                    document = inspected.setdefault(model_path, read_glb(model_path))
                    animations = document.get("animations", [])
                    variant_clips = [
                        item["clip"] for item in entry["animations"]
                        if not item.get("variants") or variant in item["variants"]
                    ]
                    self.assertEqual([item.get("name") for item in animations], variant_clips)
                    self.assertGreater(len(document.get("meshes", [])), 0)
                    self.assertNotIn("images", document)
                    self.assertNotIn("textures", document)
                    for animation in animations:
                        for channel in animation.get("channels", []):
                            self.assertLess(channel["target"]["node"], len(document["nodes"]))
                            self.assertLess(channel["sampler"], len(animation["samplers"]))
                        for sampler in animation.get("samplers", []):
                            self.assertLess(sampler["input"], len(document["accessors"]))
                            self.assertLess(sampler["output"], len(document["accessors"]))

    def test_external_textures_and_import_warnings(self) -> None:
        for entry in self.catalog:
            for group in ("textures", "emissiveTextures", "normalTextures"):
                for texture in set(entry.get(group, {}).values()) - {None}:
                    self.assertTrue((ROOT / texture).is_file(), texture)
        warnings = json.loads(
            (ROOT / "games" / "ultimate-pack" / "import-warnings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(warnings, {
            "stylized-weapons": [],
            "sci-fi-civilians": [],
            "sci-fi-battle-weapons": [],
        })

    def test_sci_fi_characters_and_weapons(self) -> None:
        entries = {entry["slug"]: entry for entry in self.catalog}
        civilian = entries["sci-fi-civilians-scificivilians-01"]
        self.assertEqual(civilian["animationCount"], 25)
        self.assertEqual(civilian["defaultClip"], "Mixamo_RifleReady")
        self.assertEqual(civilian["motionAnchorBone"], "pelvis")
        civilian_document = read_glb(ROOT / civilian["models"]["original"])
        self.assertIn("pelvis", {node.get("name") for node in civilian_document["nodes"]})
        self.assertGreaterEqual(len(civilian_document["skins"][0]["joints"]), 88)
        self.assertTrue(civilian["paletteTexture"])
        self.assertTrue(civilian["textureFlipY"])
        self.assertEqual(civilian["equipmentBone"], "prop_r")
        self.assertEqual(civilian["defaultEquipment"], "sci-fi-battle-weapons-scifirifle01-1")
        self.assertEqual(len(civilian["equipmentOptions"]), 7)
        mixamo_animations = {
            animation["clip"]: animation
            for animation in civilian["animations"]
            if animation["clip"].startswith("Mixamo_")
        }
        self.assertEqual(set(mixamo_animations), {
            "Mixamo_RifleReady",
            "Mixamo_RifleFireStanding",
            "Mixamo_RifleRunFire",
            "Mixamo_PistolReady",
            "Mixamo_PistolFireStanding",
            "Mixamo_PistolRun",
        })
        rifle_animations = {
            clip: animation for clip, animation in mixamo_animations.items()
            if clip.startswith("Mixamo_Rifle")
        }
        pistol_animations = {
            clip: animation for clip, animation in mixamo_animations.items()
            if clip.startswith("Mixamo_Pistol")
        }
        self.assertTrue(all(
            animation["equipment"] == "sci-fi-battle-weapons-scifirifle01-1"
            for animation in rifle_animations.values()
        ))
        self.assertTrue(all(
            animation["equipment"] == "sci-fi-battle-weapons-scifipistol01-1"
            for animation in pistol_animations.values()
        ))
        self.assertTrue(all(animation["weaponActionFamily"] == "rifle" for animation in rifle_animations.values()))
        self.assertTrue(all(animation["weaponActionFamily"] == "pistol" for animation in pistol_animations.values()))
        self.assertEqual(mixamo_animations["Mixamo_RifleReady"]["name"], "步枪持枪待机")
        self.assertEqual(mixamo_animations["Mixamo_RifleFireStanding"]["name"], "步枪站立射击")
        self.assertEqual(mixamo_animations["Mixamo_RifleRunFire"]["name"], "步枪跑动射击")
        self.assertTrue(mixamo_animations["Mixamo_RifleReady"]["loop"])
        self.assertFalse(mixamo_animations["Mixamo_RifleFireStanding"]["loop"])
        self.assertTrue(mixamo_animations["Mixamo_RifleRunFire"]["loop"])
        self.assertEqual(mixamo_animations["Mixamo_RifleReady"]["kind"], "idle")
        expected_support_socket = {
            "Mixamo_RifleReady": [0.286727, 0.177017, -0.052531],
            "Mixamo_RifleFireStanding": [0.286571, 0.176198, -0.054004],
            "Mixamo_RifleRunFire": [0.250690, 0.237556, -0.081915],
        }
        rifle_option = next(
            equipment for equipment in civilian["equipmentOptions"]
            if equipment["slug"] == "sci-fi-battle-weapons-scifirifle01-1"
        )
        grip = rifle_option["gripProfile"]
        for clip_name, animation in rifle_animations.items():
            self.assertEqual(animation["gripPose"], "rifle")
            for field in ("position", "scale"):
                self.assertEqual(len(animation["equipmentTransform"][field]), 3)
                self.assertTrue(all(math.isfinite(value) for value in animation["equipmentTransform"][field]))
            quaternion = animation["equipmentTransform"]["rotationQuaternion"]
            self.assertEqual(len(quaternion), 4)
            self.assertTrue(all(math.isfinite(value) for value in quaternion))
            self.assertAlmostEqual(sum(value * value for value in quaternion), 1.0, places=5)
            transform = animation["equipmentTransform"]
            right_grip = [value * transform["scale"][0] for value in grip["rightGrip"]]
            support_grip = [value * transform["scale"][0] for value in grip["supportGrip"]]
            right_socket = [
                transform["position"][index] + rotate_vector(quaternion, right_grip)[index]
                for index in range(3)
            ]
            support_socket = [
                transform["position"][index] + rotate_vector(quaternion, support_grip)[index]
                for index in range(3)
            ]
            self.assertLess(math.dist(right_socket, [0.0, 0.0, 0.0]), 0.00001)
            self.assertLess(math.dist(support_socket, expected_support_socket[clip_name]), 0.00001)
            muzzle_direction = rotate_vector(quaternion, grip["muzzleAxis"])
            support_direction = [
                expected_support_socket[clip_name][index] - right_socket[index]
                for index in range(3)
            ]
            dot = sum(muzzle_direction[index] * support_direction[index] for index in range(3))
            magnitude = math.sqrt(sum(value * value for value in support_direction))
            self.assertGreater(dot / magnitude, 0.98)
        self.assertEqual(civilian["mixamoActionSource"]["provider"], "Adobe Mixamo")
        self.assertEqual(civilian["mixamoActionSource"]["skin"], "Without Skin")
        self.assertEqual(civilian["mixamoActionSource"]["fps"], 30)
        self.assertEqual(civilian["mixamoActionSource"]["keyframeReduction"], "none")
        self.assertTrue(civilian["mixamoActionSource"]["runInPlace"])
        self.assertIn("Pistol Idle - Ready Alert Two Hand Pistol Grip", civilian["mixamoActionSource"]["motions"])
        for character_index in range(1, 7):
            character = entries[f"sci-fi-civilians-scificivilians-{character_index:02d}"]
            character_document = read_glb(ROOT / character["models"]["original"])
            self.assertIn(character["equipmentBone"], {node.get("name") for node in character_document["nodes"]})
            character_animation_names = {animation.get("name") for animation in character_document["animations"]}
            self.assertIn("Mixamo_RifleFireStanding", character_animation_names)
            self.assertIn("Mixamo_RifleRunFire", character_animation_names)
            self.assertIn("Mixamo_RifleReady", character_animation_names)
            self.assertIn("Mixamo_PistolReady", character_animation_names)
            self.assertIn("Mixamo_PistolFireStanding", character_animation_names)
            self.assertIn("Mixamo_PistolRun", character_animation_names)
            self.assertEqual(len(character_document["animations"]), 25)
            mixamo_report = character_document["asset"]["extras"]["mixamoRetarget"]["animations"]
            self.assertEqual({animation["frames"] for animation in mixamo_report}, {9, 18, 23, 36, 121})
            self.assertTrue(all(animation["channels"] == 23 for animation in mixamo_report))
            grip_report = character_document["asset"]["extras"]["rifleGripPose"]
            self.assertEqual(grip_report["fingerChannels"], 30)
            self.assertEqual(grip_report["poseAnimation"]["channels"], 53)
            pistol_grip_report = character_document["asset"]["extras"]["pistolGripPose"]
            self.assertEqual(pistol_grip_report["fingerChannels"], 30)
            self.assertEqual(set(pistol_grip_report["animations"]), set(pistol_animations))
            for clip_name in (
                "Mixamo_RifleReady",
                "Mixamo_RifleFireStanding",
                "Mixamo_RifleRunFire",
                "Mixamo_PistolReady",
                "Mixamo_PistolFireStanding",
                "Mixamo_PistolRun",
            ):
                clip = next(animation for animation in character_document["animations"] if animation["name"] == clip_name)
                finger_targets = {
                    character_document["nodes"][channel["target"]["node"]].get("name")
                    for channel in clip["channels"]
                    if character_document["nodes"][channel["target"]["node"]].get("name", "").startswith(
                        ("thumb_", "index_", "middle_", "ring_", "pinky_")
                    )
                }
                self.assertEqual(len(finger_targets), 30)
            for equipment in character["equipmentOptions"]:
                weapon = entries[equipment["slug"]]
                self.assertEqual(weapon["packageSlug"], "sci-fi-battle-weapons")
                for field in ("position", "scale"):
                    self.assertEqual(len(equipment[field]), 3)
                    self.assertTrue(all(math.isfinite(value) for value in equipment[field]))
                rotation = equipment.get("rotationQuaternion", equipment.get("rotationDeg"))
                self.assertIn(len(rotation), {3, 4})
                self.assertTrue(all(math.isfinite(value) for value in rotation))

            rifle_option = next(
                equipment for equipment in character["equipmentOptions"]
                if equipment["slug"] == "sci-fi-battle-weapons-scifirifle01-1"
            )
            grip = rifle_option["gripProfile"]
            self.assertLess(grip["rightGrip"][2], grip["supportGrip"][2])
            self.assertEqual(grip["muzzleAxis"], [0.0, 0.0, 1.0])
            self.assertEqual(grip["upAxis"], [0.0, 1.0, 0.0])

            pistol_option = next(
                equipment for equipment in character["equipmentOptions"]
                if equipment["slug"] == "sci-fi-battle-weapons-scifipistol01-1"
            )
            pistol_grip = pistol_option["gripProfile"]
            self.assertEqual(pistol_option["actionFamily"], "pistol")
            self.assertEqual(pistol_option["defaultClip"], "Mixamo_PistolReady")
            self.assertEqual(set(pistol_option["actionTransforms"]), set(pistol_animations))
            for transform in pistol_option["actionTransforms"].values():
                quaternion = transform["rotationQuaternion"]
                scaled_grip = [value * transform["scale"][0] for value in pistol_grip["rightGrip"]]
                grip_socket = [
                    transform["position"][axis] + rotate_vector(quaternion, scaled_grip)[axis]
                    for axis in range(3)
                ]
                self.assertLess(math.dist(grip_socket, [0.0, 0.0, 0.0]), 0.00001)
                muzzle_direction = rotate_vector(quaternion, pistol_grip["muzzleAxis"])
                self.assertGreater(sum(value * value for value in muzzle_direction), 0.999)

        rifle = entries["sci-fi-battle-weapons-scifirifle01-1"]
        self.assertTrue(rifle["textures"]["original"])
        self.assertTrue(rifle["emissiveTextures"]["original"])
        self.assertTrue(rifle["normalTextures"]["original"])
        rifle_document = read_glb(ROOT / rifle["models"]["original"])
        collision_prefixes = ("UCX_", "UBX_", "USP_", "UCP_")
        self.assertFalse(any(
            node.get("name", "").upper().startswith(collision_prefixes)
            for node in rifle_document["nodes"]
        ))

    def test_low_poly_materials_are_restored(self) -> None:
        entries = {entry["slug"]: entry for entry in self.catalog}
        ak = entries["low-poly-10-weapons-ak-47"]
        self.assertEqual(
            ak["textures"]["low"],
            "textures/ultimate-pack/low-poly-10/atlas-gradient.png",
        )
        self.assertEqual(ak["defaultClip"], "Ultimate_Rotation_Y_360_5s_Loop")
        document = read_glb(ROOT / ak["models"]["original"])
        colors = {
            material["name"]: material["pbrMetallicRoughness"]["baseColorFactor"]
            for material in document["materials"]
        }
        self.assertLess(colors["17 GREY-DARKEST"][0], 0.04)
        self.assertGreater(colors["28 ORANGE"][0], 0.9)
        self.assertLess(colors["28 ORANGE"][1], 0.25)

        sherman = entries["low-poly-10-ww2-sherman"]
        self.assertEqual(
            sherman["textureMaterialNamesByVariant"]["original"],
            ["LOWPOLY-COLORS"],
        )

        desktop = entries["low-poly-10-electronics-pc-desktop-gaming"]
        desktop_document = read_glb(ROOT / desktop["models"]["low"])
        transparent = next(
            material for material in desktop_document["materials"]
            if material["name"] == "LOWPOLY-COLORS-TRANSPARENT"
        )
        self.assertEqual(transparent["alphaMode"], "BLEND")


if __name__ == "__main__":
    unittest.main()
