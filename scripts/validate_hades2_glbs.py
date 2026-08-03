"""Validate Hades II catalog GLBs by importing them back into Blender."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import bpy  # type: ignore


def action_exists(action_names: set[str], clip: str) -> bool:
    return clip in action_names or any(name.endswith(clip) for name in action_names)


def glb_metadata(path: Path) -> tuple[int, set[str]]:
    data = path.read_bytes()
    if data[:4] != b"glTF" or len(data) < 20:
        raise ValueError(f"Not a valid GLB: {path}")
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if json_type != 0x4E4F534A:
        raise ValueError(f"GLB JSON chunk is missing: {path}")
    document = json.loads(data[20 : 20 + json_length].decode("utf-8").rstrip(" \0"))
    triangles = 0
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            if primitive.get("mode", 4) != 4 or "indices" not in primitive:
                raise ValueError(f"Unsupported non-indexed or non-triangle primitive: {path}")
            triangles += document["accessors"][primitive["indices"]]["count"] // 3
    action_names = {
        animation.get("name", "")
        for animation in document.get("animations", [])
        if animation.get("name")
    }
    return triangles, action_names


def validate_variant(repo_root: Path, entry: dict, variant: str) -> None:
    model_path = repo_root / entry["models"][variant]
    if not model_path.is_file():
        raise FileNotFoundError(model_path)

    triangles, action_names = glb_metadata(model_path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(model_path))
    if not any(obj.type == "MESH" for obj in bpy.context.scene.objects):
        raise ValueError(f"{entry['slug']} {variant}: Blender imported no mesh")
    expected_triangles = entry["sourceTriangles" if variant == "original" else "lowTriangles"]
    if triangles != expected_triangles:
        raise ValueError(
            f"{entry['slug']} {variant}: expected {expected_triangles} triangles, found {triangles}"
        )

    missing = [
        animation["clip"]
        for animation in entry.get("animations", [])
        if not action_exists(action_names, animation["clip"])
    ]
    if missing:
        raise ValueError(f"{entry['slug']} {variant}: missing actions {missing}")

    print(
        "HADES2_VALIDATE"
        f" slug={entry['slug']}"
        f" variant={variant}"
        f" triangles={triangles}"
        f" actions={len(action_names)}"
        f" bytes={model_path.stat().st_size}"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("Usage: validate_hades2_glbs.py <repo-root> <catalog.json>")

    repo_root = Path(argv[0]).resolve()
    catalog_path = Path(argv[1]).resolve()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for entry in catalog:
        if not entry.get("available", True):
            continue
        for variant in ("original", "low"):
            validate_variant(repo_root, entry, variant)
    return 0


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    raise SystemExit(main(arguments))
