"""Flatten and align oversized generated shoulder armor on Flux Vanguard-style rigs."""

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


def shoulder_spatial_weight(position: tuple[float, ...]) -> float:
    x, y, z = (float(value) for value in position[:3])
    abs_x = abs(x)
    horizontal = smoothstep(0.34, 0.40, abs_x) * (1.0 - smoothstep(0.52, 0.60, abs_x))
    vertical = smoothstep(1.36, 1.43, y) * (1.0 - smoothstep(1.66, 1.74, y))
    depth = smoothstep(-0.07, -0.01, z) * (1.0 - smoothstep(0.22, 0.28, z))
    return horizontal * vertical * depth


def reshape_point(
    position: tuple[float, ...],
    *,
    source_center: tuple[float, float, float],
    target_center: tuple[float, float, float],
    weight: float,
    width_scale: float = 1.04,
    height_scale: float = 0.75,
    depth_scale: float = 0.45,
) -> tuple[float, float, float]:
    x, y, z = (float(value) for value in position[:3])
    side = -1.0 if x < 0.0 else 1.0
    source_x, source_y, source_z = source_center
    target_x, target_y, target_z = target_center
    shaped_x = side * (target_x + (abs(x) - source_x) * width_scale)
    shaped_y = target_y + (y - source_y) * height_scale
    shaped_z = target_z + (z - source_z) * depth_scale
    blend = max(0.0, min(1.0, weight))
    return (
        x + (shaped_x - x) * blend,
        y + (shaped_y - y) * blend,
        z + (shaped_z - z) * blend,
    )


def write_accessor_rows(
    document: dict,
    binary: bytearray,
    accessor_index: int,
    rows: list[tuple[float, ...]],
) -> None:
    accessor = document["accessors"][accessor_index]
    view = document["bufferViews"][accessor["bufferView"]]
    component_count = COMPONENT_COUNT[accessor["type"]]
    fmt = "<" + COMPONENT_FORMAT[accessor["componentType"]] * component_count
    item_size = struct.calcsize(fmt)
    stride = view.get("byteStride", item_size)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    if accessor["count"] != len(rows):
        raise ValueError("Accessor row count does not match replacement data")
    for index, row in enumerate(rows):
        struct.pack_into(fmt, binary, start + index * stride, *row)


def reshape_shoulder_armor_positions(
    positions: list[tuple[float, ...]],
    *,
    width_scale: float = 1.04,
    height_scale: float = 0.75,
    depth_scale: float = 0.45,
    target_y_offset: float = -0.02,
) -> tuple[list[tuple[float, float, float]], int, dict[str, tuple[float, float, float]]]:
    weights = []
    accumulators = {-1: [0.0, 0.0, 0.0, 0.0], 1: [0.0, 0.0, 0.0, 0.0]}
    for position in positions:
        weight = shoulder_spatial_weight(position)
        weights.append(weight)
        if weight <= 0.0:
            continue
        side = -1 if position[0] < 0.0 else 1
        row = accumulators[side]
        row[0] += abs(position[0]) * weight
        row[1] += position[1] * weight
        row[2] += position[2] * weight
        row[3] += weight
    centers = {}
    for side in (-1, 1):
        total = accumulators[side][3]
        if total <= 0.0:
            raise ValueError(f"No shoulder vertices selected for side {side}")
        centers[side] = tuple(value / total for value in accumulators[side][:3])
    target_center = (
        (centers[-1][0] + centers[1][0]) * 0.5 + 0.005,
        (centers[-1][1] + centers[1][1]) * 0.5 + target_y_offset,
        (centers[-1][2] + centers[1][2]) * 0.5,
    )
    output = []
    changed = 0
    for position, weight in zip(positions, weights):
        side = -1 if position[0] < 0.0 else 1
        next_position = reshape_point(
            position,
            source_center=centers[side],
            target_center=target_center,
            weight=weight,
            width_scale=width_scale,
            height_scale=height_scale,
            depth_scale=depth_scale,
        )
        if next_position != tuple(position[:3]) and weight > 0.0:
            changed += 1
        output.append(next_position)
    return output, changed, {"left": centers[-1], "right": centers[1], "target": target_center}


def reshape_glb(
    source: Path,
    destination: Path,
    *,
    width_scale: float = 1.04,
    height_scale: float = 0.75,
    depth_scale: float = 0.45,
    target_y_offset: float = -0.02,
) -> dict[str, object]:
    document, binary = read_glb(source)
    mesh_nodes = [node for node in document.get("nodes", []) if "mesh" in node and "skin" in node]
    if len(mesh_nodes) != 1:
        raise ValueError(f"Expected one visible skinned mesh, found {len(mesh_nodes)}")
    mesh = document["meshes"][mesh_nodes[0]["mesh"]]
    changed = 0
    centers_used = []
    for primitive in mesh.get("primitives", []):
        attributes = primitive.get("attributes", {})
        if "POSITION" not in attributes:
            continue
        positions = [tuple(float(value) for value in row[:3]) for row in read_accessor(document, binary, attributes["POSITION"])]
        reshaped, primitive_changed, centers = reshape_shoulder_armor_positions(
            positions,
            width_scale=width_scale,
            height_scale=height_scale,
            depth_scale=depth_scale,
            target_y_offset=target_y_offset,
        )
        changed += primitive_changed
        write_accessor_rows(document, binary, attributes["POSITION"], reshaped)
        accessor = document["accessors"][attributes["POSITION"]]
        accessor["min"] = [min(row[axis] for row in reshaped) for axis in range(3)]
        accessor["max"] = [max(row[axis] for row in reshaped) for axis in range(3)]
        centers_used.append(centers)

    extras = document.setdefault("asset", {}).setdefault("extras", {})
    extras.setdefault("meshFixes", []).append(
        {
            "type": "armored-shoulder-silhouette-correction",
            "changedVertices": changed,
            "widthScale": width_scale,
            "heightScale": height_scale,
            "depthScale": depth_scale,
            "targetYOffset": target_y_offset,
        }
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_glb(destination, document, binary)
    return {"changedVertices": changed, "centers": centers_used}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--width-scale", type=float, default=1.04)
    parser.add_argument("--height-scale", type=float, default=0.75)
    parser.add_argument("--depth-scale", type=float, default=0.45)
    parser.add_argument("--target-y-offset", type=float, default=-0.02)
    args = parser.parse_args()
    print(
        reshape_glb(
            args.source.resolve(),
            args.destination.resolve(),
            width_scale=args.width_scale,
            height_scale=args.height_scale,
            depth_scale=args.depth_scale,
            target_y_offset=args.target_y_offset,
        )
    )


if __name__ == "__main__":
    main()
