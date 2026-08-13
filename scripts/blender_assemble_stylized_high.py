"""Assemble a high model while rebuilding a clean stylized head material layout."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def _load_local_runner(repo_root: Path):
    path = repo_root / "scripts" / "blender_local_head_replacement.py"
    spec = importlib.util.spec_from_file_location("stylized_head_runner", path)
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
    parser.add_argument("--head-fit-height", type=float, default=0.19)
    parser.add_argument("--neck-overlap", type=float, default=0.026)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def _triangle_count(obj) -> int:
    return sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)


def _flat_material(name: str, color: tuple[float, float, float, float], roughness: float, specular: float):
    import bpy

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Specular IOR Level"].default_value = specular
    principled.inputs["Coat Weight"].default_value = 0.0
    return material


def _rebuild_head_materials(head) -> dict[str, int]:
    import numpy as np

    mesh = head.data
    mesh.calc_loop_triangles()
    polygon_count = len(mesh.polygons)
    centers = np.empty(polygon_count * 3, dtype=np.float32)
    mesh.polygons.foreach_get("center", centers)
    centers = centers.reshape((-1, 3))
    minimum = centers.min(axis=0)
    maximum = centers.max(axis=0)
    normalized = (centers - minimum) / np.maximum(maximum - minimum, 1e-6)
    x = normalized[:, 0]
    y = normalized[:, 1]
    z = normalized[:, 2]

    skin = _flat_material("DoujieCG_Skin_Matte", (0.69, 0.48, 0.39, 1.0), 0.78, 0.18)
    hair = _flat_material("DoujieCG_Hair_DarkBrown", (0.075, 0.028, 0.014, 1.0), 0.64, 0.22)
    lips = _flat_material("DoujieCG_Lips", (0.48, 0.115, 0.105, 1.0), 0.72, 0.16)
    sclera = _flat_material("DoujieCG_EyeWhite", (0.72, 0.67, 0.61, 1.0), 0.7, 0.2)
    iris = _flat_material("DoujieCG_Iris_Brown", (0.16, 0.052, 0.018, 1.0), 0.48, 0.25)
    lash = _flat_material("DoujieCG_Lash", (0.018, 0.009, 0.006, 1.0), 0.7, 0.12)
    materials = [skin, hair, lips, sclera, iris, lash]
    mesh.materials.clear()
    for material in materials:
        mesh.materials.append(material)

    material_index = np.full(polygon_count, 1, dtype=np.int32)

    # The Hunyuan head uses +Z as the face direction and +Y as up.
    front = z > 0.62
    face_half_width = 0.19 + 0.16 * np.clip((y - 0.17) / 0.48, 0.0, 1.0)
    face = front & (y > 0.16) & (y < 0.79) & (np.abs(x - 0.5) < face_half_width)
    neck = (y < 0.28) & (np.abs(x - 0.5) < 0.27)
    ears = (y > 0.37) & (y < 0.66) & (np.abs(x - 0.5) > 0.28) & (np.abs(x - 0.5) < 0.43)
    material_index[face | neck | ears] = 0

    left_eye = (x > 0.27) & (x < 0.44)
    right_eye = (x > 0.56) & (x < 0.73)
    eye_band = front & (y > 0.525) & (y < 0.655) & (left_eye | right_eye)
    eye_center_left = ((x - 0.355) / 0.062) ** 2 + ((y - 0.585) / 0.047) ** 2 < 1.0
    eye_center_right = ((x - 0.645) / 0.062) ** 2 + ((y - 0.585) / 0.047) ** 2 < 1.0
    eye_center = front & (eye_center_left | eye_center_right)
    iris_center_left = ((x - 0.355) / 0.027) ** 2 + ((y - 0.585) / 0.034) ** 2 < 1.0
    iris_center_right = ((x - 0.645) / 0.027) ** 2 + ((y - 0.585) / 0.034) ** 2 < 1.0
    iris_center = front & (iris_center_left | iris_center_right)
    material_index[eye_band] = 5
    material_index[eye_center] = 3
    material_index[iris_center] = 4

    brow_band = front & (y > 0.665) & (y < 0.73) & (left_eye | right_eye)
    material_index[brow_band] = 5

    mouth = front & (y > 0.305) & (y < 0.405) & (np.abs(x - 0.5) < 0.135)
    material_index[mouth] = 2

    # The central nose and philtrum stay skin-colored even when the source PBR
    # texture contains baked black nostril or upper-lip shadows.
    nose = front & (y > 0.395) & (y < 0.555) & (np.abs(x - 0.5) < 0.115)
    philtrum = front & (y > 0.37) & (y < 0.435) & (np.abs(x - 0.5) < 0.07)
    material_index[nose | philtrum] = 0

    mesh.polygons.foreach_set("material_index", material_index)
    mesh.update()
    return {
        material.name: int(np.count_nonzero(material_index == index))
        for index, material in enumerate(materials)
    }


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
    head_objects = [obj for obj in implementation._mesh_objects() if obj not in before]
    head = max(head_objects, key=_triangle_count)
    for obj in head_objects:
        if obj is not head:
            bpy.data.objects.remove(obj, do_unlink=True)
    implementation._apply_world_transforms([head])
    material_report = _rebuild_head_materials(head)

    head_bounds = implementation._bounds([head])
    reference_cut_z = implementation.resolve_cut_height(
        head_bounds[0][2], head_bounds[1][2], args.head_reference_cut_ratio
    )
    actual_cut_z = implementation.resolve_cut_height(
        head_bounds[0][2], head_bounds[1][2], args.head_actual_cut_ratio
    )
    implementation._delete_vertices_by_world_z(head, actual_cut_z, delete_above=False)
    implementation._remove_loose_cut_geometry(head)
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
    head.matrix_world = (
        Matrix.Translation(body_center)
        @ Matrix.Scale(scale, 4)
        @ Matrix.Translation(-source_center)
        @ head.matrix_world
    )
    implementation._apply_world_transforms([head])

    export_objects = body_objects + [head]
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
    print({"headTriangles": _triangle_count(head), "materials": material_report})


if __name__ == "__main__":
    main()
