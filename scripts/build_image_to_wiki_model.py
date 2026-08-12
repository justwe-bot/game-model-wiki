"""Run image-to-GLB generation, rig transfer, validation, and Wiki registration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from validate_generated_character import validate


SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def run(command: list[str], cwd: Path) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def catalog_entry(
    *,
    slug: str,
    name: str,
    name_en: str,
    summary: str,
    triangles: int,
    original_size_kb: int,
    low_size_kb: int,
    rigid_prop_hand: str,
) -> dict:
    prop_note = ""
    if rigid_prop_hand != "none":
        hand_label = {"left": "左", "right": "右"}[rigid_prop_hand]
        prop_note = f"；外侧道具区域刚性绑定到{hand_label}手骨"
    return {
        "slug": slug,
        "inherits": "commando",
        "name": name,
        "nameEn": name_en,
        "bundle": "modal-triposr-l4",
        "root": "GeneratedCharacter",
        "meshes": ["GeneratedCharacterMesh"],
        "texture": "generatedVertexColors",
        "status": "Modal L4 / TripoSR 单图重建 / 自动蒙皮实验",
        "summary": summary + prop_note,
        "prefab": "Space Commando original game rig / TripoSR generated mesh",
        "rendererCount": 1,
        "animationCount": 20,
        "bakedTriangles": triangles,
        "sourceTriangles": triangles,
        "lowTriangles": triangles,
        "sourceSizeKB": original_size_kb,
        "lowSizeKB": low_size_kb,
        "defaultClip": None,
        "framingScaleByVariant": {"original": 0.96, "low": 0.96},
        "modelRotationDeg": [0, 180, 0],
        "animationsAppend": [
            {"name": "拉弓射箭", "clip": "SpaceCommando_BowShot", "kind": "attack"},
            {"name": "双手重炮射击", "clip": "SpaceCommando_HeavyFire", "kind": "attack"},
        ],
        "rig": "original-game-rig",
        "jointCount": 78,
        "skills": [
            {
                "name": "自动蒙皮验证",
                "clip": "Commando_RunForward",
                "attackType": "动作迁移",
                "range": "全身",
                "description": "使用原始游戏骨骼动作检查四肢权重、关节弯曲和根节点跟随。",
            },
            {
                "name": "重炮姿态验证",
                "clip": "SpaceCommando_HeavyFire",
                "attackType": "双手重武器射击",
                "range": "中远距离",
                "description": "复用 Space Commando 的双手重炮动作检查自动权重表现。",
            },
        ],
    }


def register_entry(path: Path, entry: dict, replace: bool) -> None:
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    matching = [index for index, item in enumerate(entries) if item.get("slug") == entry["slug"]]
    if matching and not replace:
        raise ValueError(f"Wiki entry already exists for {entry['slug']}; pass --replace to update it")
    if matching:
        entries[matching[0]] = entry
    else:
        entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--name-en", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--resolution", type=int, default=192)
    parser.add_argument("--target-height", type=float, default=1.9)
    parser.add_argument("--mirror-x", action="store_true")
    parser.add_argument("--rigid-prop-hand", choices=("none", "left", "right"), default="none")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args()

    if not SLUG_PATTERN.fullmatch(args.slug):
        raise ValueError("--slug must contain lowercase letters, digits, and single hyphens only")
    if not args.skip_generation and (args.image is None or not args.image.is_file()):
        raise ValueError("--image must point to a file unless --skip-generation is used")

    raw = repo_root / "generated" / f"{args.slug}-raw.glb"
    original = repo_root / "models" / "survivors" / f"{args.slug}-original.glb"
    low = repo_root / "models" / "survivors" / f"{args.slug}-low.glb"
    raw.parent.mkdir(parents=True, exist_ok=True)
    original.parent.mkdir(parents=True, exist_ok=True)

    if args.skip_generation:
        if not raw.is_file():
            raise FileNotFoundError(f"Missing cached raw GLB: {raw}")
    else:
        run(
            [
                sys.executable,
                "-m",
                "modal",
                "run",
                str(repo_root / "cloud" / "modal_triposr.py"),
                "--input",
                str(args.image.resolve()),
                "--output",
                str(raw),
                "--resolution",
                str(args.resolution),
            ],
            repo_root,
        )

    rig_command = [
        sys.executable,
        str(repo_root / "scripts" / "rig_generated_character.py"),
        str(raw),
        str(original),
        "--target-height",
        str(args.target_height),
        "--rigid-prop-hand",
        args.rigid_prop_hand,
    ]
    if args.mirror_x:
        rig_command.append("--mirror-x")
    run(rig_command, repo_root)
    shutil.copy2(original, low)

    original_stats = validate(original)
    low_stats = validate(low)
    if original_stats["triangles"] != low_stats["triangles"]:
        raise ValueError("Placeholder low variant must match the original until decimation is added")
    print(
        f"Validated {args.slug}: {original_stats['triangles']} triangles, "
        f"{original_stats['joints']} joints, {original_stats['animations']} animations"
    )

    if not args.no_register:
        entry = catalog_entry(
            slug=args.slug,
            name=args.name,
            name_en=args.name_en,
            summary=args.summary,
            triangles=int(original_stats["triangles"]),
            original_size_kb=round(original.stat().st_size / 1024),
            low_size_kb=round(low.stat().st_size / 1024),
            rigid_prop_hand=args.rigid_prop_hand,
        )
        register_entry(
            repo_root / "games" / "risk-of-rain-2" / "survivors.custom.json",
            entry,
            args.replace,
        )
        run([sys.executable, str(repo_root / "scripts" / "merge_ror2_survivors.py")], repo_root)

    print(f"Wiki URL: http://127.0.0.1:8765/?game=risk-of-rain-2&monster={args.slug}")


if __name__ == "__main__":
    main()
