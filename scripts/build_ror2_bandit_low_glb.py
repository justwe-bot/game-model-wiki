"""Create a mobile Bandit GLB while preserving the imported game rig."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import bpy


def triangle_count() -> int:
    total = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        obj.data.calc_loop_triangles()
        total += len(obj.data.loop_triangles)
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=str(args.source))

    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        obj.data.calc_loop_triangles()
        triangles = len(obj.data.loop_triangles)
        if triangles < 80:
            continue
        bpy.context.view_layer.objects.active = obj
        modifier = obj.modifiers.new("MobileDecimate", "DECIMATE")
        modifier.ratio = 0.5
        modifier.use_collapse_triangulate = True
        obj.modifiers.move(len(obj.modifiers) - 1, 0)
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        if obj.vertex_groups:
            bpy.ops.object.vertex_group_normalize_all(lock_active=False)

    low_triangles = triangle_count()
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(args.destination),
        export_format="GLB",
        use_selection=False,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_force_sampling=False,
        export_frame_range=False,
        export_yup=True,
        # Three.js skinning consumes four joint influences per vertex. Decimation
        # can introduce more, so let Blender keep and normalize the strongest four.
        export_all_influences=False,
    )
    args.stats.write_text(json.dumps({"lowTriangles": low_triangles}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
