"""Blender-side builder for static Risk of Rain 2 survivor GLBs."""

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


def export_glb(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        export_yup=True,
        export_apply=True,
        export_materials="EXPORT",
        export_animations=False,
        export_cameras=False,
        export_lights=False,
    )


def create_material(image: bpy.types.Image, transparent: bool) -> bpy.types.Material:
    material = bpy.data.materials.new("SurvivorTransparent" if transparent else "SurvivorOpaque")
    material.use_nodes = True
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    nodes = material.node_tree.nodes
    principled = nodes.get("Principled BSDF")
    texture_node = nodes.new("ShaderNodeTexImage")
    texture_node.image = image
    texture_node.interpolation = "Closest"
    material.node_tree.links.new(texture_node.outputs["Color"], principled.inputs["Base Color"])
    if transparent:
        material.node_tree.links.new(texture_node.outputs["Alpha"], principled.inputs["Alpha"])
    else:
        principled.inputs["Alpha"].default_value = 1.0
    principled.inputs["Roughness"].default_value = 0.78
    return material


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meshes", nargs="+", type=Path, required=True)
    parser.add_argument("--texture", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--low", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    image = bpy.data.images.load(str(args.texture), check_existing=True)
    opaque_material = create_material(image, transparent=False)
    transparent_material = create_material(image, transparent=True)

    for mesh_path in args.meshes:
        existing = set(bpy.context.scene.objects)
        bpy.ops.wm.obj_import(filepath=str(mesh_path), forward_axis="NEGATIVE_Z", up_axis="Y")
        material = transparent_material if "transparent" in mesh_path.stem else opaque_material
        for obj in set(bpy.context.scene.objects) - existing:
            if obj.type != "MESH":
                continue
            obj.data.materials.clear()
            obj.data.materials.append(material)
            for polygon in obj.data.polygons:
                polygon.use_smooth = True

    source_triangles = triangle_count()
    export_glb(args.original)

    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH" or len(obj.data.polygons) < 40:
            continue
        bpy.context.view_layer.objects.active = obj
        modifier = obj.modifiers.new("MobileDecimate", "DECIMATE")
        modifier.ratio = 0.5
        modifier.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=modifier.name)

    low_triangles = triangle_count()
    export_glb(args.low)
    args.stats.write_text(
        json.dumps(
            {
                "sourceTriangles": source_triangles,
                "lowTriangles": low_triangles,
                "sourceSizeKB": round(args.original.stat().st_size / 1024),
                "lowSizeKB": round(args.low.stat().st_size / 1024),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
