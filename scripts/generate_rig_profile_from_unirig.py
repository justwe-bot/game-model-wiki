"""Generate a standard humanoid rig profile from a UniRig-rigged GLB."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from add_ror2_bandit_rig import read_accessor, read_glb
from build_space_commando import world_matrices
from rig_hunyuan_character import matrix_point
from unirig_humanoid_mapping import CORE_PROFILE_BONES, MIRRORED_PROFILE_PAIRS


def mesh_bounds(document: dict, binary: bytearray) -> tuple[list[float], list[float]]:
    worlds = world_matrices(document)
    points: list[tuple[float, float, float]] = []
    for node_index, node in enumerate(document.get("nodes", [])):
        if "mesh" not in node:
            continue
        for primitive in document["meshes"][node["mesh"]].get("primitives", []):
            positions = read_accessor(document, binary, primitive["attributes"]["POSITION"])
            points.extend(
                matrix_point(worlds[node_index], tuple(float(value) for value in row))
                for row in positions
            )
    if not points:
        raise ValueError("UniRig GLB has no mesh vertices")
    return (
        [min(point[axis] for point in points) for axis in range(3)],
        [max(point[axis] for point in points) for axis in range(3)],
    )


def joint_origins(document: dict) -> dict[str, tuple[float, float, float]]:
    worlds = world_matrices(document)
    result = {}
    for index, node in enumerate(document.get("nodes", [])):
        name = node.get("name")
        if isinstance(name, str) and name.startswith("bone_"):
            matrix = worlds[index]
            result[name] = tuple(float(matrix[axis][3]) for axis in range(3))
    return result


def transformed_point(
    point: tuple[float, float, float],
    *,
    center_x: float,
    minimum_y: float,
    scale: float,
    depth_offset: float,
) -> list[float]:
    return [
        (point[0] - center_x) * scale,
        (point[1] - minimum_y) * scale,
        depth_offset - point[2] * scale,
    ]


def symmetrize(points: dict[str, list[float]]) -> None:
    for right_name, left_name in MIRRORED_PROFILE_PAIRS:
        right = points[right_name]
        left = points[left_name]
        x = (abs(right[0]) + abs(left[0])) * 0.5
        y = (right[1] + left[1]) * 0.5
        z = (right[2] + left[2]) * 0.5
        points[right_name] = [x, y, z]
        points[left_name] = [-x, y, z]
    for name in ("base", "pelvis", "stomach", "chest", "head"):
        points[name][0] = 0.0


def distance(first: list[float], second: list[float]) -> float:
    return math.dist(first, second)


def profile_ratios(points: dict[str, list[float]], target_height: float) -> dict[str, float]:
    return {
        "hipHeight": points["pelvis"][1] / target_height,
        "shoulderHeight": points["upper_arm.r"][1] / target_height,
        "upperArmToForearm": distance(points["upper_arm.r"], points["lower_arm.r"])
        / max(distance(points["lower_arm.r"], points["hand.r"]), 1e-8),
        "thighToCalf": distance(points["thigh.r"], points["calf.r"])
        / max(distance(points["calf.r"], points["foot.r"]), 1e-8),
    }


def proxy_ratios(path: Path) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    bones = {bone["name"]: bone for bone in payload.get("bones", [])}
    count = int(payload.get("boneCount", len(bones)))
    mapping = CORE_PROFILE_BONES.get(count)
    if mapping is None:
        raise ValueError(f"Unsupported proxy skeleton topology: {count} bones")

    def bone_length(name: str) -> float:
        bone = bones[name]
        return math.dist(bone["head"], bone["tail"])

    arm_root = mapping["upper_arm.r"]
    forearm_root = mapping["lower_arm.r"]
    thigh_root = mapping["thigh.r"]
    calf_root = mapping["calf.r"]
    root_height = float(bones[mapping["pelvis"]]["head"][2])
    maximum_height = max(float(value) for bone in bones.values() for value in (bone["head"][2], bone["tail"][2]))
    minimum_height = min(float(value) for bone in bones.values() for value in (bone["head"][2], bone["tail"][2]))
    return {
        "hipHeight": (root_height - minimum_height) / max(maximum_height - minimum_height, 1e-8),
        "upperArmToForearm": bone_length(arm_root) / max(bone_length(forearm_root), 1e-8),
        "thighToCalf": bone_length(thigh_root) / max(bone_length(calf_root), 1e-8),
    }


def build_profile(
    rigged_path: Path,
    *,
    name: str,
    target_height: float,
    depth_offset: float,
    body_yaw_deg: float,
    proxy_skeleton: Path | None = None,
) -> dict[str, object]:
    document, binary = read_glb(rigged_path)
    skins = document.get("skins", [])
    if len(skins) != 1:
        raise ValueError(f"Expected one UniRig skin, found {len(skins)}")
    joints = skins[0].get("joints", [])
    bone_count = len(joints)
    mapping = CORE_PROFILE_BONES.get(bone_count)
    if mapping is None:
        raise ValueError(f"Unsupported UniRig humanoid topology: {bone_count} bones")
    origins = joint_origins(document)
    missing = sorted(set(mapping.values()) - set(origins))
    if missing:
        raise ValueError(f"UniRig GLB is missing mapped bones: {missing}")
    minimum, maximum = mesh_bounds(document, binary)
    source_height = maximum[1] - minimum[1]
    if source_height < 1e-5:
        raise ValueError("UniRig mesh has no usable Y-up height")
    scale = target_height / source_height
    center_x = (minimum[0] + maximum[0]) * 0.5
    points = {
        target: transformed_point(
            origins[source],
            center_x=center_x,
            minimum_y=minimum[1],
            scale=scale,
            depth_offset=depth_offset,
        )
        for target, source in mapping.items()
    }
    symmetrize(points)
    ratios = profile_ratios(points, target_height)
    warnings: list[str] = []
    if not 0.45 <= ratios["hipHeight"] <= 0.62:
        warnings.append(f"hip height ratio is unusual: {ratios['hipHeight']:.3f}")
    if not 0.7 <= ratios["upperArmToForearm"] <= 1.3:
        warnings.append(
            f"upper-arm/forearm ratio is unusual: {ratios['upperArmToForearm']:.3f}"
        )
    if not 0.7 <= ratios["thighToCalf"] <= 1.35:
        warnings.append(f"thigh/calf ratio is unusual: {ratios['thighToCalf']:.3f}")

    comparison = None
    if proxy_skeleton is not None:
        proxy = proxy_ratios(proxy_skeleton)
        deltas = {
            key: abs(ratios[key] - proxy[key]) / max(abs(ratios[key]), abs(proxy[key]), 1e-8)
            for key in ("hipHeight", "upperArmToForearm", "thighToCalf")
        }
        comparison = {"proxyRatios": proxy, "relativeDeltas": deltas}
        for key, delta in deltas.items():
            if delta > 0.25:
                warnings.append(f"proxy/final {key} differs by {delta * 100:.1f}%")

    return {
        "name": name,
        "targetHeight": target_height,
        "depthOffset": depth_offset,
        "bodyYawDeg": body_yaw_deg,
        "sourceOrientation": "unirig-gltf-y-up-to-template-y-up",
        "jointWorldPositions": points,
        "unirig": {
            "source": rigged_path.name,
            "boneCount": bone_count,
            "semanticMapping": mapping,
            "meshBounds": {"min": minimum, "max": maximum},
            "normalizationScale": scale,
            "ratios": ratios,
            "proxyComparison": comparison,
            "warnings": warnings,
        },
        "notes": "Generated from UniRig joint origins; mirrored limb pairs are symmetrized before template fitting.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rigged", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--target-height", type=float, default=1.9)
    parser.add_argument("--depth-offset", type=float, default=0.08)
    parser.add_argument("--body-yaw-deg", type=float, default=0.0)
    parser.add_argument("--proxy-skeleton", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    profile = build_profile(
        args.rigged.resolve(),
        name=args.name,
        target_height=args.target_height,
        depth_offset=args.depth_offset,
        body_yaw_deg=args.body_yaw_deg,
        proxy_skeleton=args.proxy_skeleton.resolve() if args.proxy_skeleton else None,
    )
    warnings = profile["unirig"]["warnings"]
    if args.strict and warnings:
        raise ValueError("; ".join(warnings))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Generated profile: {profile['unirig']['boneCount']} UniRig bones, "
        f"{len(profile['jointWorldPositions'])} mapped joints, {len(warnings)} warnings"
    )
    for warning in warnings:
        print(f"warning: {warning}")


if __name__ == "__main__":
    main()
