from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAME_DIR = ROOT / "games" / "zombie-survivors"
SHOTGUN_SLUGS = {"tank-shotgun", "tank-doubleshotgun", "tank-supershotgun"}

# 这份合并目录把 classification 从「按文件来源」改成「按内容维度」，所以这里逐条核对
# 页面真正依赖的四个维度，以及每把武器自己的音效与特效。
class ZombieSurvivorModelsIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.games = json.loads((ROOT / "games.json").read_text(encoding="utf-8"))
        cls.index = json.loads((GAME_DIR / "models.json").read_text(encoding="utf-8"))
        cls.sounds = json.loads((GAME_DIR / "catalog.json").read_text(encoding="utf-8"))
        cls.weapons = json.loads((GAME_DIR / "weapons.json").read_text(encoding="utf-8"))
        cls.props = json.loads((GAME_DIR / "props.json").read_text(encoding="utf-8"))
        cls.sound_by_slug = {entry["slug"]: entry for entry in cls.sounds}
        cls.models_html = (ROOT / "models.html").read_text(encoding="utf-8")
        cls.category = {item["slug"]: item for item in cls.index["categories"]}

    def items_of(self, category_slug: str) -> list[dict]:
        category = self.category[category_slug]
        items = list(category.get("items", []))
        for group in category.get("groups", []):
            if group.get("kind") == "soundGroup":
                items.append({**group, "slug": f"{category_slug}-{group['slug']}"})
                continue
            items.extend(group.get("items", []))
        return items

    def test_game_registers_the_merged_catalogue(self) -> None:
        game = next(item for item in self.games if item["slug"] == "zombie-survivors")
        self.assertEqual(game["modelsCatalog"], "games/zombie-survivors/models.json")
        self.assertEqual(game["modelsPage"], "models.html")
        self.assertTrue((ROOT / game["modelsCatalog"]).is_file())
        self.assertTrue((ROOT / "models.html").is_file())
        self.assertIn("9 位人物", game["modelsSubtitle"])

    def test_counts_match_the_source_catalogues(self) -> None:
        counts = self.index["counts"]
        self.assertEqual(counts["characters"], len(self.weapons["characters"]))
        self.assertEqual(counts["weapons"], len(self.weapons["weapons"]))
        self.assertEqual(counts["monsters"], counts["enemies"] + counts["bosses"])
        self.assertEqual(counts["props"], len(self.props))
        self.assertEqual(counts["soundEntries"], len(self.sounds))
        self.assertEqual(counts["soundClips"], sum(entry["clipCount"] for entry in self.sounds))
        self.assertEqual(counts["weaponTypes"], len(self.category["weapons"]["groups"]))

    def test_dimensions_are_complete(self) -> None:
        self.assertEqual([item["slug"] for item in self.index["categories"]],
                         ["characters", "monsters", "weapons", "props", "ambience"])
        characters = self.items_of("characters")
        self.assertEqual(len(characters), 9)
        for character in characters:
            self.assertEqual(len(character["weaponSlugs"]), 5)
            self.assertEqual(character["soundCount"], len(character["soundSlugs"]))
            self.assertTrue(set(character["weaponSlugs"]) <= {w["slug"] for w in self.weapons["weapons"]})

        monsters = self.items_of("monsters")
        self.assertEqual(len(monsters), 33)
        self.assertEqual(Counter(item["kind"] for item in monsters), {"enemy": 28, "boss": 5})
        self.assertEqual(len({item["slug"] for item in monsters}), len(monsters))

        props = self.items_of("props")
        self.assertEqual(len(props), len(self.props))
        self.assertTrue(all(item["model"] for item in props))

    def test_every_weapon_is_grouped_and_carries_its_own_sounds(self) -> None:
        weapons = self.items_of("weapons")
        slugs = [item["slug"] for item in weapons]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(set(slugs), {weapon["slug"] for weapon in self.weapons["weapons"]})
        for weapon in weapons:
            self.assertTrue(weapon["category"])
            self.assertEqual(weapon["typeSlug"], next(
                group["slug"] for group in self.category["weapons"]["groups"]
                if weapon["slug"] in [item["slug"] for item in group["items"]]
            ))
            self.assertTrue(weapon["soundSlugs"], weapon["slug"])
            self.assertTrue(weapon["sprites"], weapon["slug"])

        # 用户要求的样子：霰弹枪这一组里每把枪都直接带着枪声与换弹声。
        shotguns = next(group for group in self.category["weapons"]["groups"] if group["slug"] == "shotgun")
        self.assertEqual({item["slug"] for item in shotguns["items"]}, SHOTGUN_SLUGS)
        for shotgun in shotguns["items"]:
            tiers = {self.sound_by_slug[slug]["tier"] for slug in shotgun["soundSlugs"]}
            self.assertIn("武器射击", tiers, shotgun["slug"])
            self.assertIn("武器装填", tiers, shotgun["slug"])

    def test_every_reference_resolves(self) -> None:
        linked: set[str] = set()
        for category in self.index["categories"]:
            for item in self.items_of(category["slug"]):
                for slug in item.get("soundSlugs", []):
                    self.assertIn(slug, self.sound_by_slug)
                    linked.add(slug)
                model = item.get("model")
                if model:
                    self.assertTrue((ROOT / model).is_file(), model)
                for sprite in item.get("sprites", []):
                    self.assertTrue((ROOT / sprite).is_file(), sprite)

        # 音效明细留在 catalog.json，所以每个条目都必须能被 models.json 找到。
        self.assertEqual(linked | set(), {entry["slug"] for entry in self.sounds})

    def test_sound_payload_is_not_duplicated(self) -> None:
        blob = (GAME_DIR / "models.json").read_text(encoding="utf-8")
        self.assertNotIn('"clips"', blob)
        self.assertLess((GAME_DIR / "models.json").stat().st_size, (GAME_DIR / "catalog.json").stat().st_size)

    def test_pages_expose_the_merged_view(self) -> None:
        self.assertIn('id="modelsLink"', self.models_html)
        self.assertIn("merged-available", self.models_html)
        self.assertIn('id="player"', self.models_html)
        self.assertIn("soundBySlug", self.models_html)
        self.assertIn("searchParams.set('item'", self.models_html)
        for page in ("index.html", "props.html", "sounds.html", "weapons.html"):
            html = (ROOT / page).read_text(encoding="utf-8")
            self.assertIn('id="modelsLink"', html, page)
            self.assertIn("merged-available", html, page)
            self.assertIn("activeGame.modelsCatalog", html, page)


if __name__ == "__main__":
    unittest.main()
