"""Make a Hunyuan GLB matte without changing its geometry or base color."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--roughness", type=float, default=0.78)
    parser.add_argument("--specular", type=float, default=0.12)
    parser.add_argument("--normal-strength", type=float, default=0.08)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))

    for material in bpy.data.materials:
        if not material.use_nodes or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                node.inputs["Roughness"].default_value = args.roughness
                node.inputs["Specular IOR Level"].default_value = args.specular
            elif node.type == "NORMAL_MAP":
                node.inputs["Strength"].default_value = args.normal_strength

        # A packed Hunyuan roughness texture overrides the scalar roughness.
        for link in list(material.node_tree.links):
            if (
                link.to_node.type == "BSDF_PRINCIPLED"
                and link.to_socket.name in {"Roughness", "Metallic"}
            ):
                material.node_tree.links.remove(link)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(args.output.resolve()),
        export_format="GLB",
        export_apply=True,
        export_materials="EXPORT",
    )


if __name__ == "__main__":
    main()
