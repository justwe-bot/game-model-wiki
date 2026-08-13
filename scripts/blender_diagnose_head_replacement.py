"""Inspect Hunyuan mesh topology around the biological head replacement cuts."""

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


class _ModalApp(_ModalBuilder):
    def __init__(self, *_args, **_kwargs):
        pass

    def function(self, *_args, **_kwargs):
        return lambda function: function

    def local_entrypoint(self, *_args, **_kwargs):
        return lambda function: function


def _load_implementation(repo_root: Path):
    module = types.ModuleType("modal")
    module.App = _ModalApp
    module.Image = _ModalBuilder()
    module.Volume = _ModalBuilder()
    sys.modules["modal"] = module
    path = repo_root / "cloud" / "modal_character_head_replacement.py"
    spec = importlib.util.spec_from_file_location("diagnose_head_replacement", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load implementation: {path}")
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    return implementation


def _merge_by_distance(obj, distance: float) -> int:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=distance)
    removed = before - len(bm.verts)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return removed


def _boundary_components(obj) -> list[dict[str, object]]:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
    neighbors: dict[object, list[object]] = {}
    for edge in boundary_edges:
        for vertex in edge.verts:
            neighbors.setdefault(vertex, []).append(edge)
    remaining = set(boundary_edges)
    components = []
    while remaining:
        seed = remaining.pop()
        edges = [seed]
        vertices = set(seed.verts)
        stack = list(seed.verts)
        while stack:
            vertex = stack.pop()
            for edge in neighbors.get(vertex, []):
                if edge not in remaining:
                    continue
                remaining.remove(edge)
                edges.append(edge)
                for linked in edge.verts:
                    if linked not in vertices:
                        vertices.add(linked)
                        stack.append(linked)
        center = [
            sum(float(vertex.co[axis]) for vertex in vertices) / len(vertices)
            for axis in range(3)
        ]
        components.append(
            {
                "edges": len(edges),
                "vertices": len(vertices),
                "center": center,
                "zRange": [
                    min(float(vertex.co.z) for vertex in vertices),
                    max(float(vertex.co.z) for vertex in vertices),
                ],
            }
        )
    bm.free()
    return sorted(components, key=lambda item: item["edges"], reverse=True)


def _mesh_components(obj) -> list[dict[str, object]]:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    remaining = set(bm.verts)
    components = []
    while remaining:
        seed = remaining.pop()
        vertices = {seed}
        stack = [seed]
        while stack:
            vertex = stack.pop()
            for edge in vertex.link_edges:
                linked = edge.other_vert(vertex)
                if linked in remaining:
                    remaining.remove(linked)
                    vertices.add(linked)
                    stack.append(linked)
        edges = {edge for vertex in vertices for edge in vertex.link_edges}
        faces = {face for vertex in vertices for face in vertex.link_faces}
        material_faces: dict[int, int] = {}
        for face in faces:
            material_faces[face.material_index] = material_faces.get(face.material_index, 0) + 1
        components.append(
            {
                "vertices": len(vertices),
                "edges": len(edges),
                "faces": len(faces),
                "boundaryEdges": sum(1 for edge in edges if edge.is_boundary),
                "bounds": {
                    "min": [min(float(vertex.co[axis]) for vertex in vertices) for axis in range(3)],
                    "max": [max(float(vertex.co[axis]) for vertex in vertices) for axis in range(3)],
                },
                "materialFaces": material_faces,
            }
        )
    bm.free()
    return sorted(components, key=lambda item: item["faces"], reverse=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--body-cut-ratio", type=float, default=0.865)
    parser.add_argument("--head-cut-ratio", type=float, default=0.12)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    import bpy

    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    implementation = _load_implementation(repo_root)
    report: dict[str, object] = {}

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.body.resolve()))
    body = implementation._join_meshes(implementation._mesh_objects(), "BodyDiagnostic")
    implementation._apply_world_transforms([body])
    report["bodyImported"] = implementation._mesh_stats(body)
    report["bodyImportedComponents"] = _mesh_components(body)
    report["bodyMergeByDistance"] = {
        "distance": 1e-7,
        "removedVertices": _merge_by_distance(body, 1e-7),
        "stats": implementation._mesh_stats(body),
    }
    body_bounds = implementation._bounds([body])
    body_cut_z = implementation.resolve_cut_height(
        body_bounds[0][2], body_bounds[1][2], args.body_cut_ratio
    )
    implementation._delete_vertices_by_world_z(body, body_cut_z, delete_above=True)
    report["bodyCut"] = {
        "cutZ": body_cut_z,
        "stats": implementation._mesh_stats(body),
        "boundaryComponents": _boundary_components(body),
        "meshComponents": _mesh_components(body),
    }

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.head.resolve()))
    head = implementation._join_meshes(implementation._mesh_objects(), "HeadDiagnostic")
    implementation._apply_world_transforms([head])
    report["headImported"] = implementation._mesh_stats(head)
    report["headImportedComponents"] = _mesh_components(head)
    report["headMergeByDistance"] = {
        "distance": 1e-7,
        "removedVertices": _merge_by_distance(head, 1e-7),
        "stats": implementation._mesh_stats(head),
    }
    head_bounds = implementation._bounds([head])
    head_cut_z = implementation.resolve_cut_height(
        head_bounds[0][2], head_bounds[1][2], args.head_cut_ratio
    )
    implementation._delete_vertices_by_world_z(head, head_cut_z, delete_above=False)
    report["headCut"] = {
        "cutZ": head_cut_z,
        "stats": implementation._mesh_stats(head),
        "boundaryComponents": _boundary_components(head),
        "meshComponents": _mesh_components(head),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
