"""Refine the approved high-model candidate without changing its topology."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--roughness", type=float, default=0.76)
    parser.add_argument("--specular-ior-level", type=float, default=0.14)
    parser.add_argument("--normal-strength", type=float, default=0.1)
    parser.add_argument("--add-eye-overlays", action="store_true")
    parser.add_argument("--add-nose-occluders", action="store_true")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def _triangle_count(obj) -> int:
    return sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)


def _bounds(obj):
    from mathutils import Vector

    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        Vector(tuple(min(point[axis] for point in points) for axis in range(3))),
        Vector(tuple(max(point[axis] for point in points) for axis in range(3))),
    )


def _tune_material(material, roughness: float, specular: float, normal_strength: float) -> None:
    if material is None or not material.use_nodes or material.node_tree is None:
        return
    tree = material.node_tree
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            for socket_name, value in (
                ("Metallic", 0.0),
                ("Roughness", roughness),
                ("Specular IOR Level", specular),
                ("Coat Weight", 0.0),
            ):
                socket = node.inputs.get(socket_name)
                if socket is None:
                    continue
                for link in list(socket.links):
                    tree.links.remove(link)
                socket.default_value = value
        elif node.type == "NORMAL_MAP":
            node.inputs["Strength"].default_value = normal_strength


def _flat_material(name: str, color, roughness: float):
    import bpy

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Specular IOR Level"].default_value = 0.12
    principled.inputs["Coat Weight"].default_value = 0.0
    return material


def _front_surface_y(head, x: float, z: float, minimum, maximum) -> float:
    from mathutils import Vector

    depth = maximum.y - minimum.y
    origin_world = Vector((x, minimum.y - depth * 0.25, z))
    direction_world = Vector((0.0, 1.0, 0.0))
    inverse = head.matrix_world.inverted()
    hit, location, _normal, _face = head.ray_cast(
        inverse @ origin_world,
        inverse.to_3x3() @ direction_world,
    )
    if not hit:
        return minimum.y + depth * 0.18
    return (head.matrix_world @ location).y


def _add_eye_overlays(head):
    import bpy

    minimum, maximum = _bounds(head)
    width = maximum.x - minimum.x
    height = maximum.z - minimum.z
    depth = maximum.y - minimum.y
    center_x = (minimum.x + maximum.x) * 0.5
    eye_z = minimum.z + height * 0.585
    iris_radius = height * 0.026
    pupil_radius = iris_radius * 0.43
    brown = _flat_material("DoujieCG_V5_Iris", (0.12, 0.035, 0.012, 1.0), 0.68)
    black = _flat_material("DoujieCG_V5_Pupil", (0.006, 0.003, 0.002, 1.0), 0.76)
    overlays = []
    for side in (-1.0, 1.0):
        eye_x = center_x + side * width * 0.142
        surface_y = _front_surface_y(head, eye_x, eye_z, minimum, maximum)
        for name, radius, outward, material in (
            ("Iris", iris_radius, depth * 0.004, brown),
            ("Pupil", pupil_radius, depth * 0.007, black),
        ):
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=32,
                ring_count=16,
                radius=radius,
                location=(eye_x, surface_y - outward, eye_z),
            )
            obj = bpy.context.object
            obj.name = f"DoujieCG_V5_{name}_{'L' if side < 0 else 'R'}"
            obj.scale.y = 0.1
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            obj.data.materials.append(material)
            overlays.append(obj)
    return overlays


def _add_nose_occluders(head):
    import bpy

    minimum, maximum = _bounds(head)
    width = maximum.x - minimum.x
    height = maximum.z - minimum.z
    depth = maximum.y - minimum.y
    center_x = (minimum.x + maximum.x) * 0.5
    nose_z = minimum.z + height * 0.365
    skin = _flat_material("DoujieCG_V5_Nose_Skin", (0.64, 0.44, 0.39, 1.0), 0.8)
    occluders = []
    for side in (-1.0, 1.0):
        x = center_x + side * width * 0.038
        surface_y = _front_surface_y(head, x, nose_z, minimum, maximum)
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=24,
            ring_count=12,
            radius=height * 0.009,
            location=(x, surface_y - depth * 0.005, nose_z),
        )
        obj = bpy.context.object
        obj.name = f"DoujieCG_V5_Nose_{'L' if side < 0 else 'R'}"
        obj.scale.x = 1.35
        obj.scale.y = 0.08
        obj.scale.z = 0.55
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.data.materials.append(skin)
        occluders.append(obj)
    return occluders


def main() -> None:
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) < 2:
        raise RuntimeError("Expected separate body and head meshes")

    body = max(meshes, key=lambda obj: _bounds(obj)[1].z - _bounds(obj)[0].z)
    head = max(
        (obj for obj in meshes if obj != body),
        key=_triangle_count,
    )

    head_materials = {material for material in head.data.materials if material is not None}
    for material in head_materials:
        _tune_material(material, args.roughness, args.specular_ior_level, args.normal_strength)

    overlays = _add_eye_overlays(head) if args.add_eye_overlays else []
    if args.add_nose_occluders:
        overlays.extend(_add_nose_occluders(head))
    export_objects = meshes + overlays
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in export_objects:
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
        "body": body.name,
        "head": head.name,
        "headTriangles": _triangle_count(head),
        "headMaterials": sorted(material.name for material in head_materials),
        "eyeObjects": len(overlays),
        "roughness": args.roughness,
        "specularIorLevel": args.specular_ior_level,
        "normalStrength": args.normal_strength,
    })


if __name__ == "__main__":
    main()
