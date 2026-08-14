"""Report connected mesh components for a GLB or FBX asset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def component_report(obj) -> list[dict[str, object]]:
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
        faces = {face for vertex in vertices for face in vertex.link_faces}
        edges = {edge for vertex in vertices for edge in vertex.link_edges}
        components.append(
            {
                "vertices": len(vertices),
                "edges": len(edges),
                "faces": len(faces),
                "boundaryEdges": sum(1 for edge in edges if edge.is_boundary),
                "nonManifoldEdges": sum(1 for edge in edges if not edge.is_manifold),
                "bounds": {
                    "minimum": [
                        min(float(vertex.co[axis]) for vertex in vertices)
                        for axis in range(3)
                    ],
                    "maximum": [
                        max(float(vertex.co[axis]) for vertex in vertices)
                        for axis in range(3)
                    ],
                },
            }
        )
    bm.free()
    return sorted(components, key=lambda item: item["faces"], reverse=True)


def main() -> None:
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    suffix = args.input.suffix.lower()
    if suffix == ".glb":
        bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(args.input.resolve()))
    else:
        raise ValueError(f"Unsupported format: {suffix}")

    report = {}
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            report[obj.name] = component_report(obj)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
