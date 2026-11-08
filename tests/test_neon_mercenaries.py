from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACTIONS = {"Grizzly": 6, "Scorpion": 19, "Dove": 4}
LEAK_TOKENS = ("max_tac", "maxtac", "kangtao", "traumateam", "corpo", "silverhand", "cyberpunk")


class NeonMercenariesArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.games = json.loads((ROOT / "games.json").read_text(encoding="utf-8"))
        cls.game = next(item for item in cls.games if item["slug"] == "neon-mercenaries")
        cls.catalog = json.loads(
            (ROOT / "games" / "neon-mercenaries" / "catalog.json").read_text(encoding="utf-8")
        )
        cls.index = json.loads(
            (ROOT / "games" / "neon-mercenaries" / "weapon_audio" / "audio_index.json")
            .read_text(encoding="utf-8")
        )
        cls.mercenaries_html = (ROOT / "mercenaries.html").read_text(encoding="utf-8")
        cls.index_html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.models_html = (ROOT / "models.html").read_text(encoding="utf-8")

    def test_game_is_registered_with_archive_page(self) -> None:
        self.assertEqual(self.game["name"], "霓虹佣兵")
        self.assertEqual(self.game["nameEn"], "Neon Mercenaries")
        self.assertEqual(self.game["catalog"], "games/neon-mercenaries/catalog.json")
        self.assertEqual(self.game["mercenariesPage"], "mercenaries.html")
        self.assertEqual(self.game["tiers"], ["灰熊", "毒蝎", "白鸽"])
        self.assertTrue((ROOT / "mercenaries.html").is_file())

    def test_catalog_counts_factions_and_slugs(self) -> None:
        chars = self.catalog["characters"]
        self.assertEqual(len(chars), 29)
        self.assertEqual(self.catalog["name"], "霓虹佣兵")
        self.assertEqual(self.catalog["slug"], "neon-mercenaries")
        slugs = [ch["slug"] for ch in chars]
        self.assertEqual(len(set(slugs)), 29)
        for faction, count in FACTIONS.items():
            self.assertEqual(sum(1 for ch in chars if ch["faction"] == faction), count)
        for ch in chars:
            self.assertTrue(ch["path"].startswith(f"characters/{ch['faction']}/"))
            self.assertTrue((ROOT / "games" / "neon-mercenaries" / ch["path"]).is_dir())

    def test_identity_fields_carry_no_source_game_names(self) -> None:
        for ch in self.catalog["characters"]:
            identity = ch["identity"]
            for key in ("catalogId", "faction", "appearance", "appDepotPath", "entityDepotPath"):
                value = str(identity.get(key, "")).lower()
                for token in LEAK_TOKENS:
                    self.assertNotIn(token, value, f"{ch['slug']}.{key}")

    def test_acceptance_is_declared_unaccepted(self) -> None:
        acceptance = self.catalog["acceptance"]
        self.assertEqual(acceptance["charactersAccepted"], 0)
        self.assertEqual(acceptance["charactersTotal"], 29)
        self.assertEqual(acceptance["motionCategoriesAccepted"], 0)
        self.assertEqual(acceptance["weaponAudioAccepted"], 0)
        self.assertEqual(acceptance["weaponAudioTotal"], 42)

    def test_render_and_recording_media_exist(self) -> None:
        base = ROOT / "games" / "neon-mercenaries"
        for ch in self.catalog["characters"]:
            char_dir = base / ch["path"]
            for rel in ch["renders"]:
                self.assertTrue((char_dir / "06_renders" / rel).is_file(), rel)
            recordings = ch["recordings"]
            if isinstance(recordings, dict):
                recordings = [item for group in recordings.values() for item in group]
            for rel in recordings:
                self.assertTrue((char_dir / "07_recordings" / rel).is_file(), rel)

    def test_audio_index_covers_every_leaf_once(self) -> None:
        self.assertEqual(len(self.index["weapons"]), 42)
        self.assertIn("silverline", [w["code"] for w in self.index["weapons"]])
        base = ROOT / "games" / "neon-mercenaries" / "weapon_audio"
        seen = set()
        for weapon in self.index["weapons"]:
            self.assertTrue((base / weapon["dir"]).is_dir())
            self.assertGreater(weapon["events"], 0)
            for item in weapon["items"]:
                self.assertTrue(item["label"])
                for name in item["files"]:
                    self.assertNotIn(name, seen, f"duplicate leaf {name}")
                    seen.add(name)
                    self.assertTrue((base / weapon["dir"] / "wav" / name).is_file(), name)
        self.assertEqual(self.index["totals"]["leaves"], len(seen))

    def test_pages_wire_the_archive_navigation(self) -> None:
        self.assertIn("activeGame.mercenariesPage", self.index_html)
        self.assertIn("nextGame.mercenariesPage", self.models_html)
        self.assertIn("searchParams.set('char'", self.mercenaries_html)
        self.assertIn("audio_index.json", self.mercenaries_html)
        self.assertIn("mercenariesPage", self.mercenaries_html)


if __name__ == "__main__":
    unittest.main()
