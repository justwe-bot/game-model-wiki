from __future__ import annotations

import json
import struct
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FULL_CATALOG = ROOT / "games" / "ultimate-pack" / "catalog.local.json"
EXPECTED = {
    "robots-01": (15, 292),
    "robots-02": (15, 275),
    "monsters-03": (16, 285),
    "low-poly-10": (3137, 44452),
    "stylized-weapons": (648, 0),
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


class UltimatePackInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.games = json.loads((ROOT / "games.json").read_text(encoding="utf-8"))
        cls.inventory = json.loads(
            (ROOT / "games" / "ultimate-pack" / "inventory.json").read_text(encoding="utf-8")
        )
        cls.entries = cls.inventory["entries"]
        cls.action_sets = cls.inventory["actionSets"]

    def test_category_and_packages_are_registered(self) -> None:
        game = next(item for item in self.games if item["slug"] == "ultimate-pack")
        self.assertEqual(game["catalog"], "games/ultimate-pack/catalog.local.json")
        self.assertEqual(game["fallbackCatalog"], "games/ultimate-pack/inventory.json")
        self.assertIn("3,831 个模型", game["subtitle"])
        self.assertIn("45,304 组动作", game["subtitle"])

    def test_public_inventory_has_all_models_and_actions(self) -> None:
        self.assertTrue(self.inventory["metadataOnly"])
        self.assertEqual(len(self.entries), 3831)
        self.assertEqual(len({entry["slug"] for entry in self.entries}), len(self.entries))
        counts = Counter(entry["packageSlug"] for entry in self.entries)
        actions = Counter()
        for entry in self.entries:
            action_set = self.action_sets[entry["animationSet"]]
            self.assertEqual(entry["animationCount"], len(action_set))
            actions[entry["packageSlug"]] += len(action_set)
            self.assertFalse(entry["available"])
            for forbidden in ("models", "textures", "emissiveTextures", "normalTextures"):
                self.assertNotIn(forbidden, entry)
        self.assertEqual(dict(counts), {slug: value[0] for slug, value in EXPECTED.items()})
        self.assertEqual(dict(actions), {slug: value[1] for slug, value in EXPECTED.items()})

    def test_public_metadata_does_not_reference_commercial_files(self) -> None:
        raw = (ROOT / "games" / "ultimate-pack" / "inventory.json").read_text(encoding="utf-8")
        self.assertNotIn("models/ultimate-pack/", raw)
        self.assertNotIn("textures/ultimate-pack/", raw)
        self.assertNotIn(".glb", raw.lower())
        self.assertNotIn(".fbx", raw.lower())


@unittest.skipUnless(FULL_CATALOG.is_file(), "local purchased Ultimate Pack assets are not generated")
class UltimatePackLocalAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads(FULL_CATALOG.read_text(encoding="utf-8"))

    def test_all_local_models_and_actions_are_valid(self) -> None:
        self.assertEqual(len(self.catalog), 3831)
        inspected: dict[Path, dict] = {}
        for entry in self.catalog:
            with self.subTest(slug=entry["slug"]):
                for variant, model in entry["models"].items():
                    model_path = ROOT / model
                    self.assertTrue(model_path.is_file(), model)
                    document = inspected.setdefault(model_path, read_glb(model_path))
                    variant_clips = [
                        item["clip"]
                        for item in entry["animations"]
                        if not item.get("variants") or variant in item["variants"]
                    ]
                    self.assertEqual(
                        [item.get("name") for item in document.get("animations", [])],
                        variant_clips,
                    )
                    self.assertGreater(len(document.get("meshes", [])), 0)
                    for animation in document.get("animations", []):
                        for channel in animation.get("channels", []):
                            self.assertLess(channel["target"]["node"], len(document["nodes"]))
                            self.assertLess(channel["sampler"], len(animation["samplers"]))
                        for sampler in animation.get("samplers", []):
                            self.assertLess(sampler["input"], len(document["accessors"]))
                            self.assertLess(sampler["output"], len(document["accessors"]))


if __name__ == "__main__":
    unittest.main()
