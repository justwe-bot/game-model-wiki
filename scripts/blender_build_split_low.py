"""Build a material-preserving low model from approved body and head high models."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def _load_local_runner(repo_root: Path):
    path = repo_root / "scripts" / "blender_local_head_replacement.py"
    spec = importlib.util.spec_from_file_location("split_low_head_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load local head runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--output-glb", type=Path, required=True)
    parser.add_argument("--output-fbx", type=Path, required=True)
    parser.add_argument("--body-triangles", type=int, default=60000)
    parser.add_argument("--head-triangles", type=int, default=80000)
    parser.add_argument("--head-reference-cut-ratio", type=float, default=0.12)
    parser.add_argument("--head-fit-height", type=float, default=0.23)
    parser.add_argument("--neck-overlap", type=float, default=0.055)
    parser.add_argument("--target-height", type=float, default=1.9)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def _triangle_count(obj) -> int:
    return sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)


def _decimate(obj, target_triangles: int) -> dict[str, int | float]:
    import bpy

    before = _triangle_count(obj)
    if before <= target_triangles:
        return {"before": before, "after": before, "ratio": 1.0}
    modifier = obj.modifiers.new("MaterialPreservingDecimate", "DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(0.001, min(1.0, target_triangles / before))
    modifier.use_collapse_triangulate = True
    modifier.delimit = {"UV", "MATERIAL", "SEAM"}
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    after = _triangle_count(obj)
    return {"before": before, "after": after, "ratio": after / before}


def main() -> None:
    import bpy
    from mathutils import Matrix, Vector

    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    implementation = _load_local_runner(repo_root)._load_head_replacement(repo_root)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.body.resolve()))
    body = implementation._join_meshes(implementation._mesh_objects(), "SplitLowBody")
    implementation._apply_world_transforms([body])
    body_bounds = implementation._bounds([body])
    body_report = _decimate(body, args.body_triangles)

    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(args.head.resolve()))
    head = implementation._join_meshes(
        [obj for obj in implementation._mesh_objects() if obj not in before],
        "SplitLowHead",
    )
    implementation._apply_world_transforms([head])
    head_bounds = implementation._bounds([head])
    reference_cut_z = implementation.resolve_cut_height(
        head_bounds[0][2], head_bounds[1][2], args.head_reference_cut_ratio
    )
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
    head_report = _decimate(head, args.head_triangles)

    assembled_bounds = implementation._bounds([body, head])
    height = assembled_bounds[1][2] - assembled_bounds[0][2]
    final_scale = args.target_height / height
    for obj in (body, head):
        obj.scale = (final_scale, final_scale, final_scale)
        obj.location.z -= assembled_bounds[0][2]
    implementation._apply_world_transforms([body, head])

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
    print({"body": body_report, "head": head_report})


if __name__ == "__main__":
    main()
