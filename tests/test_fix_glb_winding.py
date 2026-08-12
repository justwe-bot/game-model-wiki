from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "fix_glb_winding.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("fix_glb_winding_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
WINDING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WINDING)


class FixGlbWindingTests(unittest.TestCase):
    def test_reverse_triangle_indices_preserves_triangle_order(self) -> None:
        self.assertEqual(
            WINDING.reverse_triangle_indices([0, 1, 2, 3, 4, 5]),
            [0, 2, 1, 3, 5, 4],
        )

    def test_orientation_score_detects_reversed_winding(self) -> None:
        positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        normals = [(0.0, 0.0, 1.0)] * 3
        self.assertEqual(WINDING.orientation_score(positions, normals, [0, 1, 2]), 1.0)
        self.assertEqual(WINDING.orientation_score(positions, normals, [0, 2, 1]), -1.0)


if __name__ == "__main__":
    unittest.main()
