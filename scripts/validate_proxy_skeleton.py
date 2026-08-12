"""Validate an anonymous UniRig proxy skeleton before final rig transfer."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from add_ror2_bandit_rig import read_accessor, read_glb
from build_space_commando import world_matrices
from rig_hunyuan_character import matrix_point


EXPECTED_PARENTS = {
    "bone_0": None,
    "bone_1": "bone_0",
    "bone_2": "bone_1",
    "bone_3": "bone_2",
    "bone_4": "bone_3",
    "bone_5": "bone_4",
    "bone_6": "bone_3",
    "bone_7": "bone_6",
    "bone_8": "bone_7",
    "bone_9": "bone_8",
    "bone_10": "bone_9",
    "bone_11": "bone_10",
    "bone_12": "bone_11",
    "bone_13": "bone_9",
    "bone_14": "bone_13",
    "bone_15": "bone_14",
    "bone_16": "bone_3",
    "bone_17": "bone_16",
    "bone_18": "bone_17",
    "bone_19": "bone_18",
    "bone_20": "bone_19",
    "bone_21": "bone_20",
    "bone_22": "bone_21",
    "bone_23": "bone_19",
    "bone_24": "bone_23",
    "bone_25": "bone_24",
    "bone_26": "bone_0",
    "bone_27": "bone_26",
    "bone_28": "bone_27",
    "bone_29": "bone_28",
    "bone_30": "bone_0",
    "bone_31": "bone_30",
    "bone_32": "bone_31",
    "bone_33": "bone_32",
}

MIRROR_PAIRS = [
    (f"bone_{right}", f"bone_{left}")
    for right, left in zip(range(6, 16), range(16, 26))
] + [
    ("bone_26", "bone_30"),
    ("bone_27", "bone_31"),
    ("bone_28", "bone_32"),
    ("bone_29", "bone_33"),
]


def distance(first: list[float], second: list[float]) -> float:
    return math.dist(first, second)


def mesh_bounds(path: Path) -> tuple[list[float], list[float]]:
    document, binary = read_glb(path)
    matrices = world_matrices(document)
    points: list[tuple[float, float, float]] = []
    for node_index, node in enumerate(document.get("nodes", [])):
        mesh_index = node.get("mesh")
        if mesh_index is None:
            continue
        matrix = matrices[node_index]
        for primitive in document["meshes"][mesh_index].get("primitives", []):
            positions = read_accessor(document, binary, primitive["attributes"]["POSITION"])
            points.extend(matrix_point(matrix, tuple(float(value) for value in row)) for row in positions)
    if not points:
        raise ValueError(f"{path}: no mesh vertices")
    return (
        [min(point[axis] for point in points) for axis in range(3)],
        [max(point[axis] for point in points) for axis in range(3)],
    )


def validate(skeleton_path: Path, mesh_path: Path) -> dict[str, object]:
    payload = json.loads(skeleton_path.read_text(encoding="utf-8"))
    bones = {bone["name"]: bone for bone in payload.get("bones", [])}
    failures: list[str] = []

    if payload.get("boneCount") != len(EXPECTED_PARENTS):
        failures.append(f"expected 34 bones, found {payload.get('boneCount')}")
    if set(bones) != set(EXPECTED_PARENTS):
        missing = sorted(set(EXPECTED_PARENTS) - set(bones))
        extra = sorted(set(bones) - set(EXPECTED_PARENTS))
        failures.append(f"unexpected bone set; missing={missing}, extra={extra}")
    for name, expected_parent in EXPECTED_PARENTS.items():
        if name in bones and bones[name].get("parent") != expected_parent:
            failures.append(
                f"{name} parent is {bones[name].get('parent')}, expected {expected_parent}"
            )

    lengths = {
        name: distance(bone["head"], bone["tail"])
        for name, bone in bones.items()
    }
    required_positive = ["bone_7", "bone_8", "bone_17", "bone_18", "bone_26", "bone_27", "bone_30", "bone_31"]
    for name in required_positive:
        if lengths.get(name, 0.0) < 0.1:
            failures.append(f"{name} is too short: {lengths.get(name, 0.0):.4f}")

    pair_deltas = []
    for right_name, left_name in MIRROR_PAIRS:
        if right_name not in bones or left_name not in bones:
            continue
        right = bones[right_name]
        left = bones[left_name]
        for endpoint in ("head", "tail"):
            right_point = right[endpoint]
            left_point = left[endpoint]
            pair_deltas.append(
                max(
                    abs(right_point[0] + left_point[0]),
                    abs(right_point[1] - left_point[1]),
                    abs(right_point[2] - left_point[2]),
                )
            )
    max_mirror_delta = max(pair_deltas, default=math.inf)
    if max_mirror_delta > 0.025:
        failures.append(f"left/right skeleton asymmetry is too high: {max_mirror_delta:.4f}")

    length_pairs = [
        ("upper arms", "bone_7", "bone_17"),
        ("forearms", "bone_8", "bone_18"),
        ("thighs", "bone_26", "bone_30"),
        ("calves", "bone_27", "bone_31"),
    ]
    length_ratios = {}
    for label, right_name, left_name in length_pairs:
        right_length = lengths.get(right_name, 0.0)
        left_length = lengths.get(left_name, 0.0)
        ratio = abs(right_length - left_length) / max(right_length, left_length, 1e-8)
        length_ratios[label] = ratio
        if ratio > 0.05:
            failures.append(f"{label} differ by {ratio * 100:.1f}%")

    for side, chain in {
        "right": ("bone_26", "bone_27", "bone_28", "bone_29"),
        "left": ("bone_30", "bone_31", "bone_32", "bone_33"),
    }.items():
        if not all(name in bones for name in chain):
            continue
        heights = [bones[name]["head"][2] for name in chain]
        if not all(first > second for first, second in zip(heights, heights[1:])):
            failures.append(f"{side} hip/knee/ankle/toe chain is not descending")
        foot_depth = bones[chain[-1]]["tail"][1] - bones[chain[-2]]["head"][1]
        if foot_depth > -0.05:
            failures.append(f"{side} foot points backward or lacks forward depth: {foot_depth:.4f}")

    minimum, maximum = mesh_bounds(mesh_path)
    margin = (0.08, 0.12, 0.08)
    outside = []
    for name, bone in bones.items():
        x, depth, height = (float(value) for value in bone["head"])
        point = (x, height, depth)
        if any(
            point[axis] < minimum[axis] - margin[axis]
            or point[axis] > maximum[axis] + margin[axis]
            for axis in range(3)
        ):
            outside.append(name)
    if outside:
        failures.append(f"bone heads outside proxy mesh bounds: {outside}")

    report = {
        "passed": not failures,
        "skeleton": str(skeleton_path),
        "mesh": str(mesh_path),
        "boneCount": len(bones),
        "meshBounds": {"min": minimum, "max": maximum},
        "maxMirrorDelta": max_mirror_delta,
        "pairedLengthDeltaRatios": length_ratios,
        "segmentLengths": {name: lengths[name] for name in sorted(lengths)},
        "failures": failures,
    }
    if failures:
        raise ValueError("; ".join(failures))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skeleton", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = validate(args.skeleton.resolve(), args.mesh.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"Proxy skeleton passed: {report['boneCount']} bones, "
        f"max mirror delta {report['maxMirrorDelta']:.4f}"
    )


if __name__ == "__main__":
    main()
