"""Assemble an untouched high-resolution body and head with a hidden collar overlap."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def _load_local_runner(repo_root: Path):
    path = repo_root / "scripts" / "blender_local_head_replacement.py"
    spec = importlib.util.spec_from_file_location("overlap_head_runner", path)
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
    parser.add_argument("--head-fit-height", type=float, default=0.23)
    parser.add_argument("--neck-overlap", type=float, default=0.026)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    import bpy
    from mathutils import Matrix, Vector

    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    implementation = _load_local_runner(repo_root)._load_head_replacement(repo_root)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.body.resolve()))
    body = implementation._join_meshes(implementation._mesh_objects(), "HighBody")
    implementation._apply_world_transforms([body])
    implementation._weld_coincident_vertices(body)
    body_bounds = implementation._bounds([body])
    body_anchor_z = body_bounds[1][2]

    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(args.head.resolve()))
    head_objects = [
        obj
        for obj in implementation._mesh_objects()
        if obj not in before
    ]
    head = implementation._join_meshes(head_objects, "HighReplacementHead")
    implementation._apply_world_transforms([head])
    implementation._weld_coincident_vertices(head)
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
        body_anchor_z - args.neck_overlap,
    ))
    head.matrix_world = (
        Matrix.Translation(body_center)
        @ Matrix.Scale(scale, 4)
        @ Matrix.Translation(-source_center)
        @ head.matrix_world
    )
    implementation._apply_world_transforms([head])


    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    head.select_set(True)
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


if __name__ == "__main__":
    main()
