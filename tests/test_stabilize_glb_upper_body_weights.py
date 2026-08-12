from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "stabilize_glb_upper_body_weights.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("stabilize_glb_upper_body_weights_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
STABILIZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STABILIZE)


class StabilizeUpperBodyWeightsTests(unittest.TestCase):
    def test_neck_and_inner_shoulder_receive_chest_support(self) -> None:
        self.assertGreater(STABILIZE.armored_chest_floor((0.0, 1.55, 0.0)), 0.9)
        self.assertGreater(STABILIZE.armored_chest_floor((0.24, 1.52, 0.0)), 0.6)
        self.assertGreater(STABILIZE.armored_chest_floor((-0.206, 1.683, 0.1)), 0.94)
        self.assertGreater(STABILIZE.armored_chest_floor((0.206, 1.683, 0.1)), 0.94)
        self.assertEqual(STABILIZE.armored_chest_floor((0.55, 1.52, 0.0)), 0.0)
        self.assertEqual(STABILIZE.armored_chest_floor((0.0, 1.82, 0.0)), 0.0)

    def test_stabilize_row_preserves_normalized_weights(self) -> None:
        joints, weights = STABILIZE.stabilize_row(
            (60, 4, 0, 0),
            (0.8, 0.2, 0.0, 0.0),
            chest_slot=3,
            chest_floor=0.9,
        )
        self.assertEqual(joints[0], 3)
        self.assertAlmostEqual(weights[0], 0.9)
        self.assertAlmostEqual(sum(weights), 1.0)


if __name__ == "__main__":
    unittest.main()
