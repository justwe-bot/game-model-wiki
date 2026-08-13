"""Locate UV bounds for small facial regions and extract the head base-color texture."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--texture-output", type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def _bounds(obj):
    from mathutils import Vector

    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        Vector(tuple(min(point[axis] for point in points) for axis in range(3))),
        Vector(tuple(max(point[axis] for point in points) for axis in range(3))),
    )


def _triangles(obj) -> int:
    return sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)


def _base_color_image(obj):
    for material in obj.data.materials:
        if material is None or not material.use_nodes or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            socket = node.inputs.get("Base Color")
            if socket is None or not socket.links:
                continue
            source = socket.links[0].from_node
            if source.type == "TEX_IMAGE" and source.image is not None:
                return source.image
    raise RuntimeError("No linked head base-color image found")


def _region_uv_bounds(obj, x_ratio: float, z_min_ratio: float, z_max_ratio: float):
    minimum, maximum = _bounds(obj)
    width = maximum.x - minimum.x
    height = maximum.z - minimum.z
    depth = maximum.y - minimum.y
    center_x = (minimum.x + maximum.x) * 0.5
    x_limit = width * x_ratio
    z_min = minimum.z + height * z_min_ratio
    z_max = minimum.z + height * z_max_ratio
    front_limit = minimum.y + depth * 0.42
    uv_layer = obj.data.uv_layers.active
    if uv_layer is None:
        raise RuntimeError("Head mesh has no active UV layer")

    values = []
    polygon_count = 0
    for polygon in obj.data.polygons:
        center = obj.matrix_world @ polygon.center
        if abs(center.x - center_x) > x_limit:
            continue
        if center.z < z_min or center.z > z_max or center.y > front_limit:
            continue
        polygon_count += 1
        for loop_index in polygon.loop_indices:
            values.append(tuple(uv_layer.data[loop_index].uv))
    if not values:
        return None
    return {
        "polygons": polygon_count,
        "uv": (
            (min(value[0] for value in values), min(value[1] for value in values)),
            (max(value[0] for value in values), max(value[1] for value in values)),
        ),
        "world": {
            "x": (center_x - x_limit, center_x + x_limit),
            "z": (z_min, z_max),
            "frontYMax": front_limit,
        },
    }


def main() -> None:
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    body = max(meshes, key=lambda obj: _bounds(obj)[1].z - _bounds(obj)[0].z)
    head = max((obj for obj in meshes if obj != body), key=_triangles)
    image = _base_color_image(head)
    args.texture_output.parent.mkdir(parents=True, exist_ok=True)
    image.file_format = "PNG"
    image.save_render(str(args.texture_output.resolve()))
    print({
        "head": head.name,
        "image": image.name,
        "imageSize": tuple(image.size),
        "textureOutput": str(args.texture_output.resolve()),
        "nose": _region_uv_bounds(head, 0.085, 0.31, 0.43),
        "mouth": _region_uv_bounds(head, 0.15, 0.20, 0.31),
        "eyes": _region_uv_bounds(head, 0.34, 0.48, 0.68),
    })


if __name__ == "__main__":
    main()
