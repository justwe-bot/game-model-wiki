"""Build games/zombie-survivors/models.json: one merged 人物/怪物/武器/道具 index.

The category used to be split across three catalogues (weapons.json, catalog.json for
sounds, props.json for scene props), so a weapon's model, its fire/reload sounds and its
hit VFX lived in three different files. This script folds them into a single tree and
keeps the per-weapon sound list, so the merged page can show one entry per weapon.

Nothing is duplicated: sound payloads stay in catalog.json and are referenced by slug.

Usage:
    python scripts/build_yazs_models_index.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME_SLUG = "zombie-survivors"
GAME_DIR = ROOT / "games" / GAME_SLUG
WEAPONS_PATH = GAME_DIR / "weapons.json"
SOUNDS_PATH = GAME_DIR / "catalog.json"
PROPS_PATH = GAME_DIR / "props.json"
OUTPUT_PATH = GAME_DIR / "models.json"
GAMES_PATH = ROOT / "games.json"
MODELS_PAGE = "models.html"

# Weapons are grouped by form factor rather than by owner, because that is how the
# wiki is browsed: "霰弹枪" should collect every shotgun and its sounds in one place.
WEAPON_TYPES: list[tuple[str, str, str, list[str]]] = [
    ("shotgun", "霰弹枪", "Shotguns", ["tank-shotgun", "tank-doubleshotgun", "tank-supershotgun"]),
    ("handgun", "手枪", "Handguns", ["swat-pistol", "medic-handgun", "medic-syringethrow"]),
    ("rifle", "步枪与冲锋枪", "Rifles & SMGs", ["swat-rifle", "swat-sniper", "swat-uzi", "medic-syringerifle"]),
    ("heavy", "机枪与重武器", "Heavy Weapons", ["tank-minigun", "tank-rocket", "swat-grenadelauncher", "mechanic-nitrogun"]),
    ("bow", "弓与弩", "Bows & Crossbows", [
        "huntress-bow", "huntress-bowexplosive", "huntress-bowfreezing", "huntress-bowmulti",
        "huntress-bowpoison", "ranger-crossbow", "ranger-crossbowrepeater", "ranger-crossbowfirebrand",
        "ranger-crossbowshockspike", "ranger-crossbowbarber",
    ]),
    ("melee", "近战与投掷", "Melee & Thrown", [
        "ninja-katana", "ninja-katanasplash", "ninja-katanasuperslash", "ninja-katanamultislash",
        "ninja-katanahurricanewave", "mechanic-chainsaw", "mechanic-lancer", "mechanic-keyblade",
        "mechanic-chaosengine", "pyro-axethrow", "pyro-fireaxe", "pyro-infernax",
    ]),
    ("fire", "火焰与燃烧", "Fire", ["pyro-blowtorch", "pyro-flamethrower"]),
    ("energy", "能量与科技", "Energy & Tech", ["eng-blaster", "eng-laser", "eng-plasma", "eng-taser", "eng-tesla"]),
    ("flask", "药剂与投掷瓶", "Flasks", ["medic-dotabottlethrow", "medic-freezebottlethrow"]),
]

# Tile order for the world/system tier list; unclassified entries keep their own tier.
AMBIENCE_TIER_ORDER = ["事件与机关", "拾取与成长", "界面", "音乐与环境"]

# Group names are Chinese, so an explicit map keeps group slugs readable and unique
# (a bare slugify would collapse every Chinese name to the same fallback).
GROUP_SLUGS = {
    "植被": "vegetation", "生活道具": "household", "未命名模型": "unnamed",
    "建筑与结构": "buildings", "装饰与陈设": "decor", "载具与部件": "vehicles",
    "废墟与残骸": "ruins", "其它": "misc", "地形与道路": "terrain",
    "事件与机关": "events", "拾取与成长": "pickups", "界面": "ui",
    "音乐与环境": "music", "武器射击": "weapon-fire", "武器装填": "weapon-reload",
    "近战与投掷": "melee-throw", "技能与召唤": "skills", "爆炸与命中": "explosions",
    "角色动作": "character-actions", "敌人": "enemies", "头目": "bosses",
}

MODEL_NOTE = (
    "本分类目前没有三维模型：游戏是 Unity IL2CPP 构建，现有提取脚本只导出了关卡场景道具。"
    "该条目聚合的是官方名称、说明、音效与 2D 命中特效。"
)


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def group_slug(name: str, used: set[str]) -> str:
    base = GROUP_SLUGS.get(name) or slugify(name) or "group"
    slug, index = base, 2
    while slug in used:
        slug = f"{base}-{index}"
        index += 1
    used.add(slug)
    return slug


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build() -> dict:
    weapons_doc = load(WEAPONS_PATH)
    sounds = load(SOUNDS_PATH)
    props = load(PROPS_PATH)

    weapons = weapons_doc["weapons"]
    weapon_by_slug = {w["slug"]: w for w in weapons}
    if len(weapon_by_slug) != len(weapons):
        raise SystemExit("duplicate weapon slug in weapons.json")
    sound_by_slug = {s["slug"]: s for s in sounds}
    if len(sound_by_slug) != len(sounds):
        raise SystemExit("duplicate sound slug in catalog.json")

    # --- weapon type assignment ------------------------------------------------
    type_of: dict[str, tuple[str, str, str]] = {}
    for type_slug, type_name, type_name_en, slugs in WEAPON_TYPES:
        for slug in slugs:
            if slug not in weapon_by_slug:
                raise SystemExit(f"weapon type {type_slug} references unknown weapon {slug}")
            if slug in type_of:
                raise SystemExit(f"weapon {slug} assigned to two types")
            type_of[slug] = (type_slug, type_name, type_name_en)
    unmapped = [w["slug"] for w in weapons if w["slug"] not in type_of]
    if unmapped:
        raise SystemExit(f"weapons missing a type: {unmapped}")

    # --- character dimensions --------------------------------------------------
    character_order: list[tuple[str, str]] = []  # (中文名, 英文名)
    for weapon in weapons:
        pair = (weapon["character"], weapon.get("characterEn", weapon["character"]))
        if pair not in character_order:
            character_order.append(pair)

    weapons_of_character: dict[str, list[str]] = {name: [] for name, _ in character_order}
    for weapon in weapons:
        weapons_of_character[weapon["character"]].append(weapon["slug"])

    # --- monster dimensions ----------------------------------------------------
    monster_order: list[tuple[str, str]] = []  # (显示名, kind)
    for tier, kind in (("敌人", "enemy"), ("头目", "boss")):
        for entry in sounds:
            if entry["tier"] != tier:
                continue
            for name in entry.get("usedBy", []):
                if (name, kind) not in monster_order:
                    monster_order.append((name, kind))

    # --- which sounds belong where --------------------------------------------
    # Start from the explicit weapon -> audio links in weapons.json, then widen with the
    # usedBy names so shared effects reach every weapon they belong to.
    weapon_sounds: dict[str, list[str]] = {w["slug"]: list(w.get("audio", [])) for w in weapons}

    character_sounds: dict[str, list[str]] = {name: [] for name, _ in character_order}
    monster_sounds: dict[str, list[str]] = {name: [] for name, _ in monster_order}
    unclassified: list[str] = []

    character_names = {name for name, _ in character_order}
    monster_names = {name for name, _ in monster_order}
    weapon_names = {w["name"]: w["slug"] for w in weapons}
    weapon_name_set = set(weapon_names)

    for entry in sounds:
        slug = entry["slug"]
        used = set(entry.get("usedBy", []))
        matched = False
        for name in used & character_names:
            character_sounds[name].append(slug)
            matched = True
        for name in used & monster_names:
            monster_sounds[name].append(slug)
            matched = True
        # A sound can name a weapon directly (shared hit/status effects), which is how
        # 冰冻箭 / 液氮枪 / 冰冻瓶 all end up owning 冰冻特效.
        for name in used & weapon_name_set:
            weapon_slug = weapon_names[name]
            if slug not in weapon_sounds[weapon_slug]:
                weapon_sounds[weapon_slug].append(slug)
            matched = True
        # A sound listed on a weapon also belongs to that weapon's owner.
        for weapon in weapons:
            if slug in weapon_sounds[weapon["slug"]]:
                owner = weapon["character"]
                if slug not in character_sounds[owner]:
                    character_sounds[owner].append(slug)
                matched = True
        if not matched:
            unclassified.append(slug)

    def sound_stats(slugs: list[str]) -> tuple[int, int, float]:
        clips = sum(sound_by_slug[s]["clipCount"] for s in slugs)
        seconds = round(sum(sound_by_slug[s]["durationSec"] for s in slugs), 2)
        tiers = sorted({sound_by_slug[s]["tier"] for s in slugs}, key=lambda t: _tier_key(t))
        return clips, seconds, tiers

    tier_order = [t for t in dict.fromkeys(s["tier"] for s in sounds)]

    def _tier_key(tier: str) -> int:
        return tier_order.index(tier) if tier in tier_order else len(tier_order)

    # --- characters ------------------------------------------------------------
    character_items = []
    for name, name_en in character_order:
        slugs = character_sounds[name]
        clips, seconds, tiers = sound_stats(slugs)
        character_items.append({
            "kind": "character",
            "slug": slugify(name_en) or slugify(name),
            "name": name,
            "nameEn": name_en,
            "model": None,
            "modelNote": MODEL_NOTE,
            "weapons": [weapon_by_slug[s]["name"] for s in weapons_of_character[name]],
            "weaponSlugs": weapons_of_character[name],
            "weaponTypes": list(dict.fromkeys(type_of[s][1] for s in weapons_of_character[name])),
            "soundSlugs": slugs,
            "soundCount": len(slugs),
            "clipCount": clips,
            "durationSec": seconds,
            "soundTiers": tiers,
        })

    # --- monsters --------------------------------------------------------------
    kind_labels = {"enemy": "普通敌人", "boss": "头目"}
    monster_groups = {"enemy": [], "boss": []}
    monster_counts = {"enemy": 0, "boss": 0}
    for name, kind in monster_order:
        monster_counts[kind] += 1
        slugs = monster_sounds[name]
        clips, seconds, tiers = sound_stats(slugs)
        monster_groups[kind].append({
            "kind": kind,
            "slug": f"{kind}-{monster_counts[kind]:02d}",
            "name": name,
            "nameEn": name,
            "model": None,
            "modelNote": MODEL_NOTE,
            "soundSlugs": slugs,
            "soundCount": len(slugs),
            "clipCount": clips,
            "durationSec": seconds,
            "soundTiers": tiers,
        })

    # --- weapons ---------------------------------------------------------------
    weapon_groups = []
    for type_slug, type_name, type_name_en, slugs in WEAPON_TYPES:
        items = []
        for slug in slugs:
            weapon = weapon_by_slug[slug]
            weapon_slugs = weapon_sounds[slug]
            clips, seconds, tiers = sound_stats(weapon_slugs)
            items.append({
                "kind": "weapon",
                "slug": slug,
                "name": weapon["name"],
                "nameEn": weapon["nameEn"],
                "category": type_name,
                "typeSlug": type_slug,
                "character": weapon["character"],
                "characterEn": weapon.get("characterEn", weapon["character"]),
                "characterSlug": slugify(weapon.get("characterEn", weapon["character"])),
                "summary": weapon.get("summary", ""),
                "summaryEn": weapon.get("summaryEn", ""),
                "model": None,
                "modelNote": MODEL_NOTE,
                "vfx": weapon.get("hitEffect") or {},
                "sprites": sorted({
                    value for key, value in (weapon.get("hitEffect") or {}).items()
                    if key.endswith("Sprite") and isinstance(value, str)
                }),
                "soundSlugs": weapon_slugs,
                "soundCount": len(weapon_slugs),
                "clipCount": clips,
                "durationSec": seconds,
                "soundTiers": tiers,
            })
        weapon_groups.append({
            "slug": type_slug,
            "name": type_name,
            "nameEn": type_name_en,
            "items": items,
        })

    # --- scene props -----------------------------------------------------------
    prop_groups: dict[str, list[dict]] = {}
    for prop in props:
        prop_groups.setdefault(prop["category"], []).append({
            "kind": "prop",
            "slug": prop["slug"],
            "name": prop["name"],
            "nameEn": prop.get("name", ""),
            "category": prop["category"],
            "model": prop.get("model"),
            "sourceScene": prop.get("sourceScene"),
            "inGameObject": prop.get("inGameObject"),
            "triangles": prop.get("triangles"),
            "subMeshes": prop.get("subMeshes"),
            "materials": prop.get("materials", []),
            "textures": prop.get("textures", {}),
            "sizeKB": prop.get("sizeKB"),
        })

    # --- world / system sounds -------------------------------------------------
    # Group every unclassified entry by its own tier so no sound entry is dropped.
    unclassified_set = set(unclassified)
    unclassified_by_tier: dict[str, list[str]] = {}
    for entry in sounds:
        if entry["slug"] in unclassified_set:
            unclassified_by_tier.setdefault(entry["tier"], []).append(entry["slug"])

    ordered_tiers = [tier for tier in AMBIENCE_TIER_ORDER if tier in unclassified_by_tier]
    ordered_tiers += [
        tier for tier in tier_order
        if tier in unclassified_by_tier and tier not in AMBIENCE_TIER_ORDER
    ]

    ambience_groups = []
    used_group_slugs: set[str] = set()
    for tier in ordered_tiers:
        slugs = unclassified_by_tier[tier]
        clips, seconds, _ = sound_stats(slugs)
        ambience_groups.append({
            "slug": group_slug(tier, used_group_slugs),
            "name": tier,
            "kind": "soundGroup",
            "soundSlugs": slugs,
            "soundCount": len(slugs),
            "clipCount": clips,
            "durationSec": seconds,
        })

    matched = len(sounds) - len(unclassified)
    total_clips = sum(s["clipCount"] for s in sounds)

    used_prop_group_slugs: set[str] = set()
    prop_group_list = [
        {"slug": group_slug(category, used_prop_group_slugs), "name": category, "items": items}
        for category, items in prop_groups.items()
    ]

    return {
        "schemaVersion": "zombie-survivors-models-1.0",
        "game": GAME_SLUG,
        "name": "Zombie Survivors 模型总览",
        "nameEn": "Zombie Survivors Model Overview",
        "generatedBy": "scripts/build_yazs_models_index.py",
        "sources": {
            "weapons": f"games/{GAME_SLUG}/weapons.json",
            "sounds": f"games/{GAME_SLUG}/catalog.json",
            "props": f"games/{GAME_SLUG}/props.json",
        },
        "notes": {
            "structure": "把原先分散的 weapons.json（武器与描述）、catalog.json（音效）与 props.json（场景道具）"
                         "合并成 人物 / 怪物 / 武器 / 场景道具 四个维度：每个武器条目直接带上它的射击、换弹、技能音效与命中特效。",
            "threeDimensional": "本分类目前只有场景道具的三维模型（models/zombie-survivors/*.glb）。人物、怪物与武器没有三维模型，"
                                "条目内聚合的是官方名称、说明、音效与 2D 命中特效贴图。",
            "sounds": "音效明细（音频文件、时长、采样率）仍在 games/zombie-survivors/catalog.json，本文件按 slug 引用，避免重复维护。",
        },
        "counts": {
            "characters": len(character_items),
            "monsters": len(monster_order),
            "enemies": monster_counts["enemy"],
            "bosses": monster_counts["boss"],
            "weapons": len(weapons),
            "weaponTypes": len(WEAPON_TYPES),
            "props": len(props),
            "soundEntries": len(sounds),
            "soundClips": total_clips,
            "soundEntriesLinked": matched,
            "soundEntriesUnlinked": len(unclassified),
        },
        "categories": [
            {
                "slug": "characters",
                "name": "人物模型",
                "nameEn": "Characters",
                "detail": f"{len(character_items)} 位幸存者，每位 5 把专属武器；音效按人物归集",
                "items": character_items,
            },
            {
                "slug": "monsters",
                "name": "怪物模型",
                "nameEn": "Monsters",
                "detail": f"{monster_counts['enemy']} 类普通敌人 + {monster_counts['boss']} 个头目",
                "groups": [
                    {"slug": "enemy", "name": kind_labels["enemy"], "items": monster_groups["enemy"]},
                    {"slug": "boss", "name": kind_labels["boss"], "items": monster_groups["boss"]},
                ],
            },
            {
                "slug": "weapons",
                "name": "武器模型",
                "nameEn": "Weapons",
                "detail": f"{len(weapons)} 把武器按类型分成 {len(WEAPON_TYPES)} 组，每组内是武器本体、音效与命中特效",
                "groups": weapon_groups,
            },
            {
                "slug": "props",
                "name": "场景道具",
                "nameEn": "Scene Props",
                "detail": f"{len(props)} 个关卡场景资产，是本分类唯一带三维模型的部分",
                "groups": prop_group_list,
            },
            {
                "slug": "ambience",
                "name": "其它音效",
                "nameEn": "Other Sounds",
                "detail": "未归入人物、怪物或武器的音效：事件与机关、拾取成长、界面、音乐与环境，"
                          "以及源数据没有标明归属的通用命中与技能音效",
                "groups": ambience_groups,
            },
        ],
    }


def patch_games_json(subtitle: str) -> None:
    """Point games.json at the merged catalogue without touching other build steps' keys."""
    games = load(GAMES_PATH)
    for game in games:
        if game.get("slug") != GAME_SLUG:
            continue
        game["modelsCatalog"] = f"games/{GAME_SLUG}/models.json"
        game["modelsPage"] = MODELS_PAGE
        game["modelsSubtitle"] = subtitle
        GAMES_PATH.write_text(json.dumps(games, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"更新 {GAMES_PATH.relative_to(ROOT)}：modelsCatalog / modelsPage / modelsSubtitle")
        return
    print(f"[warn] {GAMES_PATH.relative_to(ROOT)} 中没有 {GAME_SLUG} 条目，未登记合并目录", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    document = build()

    # Every referenced slug and file must resolve before anything is written.
    problems: list[str] = []
    sound_slugs = {s["slug"] for s in load(SOUNDS_PATH)}
    for category in document["categories"]:
        items = list(category.get("items", []))
        for group in category.get("groups", []):
            items.extend(group.get("items", []))
            if group.get("kind") == "soundGroup":
                items.append(group)
        for item in items:
            if item.get("kind") == "soundGroup":
                for slug in item["soundSlugs"]:
                    if slug not in sound_slugs:
                        problems.append(f"{category['slug']}/{item['slug']}: unknown sound {slug}")
                continue
            for slug in item.get("soundSlugs", []):
                if slug not in sound_slugs:
                    problems.append(f"{category['slug']}/{item['slug']}: unknown sound {slug}")
            model = item.get("model")
            if model and not (ROOT / model).exists():
                problems.append(f"{category['slug']}/{item['slug']}: missing model {model}")
    if problems:
        for problem in problems[:20]:
            print(f"[FAIL] {problem}", file=sys.stderr)
        return 1

    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = document["counts"]
    subtitle = (
        f"{counts['characters']} 位人物 / {counts['monsters']} 类怪物"
        f"（{counts['enemies']} 敌人 + {counts['bosses']} 头目）/ {counts['weapons']} 把武器"
        f"（{counts['weaponTypes']} 类）/ {counts['props']} 个场景道具，音效与特效按条目合并"
    )
    patch_games_json(subtitle)
    print(
        f"写入 {args.out.relative_to(ROOT)}："
        f"人物 {counts['characters']} / 怪物 {counts['monsters']}（敌人 {counts['enemies']} + 头目 {counts['bosses']}）"
        f" / 武器 {counts['weapons']}（{counts['weaponTypes']} 类） / 道具 {counts['props']}"
    )
    print(
        f"音效 {counts['soundEntries']} 条 / {counts['soundClips']} 个片段："
        f"已归类 {counts['soundEntriesLinked']}，未归类 {counts['soundEntriesUnlinked']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
