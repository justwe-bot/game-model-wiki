"""Print mesh and PBR material details from a GLB without modifying it."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    import bpy
    from mathutils import Vector

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        triangles = sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)
        dimensions = tuple(round(value, 6) for value in obj.dimensions)
        bounds = [obj.matrix_world @ Vector(obj.bound_box[index]) for index in range(8)]
        minimum = tuple(round(min(point[axis] for point in bounds), 6) for axis in range(3))
        maximum = tuple(round(max(point[axis] for point in bounds), 6) for axis in range(3))
        print(
            "MESH",
            obj.name,
            "vertices=", len(obj.data.vertices),
            "triangles=", triangles,
            "dimensions=", dimensions,
            "bounds=", (minimum, maximum),
            "materials=", [material.name if material else None for material in obj.data.materials],
        )

    for material in bpy.data.materials:
        print("MATERIAL", material.name, "use_nodes=", material.use_nodes)
        if not material.use_nodes or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                print(
                    "  PRINCIPLED",
                    node.name,
                    "metallic=", round(node.inputs["Metallic"].default_value, 4),
                    "roughness=", round(node.inputs["Roughness"].default_value, 4),
                    "specular_ior=", round(node.inputs["Specular IOR Level"].default_value, 4),
                )
            elif node.type == "TEX_IMAGE":
                image = node.image
                print(
                    "  IMAGE",
                    node.name,
                    image.name if image else None,
                    tuple(image.size) if image else None,
                    image.colorspace_settings.name if image else None,
                )
            elif node.type == "NORMAL_MAP":
                print("  NORMAL", node.name, "strength=", round(node.inputs["Strength"].default_value, 4))
        for link in material.node_tree.links:
            print(
                "  LINK",
                link.from_node.name,
                link.from_socket.name,
                "->",
                link.to_node.name,
                link.to_socket.name,
            )


if __name__ == "__main__":
    main()
