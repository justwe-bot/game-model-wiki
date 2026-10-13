"""Extract weapon on-hit effects for Yet Another Zombie Survivors.

Each survivor bundle carries particle systems named `<Weapon>_Hit_01` that fire when a
projectile connects with an enemy. The particle *module* parameters (colour, size, speed)
cannot be read back here because the IL2CPP build strips the ParticleSystem type tree, but
the rendered artwork can: the ParticleSystemRenderer sits on the same GameObject and still
points at real materials and textures.

So this script extracts the hit artwork and sound, and records which sprite each weapon
actually uses. Playback timing in the Wiki preview is a reconstruction; the sprites,
colours and sounds are the game's own.
"""

from __future__ import annotations

import argparse
import json
import re
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
VFX_DIR = ROOT / "textures" / GAME_SLUG / "vfx"

# 命中特效用到的贴图：撞击光点、逐帧命中球、血雾/血爆/血溅/血弧、弹着点、枪口火焰。
WANTED_TEXTURES = [
    "Point12",
    "tex_vfx-ult_particle_spritesheet_hitBall",
    "T_BloodBurst_Mask",
    "T_BloodSpray_Mask",
    "T_Blood_Mist_Mask",
    "T_Blood_Arc_Mask",
    "bloodsplash2",
    "bloodsplash",
    "gore_texture",
    "ImpactHit_02",
    "Flame1",
    "Circle4_Explosion",
    # 开火 / 弹道 / 投掷物 / 弹孔
    "muzzleflash_1_blurred",
    "GatlingGun_L3_MuzzelFlash",
    "GatlingGun_L3_BulletTraser",
    "kunai1-128",
    "BulletHole_01",
    "Spark1",
    "Trail_01",
    "SmokePuff01",
    "Flash21",
]

# 完整战斗序列用到的贴图：能量类武器用游戏里的青色开火闪光，忍者用苦无当投掷物。
MUZZLE_ENERGY = "Flash21"
MUZZLE_DEFAULT = "muzzleflash_1_blurred"
TRACER_SPRITE = "GatlingGun_L3_BulletTraser"
KUNAI_SPRITE = "kunai1-128"
ENERGY_KEYS = {"Eng_Laser", "Eng_Plasma", "Eng_Taser", "Eng_Tesla", "Ranger_CrossbowShockspike"}
KUNAI_KEYS = {"Ninja_Katana", "Ninja_KatanaSplash", "Ninja_KatanaMultiSlash",
              "Ninja_KatanaSuperSlash", "Ninja_KatanaHurricaneWave"}

# 粒子渲染器所在 GameObject 的名字 -> 武器 key（来自 I2 本地化里的 Powerups/Weapons/<key>）
HIT_VFX_BY_WEAPON = {
    # 游侠：弩 / 刺网弩 / 电击弩
    "Ranger_Crossbow": "Arrow_Hit_01",
    "Ranger_CrossbowRepeater": "Arrow_Hit_01",
    "Ranger_CrossbowBarber": "CrossbowBarbed_Hit_01",
    "Ranger_CrossbowShockspike": "Shiur_Hit_01",
    "Ranger_CrossbowFirebrand": "Arrow_Hit_01",
    # 女猎手：弓与元素箭
    "Huntress_Bow": "Arrow_Hit_01",
    "Huntress_BowExplosive": "Arrow_Hit_01",
    "Huntress_BowFreezing": "Arrow_Hit_01_Blue",
    "Huntress_BowPoison": "Arrow_Hit_01_Toxic",
    "Huntress_BowMulti": "Arrow_Hit_01",
    # 忍者
    "Ninja_Katana": "Katana_Hit_01",
    "Ninja_KatanaSplash": "Katana_Hit_01",
    "Ninja_KatanaMultiSlash": "Katana_Hit_01",
    "Ninja_KatanaSuperSlash": "SuperSlash_Hit_01",
    "Ninja_KatanaHurricaneWave": "SuperSlash_Hit_01",
    # 纵火者
    "Pyro_Fireaxe": "Axe_Hit_01",
    "Pyro_Infernax": "Axe_Hit_01",
    "Pyro_AxeThrow": "ThrowingAxe_Hit_01",
    "Pyro_Blowtorch": "Nail_Hit_01",
    "Pyro_Flamethrower": "Nail_Hit_01",
    # SWAT / 坦克 / 机械师 / 医生 / 工程师：弹道武器共用弹着粒子
    "Swat_Pistol": "Bullet_Hit_01",
    "Swat_Rifle": "Bullet_Hit_01",
    "Swat_Sniper": "Bullet_Hit_01",
    "Swat_Uzi": "Bullet_Hit_01",
    "Swat_GrenadeLauncher": "Bullet_Hit_01",
    "Tank_Shotgun": "Bullet_Hit_01",
    "Tank_DoubleShotgun": "Bullet_Hit_01",
    "Tank_SuperShotgun": "Bullet_Hit_01",
    "Tank_Minigun": "Bullet_Hit_01",
    "Tank_Rocket": "Bullet_Hit_01",
    "Mechanic_KeyBlade": "KeyBlade_Hit_01",
    "Mechanic_ChaosEngine": "KeyBlade_Hit_01",
    "Mechanic_Lancer": "Bullet_Hit_01",
    "Mechanic_NitroGun": "Bullet_Hit_01",
    "Mechanic_Chainsaw": "Bullet_Hit_01",
    "Medic_Handgun": "Bullet_Hit_01",
    "Medic_SyringeThrow": "Syringe_Hit_01",
    "Medic_SyringeRifle": "Syringe_Hit_01",
    "Medic_DotaBottleThrow": "Bullet_Hit_01",
    "Medic_FreezeBottleThrow": "Bullet_Hit_01",
    "Eng_Blaster": "Bullet_Hit_01",
    "Eng_Laser": "Bullet_Hit_01",
    "Eng_Plasma": "Bullet_Hit_01",
    "Eng_Taser": "Bullet_Hit_01",
    "Eng_Tesla": "Bullet_Hit_01",
}

# 命中音效：按伤害类型选，slug 对应 catalog.json 里的音效条目。
SOUND_BY_KEY = {
    "default": "hit-flesh",
    "Huntress_Bow": "arrow-penetrate",
    "Huntress_BowExplosive": "arrow-penetrate",
    "Huntress_BowFreezing": "arrow-penetrate",
    "Huntress_BowPoison": "arrow-penetrate",
    "Huntress_BowMulti": "arrow-penetrate",
    "Ranger_Crossbow": "arrow-penetrate",
    "Ranger_CrossbowRepeater": "arrow-penetrate",
    "Ranger_CrossbowBarber": "ranger-crossbow-barber-cut",
    "Ranger_CrossbowFirebrand": "arrow-penetrate",
    "Ranger_CrossbowShockspike": "ranger-crossbow-shockspike-activate",
    "Ninja_Katana": "blade-hit",
    "Ninja_KatanaSplash": "blade-hit",
    "Ninja_KatanaMultiSlash": "blade-hit",
    "Ninja_KatanaSuperSlash": "ninja-soul-reaper-impact",
    "Ninja_KatanaHurricaneWave": "ninja-windcutter",
    "Pyro_Fireaxe": "blade-hit",
    "Pyro_Infernax": "blade-hit",
    "Pyro_AxeThrow": "blade-hit",
    "Mechanic_KeyBlade": "mechanic-spanner-hit",
    "Mechanic_ChaosEngine": "mechanic-spanner-hit",
    "Mechanic_NitroGun": "mechanic-nitro-gun-hit",
    "Eng_Laser": "engineer-laser-hit",
    "Eng_Tesla": "engineer-tesla-loop",
    "Eng_Taser": "engineer-taser-fire",
    "Eng_Plasma": "engineer-plasma-loop",
    "Medic_SyringeThrow": "medic-syringe-gun-fire",
    "Medic_SyringeRifle": "medic-syringer-fire",
}

# 撞击光点颜色：血爆用游戏 Gore 材质自身的颜色 (1.0, 0.11, 0.11)；
# 能量/元素类按伤害类型的通用色，页面上会标注这是按伤害类型分配的。
IMPACT_COLOR_BY_KEY = {
    "Huntress_BowFreezing": "#7fd4ff",
    "Huntress_BowPoison": "#8ede5a",
    "Ranger_CrossbowShockspike": "#ffe066",
    "Eng_Laser": "#ff6b6b",
    "Eng_Plasma": "#9b8cff",
    "Eng_Taser": "#ffe066",
    "Eng_Tesla": "#9be7ff",
    "Medic_SyringeThrow": "#8ede5a",
    "Medic_SyringeRifle": "#8ede5a",
}
DEFAULT_IMPACT_COLOR = "#ff1c1c"        # Gore 材质的 (1.0, 0.11, 0.11) 换算所得

IMPACT_SPRITE = "Point12"
FLIPBOOK_SPRITE = "tex_vfx-ult_particle_spritesheet_hitBall"
BLOOD_SPRITE_BY_KIND = {
    "ballistic": "T_BloodBurst_Mask",
    "blade": "T_BloodSpray_Mask",
    "arrow": "T_BloodSpray_Mask",
    "energy": "T_Blood_Mist_Mask",
    "fire": "T_Blood_Arc_Mask",
    "toxic": "T_Blood_Mist_Mask",
}
KIND_BY_KEY = {
    "Huntress_Bow": "arrow", "Huntress_BowExplosive": "arrow", "Huntress_BowFreezing": "arrow",
    "Huntress_BowPoison": "toxic", "Huntress_BowMulti": "arrow",
    "Ranger_Crossbow": "arrow", "Ranger_CrossbowRepeater": "arrow",
    "Ranger_CrossbowBarber": "arrow", "Ranger_CrossbowFirebrand": "fire",
    "Ranger_CrossbowShockspike": "energy",
    "Ninja_Katana": "blade", "Ninja_KatanaSplash": "blade", "Ninja_KatanaMultiSlash": "blade",
    "Ninja_KatanaSuperSlash": "blade", "Ninja_KatanaHurricaneWave": "blade",
    "Pyro_Fireaxe": "fire", "Pyro_Infernax": "fire", "Pyro_AxeThrow": "blade",
    "Pyro_Blowtorch": "fire", "Pyro_Flamethrower": "fire",
    "Mechanic_KeyBlade": "ballistic", "Mechanic_ChaosEngine": "ballistic",
    "Eng_Laser": "energy", "Eng_Plasma": "energy", "Eng_Taser": "energy", "Eng_Tesla": "energy",
    "Medic_SyringeThrow": "toxic", "Medic_SyringeRifle": "toxic",
}


def load_bundle(path: Path):
    env = UnityPy.load(str(path))
    go_names, mats, texs, renderers = {}, {}, {}, {}
    for obj in env.objects:
        t = obj.type.name
        try:
            name = obj.peek_name()
        except Exception:
            name = None
        if t == "GameObject":
            go_names[obj.path_id] = name
        elif t == "Material":
            mats[obj.path_id] = obj
        elif t == "Texture2D":
            texs[obj.path_id] = obj
        elif t == "ParticleSystemRenderer":
            try:
                r = obj.read()
            except Exception:
                continue
            go = getattr(r, "m_GameObject", None)
            if go is not None:
                renderers[go.m_PathID] = [m.m_PathID for m in (getattr(r, "m_Materials", None) or []) if m]
    return go_names, mats, texs, renderers


def export_hit_textures(data_dir: Path) -> dict:
    """Write every hit/blood VFX texture to textures/<game>/vfx and return a name index."""
    VFX_DIR.mkdir(parents=True, exist_ok=True)
    bundles = data_dir / "StreamingAssets" / "AssetBundles"
    sources = [data_dir / "sharedassets1.assets", data_dir / "sharedassets3.assets"]
    sources += sorted(p for p in bundles.iterdir() if p.is_file())
    index = {}
    for path in sources:
        if not path.is_file() or path.stat().st_size < 1024:
            continue
        try:
            env = UnityPy.load(str(path))
        except Exception:
            continue
        for obj in env.objects:
            if obj.type.name != "Texture2D":
                continue
            try:
                name = obj.peek_name()
            except Exception:
                continue
            if name not in WANTED_TEXTURES or name in index:
                continue
            try:
                img = obj.read().image
            except Exception:
                continue
            if img is None:
                continue
            png = VFX_DIR / f"{name}.png"
            img.convert("RGBA").save(png)
            index[name] = {
                "file": f"textures/{GAME_SLUG}/vfx/{png.name}",
                "width": img.width,
                "height": img.height,
                "sourceFile": path.name,
            }
            print(f"  贴图 {name:44s} {img.width}x{img.height}")
    return index


def find_hit_particle_textures(data_dir: Path) -> dict:
    """Map `<Weapon>_Hit_*` particle GameObjects to the texture names they actually use."""
    bundles = data_dir / "StreamingAssets" / "AssetBundles"
    sources = [data_dir / "sharedassets1.assets"] + sorted(p for p in bundles.iterdir() if p.is_file())
    mapping = {}
    for path in sources:
        if not path.is_file() or path.stat().st_size < 1024:
            continue
        try:
            go_names, mats, texs, renderers = load_bundle(path)
        except Exception:
            continue
        for go_pid, mat_pids in renderers.items():
            name = go_names.get(go_pid)
            if not name or "_hit" not in name.lower():
                continue
            for pid in mat_pids:
                mat_obj = mats.get(pid)
                if mat_obj is None:
                    continue
                try:
                    mat = mat_obj.read()
                except Exception:
                    continue
                for slot, pair in mat.m_SavedProperties.m_TexEnvs:
                    t = getattr(pair, "m_Texture", None)
                    if t is None or not t.m_PathID:
                        continue
                    tex_obj = texs.get(t.m_PathID)
                    if tex_obj is None:
                        continue
                    mapping.setdefault(name, set()).add(tex_obj.peek_name())
    return {k: sorted(v) for k, v in mapping.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    args = parser.parse_args()

    if not args.game.is_dir():
        print(f"游戏数据目录不存在: {args.game}", file=sys.stderr)
        return 2
    if not WEAPONS_PATH.exists():
        print(f"先运行 extract_yazs_weapons.py 生成 {WEAPONS_PATH}", file=sys.stderr)
        return 2

    print("导出命中/血效贴图…")
    vfx_index = export_hit_textures(args.game)
    print(f"共 {len(vfx_index)} 张")

    print("\n解析各武器命中粒子使用的贴图…")
    particle_textures = find_hit_particle_textures(args.game)
    print(f"找到 {len(particle_textures)} 个命中粒子：")
    for name in sorted(particle_textures):
        print(f"   {name:34s} -> {particle_textures[name]}")

    data = json.loads(WEAPONS_PATH.read_text(encoding="utf-8"))
    weapons = data.get("weapons", [])
    missing = []
    for weapon in weapons:
        key = weapon["key"]
        vfx_name = HIT_VFX_BY_WEAPON.get(key)
        if vfx_name is None:
            missing.append(key)
            continue
        kind = KIND_BY_KEY.get(key, "ballistic")
        weapon["hitEffect"] = {
            "particle": vfx_name,
            "particleTextures": particle_textures.get(vfx_name, []),
            "impactSprite": vfx_index.get(IMPACT_SPRITE, {}).get("file"),
            "flipbookSprite": vfx_index.get(FLIPBOOK_SPRITE, {}).get("file"),
            "flipbookGrid": [8, 8],
            "bloodSprite": vfx_index.get(BLOOD_SPRITE_BY_KIND.get(kind), {}).get("file"),
            "muzzleSprite": vfx_index.get(MUZZLE_ENERGY if key in ENERGY_KEYS else MUZZLE_DEFAULT, {}).get("file"),
            "muzzleName": MUZZLE_ENERGY if key in ENERGY_KEYS else MUZZLE_DEFAULT,
            "projectileSprite": vfx_index.get(KUNAI_SPRITE if key in KUNAI_KEYS else TRACER_SPRITE, {}).get("file"),
            "projectileName": KUNAI_SPRITE if key in KUNAI_KEYS else TRACER_SPRITE,
            "kind": kind,
            "color": IMPACT_COLOR_BY_KEY.get(key, DEFAULT_IMPACT_COLOR),
            "sound": SOUND_BY_KEY.get(key, SOUND_BY_KEY["default"]),
        }

    if missing:
        print(f"\n以下武器还没有命中特效映射：{missing}", file=sys.stderr)

    data["hitEffectAssets"] = vfx_index
    data["hitEffectSources"] = {
        "particleRenderer": "各角色包中的 ParticleSystemRenderer（按 GameObject 名 <武器>_Hit_01 关联）",
        "note": "IL2CPP 构建剥离了 ParticleSystem 的 type tree，粒子颜色/尺寸/速度无法读回；"
                "预览里的播放节奏是按游戏特效惯例重建的，贴图、颜色与音效取自游戏原始数据。",
    }
    WEAPONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写入 {WEAPONS_PATH.relative_to(ROOT)}：{sum(1 for w in weapons if w.get('hitEffect'))} 把武器带命中特效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
