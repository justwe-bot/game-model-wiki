"""Reuse the replacement-head material on neck bridge faces."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    mesh_object = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")

    bridge_indices = {
        index
        for index, material in enumerate(mesh_object.data.materials)
        if material is not None and material.name.startswith("NeckBridgeSkin")
    }
    head_index = next(
        index
        for index, material in enumerate(mesh_object.data.materials)
        if material is not None and material.name.startswith("Material.002")
    )
    changed = 0
    for polygon in mesh_object.data.polygons:
        if polygon.material_index in bridge_indices:
            polygon.material_index = head_index
            changed += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(args.output.resolve()),
        export_format="GLB",
        export_apply=True,
        export_materials="EXPORT",
    )
    print({"bridgeFacesReassigned": changed, "headMaterialIndex": head_index})


if __name__ == "__main__":
    main()
