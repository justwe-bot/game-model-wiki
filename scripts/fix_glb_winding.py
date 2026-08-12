"""Correct triangle winding in an existing GLB without changing its rig or animations."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from add_ror2_bandit_rig import COMPONENT_FORMAT, read_accessor, read_glb, write_glb


def reverse_triangle_indices(indices: list[int]) -> list[int]:
    if len(indices) % 3:
        raise ValueError("Triangle index count must be divisible by three")
    result = list(indices)
    for offset in range(0, len(result), 3):
        result[offset + 1], result[offset + 2] = result[offset + 2], result[offset + 1]
    return result


def orientation_score(
    positions: list[tuple[float, ...]],
    normals: list[tuple[float, ...]],
    indices: list[int],
) -> float:
    score = 0.0
    samples = 0
    step = max(3, (len(indices) // 12000) // 3 * 3)
    for offset in range(0, len(indices) - 2, step):
        a, b, c = (indices[offset], indices[offset + 1], indices[offset + 2])
        pa, pb, pc = positions[a], positions[b], positions[c]
        ux, uy, uz = (pb[axis] - pa[axis] for axis in range(3))
        vx, vy, vz = (pc[axis] - pa[axis] for axis in range(3))
        geometric = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        normal = tuple(normals[a][axis] + normals[b][axis] + normals[c][axis] for axis in range(3))
        dot = sum(geometric[axis] * normal[axis] for axis in range(3))
        if abs(dot) < 1e-12:
            continue
        score += 1.0 if dot > 0.0 else -1.0
        samples += 1
    return score / samples if samples else 0.0


def write_indices(document: dict, binary: bytearray, accessor_index: int, indices: list[int]) -> None:
    accessor = document["accessors"][accessor_index]
    if accessor.get("type") != "SCALAR" or accessor.get("count") != len(indices):
        raise ValueError("Index accessor does not match the replacement index data")
    component_type = accessor["componentType"]
    if component_type not in {5121, 5123, 5125}:
        raise ValueError(f"Unsupported index component type: {component_type}")
    view = document["bufferViews"][accessor["bufferView"]]
    fmt = "<" + COMPONENT_FORMAT[component_type]
    size = struct.calcsize(fmt)
    stride = view.get("byteStride", size)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    for row, index in enumerate(indices):
        struct.pack_into(fmt, binary, start + row * stride, index)


def fix_winding(source: Path, destination: Path) -> dict[str, int | float]:
    document, binary = read_glb(source)
    fixed_triangles = 0
    scores: list[float] = []
    for node in document.get("nodes", []):
        if "mesh" not in node:
            continue
        for primitive in document["meshes"][node["mesh"]].get("primitives", []):
            if primitive.get("mode", 4) != 4 or "indices" not in primitive:
                continue
            attributes = primitive.get("attributes", {})
            if "POSITION" not in attributes or "NORMAL" not in attributes:
                continue
            positions = read_accessor(document, binary, attributes["POSITION"])
            normals = read_accessor(document, binary, attributes["NORMAL"])
            indices = [int(row[0]) for row in read_accessor(document, binary, primitive["indices"])]
            before = orientation_score(positions, normals, indices)
            scores.append(before)
            if before >= 0.0:
                continue
            write_indices(document, binary, primitive["indices"], reverse_triangle_indices(indices))
            fixed_triangles += len(indices) // 3

    if fixed_triangles == 0:
        raise ValueError(f"No reversed visible triangle primitives found in {source}")
    extras = document.setdefault("asset", {}).setdefault("extras", {})
    extras.setdefault("meshFixes", []).append(
        {"type": "reverse-triangle-winding", "triangles": fixed_triangles}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_glb(destination, document, binary)
    return {
        "triangles": fixed_triangles,
        "minimumOrientationScoreBefore": min(scores),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(fix_winding(args.source.resolve(), args.destination.resolve()))


if __name__ == "__main__":
    main()
