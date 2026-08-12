"""Stabilize collar and inner shoulder weights on a generated armored character."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from add_ror2_bandit_rig import COMPONENT_COUNT, COMPONENT_FORMAT, read_accessor, read_glb, write_glb


def smoothstep(start: float, end: float, value: float) -> float:
    if start == end:
        return 1.0 if value >= end else 0.0
    ratio = max(0.0, min(1.0, (value - start) / (end - start)))
    return ratio * ratio * (3.0 - 2.0 * ratio)


def armored_chest_floor(position: tuple[float, ...]) -> float:
    x, y, _z = (float(value) for value in position[:3])
    abs_x = abs(x)

    neck_horizontal = 1.0 - smoothstep(0.14, 0.25, abs_x)
    neck_vertical = smoothstep(1.40, 1.48, y) * (1.0 - smoothstep(1.70, 1.78, y))
    neck_floor = 0.985 * neck_horizontal * neck_vertical

    shoulder_horizontal = smoothstep(0.12, 0.18, abs_x) * (
        1.0 - smoothstep(0.32, 0.46, abs_x)
    )
    shoulder_vertical = smoothstep(1.34, 1.43, y) * (
        1.0 - smoothstep(1.70, 1.80, y)
    )
    shoulder_floor = 0.95 * shoulder_horizontal * shoulder_vertical
    return max(neck_floor, shoulder_floor)


def stabilize_row(
    joints: tuple[int, ...],
    weights: tuple[float, ...],
    *,
    chest_slot: int,
    chest_floor: float,
) -> tuple[tuple[int, int, int, int], tuple[float, float, float, float]]:
    merged: dict[int, float] = {}
    for joint, weight in zip(joints, weights):
        if weight > 0.0:
            merged[int(joint)] = merged.get(int(joint), 0.0) + float(weight)
    current_chest = merged.get(chest_slot, 0.0)
    if chest_floor > current_chest:
        remaining_before = max(1e-8, 1.0 - current_chest)
        remaining_after = max(0.0, 1.0 - chest_floor)
        scale = remaining_after / remaining_before
        for joint in list(merged):
            if joint != chest_slot:
                merged[joint] *= scale
        merged[chest_slot] = chest_floor

    selected = sorted(merged.items(), key=lambda item: item[1], reverse=True)[:4]
    total = sum(weight for _, weight in selected)
    if total <= 0.0:
        selected = [(chest_slot, 1.0)]
        total = 1.0
    output_joints = [joint for joint, _weight in selected]
    output_weights = [weight / total for _joint, weight in selected]
    while len(output_joints) < 4:
        output_joints.append(0)
        output_weights.append(0.0)
    return tuple(output_joints), tuple(output_weights)


def stabilize_upper_body_weights(
    positions: list[tuple[float, ...]],
    rows: list[tuple[tuple[int, ...], tuple[float, ...]]],
    *,
    chest_slot: int,
) -> tuple[list[tuple[tuple[int, int, int, int], tuple[float, float, float, float]]], int]:
    output = []
    changed = 0
    for position, (joints, weights) in zip(positions, rows):
        floor = armored_chest_floor(position)
        next_row = stabilize_row(joints, weights, chest_slot=chest_slot, chest_floor=floor)
        if next_row != (tuple(joints), tuple(weights)) and floor > 0.0:
            changed += 1
        output.append(next_row)
    return output, changed


def write_accessor_rows(
    document: dict,
    binary: bytearray,
    accessor_index: int,
    rows: list[tuple[int, ...] | tuple[float, ...]],
) -> None:
    accessor = document["accessors"][accessor_index]
    view = document["bufferViews"][accessor["bufferView"]]
    component_type = accessor["componentType"]
    component_count = COMPONENT_COUNT[accessor["type"]]
    fmt = "<" + COMPONENT_FORMAT[component_type] * component_count
    size = struct.calcsize(fmt)
    stride = view.get("byteStride", size)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    if accessor["count"] != len(rows):
        raise ValueError("Accessor row count does not match replacement data")
    for index, row in enumerate(rows):
        struct.pack_into(fmt, binary, start + index * stride, *row)


def stabilize_glb(source: Path, destination: Path) -> dict[str, int]:
    document, binary = read_glb(source)
    mesh_nodes = [node for node in document.get("nodes", []) if "mesh" in node and "skin" in node]
    if len(mesh_nodes) != 1:
        raise ValueError(f"Expected one visible skinned mesh, found {len(mesh_nodes)}")
    node = mesh_nodes[0]
    skin = document["skins"][node["skin"]]
    joint_names = [document["nodes"][index].get("name", "") for index in skin["joints"]]
    if "chest" not in joint_names:
        raise ValueError("Target skin has no chest joint")
    chest_slot = joint_names.index("chest")
    changed = 0
    vertices = 0
    for primitive in document["meshes"][node["mesh"]].get("primitives", []):
        attributes = primitive.get("attributes", {})
        required = {"POSITION", "JOINTS_0", "WEIGHTS_0"}
        if not required.issubset(attributes):
            continue
        positions = read_accessor(document, binary, attributes["POSITION"])
        joints = read_accessor(document, binary, attributes["JOINTS_0"])
        weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
        rows = [
            (tuple(int(value) for value in joint_row), tuple(float(value) for value in weight_row))
            for joint_row, weight_row in zip(joints, weights)
        ]
        stabilized, primitive_changed = stabilize_upper_body_weights(
            positions,
            rows,
            chest_slot=chest_slot,
        )
        write_accessor_rows(
            document,
            binary,
            attributes["JOINTS_0"],
            [row[0] for row in stabilized],
        )
        write_accessor_rows(
            document,
            binary,
            attributes["WEIGHTS_0"],
            [row[1] for row in stabilized],
        )
        changed += primitive_changed
        vertices += len(positions)

    extras = document.setdefault("asset", {}).setdefault("extras", {})
    extras.setdefault("meshFixes", []).append(
        {"type": "armored-upper-body-weight-stabilization", "changedVertices": changed}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_glb(destination, document, binary)
    return {"vertices": vertices, "changedVertices": changed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(stabilize_glb(args.source.resolve(), args.destination.resolve()))


if __name__ == "__main__":
    main()
