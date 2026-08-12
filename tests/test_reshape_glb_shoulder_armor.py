from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "reshape_glb_shoulder_armor.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("reshape_glb_shoulder_armor_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RESHAPE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESHAPE)


class ReshapeShoulderArmorTests(unittest.TestCase):
    def test_spatial_mask_targets_outer_shoulders_symmetrically(self) -> None:
        left = RESHAPE.shoulder_spatial_weight((-0.46, 1.53, 0.1))
        right = RESHAPE.shoulder_spatial_weight((0.46, 1.53, 0.1))
        self.assertGreater(left, 0.95)
        self.assertEqual(left, right)
        self.assertEqual(RESHAPE.shoulder_spatial_weight((0.2, 1.53, 0.1)), 0.0)
        self.assertEqual(RESHAPE.shoulder_spatial_weight((0.67, 1.53, 0.1)), 0.0)
        self.assertEqual(RESHAPE.shoulder_spatial_weight((0.9, 1.53, 0.1)), 0.0)

    def test_reshape_flattens_depth_and_height(self) -> None:
        point = RESHAPE.reshape_point(
            (0.65, 1.63, 0.2),
            source_center=(0.55, 1.53, 0.1),
            target_center=(0.55, 1.53, 0.1),
            weight=1.0,
        )
        self.assertAlmostEqual(point[0], 0.654)
        self.assertAlmostEqual(point[1], 1.605)
        self.assertAlmostEqual(point[2], 0.145)

    def test_hand_positions_are_outside_the_shoulder_mask(self) -> None:
        positions = [
            (-0.46, 1.53, 0.1),
            (0.46, 1.53, 0.1),
            (-0.70, 1.53, 0.1),
            (0.70, 1.53, 0.1),
        ]
        reshaped, _changed, _centers = RESHAPE.reshape_shoulder_armor_positions(positions)
        self.assertEqual(reshaped[2], positions[2])
        self.assertEqual(reshaped[3], positions[3])


if __name__ == "__main__":
    unittest.main()
