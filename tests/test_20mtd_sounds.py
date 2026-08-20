from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SLUGS = {
    "gunfire",
    "crossbow-fire",
    "crossbow-charge",
    "reload-start",
    "reload-finish",
    "thunder-caller-retrieve",
    "magic-bow-retrieve",
    "batgun-fire",
    "flame-cannon-fire",
    "spray-gun-fire",
    "grenade-explosion",
}
TIER_ORDER = [
    "武器射击",
    "武器换弹",
    "近战挥砍",
    "法术技能",
    "角色动作",
    "敌人",
    "拾取增益",
    "界面",
    "音乐",
]


class TwentyMinutesTillDawnSoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.games = json.loads((ROOT / "games.json").read_text(encoding="utf-8"))
        cls.catalog = json.loads(
            (ROOT / "games" / "20-minutes-till-dawn" / "catalog.json").read_text(encoding="utf-8")
        )
        cls.index_html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.sounds_html = (ROOT / "sounds.html").read_text(encoding="utf-8")

    def test_game_is_registered_as_sounds_default(self) -> None:
        game = next(item for item in self.games if item["slug"] == "20-minutes-till-dawn")
        self.assertEqual(game["catalog"], "games/20-minutes-till-dawn/catalog.json")
        self.assertEqual(game["soundsPage"], "sounds.html")
        self.assertEqual(game["defaultView"], "sounds")
        self.assertEqual(game["tiers"], TIER_ORDER)
        self.assertIn("86 组音效事件", game["subtitle"])
        self.assertTrue((ROOT / "sounds.html").is_file())

    def test_catalog_counts_and_required_weapon_events(self) -> None:
        slugs = [entry["slug"] for entry in self.catalog]
        self.assertEqual(len(self.catalog), 86)
        self.assertEqual(len(set(slugs)), 86)
        self.assertTrue(REQUIRED_SLUGS.issubset(slugs))
        tiers = Counter(entry["tier"] for entry in self.catalog)
        self.assertEqual(tiers["武器射击"], 13)
        self.assertEqual(tiers["武器换弹"], 4)
        self.assertEqual(tiers["近战挥砍"], 4)
        self.assertEqual(tiers["音乐"], 2)
        self.assertEqual(sum(entry["clipCount"] for entry in self.catalog), 97)
        unused = [entry["slug"] for entry in self.catalog if entry["tier"] == "未挂接采样"]
        self.assertEqual(unused, [])

    def test_preview_files_exist_and_match_catalog(self) -> None:
        files = set()
        for entry in self.catalog:
            self.assertTrue(entry["name"])
            self.assertTrue(entry["nameEn"])
            self.assertTrue(entry["sourceName"])
            self.assertGreater(entry["durationSec"], 0)
            self.assertEqual(entry["clipCount"], len(entry["clips"]))
            self.assertGreater(len(entry["clips"]), 0)
            for clip in entry["clips"]:
                path = ROOT / clip["file"]
                self.assertTrue(path.is_file(), clip["file"])
                self.assertGreater(path.stat().st_size, 800)
                self.assertEqual(path.suffix, ".m4a")
                self.assertGreater(clip["durationSec"], 0)
                self.assertGreater(clip["sampleRate"], 0)
                self.assertGreater(clip["channels"], 0)
                files.add(path)
        extracted = list((ROOT / "sounds" / "20-minutes-till-dawn").glob("*.m4a"))
        self.assertEqual(len(extracted), 85)
        self.assertEqual(set(extracted), files)

    def test_index_and_sounds_pages_wire_navigation(self) -> None:
        self.assertIn('id="soundsLink"', self.index_html)
        self.assertIn("sounds-available", self.index_html)
        self.assertIn("nextGame.defaultView === 'sounds'", self.index_html)
        self.assertIn("activeGame.defaultView === 'sounds'", self.index_html)
        self.assertIn("activeGame.soundsPage", self.index_html)
        self.assertIn('id="playButton"', self.sounds_html)
        self.assertIn("loadClip(true)", self.sounds_html)
        self.assertIn("searchParams.set('sound'", self.sounds_html)
        self.assertIn("models-unavailable", self.sounds_html)


if __name__ == "__main__":
    unittest.main()
