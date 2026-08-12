"""Validate an image-generated mesh rigged with the Space Commando template."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from add_ror2_bandit_rig import read_accessor, read_glb


def validate(path: Path) -> dict[str, int | float]:
    document, binary = read_glb(path)
    extras = document.get("asset", {}).get("extras", {})
    rig = extras.get("rig", {})
    generation = extras.get("imageToModel", {})
    if rig.get("source") != "space-commando-original-game-rig" or rig.get("originalGameRig") is not True:
        raise ValueError(f"{path}: missing Space Commando rig provenance")
    if generation.get("generator") != "stabilityai/TripoSR":
        raise ValueError(f"{path}: missing TripoSR generation provenance")
    skins = document.get("skins", [])
    if len(skins) != 1 or len(skins[0].get("joints", [])) != 78:
        raise ValueError(f"{path}: expected one 78-joint skin")
    if len(document.get("animations", [])) != 20:
        raise ValueError(f"{path}: expected 20 inherited animations")
    mesh_nodes = [node for node in document.get("nodes", []) if "mesh" in node]
    if len(mesh_nodes) != 1 or mesh_nodes[0].get("skin") != 0:
        raise ValueError(f"{path}: expected one visible skinned mesh")

    vertices = 0
    triangles = 0
    weight_error = 0.0
    for primitive in document["meshes"][mesh_nodes[0]["mesh"]]["primitives"]:
        attributes = primitive["attributes"]
        required = {"POSITION", "NORMAL", "COLOR_0", "JOINTS_0", "WEIGHTS_0"}
        if not required.issubset(attributes):
            raise ValueError(f"{path}: generated primitive is missing {sorted(required - set(attributes))}")
        positions = read_accessor(document, binary, attributes["POSITION"])
        normals = read_accessor(document, binary, attributes["NORMAL"])
        colors = read_accessor(document, binary, attributes["COLOR_0"])
        joints = read_accessor(document, binary, attributes["JOINTS_0"])
        weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
        if len({len(positions), len(normals), len(colors), len(joints), len(weights)}) != 1:
            raise ValueError(f"{path}: generated vertex attribute counts differ")
        vertices += len(positions)
        triangles += document["accessors"][primitive["indices"]]["count"] // 3
        for joint_row, weight_row in zip(joints, weights):
            if max(joint_row) >= 78:
                raise ValueError(f"{path}: joint index exceeds inherited skin")
            weight_error = max(weight_error, abs(sum(weight_row) - 1.0))
    if vertices < 1000 or triangles < 1000:
        raise ValueError(f"{path}: generated geometry is unexpectedly sparse")
    if weight_error > 1e-5:
        raise ValueError(f"{path}: skin weights are not normalized: {weight_error}")
    return {
        "vertices": vertices,
        "triangles": triangles,
        "joints": 78,
        "animations": 20,
        "weightError": weight_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.paths:
        try:
            stats = validate(path)
            print(
                f"{path}: {stats['triangles']} triangles, {stats['vertices']} vertices, "
                f"{stats['joints']} joints, {stats['animations']} animations, "
                f"weightError={stats['weightError']:.2e}"
            )
        except Exception as error:
            failed = True
            print(error, file=sys.stderr)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
