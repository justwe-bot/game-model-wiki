"""Build the purchased Unity Ultimate Pack library for the local Wiki."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import struct
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image

from merge_glb_animations import (
    append_accessor, glb_stats, node_mapping, read_glb, strip_texture_references, write_glb,
)


ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = ROOT / "models" / "ultimate-pack"
TEXTURES_ROOT = ROOT / "textures" / "ultimate-pack"
REPORTS_ROOT = ROOT / "generated" / "ultimate-pack" / "reports"
CACHE_ROOT = ROOT / "generated" / "ultimate-pack" / "conversion-cache"
CATALOG_PATH = ROOT / "games" / "ultimate-pack" / "catalog.local.json"

PACKS = {
    "robots-01": {
        "name": "Robots Ultimate Pack 01 Cute Series [1.0]",
        "type": "character",
        "source": Path(r"C:\baidunetdiskdownload\Robots Ultimate Pack 01 Cute Series [1.0] - extracted\Assets\Robots Ultimate Pack 01 Cute Series"),
        "directorySuffix": " Robot Cute Series",
        "tier": "Cute Series",
        "kind": "monster",
    },
    "robots-02": {
        "name": "Robots Ultimate Pack 02 Cute Series [1.1]",
        "type": "character",
        "source": Path(r"C:\baidunetdiskdownload\Robots Ultimate Pack 02 Cute Series [1.1]\Robots Ultimate Pack 02 Cute Series [1.1] - extracted\Assets\Robots Ultimate Pack 02 Cute Series"),
        "directorySuffix": " Robot Cute Series",
        "tier": "Cute Series",
        "kind": "monster",
    },
    "monsters-03": {
        "name": "Monsters Ultimate Pack 03 Cute Series [1.0]",
        "type": "character",
        "source": Path(r"C:\baidunetdiskdownload\C_0244_怪兽终极包03可爱系列Monsters Ultimate Pack 03 Cute Series\Monsters Ultimate Pack 03 Cute Series v1.0 - extracted\Assets\Monsters Ultimate Pack 03 Cute Series"),
        "directorySuffix": " Cute Series",
        "tier": "Cute Series",
        "kind": "monster",
    },
    "low-poly-10": {
        "name": "Low Poly Ultimate Pack 10.0",
        "type": "low-poly",
        "source": Path(r"C:\baidunetdiskdownload\Low Poly Ultimate Pack 10.0\Low Poly Ultimate Pack 10.0 - extracted\Assets\polyperfect\Low Poly Ultimate Pack"),
        "tier": "Low Poly",
        "kind": "prop",
    },
    "stylized-weapons": {
        "name": "Ultimate Stylized Weapons Low Poly 3D Models Pack [1.0]",
        "type": "static",
        "source": Path(r"C:\baidunetdiskdownload\Unity Ultimate Stylized Weapons Low Poly 3D Models Pack 1.0\ithappy\3D ModelsPropsWeapons\Ultimate Stylized Weapons - Low Poly 3D Models Pack - extracted\Assets\ithappy\Ultimate_Stylized_Weapons\Meshes"),
        "tier": "Weapons",
        "kind": "weapon",
    },
}

CHINESE_NAMES = {
    "ball-robot": "球形机器人", "blast-robot": "爆破机器人", "bot-robot": "机械机器人",
    "boxy-robot": "方盒机器人", "delivery-robot": "配送机器人", "grid-robot": "网格机器人",
    "gripper-robot": "抓取机器人", "hermit-robot": "隐士机器人", "jellyfish-robot": "水母机器人",
    "metal-robot": "金属机器人", "nexus-robot": "枢纽机器人", "stinger-robot": "刺针机器人",
    "tanker-robot": "坦克机器人", "tech-robot": "科技机器人", "tentacles-robot": "触手机器人",
}

LOW_POLY_SHARED_ACTIONS = [
    ("Ultimate_Position_X_Loop", "translation", [0, 2.5, 5], [(0, 0, 0), (1, 0, 0), (0, 0, 0)]),
    ("Ultimate_Position_Y_Loop", "translation", [0, 2.5, 5], [(0, 0, 0), (0, 1, 0), (0, 0, 0)]),
    ("Ultimate_Position_Z_Loop", "translation", [0, 2.5, 5], [(0, 0, 0), (0, 0, 1), (0, 0, 0)]),
    ("Ultimate_Rotation_X_360_3s_Loop", "rotation", [0, 1.5, 3], [(0, 0, 0), (180, 0, 0), (360, 0, 0)]),
    ("Ultimate_Rotation_X_5_5s_Loop", "rotation", [0, 2.5, 5], [(0, 0, 0), (5, 0, 0), (0, 0, 0)]),
    ("Ultimate_Rotation_Y_360_3s_Loop", "rotation", [0, 1.5, 3], [(0, 0, 0), (0, 180, 0), (0, 360, 0)]),
    ("Ultimate_Rotation_Y_360_5s_Loop", "rotation", [0, 2.5, 5], [(0, 0, 0), (0, 180, 0), (0, 360, 0)]),
    ("Ultimate_Rotation_Y_45_10s_Loop", "rotation", [0, 5, 10], [(0, 0, 0), (0, 45, 0), (0, 0, 0)]),
    ("Ultimate_Rotation_Z_2_5s_Loop", "rotation", [0, 2.5, 5], [(0, 0, 0), (0, 0, 2), (0, 0, 0)]),
    ("Ultimate_Rotation_Z_360_3s_Loop", "rotation", [0, 1.5, 3], [(0, 0, 0), (0, 0, 180), (0, 0, 360)]),
    ("Ultimate_Rotation_Z_5_5s_Loop", "rotation", [0, 2.5, 5], [(0, 0, 0), (0, 0, 5), (0, 0, 0)]),
    ("Ultimate_Scale_110_2s_Loop", "scale", [0, 1, 2], [(1, 1, 1), (1.1, 1.1, 1.1), (1, 1, 1)]),
    ("Ultimate_Scale_Appear", "scale", [0, 1], [(0, 0, 0), (1, 1, 1)]),
    ("Ultimate_Scale_Appear_Bounce", "scale", [0, 0.5, 0.8333333, 1], [(0, 0, 0), (1.1, 1.1, 1.1), (1, 1, 1), (1, 1, 1)]),
]
LOW_POLY_PERSON_ACTIONS = {
    "Armature|dead-pose": "Ultimate_Dead_Pose",
    "Armature|idle-standing": "Ultimate_Idle_Standing",
    "Armature|run-cycle": "Ultimate_Run_Cycle",
    "Armature|sitting-pose": "Ultimate_Sitting_Pose",
    "Armature|walk-cycle": "Ultimate_Walk_Cycle",
}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def display_name(stem: str) -> str:
    value = re.sub(r"^(SM|SKM)_", "", stem, flags=re.I)
    value = re.sub(r"_Rig$", "", value, flags=re.I)
    return value.replace("_", " ").strip()


def animation_kind(name: str) -> str:
    lower = name.lower()
    if "idle" in lower or "defend" in lower or "working" in lower or "grip" in lower:
        return "idle"
    if "spawn" in lower or "underground" in lower or "appear" in lower:
        return "spawn"
    if "die" in lower or "dead" in lower:
        return "death"
    if "damage" in lower:
        return "hurt"
    if any(word in lower for word in ("attack", "projectile", "blast", "shoot", "fire", "punch", "kick", "slash", "spell")):
        return "attack"
    if any(word in lower for word in ("run", "walk", "fly", "dash", "jump", "turn", "spiral", "tumble", "position", "rotation", "scale")):
        return "movement"
    return "other"


def convert_psd(source: Path, output: Path) -> str | None:
    if not source.is_file():
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert("RGBA").save(output, optimize=True)
    return output.relative_to(ROOT).as_posix()


def preferred_texture(paths: list[Path], emission: bool, name: str) -> Path | None:
    candidates = [path for path in paths if ("emission" in path.stem.lower()) == emission]
    if not candidates:
        return None
    expected = name.lower()
    return sorted(candidates, key=lambda path: (
        not path.stem.lower().startswith(expected), "blue" not in path.stem.lower(),
        "red" in path.stem.lower(), len(path.name),
    ))[0]


def convert_fbx(converter: Path, source: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    target = output.with_suffix(".glb")
    if target.is_file() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return target
    command = [
        str(converter), "-i", str(source), "-o", str(output), "-b",
        "--anim-framerate", "bake30", "--normalize-weights", "1",
        "--skinning-weights", "4", "--pbr-metallic-roughness",
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        short_source = CACHE_ROOT / "short-inputs" / f"{hashlib.sha1(str(source).encode()).hexdigest()}.fbx"
        short_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, short_source)
        command[2] = str(short_source)
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        short_source.unlink()
    sanitize_glb(target)
    return target


def sanitize_glb(path: Path) -> dict:
    document, binary = read_glb(path)
    strip_texture_references(document)
    write_glb(path, document, binary)
    return document


def report_for_glb(path: Path) -> dict:
    document, _ = read_glb(path)
    animations = []
    for animation in document.get("animations", []):
        animations.append({
            "name": animation.get("name") or "Animation",
            "clip": animation.get("name") or "Animation",
            "channels": len(animation.get("channels", [])),
            "samplers": len(animation.get("samplers", [])),
        })
    return {
        **glb_stats(document), "animations": animations,
        "animationCount": len(animations), "sizeBytes": path.stat().st_size,
    }


def append_animations(base_path: Path, source_path: Path, output: Path, rename: dict[str, str] | None = None) -> dict:
    base, base_binary = read_glb(base_path)
    source, source_binary = read_glb(source_path)
    strip_texture_references(base)
    binary = bytearray(base_binary)
    base["animations"] = []
    for source_animation in source.get("animations", []):
        referenced_nodes = {channel["target"]["node"] for channel in source_animation.get("channels", [])}
        mapping = node_mapping(base, source, referenced_nodes)
        source_name = source_animation.get("name") or "Animation"
        animation = {"name": (rename or {}).get(source_name, source_name), "samplers": [], "channels": []}
        for sampler in source_animation.get("samplers", []):
            animation["samplers"].append({
                **sampler,
                "input": append_accessor(base, binary, source, source_binary, sampler["input"]),
                "output": append_accessor(base, binary, source, source_binary, sampler["output"]),
            })
        for channel in source_animation.get("channels", []):
            target = dict(channel["target"])
            target["node"] = mapping[target["node"]]
            animation["channels"].append({**channel, "target": target})
        base["animations"].append(animation)
    write_glb(output, base, bytes(binary))
    return report_for_glb(output)


def quaternion(euler: tuple[float, float, float]) -> tuple[float, float, float, float]:
    x, y, z = (math.radians(value) / 2 for value in euler)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


def append_float_accessor(document: dict, binary: bytearray, values: list, width: int) -> int:
    while len(binary) % 4:
        binary.append(0)
    offset = len(binary)
    flat = [component for value in values for component in (value if isinstance(value, tuple) else (value,))]
    payload = struct.pack(f"<{len(flat)}f", *flat)
    binary.extend(payload)
    document.setdefault("bufferViews", []).append({"buffer": 0, "byteOffset": offset, "byteLength": len(payload)})
    accessor = {
        "bufferView": len(document["bufferViews"]) - 1, "componentType": 5126,
        "count": len(values), "type": {1: "SCALAR", 3: "VEC3", 4: "VEC4"}[width],
    }
    if width == 1:
        accessor["min"] = [min(values)]
        accessor["max"] = [max(values)]
    document.setdefault("accessors", []).append(accessor)
    return len(document["accessors"]) - 1


def append_generic_animations(path: Path) -> dict:
    document, base_binary = read_glb(path)
    binary = bytearray(base_binary)
    target = next((index for index, node in enumerate(document.get("nodes", [])) if "mesh" in node), 0)
    animations = document.setdefault("animations", [])
    existing = {animation.get("name") for animation in animations}
    for name, channel_path, times, values in LOW_POLY_SHARED_ACTIONS:
        if name in existing:
            continue
        outputs = [quaternion(value) for value in values] if channel_path == "rotation" else values
        time_accessor = append_float_accessor(document, binary, times, 1)
        value_accessor = append_float_accessor(document, binary, outputs, 4 if channel_path == "rotation" else 3)
        animations.append({
            "name": name,
            "samplers": [{"input": time_accessor, "output": value_accessor, "interpolation": "LINEAR"}],
            "channels": [{"sampler": 0, "target": {"node": target, "path": channel_path}}],
        })
    write_glb(path, document, bytes(binary))
    return report_for_glb(path)


def animation_metadata(report: dict) -> list[dict]:
    return [{
        "name": item["name"] if item["channels"] else f"{item['name']}（源占位）",
        "clip": item["clip"], "kind": animation_kind(item["name"]),
        "sourceHasKeyframes": item["channels"] > 0,
    } for item in report.get("animations", [])]


def variant_animation_metadata(reports: dict[str, dict]) -> list[dict]:
    merged = {}
    order = []
    for variant in ("original", "low"):
        for item in animation_metadata(reports.get(variant, {})):
            clip = item["clip"]
            if clip not in merged:
                merged[clip] = {**item, "variants": []}
                order.append(clip)
            merged[clip]["variants"].append(variant)
    return [merged[clip] for clip in order]


def base_entry(pack_slug: str, package: dict, slug: str, name: str, report: dict, model: str) -> dict:
    animations = animation_metadata(report)
    idle = [item for item in animations if item["kind"] == "idle"]
    default = (idle[0]["clip"] if idle else animations[0]["clip"]) if animations else None
    return {
        "slug": f"{pack_slug}-{slug}", "name": CHINESE_NAMES.get(slug, name), "nameEn": name,
        "package": package["name"], "packageSlug": pack_slug, "kind": package["kind"],
        "tier": package["tier"], "status": f"{report['animationCount']} 个动作" if animations else "静态模型",
        "models": {"original": model, "low": model},
        "textures": {"original": None, "low": None},
        "emissiveTextures": {"original": None, "low": None},
        "normalTextures": {"original": None, "low": None},
        "rendererCount": report["rendererCount"], "bakedTriangles": report["triangles"],
        "sourceTriangles": report["triangles"], "lowTriangles": report["triangles"],
        "sourceSizeKB": round(report["sizeBytes"] / 1024), "lowSizeKB": round(report["sizeBytes"] / 1024),
        "animationCount": report["animationCount"], "animations": animations, "defaultClip": default,
        "preserveMaterials": True, "jointCount": report["jointCount"],
        "topology": {"vertices": report["vertices"]}, "rigSource": "original-unity-fbx",
        "originalGameRig": bool(report["jointCount"]),
        "framingScaleByVariant": {"original": 1, "low": 1},
        "mobileFramingScaleByVariant": {"original": 0.94, "low": 0.94},
        "cameraTargetY": 1.25,
        "limitations": "购买的 Unity 商业资产，仅供本地 Wiki 与已获许可的个人项目使用；公开分发前请核对原资产许可。",
    }


def character_specs(pack_slug: str, package: dict) -> list[dict]:
    specs = []
    for directory in sorted(package["source"].iterdir()):
        if not directory.is_dir() or directory.name.lower().startswith("cute series demo"):
            continue
        fbx_files = sorted(directory.rglob("*.FBX")) + sorted(directory.rglob("*.fbx"))
        fbx_files = list(dict.fromkeys(fbx_files))
        action_files = [path for path in fbx_files if "@" in path.stem]
        bases = [path for path in fbx_files if "@" not in path.stem]
        if not bases:
            continue
        primary = max(bases, key=lambda path: path.stat().st_size)
        name = primary.stem
        slug = slugify(name)
        specs.append({"slug": slug, "name": name, "base": primary, "actions": action_files, "directory": directory})
        for extra in bases:
            if extra != primary:
                extra_slug = slugify(extra.stem)
                specs.append({"slug": extra_slug, "name": extra.stem, "base": extra, "actions": [], "directory": directory})
    unique = {}
    for spec in specs:
        unique.setdefault(spec["slug"], spec)
    return list(unique.values())


def build_character_pack(pack_slug: str, package: dict, converter: Path) -> list[dict]:
    entries = []
    for index, spec in enumerate(character_specs(pack_slug, package), 1):
        print(f"[{pack_slug} {index}] {spec['name']}", flush=True)
        output = MODELS_ROOT / pack_slug / f"{spec['slug']}.glb"
        report_path = REPORTS_ROOT / pack_slug / f"{spec['slug']}.json"
        cache = CACHE_ROOT / pack_slug / spec["slug"]
        if spec["actions"]:
            base_glb = convert_fbx(converter, spec["base"], cache / "base")
            action_specs = []
            for action_index, action in enumerate(spec["actions"]):
                action_glb = convert_fbx(converter, action, cache / f"action-{action_index:03d}")
                action_name = action.stem.split("@", 1)[1]
                action_specs.append({
                    "name": action_name,
                    "clip": f"Ultimate_{re.sub(r'[^A-Za-z0-9]+', '_', action_name).strip('_')}",
                    "path": str(action_glb),
                })
            actions_file = cache / "actions.json"
            actions_file.write_text(json.dumps(action_specs, indent=2), encoding="utf-8")
            subprocess.run([
                sys.executable, str(ROOT / "scripts" / "merge_glb_animations.py"),
                "--base", str(base_glb), "--actions", str(actions_file),
                "--output", str(output), "--report", str(report_path),
            ], check=True)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            convert_fbx(converter, spec["base"], output.with_suffix(""))
            report = report_for_glb(output)
        report.update({"slug": spec["slug"], "packageSlug": pack_slug})
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        entry = base_entry(pack_slug, package, spec["slug"], spec["name"], report, output.relative_to(ROOT).as_posix())
        entry["summary"] = f"{package['name']} 中的 {spec['name']}，保留原始 Unity FBX 网格、骨架与 {report['animationCount']} 组具名源动作。"
        entry["modelSource"] = package["name"]
        entry["prefabSource"] = "原始 Unity FBX 骨架与动作集" if spec["actions"] else "原始 Unity 静态 FBX"
        psds = sorted(spec["directory"].rglob("*.psd"))
        base_psd = preferred_texture(psds, False, spec["name"])
        emission_psd = preferred_texture(psds, True, spec["name"])
        base_texture = convert_psd(base_psd, TEXTURES_ROOT / pack_slug / f"{spec['slug']}-base.png") if base_psd else None
        emission = convert_psd(emission_psd, TEXTURES_ROOT / pack_slug / f"{spec['slug']}-emission.png") if emission_psd else None
        entry["textures"] = {"original": base_texture, "low": base_texture}
        entry["emissiveTextures"] = {"original": emission, "low": emission}
        entries.append(entry)
        if cache.exists():
            shutil.rmtree(cache)
    return entries


def normalize_low_poly(path: Path, mesh_root: Path) -> tuple[str, str, str]:
    relative = path.relative_to(mesh_root)
    parts = [re.sub(r"_(M|T)$", "", part) for part in relative.parts[:-1] if not re.fullmatch(r"Rigs?_(M|T)", part)]
    parts = [re.sub(r"[_\s]+", " ", part).strip() for part in parts]
    category = parts[0] if parts else "Other"
    stem = re.sub(r"^(SM|SKM)_", "", path.stem, flags=re.I)
    stem = re.sub(r"_Rig$", "", stem, flags=re.I)
    key = "/".join([*(slugify(part) for part in parts), slugify(stem)])
    return key, category, stem


def low_poly_specs(package: dict) -> list[dict]:
    grouped: dict[str, dict] = defaultdict(dict)
    metadata = {}
    for variant in ("M", "T"):
        mesh_root = package["source"] / f"_{variant}" / f"Meshes_{variant}"
        files = sorted(mesh_root.rglob("*.fbx"))
        rig_keys = set()
        for path in files:
            key, _, _ = normalize_low_poly(path, mesh_root)
            if path.stem.lower().startswith("skm_") and path.stem.lower().endswith("_rig"):
                rig_keys.add(key)
        for path in files:
            key, category, stem = normalize_low_poly(path, mesh_root)
            is_static_person = category.lower() == "people" and path.stem.lower().startswith("sm_") and key in rig_keys
            if is_static_person:
                continue
            grouped[key][variant] = path
            metadata[key] = (category, stem)
    specs = []
    used_slugs = set()
    for key, variants in sorted(grouped.items()):
        category, stem = metadata[key]
        base_slug = slugify(f"{category}-{stem}")
        slug = base_slug
        counter = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        used_slugs.add(slug)
        specs.append({"slug": slug, "name": display_name(stem), "category": category, "variants": variants})
    return specs


def build_low_poly(package_slug: str, package: dict, converter: Path) -> list[dict]:
    entries = []
    specs = low_poly_specs(package)
    people_actions = convert_fbx(
        converter,
        package["source"] / "Animations" / "People_Animation" / "SKM_People_Animations.fbx",
        CACHE_ROOT / package_slug / "people-actions",
    )
    for index, spec in enumerate(specs, 1):
        if index == 1 or index % 100 == 0:
            print(f"[{package_slug}] {index}/{len(specs)}", flush=True)
        reports = {}
        models = {}
        for variant, label in (("M", "original"), ("T", "low")):
            source = spec["variants"].get(variant)
            if not source:
                continue
            output = MODELS_ROOT / package_slug / variant.lower() / f"{spec['slug']}.glb"
            convert_fbx(converter, source, output.with_suffix(""))
            if source.stem.lower().startswith("skm_") and source.stem.lower().endswith("_rig"):
                try:
                    append_animations(output, people_actions, output, LOW_POLY_PERSON_ACTIONS)
                except ValueError:
                    # A few files use a non-humanoid rig despite the Rig suffix.
                    pass
            reports[label] = append_generic_animations(output)
            models[label] = output.relative_to(ROOT).as_posix()
        fallback_label = "original" if "original" in models else "low"
        models.setdefault("original", models[fallback_label])
        models.setdefault("low", models[fallback_label])
        reports.setdefault("original", reports[fallback_label])
        reports.setdefault("low", reports[fallback_label])
        report = reports.get("original") or reports["low"]
        entry = base_entry(package_slug, package, spec["slug"], spec["name"], report, models["original"])
        entry["animations"] = variant_animation_metadata(reports)
        entry["animationCount"] = len(entry["animations"])
        idle = [item for item in entry["animations"] if item["kind"] == "idle"]
        entry["defaultClip"] = (idle[0]["clip"] if idle else entry["animations"][0]["clip"]) if entry["animations"] else None
        entry["models"] = models
        entry["category"] = spec["category"]
        entry["tier"] = spec["category"]
        entry["status"] = f"{entry['animationCount']} 个动作" if entry["animationCount"] else spec["category"]
        entry["summary"] = f"{package['name']} / {spec['category']} 中的 {spec['name']}。M 与 T 材质版本合并在同一条目中。"
        entry["modelSource"] = package["name"]
        entry["prefabSource"] = "原始 Unity FBX；M/T 材质版本配对"
        entry["sourceTriangles"] = reports.get("original", report)["triangles"]
        entry["lowTriangles"] = reports.get("low", report)["triangles"]
        entry["sourceSizeKB"] = round(reports.get("original", report)["sizeBytes"] / 1024)
        entry["lowSizeKB"] = round(reports.get("low", report)["sizeBytes"] / 1024)
        entry["sharedPackageActions"] = [item[0] for item in LOW_POLY_SHARED_ACTIONS]
        entries.append(entry)
    return entries


def build_static_pack(package_slug: str, package: dict, converter: Path) -> list[dict]:
    entries = []
    warnings = []
    files = sorted(package["source"].rglob("*.fbx"))
    for index, source in enumerate(files, 1):
        if index == 1 or index % 100 == 0:
            print(f"[{package_slug}] {index}/{len(files)}", flush=True)
        category = source.parent.name
        slug = slugify(f"{category}-{source.stem}")
        output = MODELS_ROOT / package_slug / slug[0] / f"{slug}.glb"
        try:
            convert_fbx(converter, source, output.with_suffix(""))
        except subprocess.CalledProcessError as error:
            warnings.append({"source": str(source), "exitCode": error.returncode})
            continue
        report = report_for_glb(output)
        entry = base_entry(package_slug, package, slug, display_name(source.stem), report, output.relative_to(ROOT).as_posix())
        entry["category"] = category
        entry["tier"] = category
        entry["status"] = category
        entry["summary"] = f"{package['name']} / {category} 中的 {display_name(source.stem)} 静态武器或配件。"
        entry["modelSource"] = package["name"]
        entry["prefabSource"] = "原始 Unity FBX 与对应 Prefab"
        entries.append(entry)
    warnings_path = CATALOG_PATH.parent / "import-warnings.local.json"
    warnings_path.write_text(json.dumps({package_slug: warnings}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entries


def load_existing_pack01() -> list[dict]:
    if not CATALOG_PATH.is_file():
        return []
    entries = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return [entry for entry in entries if entry.get("packageSlug") == "robots-01" or not entry.get("packageSlug")]


def normalize_existing_pack01(entries: list[dict]) -> list[dict]:
    package = PACKS["robots-01"]
    normalized = []
    for entry in entries:
        entry["slug"] = entry["slug"].removeprefix("robots-01-")
        entry["slug"] = f"robots-01-{entry['slug']}"
        entry["package"] = package["name"]
        entry["packageSlug"] = "robots-01"
        entry["modelSource"] = package["name"]
        normalized.append(entry)
    return normalized


def write_catalog(entries: list[dict]) -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entries.sort(key=lambda item: (item.get("packageSlug", ""), item.get("category", ""), item["nameEn"]))
    CATALOG_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = defaultdict(lambda: {"models": 0, "actions": 0})
    for entry in entries:
        summary[entry["packageSlug"]]["models"] += 1
        summary[entry["packageSlug"]]["actions"] += entry.get("animationCount", 0)
    (CATALOG_PATH.parent / "packages.json").write_text(json.dumps({
        slug: {"name": PACKS[slug]["name"], **counts} for slug, counts in summary.items()
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--package", choices=[*PACKS, "all"], default="all")
    parser.add_argument("--catalog-only", action="store_true")
    args = parser.parse_args()
    for directory in (MODELS_ROOT, TEXTURES_ROOT, REPORTS_ROOT, CACHE_ROOT):
        directory.mkdir(parents=True, exist_ok=True)

    selected = list(PACKS) if args.package == "all" else [args.package]
    existing = json.loads(CATALOG_PATH.read_text(encoding="utf-8")) if CATALOG_PATH.is_file() else []
    by_package = defaultdict(list)
    for entry in existing:
        by_package[entry.get("packageSlug", "robots-01")].append(entry)
    by_package["robots-01"] = normalize_existing_pack01(by_package["robots-01"])

    if not args.catalog_only:
        for package_slug in selected:
            package = PACKS[package_slug]
            if package_slug == "robots-01" and by_package[package_slug]:
                continue
            if package["type"] == "character":
                by_package[package_slug] = build_character_pack(package_slug, package, args.converter)
            elif package["type"] == "low-poly":
                by_package[package_slug] = build_low_poly(package_slug, package, args.converter)
            else:
                by_package[package_slug] = build_static_pack(package_slug, package, args.converter)
            write_catalog([entry for values in by_package.values() for entry in values])

    write_catalog([entry for values in by_package.values() for entry in values])


if __name__ == "__main__":
    main()
