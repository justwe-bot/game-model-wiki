"""Transfer accepted high-resolution materials and UVs to a Mixamo-skinned mesh."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import types


class _ModalBuilder:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: self

    def commit(self) -> None:
        return None


class _ModalApp(_ModalBuilder):
    def __init__(self, *_args, **_kwargs):
        pass

    def function(self, *_args, **_kwargs):
        return lambda function: function

    def local_entrypoint(self, *_args, **_kwargs):
        return lambda function: function


def _load_mesh_pipeline(repo_root: Path):
    module = types.ModuleType("modal")
    module.App = _ModalApp
    module.Image = _ModalBuilder()
    module.Volume = _ModalBuilder()
    sys.modules["modal"] = module
    path = repo_root / "cloud" / "modal_character_mesh.py"
    spec = importlib.util.spec_from_file_location("transfer_character_materials", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load mesh pipeline: {path}")
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)
    return pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--base-fbx", type=Path, required=True)
    parser.add_argument("--output-fbx", type=Path, required=True)
    parser.add_argument("--preview-glb", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--target-height", type=float, default=1.9)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def _transfer_surface_data(source, target) -> dict[str, object]:
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.geometry import barycentric_transform

    if not source.data.uv_layers:
        raise RuntimeError("Accepted high-resolution source has no UV map")
    if not target.data.uv_layers:
        target.data.uv_layers.new(name="UVMap")

    target.data.materials.clear()
    for material in source.data.materials:
        target.data.materials.append(material)

    source_mesh = source.data
    source_mesh.calc_loop_triangles()
    source_vertices = [source.matrix_world @ vertex.co for vertex in source_mesh.vertices]
    source_triangles = [tuple(int(index) for index in triangle.vertices) for triangle in source_mesh.loop_triangles]
    source_triangle_polygons = [int(triangle.polygon_index) for triangle in source_mesh.loop_triangles]
    source_triangle_loops = [tuple(int(index) for index in triangle.loops) for triangle in source_mesh.loop_triangles]
    bvh = BVHTree.FromPolygons(source_vertices, source_triangles, all_triangles=True)
    if bvh is None:
        raise RuntimeError("Could not build a BVH for the accepted high-resolution source")

    source_uv = source_mesh.uv_layers.active.data
    target_uv = target.data.uv_layers.active.data
    transferred_loops = 0
    maximum_distance = 0.0
    for loop in target.data.loops:
        point = target.matrix_world @ target.data.vertices[loop.vertex_index].co
        location, _normal, triangle_index, distance = bvh.find_nearest(point)
        if location is None or triangle_index is None:
            raise RuntimeError(f"No accepted-source surface found for target loop {loop.index}")
        triangle_vertices = source_triangles[triangle_index]
        triangle_loops = source_triangle_loops[triangle_index]
        uv = barycentric_transform(
            location,
            source_vertices[triangle_vertices[0]],
            source_vertices[triangle_vertices[1]],
            source_vertices[triangle_vertices[2]],
            Vector((*source_uv[triangle_loops[0]].uv, 0.0)),
            Vector((*source_uv[triangle_loops[1]].uv, 0.0)),
            Vector((*source_uv[triangle_loops[2]].uv, 0.0)),
        )
        target_uv[loop.index].uv = uv.xy
        transferred_loops += 1
        maximum_distance = max(maximum_distance, float(distance))

    for polygon in target.data.polygons:
        center = target.matrix_world @ polygon.center
        _location, _normal, triangle_index, _distance = bvh.find_nearest(center)
        if triangle_index is None:
            raise RuntimeError(f"No accepted-source material found for polygon {polygon.index}")
        source_polygon = source_mesh.polygons[source_triangle_polygons[triangle_index]]
        polygon.material_index = int(source_polygon.material_index)
    target.data.update()

    material_counts = {index: 0 for index in range(len(target.data.materials))}
    for polygon in target.data.polygons:
        index = int(polygon.material_index)
        material_counts[index] = material_counts.get(index, 0) + 1
    return {
        "sourceUv": source.data.uv_layers.active.name,
        "targetUv": target.data.uv_layers.active.name,
        "materials": [material.name if material else None for material in target.data.materials],
        "polygonMaterialCounts": material_counts,
        "transferredLoops": transferred_loops,
        "maximumSurfaceDistance": maximum_distance,
    }


def main() -> None:
    import bpy

    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    pipeline = _load_mesh_pipeline(repo_root)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(
        filepath=str(args.base_fbx.resolve()),
        ignore_leaf_bones=False,
        use_image_search=True,
        automatic_bone_orientation=False,
    )
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    targets = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(armatures) != 1 or len(targets) != 1:
        raise RuntimeError(
            "Expected one Mixamo armature and one skinned mesh; "
            f"found armatures={len(armatures)}, meshes={len(targets)}"
        )
    armature = armatures[0]
    target = targets[0]
    if target.find_armature() != armature:
        raise RuntimeError("Target mesh is not skinned to the imported Mixamo armature")
    imported_objects = set(bpy.context.scene.objects)

    bpy.ops.import_scene.gltf(filepath=str(args.source.resolve()))
    source_meshes = [
        obj for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj not in imported_objects
    ]
    if not source_meshes:
        raise RuntimeError("Accepted source GLB has no newly imported mesh objects")
    source_normalization = pipeline._normalize_character(source_meshes, args.target_height)
    source = pipeline._join_meshes(source_meshes, "AcceptedHighSource")
    transfer = _transfer_surface_data(source, target)
    source.hide_set(True)
    source.hide_render = True

    args.output_fbx.parent.mkdir(parents=True, exist_ok=True)
    args.preview_glb.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.export_scene.gltf(
        filepath=str(args.preview_glb.resolve()),
        export_format="GLB",
        use_selection=True,
        export_apply=False,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_texcoords=True,
        export_normals=True,
        export_skins=True,
        export_animations=False,
        export_yup=True,
    )
    bpy.ops.export_scene.fbx(
        filepath=str(args.output_fbx.resolve()),
        use_selection=True,
        object_types={"ARMATURE", "MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        apply_unit_scale=True,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="COPY",
        embed_textures=True,
    )

    report = {
        "source": str(args.source),
        "baseFbx": str(args.base_fbx),
        "outputFbx": str(args.output_fbx),
        "previewGlb": str(args.preview_glb),
        "sourceNormalization": source_normalization,
        "targetMesh": pipeline._mesh_stats(target),
        "jointCount": len(armature.data.bones),
        "transfer": transfer,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
