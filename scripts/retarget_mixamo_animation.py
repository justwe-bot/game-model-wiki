"""Retarget Mixamo GLB animations onto the POPBOT UniRig template skeleton."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import struct

from add_ror2_bandit_rig import append_accessor, read_accessor, read_glb, write_glb


Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


# Map anatomical sides. A source heading correction is applied separately so
# a backwards-facing Mixamo upload does not force left/right bones to swap.
MIXAMO_TO_TEMPLATE = (
    ("mixamorig:Hips", "base"),
    ("mixamorig:Spine1", "stomach"),
    ("mixamorig:Spine2", "chest"),
    ("mixamorig:Head", "head"),
    ("mixamorig:LeftArm", "upper_arm.l"),
    ("mixamorig:LeftForeArm", "lower_arm.l"),
    ("mixamorig:LeftHand", "hand.l"),
    ("mixamorig:RightArm", "upper_arm.r"),
    ("mixamorig:RightForeArm", "lower_arm.r"),
    ("mixamorig:RightHand", "hand.r"),
    ("mixamorig:LeftUpLeg", "thigh.l"),
    ("mixamorig:LeftLeg", "calf.l"),
    ("mixamorig:LeftFoot", "foot.l"),
    ("mixamorig:LeftToeBase", "toe.l"),
    ("mixamorig:RightUpLeg", "thigh.r"),
    ("mixamorig:RightLeg", "calf.r"),
    ("mixamorig:RightFoot", "foot.r"),
    ("mixamorig:RightToeBase", "toe.r"),
)


@dataclass(frozen=True)
class Transform:
    translation: Vec3
    rotation: Quat
    scale: Vec3


def vec_add(a: Vec3, b: Vec3) -> Vec3:
    return tuple(a[index] + b[index] for index in range(3))  # type: ignore[return-value]


def vec_subtract(a: Vec3, b: Vec3) -> Vec3:
    return tuple(a[index] - b[index] for index in range(3))  # type: ignore[return-value]


def vec_multiply(a: Vec3, b: Vec3) -> Vec3:
    return tuple(a[index] * b[index] for index in range(3))  # type: ignore[return-value]


def vec_scale(value: Vec3, factor: float) -> Vec3:
    return tuple(component * factor for component in value)  # type: ignore[return-value]


def vec_lerp(a: tuple[float, ...], b: tuple[float, ...], amount: float) -> tuple[float, ...]:
    return tuple(a[index] + (b[index] - a[index]) * amount for index in range(len(a)))


def quat_normalize(value: Quat) -> Quat:
    length = math.sqrt(sum(component * component for component in value))
    if length <= 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(component / length for component in value)  # type: ignore[return-value]


def quat_multiply(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return quat_normalize(
        (
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )
    )


def quat_inverse(value: Quat) -> Quat:
    x, y, z, w = quat_normalize(value)
    return (-x, -y, -z, w)


def yaw_quaternion(degrees: float) -> Quat:
    half_angle = math.radians(degrees) * 0.5
    return (0.0, math.sin(half_angle), 0.0, math.cos(half_angle))


def quat_rotate(rotation: Quat, value: Vec3) -> Vec3:
    x, y, z, w = quat_normalize(rotation)
    vx, vy, vz = value
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + y * tz - z * ty,
        vy + w * ty + z * tx - x * tz,
        vz + w * tz + x * ty - y * tx,
    )


def quat_slerp(a: Quat, b: Quat, amount: float) -> Quat:
    left = quat_normalize(a)
    right = quat_normalize(b)
    dot = sum(left[index] * right[index] for index in range(4))
    if dot < 0.0:
        right = tuple(-component for component in right)  # type: ignore[assignment]
        dot = -dot
    if dot > 0.9995:
        return quat_normalize(vec_lerp(left, right, amount))  # type: ignore[arg-type]
    theta = math.acos(max(-1.0, min(1.0, dot)))
    sine = math.sin(theta)
    first = math.sin((1.0 - amount) * theta) / sine
    second = math.sin(amount * theta) / sine
    return quat_normalize(
        tuple(left[index] * first + right[index] * second for index in range(4))  # type: ignore[arg-type]
    )


def node_transform(node: dict) -> Transform:
    if "matrix" in node:
        raise ValueError("Mixamo retargeting requires TRS nodes, not matrix nodes")
    return Transform(
        tuple(float(value) for value in node.get("translation", [0.0, 0.0, 0.0])),
        quat_normalize(tuple(float(value) for value in node.get("rotation", [0.0, 0.0, 0.0, 1.0]))),
        tuple(float(value) for value in node.get("scale", [1.0, 1.0, 1.0])),
    )


def compose(parent: Transform | None, local: Transform) -> Transform:
    if parent is None:
        return local
    scaled = vec_multiply(local.translation, parent.scale)
    return Transform(
        vec_add(parent.translation, quat_rotate(parent.rotation, scaled)),
        quat_multiply(parent.rotation, local.rotation),
        vec_multiply(parent.scale, local.scale),
    )


def inverse_transform_point(parent: Transform | None, value: Vec3) -> Vec3:
    if parent is None:
        return value
    unrotated = quat_rotate(quat_inverse(parent.rotation), vec_subtract(value, parent.translation))
    return tuple(
        unrotated[index] / max(abs(parent.scale[index]), 1e-8)
        for index in range(3)
    )  # type: ignore[return-value]


def hierarchy(document: dict) -> tuple[list[int | None], list[int]]:
    nodes = document.get("nodes", [])
    parents: list[int | None] = [None] * len(nodes)
    for parent, node in enumerate(nodes):
        for child in node.get("children", []):
            parents[child] = parent

    order: list[int] = []

    def visit(index: int) -> None:
        order.append(index)
        for child in nodes[index].get("children", []):
            visit(child)

    for index, parent in enumerate(parents):
        if parent is None:
            visit(index)
    return parents, order


def world_transforms(
    local: list[Transform],
    parents: list[int | None],
    order: list[int],
) -> list[Transform]:
    world: list[Transform | None] = [None] * len(local)
    for index in order:
        parent = parents[index]
        world[index] = compose(world[parent] if parent is not None else None, local[index])
    return [value if value is not None else local[index] for index, value in enumerate(world)]


def sample_track(
    times: list[float],
    values: list[tuple[float, ...]],
    time: float,
    path: str,
    interpolation: str = "LINEAR",
) -> tuple[float, ...]:
    if time <= times[0]:
        return values[0]
    if time >= times[-1]:
        return values[-1]
    right = bisect_right(times, time)
    left = right - 1
    if interpolation == "STEP":
        return values[left]
    amount = (time - times[left]) / max(times[right] - times[left], 1e-8)
    if path == "rotation":
        return quat_slerp(values[left], values[right], amount)  # type: ignore[arg-type]
    return vec_lerp(values[left], values[right], amount)


def animation_tracks(document: dict, binary: bytearray, animation: dict) -> dict:
    tracks: dict[
        tuple[int, str],
        tuple[list[float], list[tuple[float, ...]], str],
    ] = {}
    for channel in animation.get("channels", []):
        target = channel["target"]
        sampler = animation["samplers"][channel["sampler"]]
        interpolation = sampler.get("interpolation", "LINEAR")
        if interpolation not in {"LINEAR", "STEP", "CUBICSPLINE"}:
            raise ValueError(f"Unsupported Mixamo interpolation: {interpolation}")
        times = [float(row[0]) for row in read_accessor(document, binary, sampler["input"])]
        values = [tuple(float(value) for value in row) for row in read_accessor(document, binary, sampler["output"])]
        if interpolation == "CUBICSPLINE":
            if len(values) != len(times) * 3:
                raise ValueError("CUBICSPLINE tracks must contain in-tangent, value, and out-tangent rows")
            values = values[1::3]
            interpolation = "LINEAR"
        tracks[(target["node"], target["path"])] = (times, values, interpolation)
    return tracks


def packed_floats(rows: list[tuple[float, ...]]) -> bytes:
    return b"".join(struct.pack("<" + "f" * len(row), *row) for row in rows)


def require_finite_rows(label: str, rows: list[tuple[float, ...]]) -> None:
    if not rows:
        raise ValueError(f"{label} has no keyframes")
    for row_index, row in enumerate(rows):
        if not all(math.isfinite(value) for value in row):
            raise ValueError(f"{label} contains a non-finite value at keyframe {row_index}")


def skeleton_scale(rest_world: list[Transform], names: dict[str, int], hips: str, feet: tuple[str, str]) -> float:
    hip_position = rest_world[names[hips]].translation
    distances = []
    for foot in feet:
        foot_position = rest_world[names[foot]].translation
        distances.append(math.sqrt(sum((hip_position[index] - foot_position[index]) ** 2 for index in range(3))))
    return sum(distances) / len(distances)


def retarget_animation(
    target_document: dict,
    target_binary: bytearray,
    source_path: Path,
    animation_name: str,
    source_yaw_degrees: float = 0.0,
) -> dict:
    source_document, source_binary = read_glb(source_path)
    source_animations = source_document.get("animations", [])
    if len(source_animations) != 1:
        raise ValueError(f"Expected one Mixamo animation in {source_path}, found {len(source_animations)}")

    source_names = {node.get("name", ""): index for index, node in enumerate(source_document.get("nodes", []))}
    target_names = {node.get("name", ""): index for index, node in enumerate(target_document.get("nodes", []))}
    missing_source = [name for name, _target in MIXAMO_TO_TEMPLATE if name not in source_names]
    missing_target = [name for _source, name in MIXAMO_TO_TEMPLATE if name not in target_names]
    if missing_source or missing_target:
        raise ValueError(f"Missing mapped bones: source={missing_source}, target={missing_target}")

    source_parents, source_order = hierarchy(source_document)
    target_parents, target_order = hierarchy(target_document)
    source_rest_local = [node_transform(node) for node in source_document["nodes"]]
    target_rest_local = [node_transform(node) for node in target_document["nodes"]]
    source_rest_world = world_transforms(source_rest_local, source_parents, source_order)
    target_rest_world = world_transforms(target_rest_local, target_parents, target_order)
    source_alignment = yaw_quaternion(source_yaw_degrees)
    source_alignment_inverse = quat_inverse(source_alignment)

    tracks = animation_tracks(source_document, source_binary, source_animations[0])
    first_times = next(iter(tracks.values()))[0]
    if not first_times:
        raise ValueError(f"Mixamo animation has no keyframes: {source_path}")
    times = first_times

    source_leg_length = skeleton_scale(
        source_rest_world,
        source_names,
        "mixamorig:Hips",
        ("mixamorig:LeftFoot", "mixamorig:RightFoot"),
    )
    target_leg_length = skeleton_scale(
        target_rest_world,
        target_names,
        "base",
        ("foot.l", "foot.r"),
    )
    motion_scale = target_leg_length / max(source_leg_length, 1e-8)

    source_for_target = {
        target_names[target_name]: source_names[source_name]
        for source_name, target_name in MIXAMO_TO_TEMPLATE
    }
    rotation_rows = {target_index: [] for target_index in source_for_target}
    base_index = target_names["base"]
    base_translation_rows: list[tuple[float, ...]] = []

    for time in times:
        source_local = deepcopy(source_rest_local)
        for (node_index, path), (track_times, values, interpolation) in tracks.items():
            sampled = sample_track(track_times, values, time, path, interpolation)
            current = source_local[node_index]
            if path == "translation":
                source_local[node_index] = Transform(sampled, current.rotation, current.scale)  # type: ignore[arg-type]
            elif path == "rotation":
                source_local[node_index] = Transform(current.translation, quat_normalize(sampled), current.scale)  # type: ignore[arg-type]
            elif path == "scale":
                source_local[node_index] = Transform(current.translation, current.rotation, sampled)  # type: ignore[arg-type]

        source_world = world_transforms(source_local, source_parents, source_order)
        target_world: list[Transform | None] = [None] * len(target_rest_local)
        for target_index in target_order:
            parent_index = target_parents[target_index]
            parent_world = target_world[parent_index] if parent_index is not None else None
            rest_local = target_rest_local[target_index]
            local_translation = rest_local.translation
            local_rotation = rest_local.rotation

            if target_index in source_for_target:
                source_index = source_for_target[target_index]
                source_delta = quat_multiply(
                    source_world[source_index].rotation,
                    quat_inverse(source_rest_world[source_index].rotation),
                )
                source_delta = quat_multiply(
                    quat_multiply(source_alignment, source_delta),
                    source_alignment_inverse,
                )
                desired_world_rotation = quat_multiply(
                    source_delta,
                    target_rest_world[target_index].rotation,
                )
                local_rotation = quat_multiply(
                    quat_inverse(parent_world.rotation if parent_world is not None else (0.0, 0.0, 0.0, 1.0)),
                    desired_world_rotation,
                )

                previous = rotation_rows[target_index][-1] if rotation_rows[target_index] else None
                if previous is not None and sum(previous[index] * local_rotation[index] for index in range(4)) < 0.0:
                    local_rotation = tuple(-component for component in local_rotation)  # type: ignore[assignment]
                rotation_rows[target_index].append(local_rotation)

                if target_index == base_index:
                    source_translation_delta = vec_subtract(
                        source_world[source_index].translation,
                        source_rest_world[source_index].translation,
                    )
                    source_translation_delta = quat_rotate(
                        source_alignment,
                        source_translation_delta,
                    )
                    desired_world_translation = vec_add(
                        target_rest_world[target_index].translation,
                        vec_scale(source_translation_delta, motion_scale),
                    )
                    local_translation = inverse_transform_point(parent_world, desired_world_translation)
                    base_translation_rows.append(local_translation)

            target_world[target_index] = compose(
                parent_world,
                Transform(local_translation, local_rotation, rest_local.scale),
            )

    time_accessor = append_accessor(
        target_document,
        target_binary,
        packed_floats([(time,) for time in times]),
        5126,
        "SCALAR",
        len(times),
    )
    target_document["accessors"][time_accessor]["min"] = [min(times)]
    target_document["accessors"][time_accessor]["max"] = [max(times)]

    animation = {
        "name": animation_name,
        "samplers": [],
        "channels": [],
        "extras": {
            "source": source_path.name,
            "retarget": "mixamo-world-rest-delta-v3",
            "anatomicalSideMapping": True,
            "sourceYawDegrees": source_yaw_degrees,
            "motionScale": motion_scale,
        },
    }
    for target_index in target_order:
        rows = rotation_rows.get(target_index)
        if not rows:
            continue
        require_finite_rows(
            f"{animation_name}:{target_document['nodes'][target_index].get('name', target_index)} rotation",
            rows,
        )
        output = append_accessor(
            target_document,
            target_binary,
            packed_floats(rows),
            5126,
            "VEC4",
            len(rows),
        )
        sampler_index = len(animation["samplers"])
        animation["samplers"].append({"input": time_accessor, "output": output, "interpolation": "LINEAR"})
        animation["channels"].append(
            {"sampler": sampler_index, "target": {"node": target_index, "path": "rotation"}}
        )

    require_finite_rows(f"{animation_name}:base translation", base_translation_rows)
    translation_output = append_accessor(
        target_document,
        target_binary,
        packed_floats(base_translation_rows),
        5126,
        "VEC3",
        len(base_translation_rows),
    )
    sampler_index = len(animation["samplers"])
    animation["samplers"].append(
        {"input": time_accessor, "output": translation_output, "interpolation": "LINEAR"}
    )
    animation["channels"].append(
        {"sampler": sampler_index, "target": {"node": base_index, "path": "translation"}}
    )
    target_document.setdefault("animations", []).append(animation)
    return {
        "name": animation_name,
        "source": source_path.name,
        "duration": max(times),
        "frames": len(times),
        "channels": len(animation["channels"]),
        "motionScale": motion_scale,
        "sourceYawDegrees": source_yaw_degrees,
    }


def parse_animation(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("Animation must use NAME=/absolute/or/relative/source.glb")
    return name, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--animation", action="append", type=parse_animation, required=True)
    parser.add_argument("--source-yaw-degrees", type=float, default=0.0)
    args = parser.parse_args()

    document, binary = read_glb(args.target.resolve())
    results = [
        retarget_animation(
            document,
            binary,
            source.resolve(),
            name,
            args.source_yaw_degrees,
        )
        for name, source in args.animation
    ]
    extras = document.setdefault("asset", {}).setdefault("extras", {})
    extras["mixamoRetarget"] = {
        "version": 1,
        "animations": results,
    }
    rig = extras.get("rig")
    if isinstance(rig, dict):
        rig["animations"] = len(document.get("animations", []))
    write_glb(args.output.resolve(), document, binary)
    print(
        {
            "output": str(args.output.resolve()),
            "animations": results,
            "totalAnimations": len(document.get("animations", [])),
        }
    )


if __name__ == "__main__":
    main()
