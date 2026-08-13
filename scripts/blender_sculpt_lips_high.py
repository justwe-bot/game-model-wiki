"""Apply a small, non-destructive lip-profile correction to a high model."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upper-retract", type=float, default=0.003)
    parser.add_argument("--lower-retract", type=float, default=0.002)
    parser.add_argument("--upper-thin", type=float, default=0.001)
    parser.add_argument("--lower-thin", type=float, default=0.001)
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


def _gaussian(value: float, center: float, radius: float) -> float:
    return math.exp(-0.5 * ((value - center) / radius) ** 2)


def _sculpt_lips(head, upper_retract: float, lower_retract: float, upper_thin: float, lower_thin: float):
    minimum, maximum = _bounds(head)
    width = maximum.x - minimum.x
    height = maximum.z - minimum.z
    depth = maximum.y - minimum.y
    center_x = (minimum.x + maximum.x) * 0.5
    inverse = head.matrix_world.inverted()
    changed = 0

    for vertex in head.data.vertices:
        world = head.matrix_world @ vertex.co
        x = abs(world.x - center_x) / width
        z = (world.z - minimum.z) / height
        y = (world.y - minimum.y) / depth
        horizontal = _gaussian(x, 0.0, 0.055)
        front = _gaussian(y, 0.10, 0.09)
        upper = _gaussian(z, 0.335, 0.025)
        lower = _gaussian(z, 0.275, 0.028)
        upper_weight = horizontal * front * upper
        lower_weight = horizontal * front * lower
        weight = max(upper_weight, lower_weight)
        if weight < 0.001:
            continue

        world.y += upper_retract * upper_weight + lower_retract * lower_weight
        if z >= 0.305:
            world.z -= upper_thin * upper_weight
        else:
            world.z += lower_thin * lower_weight
        vertex.co = inverse @ world
        changed += 1

    return changed


def main() -> None:
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    body = max(meshes, key=lambda obj: _bounds(obj)[1].z - _bounds(obj)[0].z)
    head = max((obj for obj in meshes if obj != body), key=_triangles)
    changed = _sculpt_lips(
        head,
        args.upper_retract,
        args.lower_retract,
        args.upper_thin,
        args.lower_thin,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = head
    bpy.ops.export_scene.gltf(
        filepath=str(args.output.resolve()),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_texcoords=True,
        export_normals=True,
        export_yup=True,
    )
    print({
        "head": head.name,
        "changedVertices": changed,
        "upperRetract": args.upper_retract,
        "lowerRetract": args.lower_retract,
        "upperThin": args.upper_thin,
        "lowerThin": args.lower_thin,
    })


if __name__ == "__main__":
    main()
