"""Build weapon_audio/audio_index.json for the 霓虹佣兵 (neon-mercenaries) category.

Enumerates each weapon's decoded Wwise leaves on disk and groups them by event,
so mercenaries.html can offer every leaf as a playable file without a directory
listing. A leaf shared by several events is attributed to the first claimant
only, so no entry ever points at a missing file.

Usage: python scripts/build_mercenaries_audio_index.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WA = ROOT / 'games/neon-mercenaries/weapon_audio'

CLASS = {
    'pistol': '手枪', 'rifle': '步枪', 'smg': '冲锋枪', 'sniper': '狙击步枪',
    'shotgun': '霰弹枪', 'revol': '左轮', 'lmg': '轻机枪', 'hmg': '重机枪',
    'npc': 'NPC', 'grenade': '榴弹', 'bow': '弓', 'melee': '近战',
}
TIER = {'power': '动能', 'tech': '技术', 'smart': '智能', 'militech': '制式', 'default': '默认'}
ACTION = {
    'fire': '射击', 'fire_single': '单发射击', 'fire_auto': '连发射击', 'fire_burst': '点射',
    'fire_stop': '停射', 'fire_mech': '机械击发', 'fire_double': '双发射击', 'fire_suppressor': '消音射击',
    'fire_suppressor_single': '消音单发', 'fire_suppressor_auto': '消音连发', 'fire_suppressor_burst': '消音点射',
    'pre_fire': '预射', 'dry_fire': '空仓击发', 'chaos_fire': '混乱射击', 'chaos_fire_burst': '混乱点射',
    'equip': '装备', 'equip_first': '首次装备', 'first_equip': '首次装备', 'unequip': '收起',
    'disarm': '解除', 'grab': '抓取', 'grab_npc': 'NPC 抓取',
    'aim_enter': '瞄准进入', 'aim_exit': '瞄准退出', 'aim_enter_npc': 'NPC 瞄准进入',
    'reload': '装填', 'load': '装弹', 'unload': '退弹', 'load_open': '装填开', 'load_close': '装填闭',
    'mag_in': '弹匣插入', 'mag_out': '弹匣拔出', 'mag_in_empty': '空弹匣插入', 'mag_out_empty': '空弹匣拔出',
    'mag_in_tap': '弹匣轻敲', 'mag_drop': '弹匣脱落', 'mag_drop_not_empty': '弹匣脱落（有弹）',
    'mag_eject': '弹匣弹出', 'mag_grab': '取弹匣', 'mag_hit': '弹匣撞击', 'mag_release': '弹匣释放',
    'mag_rattle': '弹匣晃动', 'mag_spin': '弹匣旋转', 'mag_flip': '弹匣翻转', 'mag_open': '弹匣仓开',
    'mag_button': '弹匣钮', 'mag_tap': '弹匣轻敲', 'mag_tap_npc': 'NPC 弹匣轻敲',
    'mag_in_loader': '装弹器插入', 'mag_in_ring': '弹匣环插入', 'mag_empty_in': '空仓插入',
    'mag_empty_out': '空仓退出', 'mag_in_npc': 'NPC 弹匣插入', 'mag_out_npc': 'NPC 弹匣拔出',
    'mag_drop_npc': 'NPC 弹匣脱落', 'mag_open': '弹匣仓开',
    'shell_in': '霰弹入膛', 'shell_in_barrel': '霰弹入膛', 'shell_in_empty': '空仓霰弹',
    'shell_in_double': '双霰弹入膛', 'shell_insert': '霰弹装入', 'shell_eject': '霰弹退出',
    'shell_rattle': '霰弹晃动', 'shell_tap': '霰弹轻敲',
    'trigger': '扣扳机', 'handle': '枪机', 'handle_pull': '拉动枪机', 'handle_release': '枪机回位',
    'handle_close': '枪机闭合', 'handle_close_npc': 'NPC 枪机闭合', 'slide_pull': '套筒后拉',
    'slide_release': '套筒回位', 'chamber_slide_back': '弹膛套筒后退',
    'lever_lock': '杠杆锁定', 'lever_load': '杠杆装填', 'lever_handle': '杠杆枪机',
    'lever_pull': '拉杠杆', 'lever_push': '推杠杆',
    'safe_open': '开保险', 'safe_close': '关保险', 'safe_handle_open': '保险手柄开',
    'safe_handle_close': '保险手柄关', 'safe_pump_open': '保险泵开', 'safe_pump_close': '保险泵关',
    'melee': '近战攻击', 'melee_in': '近战进入', 'melee_out': '近战退出', 'quickmelee': '快速近战',
    'melee_flame': '近战火焰', 'swing': '挥击', 'swing_short': '短挥击',
    'charge': '蓄力', 'charge_start': '开始蓄力', 'charge_stop': '停止蓄力',
    'voice_01': '语音 1', 'voice_02': '语音 2', 'voice_03': '语音 3', 'voice_04': '语音 4', 'voice_05': '语音 5',
    'voice_01_stop': '语音 1 停止', 'voice_02_stop': '语音 2 停止', 'voice_03_stop': '语音 3 停止',
    'voice_04_stop': '语音 4 停止', 'voice_stop_01': '语音 1 停止', 'voice_stop_02': '语音 2 停止',
    'voice_stop_03': '语音 3 停止', 'voice_stop_04': '语音 4 停止',
    'voice_01_supp': '消音语音 1', 'voice_supp': '消音语音', 'voice_supp_stop': '消音语音停止',
    'voice_01_supp_stop': '消音语音 1 停止', 'voice_stop_01_supp': '消音语音 1 停止',
    'suppressed_voice_01': '消音语音 1', 'suppressed_voice_02': '消音语音 2', 'suppressed_voice_03': '消音语音 3',
    'single_voice_01': '单发语音 1', 'single_voice_02': '单发语音 2', 'single_voice_03': '单发语音 3',
    'single_voice_04': '单发语音 4', 'burst_voice_01': '点射语音 1', 'burst_voice_02': '点射语音 2',
    'burst_voice_03': '点射语音 3', 'burst_voice_04': '点射语音 4', 'burst_voice_01_supp': '消音点射语音 1',
    'charged_voice_01': '蓄力语音 1', 'charged_voice_02': '蓄力语音 2', 'charged_voice_03': '蓄力语音 3',
    'charged_voice_04': '蓄力语音 4',
    'shutdown': '熄火', 'init': '初始化', 'move': '移动', 'move_long': '长移动', 'servo': '伺服',
    'coolant': '冷却', 'steam': '蒸汽', 'air': '气流', 'open': '打开', 'close': '关闭',
    'cover_open': '开盖', 'cover_close': '合盖', 'cover_close_empty': '空仓合盖',
    'pump_open': '泵开', 'pump_close': '泵合', 'open_single': '单开', 'close_single': '单闭',
    'open_eject': '开膛退出', 'eject': '抛壳', 'bullets_out': '子弹退出', 'bullets_in': '子弹进入',
    'jammed': '卡壳', 'armed': '待击', 'arm': '武装', 'loop': '循环', 'enter': '进入', 'exit': '退出',
    'pull': '拉', 'push': '推', 'in': '进入', 'out': '退出', 'hard': '硬', 'soft': '软', 'stop': '停止',
    'auto': '自动', 'single': '单发', 'action': '动作', 'rattle': '晃动', 'handling': '操作',
    'ext': '室外', 'int': '室内', 'narrow': '狭窄空间', 'wide': '宽阔空间', 'street': '街道',
    'enclosed': '封闭空间', 'npc': 'NPC', 'ws': '武器皮肤', 'supp': '消音',
}


def label_for(event: str, code: str) -> str:
    name = event
    if code and code in name:
        name = name.replace(code, ' ')
    parts = [p for p in re.split(r'[_\s]+', name) if p]
    out = []
    for p in parts:
        if p == 'w':
            continue
        if p == 'gun':
            out.append('武器')
            continue
        if p == 'tail':
            out.append('环境音')
            continue
        if p == 'ma':
            out.append('人声')
            continue
        low = p.lower()
        if low in CLASS:
            out.append(CLASS[low])
        elif low in TIER:
            out.append(TIER[low])
        elif low in ACTION:
            out.append(ACTION[low])
        else:
            out.append(p)
    return ' · '.join(out) if out else event


def main() -> None:
    weapons = []
    total_files = 0
    for d in sorted(WA.glob('weapon_sounds_*')):
        m = re.match(r'weapon_sounds_(.+)_(\d{8}_\d{6})$', d.name)
        if not m:
            continue
        code, stamp = m.group(1), m.group(2)
        routes_path = d / 'event_routes.json'
        routes = json.loads(routes_path.read_text(encoding='utf-8')) if routes_path.exists() else []
        claimed = set()
        items = []
        for ev in routes:
            files = []
            for s in ev.get('sounds', []):
                fname = f"{ev['event']}__media_{s['media']}.wav"
                if fname in claimed:
                    continue
                if (d / 'wav' / fname).is_file():
                    claimed.add(fname)
                    files.append(fname)
            if files:
                items.append({'event': ev['event'], 'label': label_for(ev['event'], code), 'files': files})
        wav_dir = d / 'wav'
        other = sorted(f.name for f in wav_dir.glob('*.wav')) if wav_dir.is_dir() else []
        leftover = [f for f in other if f not in claimed]
        if leftover:
            items.append({'event': '', 'label': '其它叶片', 'files': leftover})
        leaves = sum(len(i['files']) for i in items)
        total_files += leaves
        weapons.append({
            'code': code, 'dir': d.name, 'stamp': stamp,
            'events': len(routes), 'leaves': leaves, 'items': items,
        })
    weapons.sort(key=lambda w: w['code'])
    index = {
        'stamp': max((w['stamp'] for w in weapons), default=''),
        'weapons': weapons,
        'totals': {'weapons': len(weapons), 'events': sum(w['events'] for w in weapons),
                   'leaves': total_files},
    }
    out = WA / 'audio_index.json'
    out.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{out.relative_to(ROOT)}: {len(weapons)} weapons, {total_files} leaves, '
          f'{out.stat().st_size / 1024:.0f} KB')


if __name__ == '__main__':
    main()
