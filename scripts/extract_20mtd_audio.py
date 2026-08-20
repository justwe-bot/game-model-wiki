"""Extract 20 Minutes Till Dawn audio clips and build the Wiki sound catalog."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import struct
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import UnityPy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME = Path(
    "/Users/xiongdi/Library/Application Support/Steam/steamapps/common"
    "/20MinuteTillDawn/20MinutesTillDawn.app/Contents/Resources/Data"
)
SOUNDS_DIR = ROOT / "sounds" / "20-minutes-till-dawn"
CATALOG_PATH = ROOT / "games" / "20-minutes-till-dawn" / "catalog.json"
GAMES_PATH = ROOT / "games.json"

EVENT_META = {
    "Gunfire_SFX": {
        "slug": "gunfire",
        "name": "枪械射击",
        "nameEn": "Gunfire",
        "tier": "武器射击",
        "summary": "左轮手枪、霰弹枪、双冲锋枪和榴弹发射器共用的单发射击音。",
        "usedBy": ["左轮手枪", "霰弹枪", "双枪", "榴弹发射器"],
    },
    "CrossbowFire_SFX": {
        "slug": "crossbow-fire",
        "name": "十字弩射击",
        "nameEn": "Crossbow Fire",
        "tier": "武器射击",
        "summary": "十字弩松开蓄力后的发射音，量子螺栓、疾风之箭和快速换弹也共用这组射击。",
        "usedBy": ["十字弩", "量子螺栓", "疾风之箭", "快速换弹"],
    },
    "CrossbowChargeUp_SFX": {
        "slug": "crossbow-charge",
        "name": "十字弩蓄力",
        "nameEn": "Crossbow Charge",
        "tier": "武器射击",
        "summary": "长按射击键给十字弩充能时的提示音。",
        "usedBy": ["十字弩"],
    },
    "CrossbowWoosh_SFX": {
        "slug": "crossbow-woosh",
        "name": "弩箭飞行",
        "nameEn": "Crossbow Bolt Flight",
        "tier": "武器射击",
        "summary": "十字弩箭矢飞出时的破空音。",
        "usedBy": ["十字弩"],
    },
    "Batgun_SFX": {
        "slug": "batgun-fire",
        "name": "蝙蝠枪射击",
        "nameEn": "Batgun Fire",
        "tier": "武器射击",
        "summary": "蝙蝠枪放出追踪蝙蝠时的暗影抛射音，狱火、暗影和嗜血变体共用。",
        "usedBy": ["蝙蝠枪", "狱火蝙蝠", "暗影蝙蝠", "嗜血蝙蝠"],
    },
    "FlameCannon_SFX": {
        "slug": "flame-cannon-fire",
        "name": "火炮喷射",
        "nameEn": "Flame Cannon",
        "tier": "武器射击",
        "summary": "火焰炮持续喷出燃烧弹时的短促火焰音。",
        "usedBy": ["火炮", "燎原之火", "超频效应"],
    },
    "MeteorGun_SFX": {
        "slug": "meteor-gun-fire",
        "name": "火焰流星",
        "nameEn": "Meteor Shot",
        "tier": "武器射击",
        "summary": "火焰流星变体的高伤害慢射命中音。",
        "usedBy": ["火焰流星"],
    },
    "MagicBow_SFX": {
        "slug": "magic-bow-fire",
        "name": "魔羽射出",
        "nameEn": "Magic Bow Fire",
        "tier": "武器射击",
        "summary": "魔羽把箭钉进地面时的轻盈射出音。",
        "usedBy": ["魔羽", "神箭召唤", "炸弹魔箭"],
    },
    "MagicBowRetrieve_SFX": {
        "slug": "magic-bow-retrieve",
        "name": "魔羽回收",
        "nameEn": "Magic Bow Retrieve",
        "tier": "武器换弹",
        "summary": "换弹时地面上的魔箭收回并割伤路径敌人。",
        "usedBy": ["魔羽"],
    },
    "ThunderCaller_SFX": {
        "slug": "thunder-caller-fire",
        "name": "雷霆万钧射击",
        "nameEn": "Thunder Caller Fire",
        "tier": "武器射击",
        "summary": "雷霆万钧变体射出带落雷判定的魔箭。",
        "usedBy": ["雷霆万钧"],
    },
    "ThunderCallerRetrieve_SFX": {
        "slug": "thunder-caller-retrieve",
        "name": "雷霆箭回收",
        "nameEn": "Thunder Caller Retrieve",
        "tier": "武器换弹",
        "summary": "雷霆万钧的箭矢返回时伴随的第二段雷声音。",
        "usedBy": ["雷霆万钧"],
    },
    "SprayGun_SFX": {
        "slug": "spray-gun-fire",
        "name": "水枪喷射",
        "nameEn": "Spray Gun",
        "tier": "武器射击",
        "summary": "魔法水枪、喷射器和孢子花发射时的水流音。",
        "usedBy": ["魔法水枪", "喷射器"],
    },
    "SalvoKnife_SFX": {
        "slug": "salvo-knife",
        "name": "齐射飞刀",
        "nameEn": "Salvo Knives",
        "tier": "近战挥砍",
        "summary": "齐射之刃锁定后同时甩出飞刀，三把匕首破空音会随机轮换。",
        "usedBy": ["齐射之刃"],
    },
    "SalvoTargetAcquired_SFX": {
        "slug": "salvo-lock",
        "name": "飞刀锁定",
        "nameEn": "Salvo Lock-On",
        "tier": "武器射击",
        "summary": "齐射之刃移动瞄准到敌人时的短促锁定提示。",
        "usedBy": ["齐射之刃"],
    },
    "Sword_SFX": {
        "slug": "cyclone-sword",
        "name": "旋风神剑挥砍",
        "nameEn": "Cyclone Sword Slash",
        "tier": "近战挥砍",
        "summary": "旋风神剑普通挥砍，三把标准武器破空音轮换。",
        "usedBy": ["旋风神剑", "死亡之剑", "光明之剑", "疾风之剑"],
    },
    "SwordSpin_SFX": {
        "slug": "cyclone-sword-spin",
        "name": "旋风神剑旋转",
        "nameEn": "Cyclone Sword Spin",
        "tier": "近战挥砍",
        "summary": "换弹时的旋转斩击，对周围敌人造成子弹伤害。",
        "usedBy": ["旋风神剑"],
    },
    "SwordOfDeathSpin_SFX": {
        "slug": "sword-of-death-spin",
        "name": "死亡之剑旋转",
        "nameEn": "Sword of Death Spin",
        "tier": "近战挥砍",
        "summary": "死亡之剑变体换弹旋转时更厚重的带残响挥砍。",
        "usedBy": ["死亡之剑"],
    },
    "ReloadStart_SFX": {
        "slug": "reload-start",
        "name": "开始换弹",
        "nameEn": "Reload Start",
        "tier": "武器换弹",
        "summary": "绝大多数枪械按下换弹或弹药耗尽自动换弹时的枪机拉动音。",
        "usedBy": ["左轮手枪", "霰弹枪", "双枪", "十字弩", "火炮"],
    },
    "ReloadFinish_SFX": {
        "slug": "reload-finish",
        "name": "换弹完成",
        "nameEn": "Reload Finish",
        "tier": "武器换弹",
        "summary": "弹匣到位、可以再次射击时的短促锁止音。",
        "usedBy": ["通用换弹"],
    },
    "FireballWoosh_SFX": {
        "slug": "fireball-woosh",
        "name": "火球飞出",
        "nameEn": "Fireball Whoosh",
        "tier": "法术技能",
        "summary": "火球类投射物离手时的小型火焰破空。",
        "usedBy": ["火球"],
    },
    "FireballImpact_SFX": {
        "slug": "fireball-impact",
        "name": "火球命中",
        "nameEn": "Fireball Impact",
        "tier": "法术技能",
        "summary": "火球砸中敌人或地面时的火焰冲击。",
        "usedBy": ["火球", "火焰流星"],
    },
    "FireWave_SFX": {
        "slug": "fire-wave",
        "name": "火焰波",
        "nameEn": "Fire Wave",
        "tier": "法术技能",
        "summary": "地面火焰波向前推进时的燃烧扫过音。",
        "usedBy": ["火焰波"],
    },
    "Freeze_SFX": {
        "slug": "freeze",
        "name": "冰冻",
        "nameEn": "Freeze",
        "tier": "法术技能",
        "summary": "冰矛命中并施加冰冻时的结晶音。",
        "usedBy": ["冰冻"],
    },
    "IceShatter_SFX": {
        "slug": "ice-shatter",
        "name": "碎冰",
        "nameEn": "Ice Shatter",
        "tier": "法术技能",
        "summary": "冰冻状态结束或碎冰爆炸时的碎裂音。",
        "usedBy": ["碎冰"],
    },
    "Thunder_SFX": {
        "slug": "thunder",
        "name": "落雷",
        "nameEn": "Thunder",
        "tier": "法术技能",
        "summary": "召唤落雷劈中敌人时的电击音。",
        "usedBy": ["落雷", "雷霆万钧"],
    },
    "EyeOfTheStorm_SFX": {
        "slug": "eye-of-the-storm",
        "name": "风暴之眼",
        "nameEn": "Eye of the Storm",
        "tier": "法术技能",
        "summary": "风暴之眼持续旋转时的气流音。",
        "usedBy": ["风暴之眼"],
    },
    "GaleWoosh": {
        "slug": "gale-woosh",
        "name": "疾风",
        "nameEn": "Gale",
        "tier": "法术技能",
        "summary": "疾风技能刮过时的小型气流音。",
        "usedBy": ["疾风"],
    },
    "Shockwave_SFX": {
        "slug": "shockwave",
        "name": "冲击波",
        "nameEn": "Shockwave",
        "tier": "法术技能",
        "summary": "角色或召唤物释放环形冲击波。",
        "usedBy": ["冲击波"],
    },
    "Smite_SFX": {
        "slug": "smite",
        "name": "圣击",
        "nameEn": "Smite",
        "tier": "法术技能",
        "summary": "圣击治疗或惩戒时的短促圣光音。",
        "usedBy": ["圣击"],
    },
    "SummonPulse_SFX": {
        "slug": "summon-pulse",
        "name": "召唤脉冲",
        "nameEn": "Summon Pulse",
        "tier": "法术技能",
        "summary": "召唤物脉冲爆发时的法术书翻页冲击。",
        "usedBy": ["召唤物"],
    },
    "GunGlyphSpawn_SFX": {
        "slug": "gun-glyph-spawn",
        "name": "枪之烙印出现",
        "nameEn": "Gun Glyph Spawn",
        "tier": "法术技能",
        "summary": "枪之烙印符文在地面生成印记。",
        "usedBy": ["枪之烙印"],
    },
    "GunGlyphActivate_SFX": {
        "slug": "gun-glyph-activate",
        "name": "枪之烙印爆发",
        "nameEn": "Gun Glyph Activate",
        "tier": "法术技能",
        "summary": "烙印延迟结束后炸成六发散弹。",
        "usedBy": ["枪之烙印"],
    },
    "LunaBlackHole_SFX": {
        "slug": "luna-black-hole",
        "name": "露娜黑洞",
        "nameEn": "Luna Black Hole",
        "tier": "法术技能",
        "summary": "露娜角色技能生成黑洞时的黑魔法音。",
        "usedBy": ["露娜"],
    },
    "LilithSpirit_SFX": {
        "slug": "lilith-spirit",
        "name": "莉莉丝灵魂",
        "nameEn": "Lilith Spirit",
        "tier": "法术技能",
        "summary": "莉莉丝放出灵魂时的诅咒音。",
        "usedBy": ["莉莉丝"],
    },
    "BlazingSpeed_SFX": {
        "slug": "blazing-speed",
        "name": "炽热加速",
        "nameEn": "Blazing Speed",
        "tier": "拾取增益",
        "summary": "获得炽热加速类增益时的火焰气流。",
        "usedBy": ["增益"],
    },
    "Energized_SFX": {
        "slug": "energized",
        "name": "充能",
        "nameEn": "Energized",
        "tier": "拾取增益",
        "summary": "获得充能或强化状态时的提升音。",
        "usedBy": ["增益"],
    },
    "SoulLink_SFX": {
        "slug": "soul-link",
        "name": "灵魂链接",
        "nameEn": "Soul Link",
        "tier": "拾取增益",
        "summary": "灵魂链接生效时的负面/绑定音。",
        "usedBy": ["灵魂链接"],
    },
    "HaloSpawn_SFX": {
        "slug": "halo-spawn",
        "name": "光环出现",
        "nameEn": "Halo Spawn",
        "tier": "拾取增益",
        "summary": "场上生成可拾取光环。",
        "usedBy": ["光环"],
    },
    "HaloPickup_SFX": {
        "slug": "halo-pickup",
        "name": "拾取光环",
        "nameEn": "Halo Pickup",
        "tier": "拾取增益",
        "summary": "拾取光环后的正向增益音。",
        "usedBy": ["光环"],
    },
    "HeartPickup_SFX": {
        "slug": "heart-pickup",
        "name": "拾取心脏",
        "nameEn": "Heart Pickup",
        "tier": "拾取增益",
        "summary": "拾取治疗心脏。",
        "usedBy": ["治疗"],
    },
    "XPPick_SFX": {
        "slug": "xp-pickup",
        "name": "拾取经验",
        "nameEn": "XP Pickup",
        "tier": "拾取增益",
        "summary": "吸入经验水晶时的短促计数音。",
        "usedBy": ["经验"],
    },
    "MaxCharge_SFX": {
        "slug": "max-charge",
        "name": "蓄力完成",
        "nameEn": "Max Charge",
        "tier": "武器射击",
        "summary": "十字弩或其他蓄力武器达到满蓄时的提示。",
        "usedBy": ["十字弩"],
    },
    "HolyShieldRecharge_SFX": {
        "slug": "holy-shield-recharge",
        "name": "圣盾充能",
        "nameEn": "Holy Shield Recharge",
        "tier": "拾取增益",
        "summary": "圣盾重新充满时的恢复音。",
        "usedBy": ["圣盾"],
    },
    "HolyShieldBreak_SFX": {
        "slug": "holy-shield-break",
        "name": "圣盾破碎",
        "nameEn": "Holy Shield Break",
        "tier": "拾取增益",
        "summary": "圣盾被打破时的减速/破碎音。",
        "usedBy": ["圣盾"],
    },
    "FootstepGrass_SFX": {
        "slug": "footstep-grass",
        "name": "草地脚步",
        "nameEn": "Grass Footsteps",
        "tier": "角色动作",
        "summary": "角色在草地上移动时的三组脚步随机轮换。",
        "usedBy": ["角色移动"],
    },
    "HinaDash_SFX": {
        "slug": "hina-dash",
        "name": "希娜冲刺",
        "nameEn": "Hina Dash",
        "tier": "角色动作",
        "summary": "希娜冲刺位移时的破空音。",
        "usedBy": ["希娜"],
    },
    "Dodge_SFX": {
        "slug": "dodge",
        "name": "闪避",
        "nameEn": "Dodge",
        "tier": "角色动作",
        "summary": "成功闪避伤害时的短促提示。",
        "usedBy": ["闪避"],
    },
    "Hurt_SFX": {
        "slug": "player-hurt",
        "name": "角色受伤",
        "nameEn": "Player Hurt",
        "tier": "角色动作",
        "summary": "玩家被击中时的血溅音。",
        "usedBy": ["角色"],
    },
    "DasherTransformWarning_SFX": {
        "slug": "dasher-transform-warning",
        "name": "化形预警",
        "nameEn": "Dasher Transform Warning",
        "tier": "角色动作",
        "summary": "黛歇即将在人鹿形态间切换的预警。",
        "usedBy": ["黛歇"],
    },
    "DasherTransformToDeer_SFX 1": {
        "slug": "dasher-to-deer",
        "name": "化形为鹿",
        "nameEn": "Transform to Deer",
        "tier": "角色动作",
        "summary": "黛歇切换成鹿形态。",
        "usedBy": ["黛歇"],
    },
    "DasherTransformToHuman_SFX": {
        "slug": "dasher-to-human",
        "name": "化形为人",
        "nameEn": "Transform to Human",
        "tier": "角色动作",
        "summary": "黛歇从鹿形态切回人形。",
        "usedBy": ["黛歇"],
    },
    "MonsterHit_SFX": {
        "slug": "monster-hit",
        "name": "命中敌人",
        "nameEn": "Monster Hit",
        "tier": "敌人",
        "summary": "子弹打中敌人时的极短撞击。",
        "usedBy": ["命中反馈"],
    },
    "WingedMonster_SFX": {
        "slug": "winged-monster",
        "name": "飞翼怪",
        "nameEn": "Winged Monster",
        "tier": "敌人",
        "summary": "蝙蝠类飞翼敌人死亡或受击。",
        "usedBy": ["飞翼怪"],
    },
    "EyeMonsterShoot_SFX": {
        "slug": "eye-monster-shoot",
        "name": "眼球怪射击",
        "nameEn": "Eye Monster Shot",
        "tier": "敌人",
        "summary": "眼球怪吐出投射物。",
        "usedBy": ["眼球怪"],
    },
    "DragonAttack_SFX": {
        "slug": "dragon-attack",
        "name": "巨龙攻击",
        "nameEn": "Dragon Attack",
        "tier": "敌人",
        "summary": "巨龙吐息或扑击时的带回声吼叫。",
        "usedBy": ["巨龙"],
    },
    "Boomer_SFX": {
        "slug": "boomer-explode",
        "name": "自爆怪爆炸",
        "nameEn": "Boomer Explosion",
        "tier": "敌人",
        "summary": "自爆怪靠近后引爆。",
        "usedBy": ["自爆怪"],
    },
    "GrenadeExplosion_SFX": {
        "slug": "grenade-explosion",
        "name": "榴弹爆炸",
        "nameEn": "Grenade Explosion",
        "tier": "武器射击",
        "summary": "榴弹发射器命中后的魔法爆炸，与自爆怪共用同一条爆炸采样。",
        "usedBy": ["榴弹发射器"],
    },
    "ShadowCloneAttack_SFX": {
        "slug": "shadow-clone-attack",
        "name": "影分身攻击",
        "nameEn": "Shadow Clone Attack",
        "tier": "角色动作",
        "summary": "影分身挥砍时的火把破空音。",
        "usedBy": ["影分身"],
    },
    "BoomerWarning_SFX": {
        "slug": "boomer-warning",
        "name": "自爆预警",
        "nameEn": "Boomer Warning",
        "tier": "敌人",
        "summary": "自爆怪即将爆炸时的循环警报。",
        "usedBy": ["自爆怪"],
    },
    "FrostMoth_SFX": {
        "slug": "frost-moth",
        "name": "霜蛾",
        "nameEn": "Frost Moth",
        "tier": "敌人",
        "summary": "霜蛾施加大范围冰冻。",
        "usedBy": ["霜蛾"],
    },
    "GhostPettAttack_SFX": {
        "slug": "ghost-pet-attack",
        "name": "幽灵宠物攻击",
        "nameEn": "Ghost Pet Attack",
        "tier": "敌人",
        "summary": "幽灵宠物或友方幽灵的攻击音。",
        "usedBy": ["幽灵宠物"],
    },
    "TentacleAttack_SFX": {
        "slug": "tentacle-attack",
        "name": "触手攻击",
        "nameEn": "Tentacle Attack",
        "tier": "敌人",
        "summary": "触手抽打时的水声挥击。",
        "usedBy": ["触手"],
    },
    "RhogogProjectileHit_SFX": {
        "slug": "rhogog-hit",
        "name": "罗格格弹命中",
        "nameEn": "Rhogog Projectile Hit",
        "tier": "敌人",
        "summary": "罗格格投射物命中时的毒性护盾音。",
        "usedBy": ["罗格格"],
    },
    "ShoggothWindup_SFX": {
        "slug": "shoggoth-windup",
        "name": "修格斯蓄力",
        "nameEn": "Shoggoth Windup",
        "tier": "敌人",
        "summary": "修格斯放出激光前的充能。",
        "usedBy": ["修格斯"],
    },
    "ShoggothLaser_SFX": {
        "slug": "shoggoth-laser",
        "name": "修格斯激光",
        "nameEn": "Shoggoth Laser",
        "tier": "敌人",
        "summary": "修格斯致命激光射击。",
        "usedBy": ["修格斯"],
    },
    "ShoggothDeath_SFX": {
        "slug": "shoggoth-death",
        "name": "修格斯死亡",
        "nameEn": "Shoggoth Death",
        "tier": "敌人",
        "summary": "修格斯倒下时的血爆。",
        "usedBy": ["修格斯"],
    },
    "ShubWindup_SFX": {
        "slug": "shub-windup",
        "name": "莎布蓄力",
        "nameEn": "Shub Windup",
        "tier": "敌人",
        "summary": "莎布·尼古拉丝放出特殊攻击前的充能。",
        "usedBy": ["莎布"],
    },
    "ShubSpecial_SFX": {
        "slug": "shub-special",
        "name": "莎布特殊攻击",
        "nameEn": "Shub Special",
        "tier": "敌人",
        "summary": "莎布的大型武器挥砍。",
        "usedBy": ["莎布"],
    },
    "ShubDeath_SFX": {
        "slug": "shub-death",
        "name": "莎布死亡",
        "nameEn": "Shub Death",
        "tier": "敌人",
        "summary": "莎布受击倒下的高强度惨叫。",
        "usedBy": ["莎布"],
    },
    "YogAttack": {
        "slug": "yog-attack",
        "name": "犹格攻击",
        "nameEn": "Yog Attack",
        "tier": "敌人",
        "summary": "犹格·索托斯的攻击音。",
        "usedBy": ["犹格"],
    },
    "UIClick_SFX": {
        "slug": "ui-click",
        "name": "界面点击",
        "nameEn": "UI Click",
        "tier": "界面",
        "summary": "菜单按钮按下时的通用点击。",
        "usedBy": ["菜单"],
    },
    "UIHover_SFX": {
        "slug": "ui-hover",
        "name": "界面悬停",
        "nameEn": "UI Hover",
        "tier": "界面",
        "summary": "鼠标划过按钮时的轻点。",
        "usedBy": ["菜单"],
    },
    "LevelUp_SFX": {
        "slug": "level-up",
        "name": "升级",
        "nameEn": "Level Up",
        "tier": "界面",
        "summary": "角色升级并打开强化菜单。",
        "usedBy": ["升级"],
    },
    "PowerupMenuSFX": {
        "slug": "powerup-menu",
        "name": "强化菜单",
        "nameEn": "Powerup Menu",
        "tier": "界面",
        "summary": "打开强化/祝福选择界面。",
        "usedBy": ["强化"],
    },
    "GunEvoMenu_SFX": {
        "slug": "gun-evo-menu",
        "name": "武器进化菜单",
        "nameEn": "Gun Evolution Menu",
        "tier": "界面",
        "summary": "打开武器进化三选一界面。",
        "usedBy": ["武器进化"],
    },
    "GunEvoStart_SFX": {
        "slug": "gun-evo-start",
        "name": "武器进化开始",
        "nameEn": "Gun Evolution Start",
        "tier": "界面",
        "summary": "确认进化后的过场起始音。",
        "usedBy": ["武器进化"],
    },
    "GunEvoEnd_SFX": {
        "slug": "gun-evo-end",
        "name": "武器进化完成",
        "nameEn": "Gun Evolution End",
        "tier": "界面",
        "summary": "进化动画结束、新枪到手。",
        "usedBy": ["武器进化"],
    },
    "ChestOpenLeadUp_SFX": {
        "slug": "chest-leadup",
        "name": "开箱铺垫",
        "nameEn": "Chest Drumroll",
        "tier": "界面",
        "summary": "打开宝箱前的鼓点铺垫。",
        "usedBy": ["宝箱"],
    },
    "ChestOpen_SFX": {
        "slug": "chest-open",
        "name": "打开宝箱",
        "nameEn": "Chest Open",
        "tier": "界面",
        "summary": "宝箱揭晓奖励。",
        "usedBy": ["宝箱"],
    },
    "ChestSpawnXP_SFX": {
        "slug": "chest-xp",
        "name": "宝箱经验",
        "nameEn": "Chest XP",
        "tier": "拾取增益",
        "summary": "宝箱喷出经验水晶时的单次计数。",
        "usedBy": ["宝箱"],
    },
    "UnlockLoadout_SFX": {
        "slug": "unlock-loadout",
        "name": "解锁配装",
        "nameEn": "Unlock Loadout",
        "tier": "界面",
        "summary": "解锁新角色或新武器配装。",
        "usedBy": ["解锁"],
    },
    "EggHatch_SFX": {
        "slug": "egg-hatch",
        "name": "孵化",
        "nameEn": "Egg Hatch",
        "tier": "拾取增益",
        "summary": "蛋或召唤物孵化破壳。",
        "usedBy": ["孵化"],
    },
    "YouDied_SFX": {
        "slug": "you-died",
        "name": "你死了",
        "nameEn": "You Died",
        "tier": "界面",
        "summary": "黎明前阵亡时的失败音乐。",
        "usedBy": ["结算"],
    },
    "YouSurvived_SFX": {
        "slug": "you-survived",
        "name": "你活下来了",
        "nameEn": "You Survived",
        "tier": "界面",
        "summary": "撑过二十分钟后的胜利音乐。",
        "usedBy": ["结算"],
    },
}

MUSIC_META = {
    "Pretty Dungeon LOOP": {
        "slug": "title-theme",
        "name": "标题主题",
        "nameEn": "Pretty Dungeon Loop",
        "tier": "音乐",
        "summary": "标题界面循环背景乐，由 AudioManager 在主菜单淡入播放。",
        "usedBy": ["标题界面"],
        "loop": True,
    },
    "Wasteland Combat Loop": {
        "slug": "combat-theme",
        "name": "荒原战斗",
        "nameEn": "Wasteland Combat Loop",
        "tier": "音乐",
        "summary": "局内循环战斗乐，由 AudioManager 在进入关卡后淡入播放。",
        "usedBy": ["战斗"],
        "loop": True,
    },
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


def slugify(name: str) -> str:
    text = name.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "clip"


def align4(value: int) -> int:
    return (value + 3) & ~3


def parse_header(raw: bytes):
    name_len = struct.unpack_from("<i", raw, 28)[0]
    offset = 32
    name = ""
    if name_len > 0:
        name = raw[offset : offset + name_len].decode("utf-8", "replace").rstrip("\x00")
        offset = align4(offset + name_len)
    script = struct.unpack_from("<iq", raw, 16)
    return script, name, offset


def resolve_file(externals: dict[str, list[str]], current: str, file_id: int) -> str | None:
    if file_id == 0:
        return current
    extras = externals.get(current, [])
    index = file_id - 1
    if 0 <= index < len(extras):
        return extras[index]
    return None


def load_unity(data_dir: Path):
    envs = {}
    objects = {}
    scripts = {}
    audio = {}
    externals: dict[str, list[str]] = {}
    for path in data_dir.iterdir():
        if not path.is_file():
            continue
        env = UnityPy.load(str(path))
        envs[path.name] = env
        objects[path.name] = {obj.path_id: obj for obj in env.objects}
        try:
            for _name, file_obj in getattr(env, "files", {}).items():
                extras = getattr(file_obj, "externals", None)
                if extras:
                    externals[path.name] = [
                        Path(str(getattr(item, "name", None) or getattr(item, "path", None) or item)).name
                        for item in extras
                    ]
        except Exception:
            pass
        for obj in env.objects:
            if obj.type.name == "AudioClip":
                clip = obj.read()
                audio[(path.name, obj.path_id)] = clip
            elif obj.type.name == "MonoScript":
                try:
                    script = obj.read()
                    scripts[(path.name, obj.path_id)] = f"{script.m_Namespace}.{script.m_ClassName}".strip(".")
                except Exception:
                    pass
    return objects, scripts, audio, externals


def collect_sound_events(objects, scripts, audio, externals):
    events = []
    for file_name, mapping in objects.items():
        for path_id, obj in mapping.items():
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                raw = obj.get_raw_data()
            except Exception:
                continue
            if len(raw) < 32:
                continue
            script, name, offset = parse_header(raw)
            script_file_id, script_path_id = script
            script_name = (
                scripts.get(("globalgamemanagers.assets", script_path_id))
                if script_file_id != 0
                else scripts.get((file_name, script_path_id))
            )
            if script_name != "flanne.SoundEffectSO":
                continue
            rest = raw[offset:]
            clip_count = struct.unpack_from("<i", rest, 0)[0] if len(rest) >= 4 else 0
            clips = []
            cursor = 4
            if 1 <= clip_count <= 8:
                for _ in range(clip_count):
                    if cursor + 12 > len(rest):
                        break
                    file_id, clip_path_id = struct.unpack_from("<iq", rest, cursor)
                    cursor += 12
                    source_file = resolve_file(externals, file_name, file_id)
                    clip = audio.get((source_file, clip_path_id)) if source_file else None
                    if clip is None:
                        continue
                    clips.append(clip)
            events.append({"name": name, "clips": clips})
    return events


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pcm)


def convert_to_m4a(wav_path: Path, m4a_path: Path, bitrate: str) -> None:
    m4a_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "afconvert",
        "-f",
        "m4af",
        "-d",
        "aac",
        "-b",
        bitrate,
        str(wav_path),
        str(m4a_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"afconvert failed for {wav_path.name}: {completed.stderr.strip()}")


def clip_payload(clip) -> tuple[bytes, str]:
    samples = clip.samples
    if not isinstance(samples, dict) or not samples:
        raise RuntimeError(f"AudioClip {clip.m_Name} has no samples")
    filename, payload = next(iter(samples.items()))
    return payload, filename


def build_catalog(events, audio_by_name: dict[str, object], preview_dir: Path) -> list[dict]:
    used_clip_names = set()
    catalog = []
    for event in events:
        meta = EVENT_META.get(event["name"])
        if not meta:
            raise RuntimeError(f"Missing EVENT_META for {event['name']}")
        clips = []
        for clip in event["clips"]:
            used_clip_names.add(clip.m_Name)
            clip_slug = slugify(clip.m_Name)
            preview = preview_dir / f"{clip_slug}.m4a"
            clips.append(
                {
                    "name": clip.m_Name,
                    "file": f"sounds/20-minutes-till-dawn/{preview.name}",
                    "durationSec": round(float(clip.m_Length), 3),
                    "sampleRate": int(clip.m_Frequency),
                    "channels": int(clip.m_Channels),
                    "previewSizeKB": preview.stat().st_size // 1024 if preview.exists() else 0,
                }
            )
        catalog.append(
            {
                "slug": meta["slug"],
                "name": meta["name"],
                "nameEn": meta["nameEn"],
                "tier": meta["tier"],
                "status": "已提取",
                "sourceName": event["name"],
                "summary": meta["summary"],
                "usedBy": meta["usedBy"],
                "loop": False,
                "durationSec": round(sum(item["durationSec"] for item in clips), 3),
                "clipCount": len(clips),
                "clips": clips,
            }
        )

    for clip_name, meta in MUSIC_META.items():
        clip = audio_by_name[clip_name]
        used_clip_names.add(clip_name)
        clip_slug = slugify(clip_name)
        preview = preview_dir / f"{clip_slug}.m4a"
        catalog.append(
            {
                "slug": meta["slug"],
                "name": meta["name"],
                "nameEn": meta["nameEn"],
                "tier": meta["tier"],
                "status": "已提取",
                "sourceName": "AudioManager",
                "summary": meta["summary"],
                "usedBy": meta["usedBy"],
                "loop": True,
                "durationSec": round(float(clip.m_Length), 3),
                "clipCount": 1,
                "clips": [
                    {
                        "name": clip_name,
                        "file": f"sounds/20-minutes-till-dawn/{preview.name}",
                        "durationSec": round(float(clip.m_Length), 3),
                        "sampleRate": int(clip.m_Frequency),
                        "channels": int(clip.m_Channels),
                        "previewSizeKB": preview.stat().st_size // 1024 if preview.exists() else 0,
                    }
                ],
            }
        )

    unused = sorted(set(audio_by_name) - used_clip_names)
    for clip_name in unused:
        clip = audio_by_name[clip_name]
        clip_slug = slugify(clip_name)
        preview = preview_dir / f"{clip_slug}.m4a"
        catalog.append(
            {
                "slug": clip_slug,
                "name": clip_name,
                "nameEn": clip_name,
                "tier": "未挂接采样",
                "status": "已提取",
                "sourceName": clip_name,
                "summary": "已从游戏资源提取，但当前没有对应的 SoundEffectSO 或 AudioManager 事件名。",
                "usedBy": [],
                "loop": False,
                "durationSec": round(float(clip.m_Length), 3),
                "clipCount": 1,
                "clips": [
                    {
                        "name": clip_name,
                        "file": f"sounds/20-minutes-till-dawn/{preview.name}",
                        "durationSec": round(float(clip.m_Length), 3),
                        "sampleRate": int(clip.m_Frequency),
                        "channels": int(clip.m_Channels),
                        "previewSizeKB": preview.stat().st_size // 1024 if preview.exists() else 0,
                    }
                ],
            }
        )

    catalog.sort(
        key=lambda item: (
            TIER_ORDER.index(item["tier"]) if item["tier"] in TIER_ORDER else len(TIER_ORDER),
            item["name"],
        )
    )
    return catalog


def update_games_json(catalog: list[dict]) -> None:
    games = json.loads(GAMES_PATH.read_text(encoding="utf-8"))
    event_count = len(catalog)
    clip_count = sum(item["clipCount"] for item in catalog)
    entry = {
        "slug": "20-minutes-till-dawn",
        "name": "黎明前 20 分钟",
        "nameEn": "20 Minutes Till Dawn",
        "catalog": "games/20-minutes-till-dawn/catalog.json",
        "soundsPage": "sounds.html",
        "defaultView": "sounds",
        "subtitle": f"{event_count} 组音效事件 / {clip_count} 条音频 / 射击换弹分类",
        "tiers": TIER_ORDER,
        "notice": "音效来自你本机 Steam 安装的《黎明前 20 分钟》，仅用于个人研究与 Wiki 原型。公开发布或向他人分发音频文件前请核对游戏素材许可，不要分发原始 Unity assets。",
        "assetRevision": "20260818-1",
    }
    existing = next((item for item in games if item["slug"] == entry["slug"]), None)
    if existing:
        existing.update(entry)
    else:
        games.append(entry)
    GAMES_PATH.write_text(json.dumps(games, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--skip-convert", action="store_true")
    args = parser.parse_args()
    if not args.game_data.exists():
        raise SystemExit(f"Game data not found: {args.game_data}")

    objects, scripts, audio, externals = load_unity(args.game_data)
    events = collect_sound_events(objects, scripts, audio, externals)
    if len(events) != 84:
        raise SystemExit(f"Expected 84 SoundEffectSO events, got {len(events)}")

    wav_dir = ROOT / "generated" / "20-minutes-till-dawn" / "wav"
    preview_dir = SOUNDS_DIR
    audio_by_name = {clip.m_Name: clip for clip in audio.values()}
    extracted = []
    for clip in audio.values():
        payload, filename = clip_payload(clip)
        wav_name = slugify(Path(filename).stem) + ".wav"
        wav_path = wav_dir / wav_name
        write_wav(wav_path, payload)
        m4a_path = preview_dir / (slugify(clip.m_Name) + ".m4a")
        bitrate = "128000" if clip.m_Length > 20 else "96000"
        if not args.skip_convert:
            convert_to_m4a(wav_path, m4a_path, bitrate)
        extracted.append((clip.m_Name, wav_path, m4a_path, clip.m_Length))

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog(events, audio_by_name, preview_dir)
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_games_json(catalog)

    print(f"extracted {len(extracted)} clips")
    print(f"catalog events {len(catalog)}")
    print(f"wrote {CATALOG_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
