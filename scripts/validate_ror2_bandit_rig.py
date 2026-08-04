"""Validate the reconstructed skin and bind pose in Bandit's GLBs."""

from __future__ import annotations

import argparse
from pathlib import Path

from add_ror2_bandit_rig import JOINTS, read_accessor, read_glb


EXPECTED_RIG = "reconstructed-humanoid-v1"


def validate(path: Path) -> dict[str, int | float]:
    document, binary = read_glb(path)
    rig_metadata = document.get("asset", {}).get("extras", {}).get("rig", {})
    if rig_metadata.get("source") != EXPECTED_RIG:
        raise ValueError(f"{path}: missing {EXPECTED_RIG} asset metadata")

    skins = document.get("skins", [])
    if len(skins) != 1:
        raise ValueError(f"{path}: expected one skin, found {len(skins)}")
    skin = skins[0]
    if skin.get("extras", {}).get("originalGameRig") is not False:
        raise ValueError(f"{path}: reconstructed rig provenance is not explicit")
    if len(skin["joints"]) != len(JOINTS):
        raise ValueError(
            f"{path}: expected {len(JOINTS)} joints, found {len(skin['joints'])}"
        )
    names = [document["nodes"][index].get("name") for index in skin["joints"]]
    expected_names = [joint.name for joint in JOINTS]
    if names != expected_names:
        raise ValueError(f"{path}: joint hierarchy does not match the Bandit rig")

    mesh_nodes = [node for node in document["nodes"] if "mesh" in node]
    if len(mesh_nodes) != 1 or mesh_nodes[0].get("skin") != 0:
        raise ValueError(f"{path}: mesh node is not bound to skin 0")
    if skin["skeleton"] not in mesh_nodes[0].get("children", []):
        raise ValueError(f"{path}: rig root is not parented under the mesh node")

    vertex_count = 0
    triangle_count = 0
    weight_min = 1.0
    weight_max = 1.0
    for primitive in document["meshes"][mesh_nodes[0]["mesh"]]["primitives"]:
        attributes = primitive["attributes"]
        for semantic in ("POSITION", "JOINTS_0", "WEIGHTS_0"):
            if semantic not in attributes:
                raise ValueError(f"{path}: primitive is missing {semantic}")
        positions = read_accessor(document, binary, attributes["POSITION"])
        joint_rows = read_accessor(document, binary, attributes["JOINTS_0"])
        weight_rows = read_accessor(document, binary, attributes["WEIGHTS_0"])
        if len(positions) != len(joint_rows) or len(positions) != len(weight_rows):
            raise ValueError(f"{path}: skin attribute counts do not match positions")
        for joint_row, weight_row in zip(joint_rows, weight_rows):
            if max(joint_row) >= len(skin["joints"]):
                raise ValueError(f"{path}: joint index exceeds skin joint count")
            if min(weight_row) < 0.0:
                raise ValueError(f"{path}: negative skin weight")
            weight_sum = sum(weight_row)
            weight_min = min(weight_min, weight_sum)
            weight_max = max(weight_max, weight_sum)
            if abs(weight_sum - 1.0) > 1e-5:
                raise ValueError(f"{path}: weights do not sum to one: {weight_sum}")
        vertex_count += len(positions)
        if "indices" in primitive:
            triangle_count += len(read_accessor(document, binary, primitive["indices"])) // 3
        else:
            triangle_count += len(positions) // 3

    inverse_bind = read_accessor(document, binary, skin["inverseBindMatrices"])
    if len(inverse_bind) != len(skin["joints"]):
        raise ValueError(f"{path}: inverse bind matrix count does not match joints")

    joint_set = set(skin["joints"])
    parent_of = {}
    for parent_index, node in enumerate(document["nodes"]):
        for child in node.get("children", []):
            parent_of[child] = parent_index
    absolute_positions: dict[int, tuple[float, float, float]] = {}

    def absolute_position(node_index: int) -> tuple[float, float, float]:
        if node_index in absolute_positions:
            return absolute_positions[node_index]
        translation = tuple(document["nodes"][node_index].get("translation", [0.0, 0.0, 0.0]))
        parent = parent_of.get(node_index)
        if parent in joint_set:
            parent_position = absolute_position(parent)
            value = tuple(parent_position[axis] + translation[axis] for axis in range(3))
        else:
            value = translation
        absolute_positions[node_index] = value
        return value

    bind_error = 0.0
    for node_index, matrix in zip(skin["joints"], inverse_bind):
        position = absolute_position(node_index)
        expected_identity = (matrix[0], matrix[5], matrix[10], matrix[15])
        bind_error = max(
            bind_error,
            *(abs(value - 1.0) for value in expected_identity),
            abs(matrix[12] + position[0]),
            abs(matrix[13] + position[1]),
            abs(matrix[14] + position[2]),
        )
    if bind_error > 1e-5:
        raise ValueError(f"{path}: inverse bind matrices are inconsistent: {bind_error}")

    return {
        "vertices": vertex_count,
        "triangles": triangle_count,
        "joints": len(skin["joints"]),
        "weightMin": weight_min,
        "weightMax": weight_max,
        "bindError": bind_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        result = validate(path)
        print(
            f"{path}: {result['triangles']} triangles, {result['vertices']} vertices, "
            f"{result['joints']} joints, weights={result['weightMin']:.8f}.."
            f"{result['weightMax']:.8f}, bindError={result['bindError']:.2e}"
        )


if __name__ == "__main__":
    main()
