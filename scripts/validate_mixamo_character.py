"""Validate a final Mixamo-skinned animated character GLB."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from add_ror2_bandit_rig import read_accessor, read_glb


CORE_BONES = {
    "Hips",
    "Spine",
    "Head",
    "LeftArm",
    "LeftForeArm",
    "LeftHand",
    "RightArm",
    "RightForeArm",
    "RightHand",
    "LeftUpLeg",
    "LeftLeg",
    "LeftFoot",
    "RightUpLeg",
    "RightLeg",
    "RightFoot",
}


def canonical_name(value: str) -> str:
    return value.rsplit(":", 1)[-1]


def validate(
    path: Path,
    *,
    allow_transparent_materials: bool = False,
) -> dict[str, int | float]:
    document, binary = read_glb(path)
    transparent_materials = [
        material.get("name") or f"material-{index}"
        for index, material in enumerate(document.get("materials", []))
        if material.get("alphaMode", "OPAQUE") in {"BLEND", "MASK"}
    ]
    if transparent_materials and not allow_transparent_materials:
        raise ValueError(
            f"{path}: unexpected transparent character materials: "
            f"{transparent_materials}"
        )
    skins = document.get("skins", [])
    if len(skins) != 1:
        raise ValueError(f"{path}: expected one Mixamo skin, found {len(skins)}")
    skin = skins[0]
    joints = skin.get("joints", [])
    if len(joints) < 20:
        raise ValueError(f"{path}: expected a humanoid skeleton, found {len(joints)} joints")
    joint_names = {
        canonical_name(document["nodes"][index].get("name", "")) for index in joints
    }
    missing = sorted(CORE_BONES - joint_names)
    if missing:
        raise ValueError(f"{path}: missing core Mixamo bones: {missing}")

    skinned_nodes = [
        node
        for node in document.get("nodes", [])
        if "mesh" in node and node.get("skin") == 0
    ]
    if not skinned_nodes:
        raise ValueError(f"{path}: no visible mesh is attached to the Mixamo skin")

    triangles = 0
    vertices = 0
    max_weight_error = 0.0
    weighted_vertices = 0
    for node in skinned_nodes:
        for primitive in document["meshes"][node["mesh"]].get("primitives", []):
            attributes = primitive.get("attributes", {})
            required = {"POSITION", "NORMAL", "JOINTS_0", "WEIGHTS_0"}
            if not required.issubset(attributes):
                raise ValueError(f"{path}: skinned primitive is missing {sorted(required - attributes.keys())}")
            positions = read_accessor(document, binary, attributes["POSITION"])
            joint_rows = read_accessor(document, binary, attributes["JOINTS_0"])
            weight_rows = read_accessor(document, binary, attributes["WEIGHTS_0"])
            if not (len(positions) == len(joint_rows) == len(weight_rows)):
                raise ValueError(f"{path}: vertex skin attribute counts differ")
            vertices += len(positions)
            weighted_vertices += len(weight_rows)
            for row_index, (joint_row, weight_row) in enumerate(zip(joint_rows, weight_rows)):
                if max(int(value) for value in joint_row) >= len(joints):
                    raise ValueError(f"{path}: vertex {row_index} references an invalid joint")
                if not all(math.isfinite(float(value)) for value in weight_row):
                    raise ValueError(f"{path}: vertex {row_index} has non-finite weights")
                total = sum(float(value) for value in weight_row)
                max_weight_error = max(max_weight_error, abs(total - 1.0))
            if "indices" in primitive:
                triangles += len(read_accessor(document, binary, primitive["indices"])) // 3
            else:
                triangles += len(positions) // 3

    if vertices <= 0 or triangles <= 0:
        raise ValueError(f"{path}: character has no visible geometry")
    if max_weight_error > 1e-4:
        raise ValueError(f"{path}: weights are not normalized: {max_weight_error}")

    animations = document.get("animations", [])
    if not animations:
        raise ValueError(f"{path}: final character has no animations")
    names = [animation.get("name", "") for animation in animations]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: animation names are not unique")
    for animation in animations:
        if not animation.get("channels") or not animation.get("samplers"):
            raise ValueError(f"{path}: empty animation {animation.get('name')!r}")
        driven_nodes = {
            channel.get("target", {}).get("node") for channel in animation["channels"]
        }
        if not driven_nodes.intersection(joints):
            raise ValueError(
                f"{path}: animation {animation.get('name')!r} does not drive the Mixamo skeleton"
            )
        for sampler in animation["samplers"]:
            times = read_accessor(document, binary, sampler["input"])
            values = read_accessor(document, binary, sampler["output"])
            if not times or not values:
                raise ValueError(f"{path}: animation {animation.get('name')!r} has no keys")
            if not all(
                math.isfinite(float(value))
                for row in values
                for value in row
            ):
                raise ValueError(f"{path}: animation {animation.get('name')!r} has non-finite keys")

    return {
        "triangles": triangles,
        "vertices": vertices,
        "skinnedMeshes": len(skinned_nodes),
        "joints": len(joints),
        "animations": len(animations),
        "weightError": max_weight_error,
        "weightedVertices": weighted_vertices,
        "transparentMaterials": len(transparent_materials),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="+", type=Path)
    parser.add_argument("--allow-transparent-materials", action="store_true")
    args = parser.parse_args()
    for model in args.models:
        path = model.expanduser().resolve()
        result = validate(
            path,
            allow_transparent_materials=args.allow_transparent_materials,
        )
        print(
            f"{path}: {result['triangles']} triangles, {result['vertices']} vertices, "
            f"{result['skinnedMeshes']} skinned meshes, {result['joints']} joints, "
            f"{result['animations']} animations, weightError={result['weightError']:.2e}"
        )


if __name__ == "__main__":
    main()
