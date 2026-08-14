from __future__ import annotations

import json
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
        self.assertIn("3,831 个模型", game["subtitle"])
        self.assertIn("45,304 组动作", game["subtitle"])
        counts = Counter(entry["packageSlug"] for entry in self.catalog)
        actions = Counter()
        for entry in self.catalog:
            actions[entry["packageSlug"]] += entry["animationCount"]
        self.assertEqual({slug: counts[slug] for slug in EXPECTED}, {slug: value[0] for slug, value in EXPECTED.items()})
        self.assertEqual({slug: actions[slug] for slug in EXPECTED}, {slug: value[1] for slug, value in EXPECTED.items()})

    def test_all_models_and_actions_are_valid(self) -> None:
        self.assertEqual(len(self.catalog), 3831)
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
        self.assertEqual(warnings, {"stylized-weapons": []})


if __name__ == "__main__":
    unittest.main()
