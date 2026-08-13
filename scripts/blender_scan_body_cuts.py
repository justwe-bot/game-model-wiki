"""Scan headless-body cut ratios and report boundary loop locations."""

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


def _load(repo_root: Path):
    module = types.ModuleType("modal")
    module.App = _ModalApp
    module.Image = _ModalBuilder()
    module.Volume = _ModalBuilder()
    sys.modules["modal"] = module
    path = repo_root / "cloud" / "modal_character_head_replacement.py"
    spec = importlib.util.spec_from_file_location("scan_body_cuts", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    return implementation


def _boundary_components(obj) -> list[dict[str, object]]:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    remaining = {edge for edge in bm.edges if edge.is_boundary}
    result = []
    while remaining:
        seed = remaining.pop()
        edges = [seed]
        vertices = set(seed.verts)
        stack = list(seed.verts)
        while stack:
            vertex = stack.pop()
            for edge in vertex.link_edges:
                if edge not in remaining or not edge.is_boundary:
                    continue
                remaining.remove(edge)
                edges.append(edge)
                for linked in edge.verts:
                    if linked not in vertices:
                        vertices.add(linked)
                        stack.append(linked)
        result.append(
            {
                "edges": len(edges),
                "center": [
                    sum(float(vertex.co[axis]) for vertex in vertices) / len(vertices)
                    for axis in range(3)
                ],
            }
        )
    bm.free()
    return sorted(result, key=lambda item: item["edges"], reverse=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    import bpy

    args = parse_args()
    implementation = _load(Path(__file__).resolve().parents[1])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.body.resolve()))
    source = implementation._join_meshes(implementation._mesh_objects(), "BodyCutScan")
    implementation._apply_world_transforms([source])
    implementation._weld_coincident_vertices(source)
    bounds = implementation._bounds([source])
    scans = []
    for ratio in (0.94, 0.95, 0.96, 0.97, 0.98, 0.99):
        candidate = source.copy()
        candidate.data = source.data.copy()
        bpy.context.collection.objects.link(candidate)
        cut_z = implementation.resolve_cut_height(bounds[0][2], bounds[1][2], ratio)
        removed = implementation._delete_vertices_by_world_z(
            candidate, cut_z, delete_above=True
        )
        scans.append(
            {
                "ratio": ratio,
                "cutZ": cut_z,
                "removedVertices": removed,
                "stats": implementation._mesh_stats(candidate),
                "boundaryComponents": _boundary_components(candidate),
            }
        )
        bpy.data.objects.remove(candidate, do_unlink=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scans, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
