from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_sci_fi_ultimate_packs import (  # noqa: E402
    PISTOL_EQUIPMENT_TRANSFORM,
    PISTOL_GRIP_PROFILE,
)
from merge_glb_animations import read_glb  # noqa: E402
from retarget_mixamo_animation import (  # noqa: E402
    Transform,
    animation_tracks,
    hierarchy,
    node_transform,
    quat_inverse,
    quat_multiply,
    quat_rotate,
    sample_track,
    vec_add,
    vec_subtract,
    world_transforms,
)


PISTOL_CLIPS = (
    "Mixamo_PistolReady",
    "Mixamo_PistolFireStanding",
    "Mixamo_PistolRun",
)
PHASES = (0.125, 0.375, 0.625, 0.875)


def sampled_world(document: dict, binary: bytearray, clip_name: str, phase: float):
    animation = next(animation for animation in document["animations"] if animation["name"] == clip_name)
    tracks = animation_tracks(document, binary, animation)
    duration = max(times[-1] for times, _values, _interpolation in tracks.values())
    parents, order = hierarchy(document)
    local = [node_transform(node) for node in document["nodes"]]
    for (node_index, path), (times, values, interpolation) in tracks.items():
        sampled = sample_track(times, values, duration * phase, path, interpolation)
        current = local[node_index]
        if path == "translation":
            local[node_index] = Transform(sampled, current.rotation, current.scale)
        elif path == "rotation":
            local[node_index] = Transform(current.translation, sampled, current.scale)
        elif path == "scale":
            local[node_index] = Transform(current.translation, current.rotation, sampled)
    return world_transforms(local, parents, order)


class SciFiPistolGripTests(unittest.TestCase):
    def test_two_hand_grip_and_trigger_finger_geometry(self) -> None:
        for character_index in (1, 6):
            path = (
                ROOT
                / "models"
                / "ultimate-pack"
                / "sci-fi-civilians"
                / f"scificivilians-{character_index:02d}.glb"
            )
            document, binary_bytes = read_glb(path)
            binary = bytearray(binary_bytes)
            names = {node.get("name", ""): index for index, node in enumerate(document["nodes"])}
            grip_report = document["asset"]["extras"]["pistolGripPose"]
            self.assertEqual(grip_report["customCurlAxes"], 3)
            self.assertEqual(grip_report["firePoseSource"], "Mixamo_PistolReady")
            self.assertGreaterEqual(grip_report["fireRecoil"]["distance"], 0.04)

            for clip_name in PISTOL_CLIPS:
                for phase in PHASES:
                    with self.subTest(character=character_index, clip=clip_name, phase=phase):
                        world = sampled_world(document, binary, clip_name, phase)
                        prop_r = world[names["prop_r"]]
                        prop_l = world[names["prop_l"]]
                        self.assertLess(math.dist(prop_r.translation, prop_l.translation), 0.055)

                        support_distances = {
                            finger: math.dist(
                                world[names[f"{finger}_03_l"]].translation,
                                prop_r.translation,
                            )
                            for finger in ("index", "middle", "ring", "pinky")
                        }
                        self.assertLess(support_distances["index"], 0.05)
                        self.assertLess(support_distances["middle"], 0.05)
                        self.assertLess(support_distances["ring"], 0.06)
                        self.assertLess(support_distances["pinky"], 0.08)

                        weapon_rotation = quat_multiply(
                            prop_r.rotation,
                            tuple(PISTOL_EQUIPMENT_TRANSFORM["rotationQuaternion"]),
                        )
                        index_tip_delta = quat_rotate(
                            quat_inverse(weapon_rotation),
                            vec_subtract(
                                world[names["index_03_r"]].translation,
                                prop_r.translation,
                            ),
                        )
                        index_tip = vec_add(tuple(PISTOL_GRIP_PROFILE["rightGrip"]), index_tip_delta)
                        self.assertGreater(index_tip[0], 0.02)
                        self.assertLess(index_tip[0], 0.04)
                        self.assertGreater(index_tip[1], -0.03)
                        self.assertLess(index_tip[1], -0.005)
                        self.assertGreater(index_tip[2], -0.005)
                        self.assertLess(index_tip[2], 0.02)

    def test_fire_clip_contains_upper_body_recoil(self) -> None:
        path = ROOT / "models" / "ultimate-pack" / "sci-fi-civilians" / "scificivilians-01.glb"
        document, binary_bytes = read_glb(path)
        binary = bytearray(binary_bytes)
        names = {node.get("name", ""): index for index, node in enumerate(document["nodes"])}
        animation = next(
            animation for animation in document["animations"]
            if animation["name"] == "Mixamo_PistolFireStanding"
        )
        recoil_channel = next(
            channel for channel in animation["channels"]
            if channel["target"] == {"node": names["spine_01"], "path": "translation"}
        )
        tracks = animation_tracks(document, binary, animation)
        _times, translations, _interpolation = tracks[(recoil_channel["target"]["node"], "translation")]
        rest_translation = node_transform(document["nodes"][names["spine_01"]]).translation
        self.assertGreater(
            max(math.dist(rest_translation, translation) for translation in translations),
            0.039,
        )


if __name__ == "__main__":
    unittest.main()
