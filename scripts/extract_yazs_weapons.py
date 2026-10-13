"""Extract the Yet Another Zombie Survivors weapon roster from the game's I2 localization table.

The game ships an IL2CPP build with stripped type trees, so MonoBehaviour payloads are
parsed from their raw bytes. TermData records are laid out as:

    string Term | int TermType | string[] Languages | byte[] Flags | int trailer

Languages are ordered EN, PL, ZH-CN, FR, DE, JA, KO, PT-BR, RU, ES, so index 2 is
Simplified Chinese. Weapon entries live under ``Powerups/Weapons/<key>_Name`` /
``_Desc`` and use the game's own wording, which is what the Wiki shows.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import UnityPy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Yet Another Zombie Survivors"
    r"\Yet Another Zombie Survivors_Data"
)
GAME_SLUG = "yet-another-zombie-survivors"
WEAPONS_PATH = ROOT / "games" / GAME_SLUG / "weapons.json"

ZH_INDEX = 2
EN_INDEX = 0

# Character prefix in the localization key -> survivor name shown on the Wiki.
CHARACTER_BY_PREFIX = {
    "Eng": "工程师",
    "Huntress": "女猎手",
    "Mechanic": "机械师",
    "Medic": "医生",
    "Ninja": "忍者",
    "Pyro": "纵火者",
    "Ranger": "游侠",
    "Swat": "SWAT",
    "Tank": "坦克",
}

CHARACTER_EN_BY_PREFIX = {
    "Eng": "Engineer",
    "Huntress": "Huntress",
    "Mechanic": "Mechanic",
    "Medic": "Medic",
    "Ninja": "Ninja",
    "Pyro": "Pyro",
    "Ranger": "Ranger",
    "Swat": "SWAT",
    "Tank": "Tank",
}

# Which audio catalog entries each weapon fires; slugs match extract_yazs_audio.py.
WEAPON_AUDIO = {
    "Eng_Blaster": ["engineer-blaster-fire", "engineer-energy-reload"],
    "Eng_Laser": ["engineer-laser-hit", "engineer-laser-start", "engineer-laser-loop",
                  "engineer-laser-stop", "engineer-laser-reload", "engineer-energy-reload"],
    "Eng_Plasma": ["engineer-plasma-start", "engineer-plasma-loop", "engineer-plasma-stop",
                   "engineer-plasma-reload", "engineer-energy-reload"],
    "Eng_Taser": ["engineer-taser-fire", "engineer-energy-reload"],
    "Eng_Tesla": ["engineer-tesla-start", "engineer-tesla-loop", "engineer-tesla-stop",
                  "engineer-tesla-reload", "engineer-energy-reload"],
    "Huntress_Bow": ["huntress-bow-fire"],
    "Huntress_BowExplosive": ["huntress-bow-fire"],
    "Huntress_BowFreezing": ["huntress-bow-freezing-fire", "huntress-bow-fire"],
    "Huntress_BowMulti": ["huntress-bow-fire"],
    "Huntress_BowPoison": ["huntress-bow-fire"],
    "Mechanic_Chainsaw": ["mechanic-chainsaw-standby", "mechanic-chainsaw-hitting",
                          "mechanic-chainsaw-attack-loop", "mechanic-chainsaw-attack-end"],
    "Mechanic_ChaosEngine": ["mechanic-chaos-engine"],
    "Mechanic_KeyBlade": ["mechanic-spanner-melee", "mechanic-spanner-hit", "mechanic-spanner-throw"],
    "Mechanic_Lancer": ["mechanic-lancer-burst", "mechanic-lancer-melee"],
    "Mechanic_NitroGun": ["mechanic-nitro-gun-fire", "mechanic-nitro-gun-hit"],
    "Medic_DotaBottleThrow": ["medic-experiment-throw", "medic-experiment-toxic"],
    "Medic_FreezeBottleThrow": ["medic-experiment-throw", "medic-experiment-ice"],
    "Medic_Handgun": ["medic-handgun-fire", "universal-reload"],
    "Medic_SyringeThrow": ["medic-syringe-gun-fire"],
    "Medic_SyringeRifle": ["medic-syringer-fire", "universal-reload"],
    "Ninja_Katana": ["sword-woosh", "blade-hit"],
    "Ninja_KatanaHurricaneWave": ["ninja-windcutter", "sword-woosh"],
    "Ninja_KatanaMultiSlash": ["ninja-thousand-cuts", "sword-woosh", "blade-hit"],
    "Ninja_KatanaSplash": ["sword-woosh", "blade-hit"],
    "Ninja_KatanaSuperSlash": ["ninja-soul-reaper-slash", "ninja-soul-reaper-impact", "sword-woosh"],
    "Pyro_AxeThrow": ["pyro-axerang-throw", "pyro-axerang-flying"],
    "Pyro_Blowtorch": ["pyro-flamethrower-start", "pyro-flamethrower-loop", "pyro-flamethrower-stop"],
    "Pyro_Fireaxe": ["sword-woosh", "blade-hit"],
    "Pyro_Flamethrower": ["pyro-flamethrower-start", "pyro-flamethrower-loop", "pyro-flamethrower-stop"],
    "Pyro_Infernax": ["pyro-infernax-swing", "sword-woosh"],
    "Ranger_Crossbow": ["ranger-crossbow-fire", "ranger-crossbow-penetrate", "ranger-crossbow-reload"],
    "Ranger_CrossbowBarber": ["ranger-crossbow-barber-fire", "ranger-crossbow-barber-cut",
                              "ranger-crossbow-reload"],
    "Ranger_CrossbowFirebrand": ["ranger-crossbow-firebrand-fire", "ranger-crossbow-firebrand-spread",
                                 "ranger-crossbow-penetrate", "ranger-crossbow-reload"],
    "Ranger_CrossbowRepeater": ["ranger-crossbow-repeater-fire", "ranger-crossbow-penetrate",
                                "ranger-crossbow-reload"],
    "Ranger_CrossbowShockspike": ["ranger-crossbow-shockspike-fire", "ranger-crossbow-shockspike-pulse",
                                  "ranger-crossbow-shockspike-activate", "ranger-crossbow-reload"],
    "Swat_GrenadeLauncher": ["swat-grenade-launcher-fire", "explosion-small"],
    "Swat_Pistol": ["swat-pistol-fire", "universal-reload"],
    "Swat_Rifle": ["swat-rifle-fire", "universal-reload"],
    "Swat_Sniper": ["swat-sniper-fire", "sniper-rifle-reload"],
    "Swat_Uzi": ["swat-smg-fire", "universal-reload"],
    "Tank_DoubleShotgun": ["tank-shotgun-fire", "shotgun-reload"],
    "Tank_Minigun": ["tank-minigun-start", "tank-minigun-loop", "tank-minigun-stop", "universal-reload"],
    "Tank_Rocket": ["tank-rocket-fire", "explosion-rocket"],
    "Tank_Shotgun": ["tank-shotgun-fire", "shotgun-reload"],
    "Tank_SuperShotgun": ["tank-shotgun-fire", "shotgun-reload"],
}


def align4(value: int) -> int:
    return (value + 3) & ~3


def find_localization_object(data_dir: Path, name: str = "I2Languages"):
    """Locate the I2LanguageSource MonoBehaviour holding every localized term."""
    candidates = sorted(
        (p for p in data_dir.iterdir() if p.is_file() and p.stat().st_size > 1024),
        key=lambda p: (p.suffix not in (".assets",), p.name),
    )
    for path in candidates:
        if path.name.endswith((".resS", ".resource")):
            continue
        try:
            env = UnityPy.load(str(path))
        except Exception:  # noqa: BLE001 - unrelated files may not parse
            continue
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                if obj.peek_name() != name:
                    continue
            except Exception:  # noqa: BLE001
                continue
            return path.name, obj
    return None, None


def parse_terms(raw: bytes) -> list[dict]:
    """Parse TermData records out of a raw LanguageSourceData payload."""
    name_len = struct.unpack_from("<i", raw, 28)[0]
    pos = align4(32 + name_len) + 16  # skip the four int32 header fields
    terms: list[dict] = []
    while pos < len(raw) - 8:
        length = struct.unpack_from("<i", raw, pos)[0]
        if not 0 <= length < 4096:
            break
        try:
            term = raw[pos + 4:pos + 4 + length].decode("utf-8")
        except UnicodeDecodeError:
            break
        pos = align4(pos + 4 + length)
        if pos + 8 > len(raw):
            break
        term_type = struct.unpack_from("<i", raw, pos)[0]
        pos += 4
        count = struct.unpack_from("<i", raw, pos)[0]
        pos += 4
        if not 0 <= count <= 64:
            break
        languages = []
        ok = True
        for _ in range(count):
            if pos + 4 > len(raw):
                ok = False
                break
            length = struct.unpack_from("<i", raw, pos)[0]
            pos += 4
            if not 0 <= length < 20000:
                ok = False
                break
            languages.append(raw[pos:pos + length].decode("utf-8", "replace"))
            pos = align4(pos + length)
        if not ok:
            break
        flag_count = struct.unpack_from("<i", raw, pos)[0]
        pos += 4 + align4(flag_count) + 4
        terms.append({"term": term, "type": term_type, "langs": languages})
    return terms


def build_weapons(terms: list[dict]) -> list[dict]:
    by_term = {entry["term"]: entry for entry in terms}
    prefix = "Powerups/Weapons/"
    weapons = []
    for term in sorted(by_term):
        if not term.startswith(prefix) or not term.endswith("_Name"):
            continue
        key = term[len(prefix):-len("_Name")]
        name_entry = by_term[term]
        desc_entry = by_term.get(f"{prefix}{key}_Desc")
        character_key = key.split("_", 1)[0]
        if len(name_entry["langs"]) <= ZH_INDEX or name_entry["langs"][ZH_INDEX] == "":
            continue
        weapons.append({
            "key": key,
            "slug": key.lower().replace("_", "-"),
            "name": name_entry["langs"][ZH_INDEX],
            "nameEn": name_entry["langs"][EN_INDEX],
            "character": CHARACTER_BY_PREFIX.get(character_key, character_key),
            "characterEn": CHARACTER_EN_BY_PREFIX.get(character_key, character_key),
            "summary": (desc_entry["langs"][ZH_INDEX] if desc_entry else ""),
            "summaryEn": (desc_entry["langs"][EN_INDEX] if desc_entry else ""),
            "sourceTerm": f"{prefix}{key}",
            "audio": WEAPON_AUDIO.get(key, []),
        })
    weapons.sort(key=lambda item: (item["characterEn"], item["nameEn"]))
    return weapons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME,
                        help="path to '<Game>_Data' directory")
    args = parser.parse_args()

    if not args.game.is_dir():
        print(f"游戏数据目录不存在: {args.game}", file=sys.stderr)
        return 2

    source, obj = find_localization_object(args.game)
    if obj is None:
        print("未找到 I2Languages 本地化资源", file=sys.stderr)
        return 3

    terms = parse_terms(obj.get_raw_data())
    print(f"{source}: 解析出 {len(terms)} 条本地化术语")

    weapons = build_weapons(terms)
    unknown = [weapon["key"] for weapon in weapons if weapon["key"] not in WEAPON_AUDIO]
    if unknown:
        print(f"以下武器缺少 WEAPON_AUDIO 映射，请补充后再生成：{unknown}", file=sys.stderr)
        return 4

    characters: dict[str, int] = {}
    for weapon in weapons:
        characters[weapon["character"]] = characters.get(weapon["character"], 0) + 1

    payload = {
        "game": GAME_SLUG,
        "source": source,
        "sourceAsset": "I2Languages (I2 Localization)",
        "languageIndex": {"English": EN_INDEX, "ChineseSimplified": ZH_INDEX},
        "totalWeapons": len(weapons),
        "characters": [{"name": name, "count": count} for name, count in sorted(characters.items())],
        "weapons": weapons,
    }
    WEAPONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEAPONS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写出 {WEAPONS_PATH.relative_to(ROOT)}：{len(weapons)} 把武器")
    for name, count in sorted(characters.items()):
        print(f"  {name:6s} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
