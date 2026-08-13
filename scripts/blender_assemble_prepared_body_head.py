"""Assemble a prepared body low model with a material-preserving head low model."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def _load_runner(repo_root: Path):
    path = repo_root / "scripts" / "blender_local_head_replacement.py"
    spec = importlib.util.spec_from_file_location("prepared_body_head_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load local runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--output-glb", type=Path, required=True)
    parser.add_argument("--output-fbx", type=Path, required=True)
    parser.add_argument("--body-target-height", type=float, default=1.624)
    parser.add_argument("--head-target-height", type=float, default=0.363)
    parser.add_argument("--head-reference-cut-ratio", type=float, default=0.12)
    parser.add_argument("--neck-overlap", type=float, default=0.087)
    parser.add_argument("--head-triangles", type=int, default=300000)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def _triangles(obj) -> int:
    return sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)


def main() -> None:
    import bpy
    from mathutils import Matrix, Vector

    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    implementation = _load_runner(repo_root)._load_head_replacement(repo_root)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.body.resolve()))
    body = implementation._join_meshes(implementation._mesh_objects(), "PreparedBody")
    implementation._apply_world_transforms([body])
    body_bounds = implementation._bounds([body])
    body_height = body_bounds[1][2] - body_bounds[0][2]
    body_scale = args.body_target_height / body_height
    body.matrix_world = (
        Matrix.Scale(body_scale, 4)
        @ Matrix.Translation(Vector((0.0, 0.0, -body_bounds[0][2])))
        @ body.matrix_world
    )
    implementation._apply_world_transforms([body])

    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(args.head.resolve()))
    head = implementation._join_meshes(
        [obj for obj in implementation._mesh_objects() if obj not in before],
        "PreparedHead",
    )
    implementation._apply_world_transforms([head])
    head_bounds = implementation._bounds([head])
    reference_z = implementation.resolve_cut_height(
        head_bounds[0][2], head_bounds[1][2], args.head_reference_cut_ratio
    )
    reference_height = head_bounds[1][2] - reference_z
    head_scale = args.head_target_height / reference_height
    source_center = Vector((
        (head_bounds[0][0] + head_bounds[1][0]) * 0.5,
        (head_bounds[0][1] + head_bounds[1][1]) * 0.5,
        reference_z,
    ))
    body_scaled_bounds = implementation._bounds([body])
    target_center = Vector((
        (body_scaled_bounds[0][0] + body_scaled_bounds[1][0]) * 0.5,
        (body_scaled_bounds[0][1] + body_scaled_bounds[1][1]) * 0.5,
        body_scaled_bounds[1][2] - args.neck_overlap,
    ))
    head.matrix_world = (
        Matrix.Translation(target_center)
        @ Matrix.Scale(head_scale, 4)
        @ Matrix.Translation(-source_center)
        @ head.matrix_world
    )
    implementation._apply_world_transforms([head])

    before_triangles = _triangles(head)
    if before_triangles > args.head_triangles:
        modifier = head.modifiers.new("HeadMaterialPreservingDecimate", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = args.head_triangles / before_triangles
        modifier.use_collapse_triangulate = True
        modifier.delimit = {"UV", "MATERIAL", "SEAM"}
        bpy.context.view_layer.objects.active = head
        head.select_set(True)
        bpy.ops.object.modifier_apply(modifier=modifier.name)

    args.output_glb.parent.mkdir(parents=True, exist_ok=True)
    args.output_fbx.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    head.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.export_scene.gltf(
        filepath=str(args.output_glb.resolve()),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_texcoords=True,
        export_normals=True,
        export_yup=True,
    )
    bpy.ops.export_scene.fbx(
        filepath=str(args.output_fbx.resolve()),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        apply_unit_scale=True,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="COPY",
        embed_textures=True,
    )
    print({"bodyTriangles": _triangles(body), "headTriangles": _triangles(head)})


if __name__ == "__main__":
    main()
