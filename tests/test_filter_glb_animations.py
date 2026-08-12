import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from filter_glb_animations import filter_document_animations


class FilterGlbAnimationsTests(unittest.TestCase):
    def test_keeps_only_matching_prefixes(self) -> None:
        document = {
            "animations": [
                {"name": "Commando_Idle"},
                {"name": "SpaceCommando_HeavyFire"},
                {"name": "Mixamo_Idle"},
                {"name": "Mixamo_RifleRunFire"},
            ],
            "asset": {"extras": {"rig": {"animations": 4}}},
        }

        kept, removed = filter_document_animations(document, ("Mixamo_",))

        self.assertEqual(kept, ["Mixamo_Idle", "Mixamo_RifleRunFire"])
        self.assertEqual(removed, ["Commando_Idle", "SpaceCommando_HeavyFire"])
        self.assertEqual(document["asset"]["extras"]["rig"]["animations"], 2)

    def test_rejects_empty_results(self) -> None:
        document = {"animations": [{"name": "Commando_Idle"}]}

        with self.assertRaisesRegex(ValueError, "No animations matched"):
            filter_document_animations(document, ("Mixamo_",))


if __name__ == "__main__":
    unittest.main()
