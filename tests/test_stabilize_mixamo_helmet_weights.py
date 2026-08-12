from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "stabilize_mixamo_helmet_weights.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("stabilize_mixamo_helmet_weights_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
STABILIZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STABILIZE)


class StabilizeMixamoHelmetWeightsTests(unittest.TestCase):
    def test_high_head_influence_becomes_rigid(self) -> None:
        joints, weights, mode = STABILIZE.stabilized_helmet_row(
            (5, 4, 3, 0),
            (0.6, 0.2, 0.2, 0.0),
            head_slot=5,
            neck_slot=4,
            rigid_threshold=0.35,
            minimum_head_influence=0.02,
        )
        self.assertEqual(mode, "rigid")
        self.assertEqual(joints, (5, 0, 0, 0))
        self.assertEqual(weights, (1.0, 0.0, 0.0, 0.0))

    def test_transition_keeps_only_head_and_neck(self) -> None:
        joints, weights, mode = STABILIZE.stabilized_helmet_row(
            (3, 5, 4, 7),
            (0.5, 0.25, 0.2, 0.05),
            head_slot=5,
            neck_slot=4,
            rigid_threshold=0.35,
            minimum_head_influence=0.02,
        )
        self.assertEqual(mode, "localized")
        self.assertEqual(joints[:2], (5, 4))
        self.assertAlmostEqual(weights[0], 0.25)
        self.assertAlmostEqual(weights[1], 0.75)
        self.assertAlmostEqual(sum(weights), 1.0)

    def test_unrelated_vertices_are_unchanged(self) -> None:
        row = STABILIZE.stabilized_helmet_row(
            (3, 7, 0, 0),
            (0.8, 0.2, 0.0, 0.0),
            head_slot=5,
            neck_slot=4,
            rigid_threshold=0.35,
            minimum_head_influence=0.02,
        )
        self.assertEqual(row[2], "unchanged")
        self.assertEqual(row[0], (3, 7, 0, 0))
        self.assertEqual(row[1], (0.8, 0.2, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
