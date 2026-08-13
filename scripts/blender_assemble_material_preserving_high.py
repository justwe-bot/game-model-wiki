"""Assemble the accepted body and replacement head without joining their materials."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def _load_local_runner(repo_root: Path):
    path = repo_root / "scripts" / "blender_local_head_replacement.py"
    spec = importlib.util.spec_from_file_location("material_head_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load local head runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--head-reference-cut-ratio", type=float, default=0.12)
    parser.add_argument("--head-actual-cut-ratio", type=float, default=0.02)
    parser.add_argument("--head-fit-height", type=float, default=0.195)
    parser.add_argument("--neck-overlap", type=float, default=0.026)
    parser.add_argument("--minimum-head-triangles", type=int, default=1000)
    parser.add_argument("--roughness", type=float, default=0.62)
    parser.add_argument("--specular-ior-level", type=float, default=0.28)
    parser.add_argument("--normal-strength", type=float, default=0.45)
    parser.add_argument("--add-eye-overlays", action="store_true")
    parser.add_argument("--neck-taper-start-ratio", type=float, default=0.0)
    parser.add_argument("--neck-taper-factor", type=float, default=1.0)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def _triangle_count(obj) -> int:
    return sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)


def _tune_material(material, roughness: float, specular_ior_level: float, normal_strength: float) -> None:
    if material is None or not material.use_nodes or material.node_tree is None:
        return
    tree = material.node_tree
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            for socket_name, value in (
                ("Metallic", 0.0),
                ("Roughness", roughness),
                ("Specular IOR Level", specular_ior_level),
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


def _flat_material(name: str, color: tuple[float, float, float, float], roughness: float):
    import bpy

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Specular IOR Level"].default_value = 0.18
    principled.inputs["Coat Weight"].default_value = 0.0
    return material


def _add_eye_overlays(head_bounds):
    import bpy

    minimum, maximum = head_bounds
    width = maximum[0] - minimum[0]
    depth = maximum[1] - minimum[1]
    height = maximum[2] - minimum[2]
    center_x = (minimum[0] + maximum[0]) * 0.5
    eye_y = minimum[1] + depth * 0.16
    eye_z = minimum[2] + height * 0.585
    iris_radius = height * 0.028
    pupil_radius = iris_radius * 0.42
    brown = _flat_material("DoujieCG_Iris_Brown", (0.085, 0.022, 0.006, 1.0), 0.52)
    black = _flat_material("DoujieCG_Pupil", (0.004, 0.002, 0.001, 1.0), 0.58)
    overlays = []
    for side in (-1.0, 1.0):
        eye_x = center_x + side * width * 0.142
        for name, radius, y_offset, material in (
            ("Iris", iris_radius, 0.0, brown),
            ("Pupil", pupil_radius, depth * 0.004, black),
        ):
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=48,
                ring_count=24,
                radius=radius,
                location=(eye_x, eye_y - y_offset, eye_z),
            )
            obj = bpy.context.object
            obj.name = f"DoujieCG_{name}_{'L' if side < 0 else 'R'}"
            obj.scale.y = 0.12
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            obj.data.materials.append(material)
            overlays.append(obj)
    return overlays


def _taper_neck_bottom(objects, start_z: float, end_z: float, factor: float) -> None:
    if factor >= 0.999 or start_z <= end_z:
        return
    center_x = sum((obj.bound_box[0][0] + obj.bound_box[6][0]) * 0.5 for obj in objects) / len(objects)
    center_y = sum((obj.bound_box[0][1] + obj.bound_box[6][1]) * 0.5 for obj in objects) / len(objects)
    for obj in objects:
        inverse = obj.matrix_world.inverted()
        for vertex in obj.data.vertices:
            world = obj.matrix_world @ vertex.co
            if world.z >= start_z:
                continue
            blend = max(0.0, min(1.0, (world.z - end_z) / (start_z - end_z)))
            local_factor = factor + (1.0 - factor) * blend
            world.x = center_x + (world.x - center_x) * local_factor
            world.y = center_y + (world.y - center_y) * local_factor
            vertex.co = inverse @ world


def main() -> None:
    import bpy
    from mathutils import Matrix, Vector

    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    implementation = _load_local_runner(repo_root)._load_head_replacement(repo_root)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.body.resolve()))
    body_objects = implementation._mesh_objects()
    implementation._apply_world_transforms(body_objects)
    body_bounds = implementation._bounds(body_objects)

    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(args.head.resolve()))
    imported_head_objects = [
        obj for obj in implementation._mesh_objects() if obj not in before
    ]
    head_objects = [
        obj for obj in imported_head_objects
        if _triangle_count(obj) >= args.minimum_head_triangles
    ]
    for obj in imported_head_objects:
        if obj not in head_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
    if not head_objects:
        raise RuntimeError("The head GLB did not contain a usable head mesh")

    implementation._apply_world_transforms(head_objects)
    head_bounds = implementation._bounds(head_objects)
    eye_objects = _add_eye_overlays(head_bounds) if args.add_eye_overlays else []
    reference_cut_z = implementation.resolve_cut_height(
        head_bounds[0][2], head_bounds[1][2], args.head_reference_cut_ratio
    )
    actual_cut_z = implementation.resolve_cut_height(
        head_bounds[0][2], head_bounds[1][2], args.head_actual_cut_ratio
    )
    for obj in head_objects:
        implementation._delete_vertices_by_world_z(obj, actual_cut_z, delete_above=False)
        implementation._remove_loose_cut_geometry(obj)
    cut_height = head_bounds[1][2] - actual_cut_z
    taper_start_z = actual_cut_z + cut_height * args.neck_taper_start_ratio
    _taper_neck_bottom(head_objects, taper_start_z, actual_cut_z, args.neck_taper_factor)

    reference_height = head_bounds[1][2] - reference_cut_z
    scale = args.head_fit_height / reference_height
    source_center = Vector((
        (head_bounds[0][0] + head_bounds[1][0]) * 0.5,
        (head_bounds[0][1] + head_bounds[1][1]) * 0.5,
        reference_cut_z,
    ))
    body_center = Vector((
        (body_bounds[0][0] + body_bounds[1][0]) * 0.5,
        (body_bounds[0][1] + body_bounds[1][1]) * 0.5,
        body_bounds[1][2] - args.neck_overlap,
    ))
    transform = Matrix.Translation(body_center) @ Matrix.Scale(scale, 4) @ Matrix.Translation(-source_center)
    for obj in head_objects:
        obj.matrix_world = transform @ obj.matrix_world
    for obj in eye_objects:
        obj.matrix_world = transform @ obj.matrix_world
    implementation._apply_world_transforms(head_objects + eye_objects)

    head_materials = {
        material
        for obj in head_objects
        for material in obj.data.materials
        if material is not None
    }
    for material in head_materials:
        _tune_material(
            material,
            args.roughness,
            args.specular_ior_level,
            args.normal_strength,
        )

    export_objects = body_objects + head_objects + eye_objects
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in export_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = head_objects[0]
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
    print(
        {
            "bodyObjects": len(body_objects),
            "headObjects": len(head_objects),
            "headTriangles": sum(_triangle_count(obj) for obj in head_objects),
            "headMaterials": sorted(material.name for material in head_materials),
            "eyeObjects": len(eye_objects),
            "roughness": args.roughness,
            "specularIorLevel": args.specular_ior_level,
            "normalStrength": args.normal_strength,
        }
    )


if __name__ == "__main__":
    main()
