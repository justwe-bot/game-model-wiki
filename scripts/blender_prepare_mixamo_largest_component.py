"""Export a Mixamo FBX containing only the largest connected mesh component."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def connected_components(bm):
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
        components.append((vertices, faces))
    return sorted(components, key=lambda item: len(item[1]), reverse=True)


def main() -> None:
    import bmesh
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    suffix = args.input.suffix.lower()
    if suffix == ".glb":
        bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(args.input.resolve()))
    else:
        raise ValueError(f"Unsupported input format: {suffix}")
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) != 1:
        raise ValueError(f"Expected one mesh, found {len(meshes)}")
    obj = meshes[0]

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    components = connected_components(bm)
    if not components:
        raise ValueError("Mesh has no connected components")
    largest_vertices, largest_faces = components[0]
    removed_faces = sum(len(faces) for _, faces in components[1:])
    delete_vertices = [vertex for vertices, _ in components[1:] for vertex in vertices]
    bmesh.ops.delete(bm, geom=delete_vertices, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.export_scene.fbx(
        filepath=str(args.output.resolve()),
        use_selection=True,
        object_types={"MESH"},
        apply_unit_scale=True,
        bake_space_transform=False,
        path_mode="COPY",
        embed_textures=True,
        add_leaf_bones=False,
        mesh_smooth_type="FACE",
        use_mesh_modifiers=True,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {
                "sourceComponents": len(components),
                "keptFaces": len(largest_faces),
                "removedComponents": len(components) - 1,
                "removedFaces": removed_faces,
                "output": str(args.output),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
