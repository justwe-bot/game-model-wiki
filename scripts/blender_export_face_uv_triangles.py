"""Export UV triangles selected by facial world-space regions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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


def _base_color_size(obj):
    for material in obj.data.materials:
        if material is None or not material.use_nodes or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                socket = node.inputs.get("Base Color")
                if socket and socket.links:
                    source = socket.links[0].from_node
                    if source.type == "TEX_IMAGE" and source.image is not None:
                        return tuple(source.image.size)
    raise RuntimeError("No head base-color image found")


def _polygon_world_vertices(obj, polygon):
    return [obj.matrix_world @ obj.data.vertices[index].co for index in polygon.vertices]


def _selected(center, minimum, maximum, region: str) -> bool:
    width = maximum.x - minimum.x
    height = maximum.z - minimum.z
    depth = maximum.y - minimum.y
    center_x = (minimum.x + maximum.x) * 0.5
    x = abs(center.x - center_x) / width
    z = (center.z - minimum.z) / height
    y = (center.y - minimum.y) / depth
    if region == "nose_bottom":
        return x < 0.09 and 0.37 < z < 0.44 and y < 0.30
    if region == "upper_lip_skin":
        return x < 0.11 and 0.315 < z < 0.37 and y < 0.28
    if region == "lower_lip_shadow":
        return x < 0.13 and 0.19 < z < 0.255 and y < 0.30
    if region == "neck":
        return x < 0.27 and z < 0.19
    return False


def main() -> None:
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    body = max(meshes, key=lambda obj: _bounds(obj)[1].z - _bounds(obj)[0].z)
    head = max((obj for obj in meshes if obj != body), key=_triangles)
    minimum, maximum = _bounds(head)
    uv_layer = head.data.uv_layers.active
    if uv_layer is None:
        raise RuntimeError("Head mesh has no active UV layer")

    regions = {
        "nose_bottom": [],
        "upper_lip_skin": [],
        "lower_lip_shadow": [],
        "neck": [],
    }
    for polygon in head.data.polygons:
        vertices = _polygon_world_vertices(head, polygon)
        center = sum(vertices, vertices[0].copy() * 0.0) / len(vertices)
        for region in regions:
            if not _selected(center, minimum, maximum, region):
                continue
            uvs = [tuple(uv_layer.data[index].uv) for index in polygon.loop_indices]
            for index in range(1, len(uvs) - 1):
                regions[region].append([uvs[0], uvs[index], uvs[index + 1]])

    payload = {
        "imageSize": _base_color_size(head),
        "regions": regions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload), encoding="utf-8")
    print({"output": str(args.output.resolve()), "counts": {key: len(value) for key, value in regions.items()}})


if __name__ == "__main__":
    main()
