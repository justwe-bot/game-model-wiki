from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts import retarget_mixamo_animation


class RetargetMixamoAnimationTests(unittest.TestCase):
    def test_animation_tracks_extracts_cubic_spline_values(self) -> None:
        document = {
            "animations": [],
            "nodes": [{}],
        }
        animation = {
            "samplers": [
                {"input": 0, "output": 1, "interpolation": "CUBICSPLINE"},
            ],
            "channels": [
                {"sampler": 0, "target": {"node": 0, "path": "translation"}},
            ],
        }
        rows = {
            0: [(0.0,), (1.0,)],
            1: [
                (10.0, 10.0, 10.0),
                (1.0, 2.0, 3.0),
                (20.0, 20.0, 20.0),
                (30.0, 30.0, 30.0),
                (4.0, 5.0, 6.0),
                (40.0, 40.0, 40.0),
            ],
        }
        with patch.object(
            retarget_mixamo_animation,
            "read_accessor",
            side_effect=lambda _document, _binary, accessor: rows[accessor],
        ):
            tracks = retarget_mixamo_animation.animation_tracks(document, bytearray(), animation)

        times, values, interpolation = tracks[(0, "translation")]
        self.assertEqual(times, [0.0, 1.0])
        self.assertEqual(values, [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
        self.assertEqual(interpolation, "LINEAR")

    def test_mixamo_sides_map_anatomically(self) -> None:
        mapping = dict(retarget_mixamo_animation.MIXAMO_TO_TEMPLATE)
        self.assertEqual(mapping["mixamorig:LeftUpLeg"], "thigh.l")
        self.assertEqual(mapping["mixamorig:RightUpLeg"], "thigh.r")
        self.assertEqual(mapping["mixamorig:LeftHand"], "hand.l")
        self.assertEqual(mapping["mixamorig:RightHand"], "hand.r")

    def test_half_turn_alignment_reverses_source_heading(self) -> None:
        aligned = retarget_mixamo_animation.quat_rotate(
            retarget_mixamo_animation.yaw_quaternion(180.0),
            (0.0, 0.0, 1.0),
        )
        self.assertAlmostEqual(aligned[0], 0.0, places=6)
        self.assertAlmostEqual(aligned[1], 0.0, places=6)
        self.assertAlmostEqual(aligned[2], -1.0, places=6)

    def test_main_updates_rig_animation_count(self) -> None:
        document = {
            "asset": {"extras": {"rig": {"animations": 20}}},
            "animations": [{"name": f"Existing_{index}"} for index in range(20)],
        }
        binary = bytearray()

        def append_animation(target_document, _binary, source, name, source_yaw_degrees):
            target_document["animations"].append({"name": name})
            return {
                "name": name,
                "source": source.name,
                "sourceYawDegrees": source_yaw_degrees,
            }

        with (
            patch.object(retarget_mixamo_animation, "read_glb", return_value=(document, binary)),
            patch.object(retarget_mixamo_animation, "retarget_animation", side_effect=append_animation),
            patch.object(retarget_mixamo_animation, "write_glb") as write_glb,
            patch.object(
                sys,
                "argv",
                [
                    "retarget_mixamo_animation.py",
                    "target.glb",
                    "output.glb",
                    "--animation",
                    "Mixamo_Run=source.glb",
                    "--source-yaw-degrees",
                    "180",
                ],
            ),
            patch("builtins.print"),
        ):
            retarget_mixamo_animation.main()

        self.assertEqual(document["asset"]["extras"]["rig"]["animations"], 21)
        self.assertEqual(len(document["asset"]["extras"]["mixamoRetarget"]["animations"]), 1)
        self.assertEqual(
            document["asset"]["extras"]["mixamoRetarget"]["animations"][0]["sourceYawDegrees"],
            180.0,
        )
        write_glb.assert_called_once()

    def test_require_finite_rows_rejects_invalid_keyframes(self) -> None:
        retarget_mixamo_animation.require_finite_rows("valid", [(0.0, 1.0)])
        with self.assertRaisesRegex(ValueError, "no keyframes"):
            retarget_mixamo_animation.require_finite_rows("empty", [])
        with self.assertRaisesRegex(ValueError, "non-finite"):
            retarget_mixamo_animation.require_finite_rows("invalid", [(math.nan,)])


if __name__ == "__main__":
    unittest.main()
