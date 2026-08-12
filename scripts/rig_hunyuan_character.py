"""Fit the Space Commando rig and weapon actions to a textured Hunyuan character."""

from __future__ import annotations

import argparse
from copy import deepcopy
import heapq
import json
import math
from pathlib import Path
import struct

from add_ror2_bandit_rig import append_accessor, read_accessor, read_glb, write_glb
from build_space_commando import (
    add_weapon_animations,
    append_buffer_view,
    append_mesh,
    build_weapon_attachments,
    local_matrix,
    matrix_inverse,
    matrix_multiply,
    matrix_quaternion,
    quat_from_to,
    quat_inverse,
    quat_multiply,
    quat_normalize,
    world_matrices,
)
from rig_generated_character import NearestWeightTree, interpolated_weight, source_weight_samples


Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Matrix = list[list[float]]

WEAPON_NODE_NAMES = {
    "SpaceCommandoGunL",
    "SpaceCommandoGunR",
    "SpaceCommandoBow",
    "SpaceCommandoBowStringDrawn",
    "SpaceCommandoBowStringRest",
    "SpaceCommandoArrow",
    "SpaceCommandoHeavyWeapon",
    "SpaceCommandoHeavyMuzzleFlash",
}
PROCEDURAL_ANIMATIONS = {"SpaceCommando_BowShot", "SpaceCommando_HeavyFire"}
STATIONARY_ATTACK_ANIMATIONS = {
    "Commando_FirePistolLeft",
    "Commando_FirePistolRight",
    "Commando_ReloadPistols",
    "Commando_FireFMJ",
    "Commando_FireBarrage",
    "Commando_ThrowGrenade",
}
ROOT_HEIGHT_LOCKED_ANIMATIONS = {"Commando_Idle", *STATIONARY_ATTACK_ANIMATIONS}
RIGID_LOWER_BODY_ANIMATIONS = {
    *ROOT_HEIGHT_LOCKED_ANIMATIONS,
    "Commando_RollForward",
    "Commando_RollBackward",
    "Commando_RollLeft",
    "Commando_RollRight",
    "Commando_SlideForward",
    "Commando_Jump",
}
GROUND_LOCOMOTION_ANIMATIONS = {
    "Commando_RunForward",
    "Commando_RunBackward",
    "Commando_RunLeft",
    "Commando_RunRight",
    "Commando_SprintForward",
}
LOWER_BODY_NODES = {
    "pelvis",
    "thigh.l",
    "calf.l",
    "foot.l",
    "toe.l",
    "thigh.r",
    "calf.r",
    "foot.r",
    "toe.r",
}
GROUND_GAIT_NODES = {"thigh.l", "calf.l", "foot.l", "thigh.r", "calf.r", "foot.r"}
GROUND_LOCOMOTION_PROFILES = {
    # All locomotion clips share the source's constant -16 deg root lean; the
    # base_blend below restores it fully (the ankle limits were raised to the
    # source's own foot-flex range so the compensation is never clamped).
    # Side strafes: the source thigh swings to about -71 deg during the
    # crossover; 0.70 keeps a crisp step without burying the hip in the torso.
    # The bob clamps sit beyond the scaled source deviation (max ~+-0.023) so
    # the smooth source bounce passes through without flat plateaus.
    "default": {
        "thigh_blend": 0.70,
        "thigh_rotation_limit_deg": 54.0,
        "calf_blend": 0.52,
        "foot_world_blend": 0.30,
        "foot_local_limit_deg": 34.0,
        "base_blend": 1.0,
        "time_scale": 1.0,
        "bob_scale": 0.24,
        "bob_min": -0.030,
        "bob_max": 0.030,
    },
    "Commando_RunForward": {
        # The source thigh swings -64.6 deg forward and +22.7 deg back, and
        # negative local X is the FORWARD stride. The previous profile scaled
        # that direction to 0.70 and clamped it at 42 deg, collapsing the run
        # into a short shuffle; the small backward kick was amplified instead.
        # The fitted legs are ~22% shorter than the Commando's, so keeping the
        # full forward swing restores the stride to the source proportion.
        # Bob clamps sit beyond the scaled deviation (max ~+-0.027) so the
        # source's smooth bounce is preserved instead of a square wave; the
        # deeper 0.45 scale adds a flight phase so the gait reads as a run
        # rather than a shuffle. The calf blend keeps a visible heel kick
        # without over-folding the fitted knee; the ankle limit grows with it
        # so the foot compensation completes.
        "thigh_blend": 0.45,
        "thigh_negative_scale": 1.05,
        "thigh_positive_scale": 1.0,
        "thigh_negative_limit_deg": 72.0,
        "thigh_positive_limit_deg": 30.0,
        "thigh_scale_transition_deg": 12.0,
        "thigh_smoothing_passes": 4,
        "calf_blend": 0.64,
        "foot_world_blend": 0.30,
        "foot_local_limit_deg": 38.0,
        "base_blend": 1.0,
        "time_scale": 0.90,
        "bob_scale": 0.45,
        "bob_min": -0.050,
        "bob_max": 0.050,
    },
    "Commando_RunBackward": {
        # Dedicated profile: the source backward run swings -68.7..-28 deg
        # (thigh delta up to 69 deg). Sharing the strafe default previously
        # kept only 43% of it, halving the stride. The wider foot range lets
        # the ankle fully compensate the retarget twist carried by the calf.
        # Bob clamps sit beyond the scaled deviation (max ~+-0.025).
        "thigh_blend": 0.95,
        "thigh_rotation_limit_deg": 72.0,
        "calf_blend": 0.52,
        "foot_world_blend": 0.30,
        "foot_local_limit_deg": 34.0,
        "base_blend": 1.0,
        "time_scale": 1.0,
        "bob_scale": 0.26,
        "bob_min": -0.032,
        "bob_max": 0.032,
    },
    "Commando_SprintForward": {
        # The source sprint drives the thigh -86.6 deg forward (left) with a
        # lunge-like leg offset and flexes the ankle up to ~47 deg; the
        # previous 0.34 blend kept only ~30 deg of thigh, so the sprint read
        # as a jog. The root lean is restored to the source's -32.5 deg; the
        # ankle limit matches the source's own flex range so the foot
        # compensation completes without clamping. Bob clamps sit beyond the
        # scaled deviation (max ~+-0.030).
        "thigh_blend": 0.90,
        "thigh_rotation_limit_deg": 84.0,
        "calf_blend": 0.50,
        "foot_world_blend": 0.30,
        "foot_local_limit_deg": 42.0,
        "base_blend": 1.0,
        "time_scale": 1.0,
        "bob_scale": 0.25,
        "bob_min": -0.038,
        "bob_max": 0.038,
    },
}
LIMB_RETARGET_CHAINS = (
    ("upper_arm.l", "lower_arm.l", "hand.l"),
    ("upper_arm.r", "lower_arm.r", "hand.r"),
    ("thigh.l", "calf.l", "foot.l", "toe.l"),
    ("thigh.r", "calf.r", "foot.r", "toe.r"),
)


def matrix_point(matrix: Matrix, value: tuple[float, ...]) -> Vec3:
    x, y, z = value[:3]
    w = 1.0
    result = [sum(matrix[row][column] * component for column, component in enumerate((x, y, z, w))) for row in range(4)]
    if abs(result[3]) > 1e-8:
        return (result[0] / result[3], result[1] / result[3], result[2] / result[3])
    return (result[0], result[1], result[2])


def column_major_matrix(values: tuple[float, ...]) -> Matrix:
    return [[float(values[column * 4 + row]) for column in range(4)] for row in range(4)]


def pack_matrices(matrices: list[Matrix]) -> bytes:
    values = [matrix[row][column] for matrix in matrices for column in range(4) for row in range(4)]
    return struct.pack(f"<{len(values)}f", *values)


def vector_length(value: tuple[float, ...]) -> float:
    return math.sqrt(sum(component * component for component in value[:3]))


def quat_slerp(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    amount: float,
) -> tuple[float, float, float, float]:
    first = quat_normalize(first)
    second = quat_normalize(second)
    dot = sum(a * b for a, b in zip(first, second))
    if dot < 0.0:
        second = tuple(-value for value in second)
        dot = -dot
    dot = max(-1.0, min(1.0, dot))
    if dot > 0.9995:
        return quat_normalize(tuple(a + (b - a) * amount for a, b in zip(first, second)))
    angle = math.acos(dot)
    denominator = math.sin(angle)
    first_scale = math.sin((1.0 - amount) * angle) / denominator
    second_scale = math.sin(amount * angle) / denominator
    return quat_normalize(tuple(a * first_scale + b * second_scale for a, b in zip(first, second)))


def local_x_rotation(degrees: float) -> Quat:
    half_angle = math.radians(degrees) * 0.5
    return (math.sin(half_angle), 0.0, 0.0, math.cos(half_angle))


def negative_x_hinge_rotation(reference: Quat, value: Quat, blend: float) -> Quat:
    relative = quat_normalize(quat_multiply(quat_inverse(reference), value))
    if relative[3] < 0.0:
        relative = tuple(-component for component in relative)
    signed_angle = math.degrees(2.0 * math.atan2(relative[0], relative[3]))
    return quat_normalize(quat_multiply(reference, local_x_rotation(min(0.0, signed_angle) * blend)))


def smooth_asymmetric_x_hinge_rotations(
    reference: Quat,
    values: list[tuple[float, ...]],
    negative_scale: float,
    positive_scale: float,
    negative_limit_deg: float,
    positive_limit_deg: float,
    scale_transition_deg: float,
    smoothing_passes: int,
) -> list[Quat]:
    signed_angles = []
    for value in values:
        relative = quat_normalize(quat_multiply(quat_inverse(reference), value))
        if relative[3] < 0.0:
            relative = tuple(-component for component in relative)
        signed_angles.append(math.degrees(2.0 * math.atan2(relative[0], relative[3])))

    # The source locomotion contains isolated one-frame thigh spikes. Smooth the
    # closed cycle before scaling it so the larger stride does not amplify them.
    unique_angles = signed_angles[:-1]
    for _ in range(smoothing_passes):
        unique_angles = [
            (
                unique_angles[(index - 1) % len(unique_angles)]
                + 2.0 * angle
                + unique_angles[(index + 1) % len(unique_angles)]
            )
            * 0.25
            for index, angle in enumerate(unique_angles)
        ]
    smoothed_angles = [*unique_angles, unique_angles[0]]

    result = []
    for signed_angle in smoothed_angles:
        scale_amount = smoothstep(
            (signed_angle + scale_transition_deg) / max(2.0 * scale_transition_deg, 1e-8)
        )
        scale = negative_scale + (positive_scale - negative_scale) * scale_amount
        target_angle = max(-negative_limit_deg, min(positive_limit_deg, signed_angle * scale))
        result.append(quat_normalize(quat_multiply(reference, local_x_rotation(target_angle))))
    return result


def pitch_only_world_rotation(reference: Quat, value: Quat, blend: float) -> Quat:
    relative = quat_normalize(quat_multiply(quat_inverse(reference), value))
    if relative[3] < 0.0:
        relative = tuple(-component for component in relative)
    signed_pitch = math.degrees(2.0 * math.atan2(relative[0], relative[3]))
    return quat_normalize(quat_multiply(reference, local_x_rotation(signed_pitch * blend)))


def ground_upper_body_blend(node_name: str, animation_name: str) -> float:
    if node_name == "base":
        return ground_locomotion_profile(animation_name)["base_blend"]
    if node_name == "pelvis":
        return 0.30
    if node_name.startswith(("finger", "thumb")):
        return 0.0
    if node_name.startswith(("clavicle.", "upper_arm.", "lower_arm.", "hand.", "gun.")):
        return 0.35
    if node_name in {"neck", "head"}:
        # The source Commando counter-rotates the head ~+33..+45 deg (local)
        # during locomotion to keep the gaze level under the -16 deg root lean
        # (-32.5 deg sprint). The fitted head is large and sits on a short
        # neck, so keep only 75% of that counter-rotation: the gaze drops
        # slightly (about -8..-10 deg world pitch) instead of the fully level
        # head that reads as a hooked neck on this silhouette.
        return 0.75
    if node_name in {"stomach", "chest"}:
        # Restore the source's dynamic chest lean (-17..-29 deg local) on top
        # of the root lean; the source itself keeps the chest pitched forward
        # through the whole run cycle.
        return 1.0
    return 0.40


def ground_locomotion_profile(animation_name: str) -> dict[str, float]:
    return GROUND_LOCOMOTION_PROFILES.get(animation_name, GROUND_LOCOMOTION_PROFILES["default"])


def rotation_delta_degrees(reference: Quat, value: Quat) -> float:
    relative = quat_normalize(quat_multiply(quat_inverse(reference), value))
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, abs(relative[3])))))


def limit_rotation_delta(reference: Quat, value: Quat, maximum_degrees: float) -> Quat:
    angle = rotation_delta_degrees(reference, value)
    if angle <= maximum_degrees:
        return quat_normalize(value)
    return quat_slerp(reference, value, maximum_degrees / max(angle, 1e-8))


def continuous_quaternions(values: list[Quat]) -> list[Quat]:
    result: list[Quat] = []
    for value in values:
        value = quat_normalize(value)
        if result and sum(first * second for first, second in zip(result[-1], value)) < 0.0:
            value = tuple(-component for component in value)
        result.append(value)
    return result


def smooth_closed_quaternions(values: list[Quat], passes: int) -> list[Quat]:
    """Smooth a closed rotation cycle with a sliding 1-2-1 average.

    The source locomotion carries one-frame spikes in the calf and the
    per-keyframe recomputed foot tracks inherit them; smoothing the closed
    cycle keeps the loop seamless while removing the visible jolts.
    """
    result = continuous_quaternions(values)
    for _ in range(passes):
        result = [
            quat_normalize(
                (
                    result[(index - 1) % len(result)][0] + 2.0 * result[index][0] + result[(index + 1) % len(result)][0],
                    result[(index - 1) % len(result)][1] + 2.0 * result[index][1] + result[(index + 1) % len(result)][1],
                    result[(index - 1) % len(result)][2] + 2.0 * result[index][2] + result[(index + 1) % len(result)][2],
                    result[(index - 1) % len(result)][3] + 2.0 * result[index][3] + result[(index + 1) % len(result)][3],
                )
            )
            for index in range(len(result))
        ]
    return result


def sample_track(
    times: list[float],
    values: list[tuple[float, ...]],
    time: float,
    rotation: bool,
) -> tuple[float, ...]:
    if time <= times[0]:
        return values[0]
    if time >= times[-1]:
        return values[-1]
    for index in range(len(times) - 1):
        if times[index] <= time <= times[index + 1]:
            amount = (time - times[index]) / max(times[index + 1] - times[index], 1e-8)
            if rotation:
                return quat_slerp(values[index], values[index + 1], amount)  # type: ignore[arg-type]
            return tuple(
                first + (second - first) * amount
                for first, second in zip(values[index], values[index + 1])
            )
    return values[-1]


def animated_world_rotation(
    node_index: int,
    time: float,
    tracks: dict[tuple[int, str], tuple[list[float], list[tuple[float, ...]]]],
    rest_rotations: list[Quat],
    parents: dict[int, int],
) -> Quat:
    chain = []
    current = node_index
    while True:
        chain.append(current)
        if current not in parents:
            break
        current = parents[current]
    world = (0.0, 0.0, 0.0, 1.0)
    for index in reversed(chain):
        track = tracks.get((index, "rotation"))
        local = rest_rotations[index] if track is None else sample_track(*track, time, rotation=True)
        world = quat_normalize(quat_multiply(world, local))  # type: ignore[arg-type]
    return world


def direct_weight(first: int, second: int | None = None, blend: float = 0.0):
    if second is None or blend <= 1e-6:
        return (first, 0, 0, 0), (1.0, 0.0, 0.0, 0.0)
    if blend >= 1.0 - 1e-6:
        return (second, 0, 0, 0), (1.0, 0.0, 0.0, 0.0)
    return (first, second, 0, 0), (1.0 - blend, blend, 0.0, 0.0)


def blend_weight_sets(first, second, amount: float):
    merged: dict[int, float] = {}
    for weight_set, scale_factor in ((first, 1.0 - amount), (second, amount)):
        for joint, weight in zip(*weight_set):
            if weight > 0.0:
                merged[joint] = merged.get(joint, 0.0) + float(weight) * scale_factor
    selected = sorted(merged.items(), key=lambda item: item[1], reverse=True)[:4]
    total = sum(weight for _, weight in selected)
    joints = [joint for joint, _ in selected]
    weights = [weight / total for _, weight in selected]
    while len(joints) < 4:
        joints.append(0)
        weights.append(0.0)
    return tuple(joints), tuple(weights)


def closest_chain_weight(
    point: Vec3,
    names: list[str],
    targets: dict[str, list[float]],
    joint_by_name: dict[str, int],
):
    best: tuple[float, int, float] | None = None
    for index, (start_name, end_name) in enumerate(zip(names, names[1:])):
        start = tuple(float(value) for value in targets[start_name])
        end = tuple(float(value) for value in targets[end_name])
        delta = tuple(end[axis] - start[axis] for axis in range(3))
        length_squared = sum(value * value for value in delta)
        offset = tuple(point[axis] - start[axis] for axis in range(3))
        amount = 0.0 if length_squared < 1e-8 else max(0.0, min(1.0, sum(offset[axis] * delta[axis] for axis in range(3)) / length_squared))
        closest = tuple(start[axis] + delta[axis] * amount for axis in range(3))
        distance_squared = sum((point[axis] - closest[axis]) ** 2 for axis in range(3))
        candidate = (distance_squared, index, amount)
        if best is None or candidate < best:
            best = candidate
    assert best is not None
    _, index, amount = best
    return direct_weight(joint_by_name[names[index]], joint_by_name[names[index + 1]], amount)


def smoothstep(amount: float) -> float:
    amount = max(0.0, min(1.0, amount))
    return amount * amount * (3.0 - 2.0 * amount)


def rigid_leg_weight(
    point: Vec3,
    side: str,
    targets: dict[str, list[float]],
    joint_by_name: dict[str, int],
):
    y = point[1]
    thigh_joint = joint_by_name[f"thigh.{side}"]
    calf_joint = joint_by_name[f"calf.{side}"]
    foot_joint = joint_by_name[f"foot.{side}"]
    pelvis_joint = joint_by_name["pelvis"]
    thigh_y = float(targets[f"thigh.{side}"][1])
    knee_y = float(targets[f"calf.{side}"][1])
    foot_y = float(targets[f"foot.{side}"][1])

    hip_transition_bottom = thigh_y - 0.16
    if y > hip_transition_bottom:
        pelvis_amount = smoothstep((y - hip_transition_bottom) / (thigh_y - hip_transition_bottom))
        return direct_weight(thigh_joint, pelvis_joint, pelvis_amount)

    knee_half_width = 0.075
    if y >= knee_y + knee_half_width:
        return direct_weight(thigh_joint)
    if y > knee_y - knee_half_width:
        calf_amount = smoothstep((knee_y + knee_half_width - y) / (2.0 * knee_half_width))
        return direct_weight(thigh_joint, calf_joint, calf_amount)

    shoe_top = foot_y + 0.075
    ankle_transition_top = foot_y + 0.115
    if y <= shoe_top:
        return direct_weight(foot_joint)
    if y < ankle_transition_top:
        calf_amount = smoothstep((y - shoe_top) / (ankle_transition_top - shoe_top))
        return direct_weight(foot_joint, calf_joint, calf_amount)
    return direct_weight(calf_joint)


def mesh_component_categories(mesh: dict) -> list[str]:
    positions: list[Vec3] = mesh["positions"]
    indices: list[int] = mesh["indices"]
    epsilon = 1e-5
    canonical_by_key: dict[tuple[int, int, int], int] = {}
    canonical_positions: list[Vec3] = []
    vertex_to_canonical: list[int] = []
    for point in positions:
        key = tuple(round(component / epsilon) for component in point)
        canonical = canonical_by_key.get(key)
        if canonical is None:
            canonical = len(canonical_positions)
            canonical_by_key[key] = canonical
            canonical_positions.append(point)
        vertex_to_canonical.append(canonical)

    parents = list(range(len(canonical_positions)))
    sizes = [1] * len(canonical_positions)

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(first: int, second: int) -> None:
        first = find(first)
        second = find(second)
        if first == second:
            return
        if sizes[first] < sizes[second]:
            first, second = second, first
        parents[second] = first
        sizes[first] += sizes[second]

    for offset in range(0, len(indices), 3):
        triangle = [vertex_to_canonical[index] for index in indices[offset : offset + 3]]
        union(triangle[0], triangle[1])
        union(triangle[1], triangle[2])

    bounds: dict[int, tuple[list[float], list[float]]] = {}
    for index, point in enumerate(canonical_positions):
        root = find(index)
        minimum, maximum = bounds.setdefault(root, ([math.inf] * 3, [-math.inf] * 3))
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])

    category_by_root = {}
    for root, (minimum, maximum) in bounds.items():
        if maximum[1] < 0.32:
            category = "shoe_l" if maximum[0] < 0.02 else "shoe_r"
        elif minimum[1] > 1.22 and maximum[1] < 1.46 and maximum[0] < -0.57:
            category = "hand_l"
        elif minimum[1] > 1.22 and maximum[1] < 1.46 and minimum[0] > 0.57:
            category = "hand_r"
        elif minimum[1] < 0.30 and maximum[1] < 0.98 and maximum[0] < 0.02:
            category = "leg_l"
        elif minimum[1] < 0.30 and maximum[1] < 0.98 and minimum[0] > -0.02:
            category = "leg_r"
        elif minimum[1] > 0.82 and maximum[1] < 1.18 and minimum[0] < 0.0 < maximum[0]:
            category = "lower_body"
        else:
            category = "other"
        category_by_root[root] = category

    categories = [category_by_root[find(canonical)] for canonical in vertex_to_canonical]
    if sum(category.startswith("leg_") for category in categories) < 1000:
        categories = [
            (
                "shoe_l"
                if point[1] < 0.30 and point[0] < 0.0
                else "shoe_r"
                if point[1] < 0.30
                else "leg_l"
                if point[1] < 0.84 and point[0] < -0.02
                else "leg_r"
                if point[1] < 0.84 and point[0] > 0.02
                else category
            )
            for point, category in zip(positions, categories)
        ]
    counts = {category: categories.count(category) for category in sorted(set(categories))}
    print(f"Mesh component categories: {counts}")
    return categories


def recompute_vertex_normals(mesh: dict) -> None:
    positions: list[Vec3] = mesh["positions"]
    indices: list[int] = mesh["indices"]
    accumulated = [[0.0, 0.0, 0.0] for _ in positions]
    for offset in range(0, len(indices), 3):
        first, second, third = indices[offset : offset + 3]
        a, b, c = positions[first], positions[second], positions[third]
        ab = tuple(b[axis] - a[axis] for axis in range(3))
        ac = tuple(c[axis] - a[axis] for axis in range(3))
        normal = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        for index in (first, second, third):
            for axis in range(3):
                accumulated[index][axis] += normal[axis]
    mesh["normals"] = [
        tuple(component / max(vector_length(normal), 1e-8) for component in normal)
        for normal in accumulated
    ]


def close_rigid_hands(
    mesh: dict,
    categories: list[str],
    targets: dict[str, list[float]],
) -> None:
    positions: list[Vec3] = mesh["positions"]
    closed_positions = list(positions)
    changed = 0
    for index, (point, category) in enumerate(zip(positions, categories)):
        x, y, z = point
        side = "l" if x < 0.0 else "r"
        wrist = tuple(float(value) for value in targets[f"hand.{side}"])
        direction = -1.0 if side == "l" else 1.0
        outward = direction * (x - wrist[0])
        geometric_fallback = (
            outward > -0.055
            and 1.245 < y < 1.415
            and 0.015 < z < 0.18
            and abs(x) > 0.57
        )
        if category != f"hand_{side}" and not geometric_fallback:
            continue

        wrist_blend = smoothstep((outward + 0.025) / 0.075)
        finger_amount = smoothstep((outward - 0.035) / 0.11)
        compact_outward = 0.035 + (outward - 0.035) * 0.38
        target_outward = outward + (compact_outward - outward) * finger_amount

        center_y = wrist[1] + 0.005
        center_z = wrist[2]
        vertical_scale = 0.88 - 0.25 * finger_amount
        depth_scale = 0.90 - 0.20 * finger_amount
        target = (
            wrist[0] + direction * target_outward,
            center_y + (y - center_y) * vertical_scale - 0.012 * finger_amount,
            center_z + (z - center_z) * depth_scale + 0.008 * finger_amount,
        )
        closed_positions[index] = tuple(
            point[axis] + (target[axis] - point[axis]) * wrist_blend
            for axis in range(3)
        )
        changed += 1

    mesh["positions"] = closed_positions
    recompute_vertex_normals(mesh)
    print(f"Closed rigid hand geometry: {changed} vertices")


def surface_limb_blends(mesh: dict) -> list[tuple[float, float]]:
    positions: list[Vec3] = mesh["positions"]
    indices: list[int] = mesh["indices"]
    epsilon = 1e-5
    canonical_by_key: dict[tuple[int, int, int], int] = {}
    canonical_positions: list[Vec3] = []
    vertex_to_canonical: list[int] = []
    for point in positions:
        key = tuple(round(component / epsilon) for component in point)
        canonical = canonical_by_key.get(key)
        if canonical is None:
            canonical = len(canonical_positions)
            canonical_by_key[key] = canonical
            canonical_positions.append(point)
        vertex_to_canonical.append(canonical)

    adjacency = [set() for _ in canonical_positions]
    for offset in range(0, len(indices), 3):
        triangle = [vertex_to_canonical[index] for index in indices[offset : offset + 3]]
        for start, end in zip(triangle, triangle[1:] + triangle[:1]):
            if start != end:
                adjacency[start].add(end)
                adjacency[end].add(start)

    seeds: dict[str, list[int]] = {
        "left_arm": [],
        "right_arm": [],
        "left_leg": [],
        "right_leg": [],
        "torso": [],
    }
    for index, (x, y, _z) in enumerate(canonical_positions):
        if 0.72 < y < 1.5 and x < -0.285:
            seeds["left_arm"].append(index)
        elif 0.72 < y < 1.5 and x > 0.285:
            seeds["right_arm"].append(index)
        if y < 0.68 and x < -0.035:
            seeds["left_leg"].append(index)
        elif y < 0.68 and x > 0.035:
            seeds["right_leg"].append(index)
        if 0.84 < y < 1.5 and abs(x) < 0.12:
            seeds["torso"].append(index)

    def distances_from(source_indices: list[int]) -> list[float]:
        distances = [math.inf] * len(canonical_positions)
        pending: list[tuple[float, int]] = []
        for index in source_indices:
            distances[index] = 0.0
            heapq.heappush(pending, (0.0, index))
        while pending:
            distance, current = heapq.heappop(pending)
            if distance > distances[current] + 1e-9:
                continue
            start = canonical_positions[current]
            for neighbor in adjacency[current]:
                end = canonical_positions[neighbor]
                candidate = distance + math.sqrt(sum((end[axis] - start[axis]) ** 2 for axis in range(3)))
                if candidate + 1e-9 < distances[neighbor]:
                    distances[neighbor] = candidate
                    heapq.heappush(pending, (candidate, neighbor))
        return distances

    distance_fields = {label: distances_from(indices) for label, indices in seeds.items()}
    arm_transition_width = 0.25
    leg_transition_width = 0.12
    canonical_blends = []
    for index, (x, y, _z) in enumerate(canonical_positions):
        torso_distance = distance_fields["torso"][index]
        arm_amount = 0.0
        if 0.72 < y < 1.5:
            arm_label = "left_arm" if x < 0.0 else "right_arm"
            arm_distance = distance_fields[arm_label][index]
            if math.isfinite(arm_distance):
                if not math.isfinite(torso_distance):
                    arm_amount = 1.0
                else:
                    arm_amount = max(
                        0.0,
                        min(
                            1.0,
                            (torso_distance - arm_distance + arm_transition_width) / (2.0 * arm_transition_width),
                        ),
                    )
                    arm_amount = arm_amount * arm_amount * (3.0 - 2.0 * arm_amount)

        leg_amount = 0.0
        if y < 1.02:
            leg_label = "left_leg" if x < 0.0 else "right_leg"
            leg_distance = distance_fields[leg_label][index]
            if math.isfinite(leg_distance):
                if not math.isfinite(torso_distance):
                    leg_amount = 1.0
                else:
                    leg_amount = max(
                        0.0,
                        min(
                            1.0,
                            (torso_distance - leg_distance + leg_transition_width) / (2.0 * leg_transition_width),
                        ),
                    )
                    leg_amount = leg_amount * leg_amount * (3.0 - 2.0 * leg_amount)
        canonical_blends.append((arm_amount, leg_amount))

    result = [canonical_blends[canonical] for canonical in vertex_to_canonical]
    counts = {
        "arm": sum(arm >= 0.9 for arm, _leg in result),
        "arm_transition": sum(0.1 < arm < 0.9 for arm, _leg in result),
        "leg": sum(leg >= 0.9 for _arm, leg in result),
        "leg_transition": sum(0.1 < leg < 0.9 for _arm, leg in result),
    }
    print(f"Surface limb blends: {counts}")
    return result


def stabilized_character_weight(
    point: Vec3,
    transferred,
    limb_blends: tuple[float, float],
    targets: dict[str, list[float]],
    joint_by_name: dict[str, int],
    joint_names: list[str],
    component_category: str,
):
    x, y, z = point
    arm_blend, leg_blend = limb_blends
    side = "l" if x < 0.0 else "r"
    hand_name = f"hand.{side}"
    hand_target = tuple(float(value) for value in targets[hand_name])
    hand_distance = math.sqrt(sum((point[axis] - hand_target[axis]) ** 2 for axis in range(3)))
    hand_amount = max(0.0, min(1.0, (0.2 - hand_distance) / 0.09))
    hand_amount = hand_amount * hand_amount * (3.0 - 2.0 * hand_amount)
    if y > 1.47:
        return direct_weight(joint_by_name["head"])
    torso_weight = closest_chain_weight(
        point,
        ["pelvis", "stomach", "chest"],
        targets,
        joint_by_name,
    )
    if component_category == "lower_body":
        return torso_weight
    if component_category.startswith("hand_"):
        component_side = component_category[-1]
        return direct_weight(joint_by_name[f"hand.{component_side}"])
    if component_category.startswith("shoe_"):
        component_side = component_category[-1]
        return direct_weight(joint_by_name[f"foot.{component_side}"])
    if component_category.startswith("leg_"):
        component_side = component_category[-1]
        return rigid_leg_weight(point, component_side, targets, joint_by_name)
    upper_weight = torso_weight
    if arm_blend > 1e-6 and 0.72 < y < 1.5:
        if abs(x) > 0.62 and 1.22 < y < 1.46:
            return direct_weight(joint_by_name[f"hand.{side}"])
        arm_weight = closest_chain_weight(
            point,
            [f"upper_arm.{side}", f"lower_arm.{side}", f"hand.{side}"],
            targets,
            joint_by_name,
        )
        upper_weight = blend_weight_sets(torso_weight, arm_weight, arm_blend)
    body_weight = None
    if leg_blend > 1e-6 and y < 1.02:
        leg_weight = rigid_leg_weight(
            point,
            side,
            targets,
            joint_by_name,
        )
        body_weight = blend_weight_sets(upper_weight, leg_weight, leg_blend)
    elif 0.72 < y < 1.5:
        body_weight = upper_weight
    if hand_amount > 1e-6:
        if body_weight is None:
            body_weight = direct_weight(joint_by_name[f"lower_arm.{side}"])
        return blend_weight_sets(body_weight, direct_weight(joint_by_name[hand_name]), hand_amount)
    if body_weight is not None:
        return body_weight

    merged: dict[int, float] = {}
    hand_slots = {"l": joint_by_name["hand.l"], "r": joint_by_name["hand.r"]}
    for joint, weight in zip(*transferred):
        if weight <= 0.0:
            continue
        name = joint_names[joint]
        mapped = joint
        if name.startswith(("finger", "thumb", "gun.")):
            mapped = hand_slots["l" if name.endswith(".l") else "r"]
        merged[mapped] = merged.get(mapped, 0.0) + float(weight)
    selected = sorted(merged.items(), key=lambda item: item[1], reverse=True)[:4]
    total = sum(weight for _, weight in selected)
    joints = [joint for joint, _ in selected]
    weights = [weight / total for _, weight in selected]
    while len(joints) < 4:
        joints.append(0)
        weights.append(0.0)
    return tuple(joints), tuple(weights)


def transformed_hunyuan_mesh(
    document: dict,
    binary: bytearray,
    target_height: float,
    depth_offset: float,
    body_yaw_deg: float,
) -> dict:
    mesh_nodes = [node for node in document.get("nodes", []) if "mesh" in node]
    if len(mesh_nodes) != 1:
        raise ValueError(f"Expected one Hunyuan mesh node, found {len(mesh_nodes)}")
    primitives = document["meshes"][mesh_nodes[0]["mesh"]].get("primitives", [])
    if len(primitives) != 1:
        raise ValueError(f"Expected one Hunyuan primitive, found {len(primitives)}")
    primitive = primitives[0]
    attributes = primitive["attributes"]
    required = {"POSITION", "NORMAL", "TEXCOORD_0"}
    if not required.issubset(attributes) or "indices" not in primitive:
        raise ValueError(f"Hunyuan primitive is missing {sorted(required - set(attributes))} or indices")

    raw_positions = read_accessor(document, binary, attributes["POSITION"])
    raw_normals = read_accessor(document, binary, attributes["NORMAL"])
    texcoords = read_accessor(document, binary, attributes["TEXCOORD_0"])
    indices = [int(row[0]) for row in read_accessor(document, binary, primitive["indices"])]
    if not (len(raw_positions) == len(raw_normals) == len(texcoords)):
        raise ValueError("Hunyuan vertex attribute counts differ")

    minimum_z = min(point[2] for point in raw_positions)
    maximum_z = max(point[2] for point in raw_positions)
    if maximum_z - minimum_z < 1e-5:
        raise ValueError("Hunyuan mesh has no usable height")
    scale = target_height / (maximum_z - minimum_z)
    center_x = (min(point[0] for point in raw_positions) + max(point[0] for point in raw_positions)) * 0.5
    center_depth = (min(point[1] for point in raw_positions) + max(point[1] for point in raw_positions)) * 0.5
    yaw = math.radians(body_yaw_deg)
    cosine, sine = math.cos(yaw), math.sin(yaw)
    positions = []
    for point in raw_positions:
        local_x = (point[0] - center_x) * scale
        local_z = (point[1] - center_depth) * scale
        positions.append(
            (
                cosine * local_x + sine * local_z,
                (maximum_z - point[2]) * scale,
                -sine * local_x + cosine * local_z + depth_offset,
            )
        )
    normals = []
    for normal in raw_normals:
        local_x, local_y, local_z = float(normal[0]), -float(normal[2]), float(normal[1])
        transformed = (
            cosine * local_x + sine * local_z,
            local_y,
            -sine * local_x + cosine * local_z,
        )
        length = max(vector_length(transformed), 1e-8)
        normals.append(tuple(component / length for component in transformed))
    return {
        "positions": positions,
        "normals": normals,
        "texcoords": [tuple(float(value) for value in row) for row in texcoords],
        "indices": indices,
        "material": primitive.get("material", 0),
        "scale": scale,
    }


def fit_skeleton(document: dict, targets: dict[str, list[float]]) -> tuple[dict[int, tuple[float, float, float]], dict[int, tuple[float, float, float]]]:
    nodes = document["nodes"]
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    parents: dict[int, int] = {}
    for parent, node in enumerate(nodes):
        for child in node.get("children", []):
            parents[child] = parent
    old_translations = {
        node_by_name[name]: tuple(float(value) for value in nodes[node_by_name[name]].get("translation", [0.0, 0.0, 0.0]))
        for name in targets
    }

    pending = set(targets)
    while pending:
        progress = False
        for name in list(pending):
            index = node_by_name.get(name)
            if index is None:
                raise ValueError(f"Rig profile references missing joint {name}")
            parent = parents.get(index)
            parent_name = nodes[parent].get("name") if parent is not None else None
            if parent_name in pending:
                continue
            target = tuple(float(value) for value in targets[name])
            if parent is None:
                local = target
            else:
                parent_inverse = matrix_inverse(world_matrices(document)[parent])
                local = matrix_point(parent_inverse, target)
            nodes[index]["translation"] = list(local)
            pending.remove(name)
            progress = True
        if not progress:
            raise ValueError(f"Unable to resolve rig profile order: {sorted(pending)}")

    new_translations = {
        index: tuple(float(value) for value in nodes[index].get("translation", [0.0, 0.0, 0.0]))
        for index in old_translations
    }
    return old_translations, new_translations


def retarget_animation_translations(
    document: dict,
    binary: bytearray,
    old_translations: dict[int, tuple[float, float, float]],
    new_translations: dict[int, tuple[float, float, float]],
) -> None:
    for animation in document.get("animations", []):
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            node_index = target.get("node")
            if target.get("path") != "translation" or node_index not in old_translations:
                continue
            sampler = animation["samplers"][channel["sampler"]]
            values = read_accessor(document, binary, sampler["output"])
            old_rest = old_translations[node_index]
            new_rest = new_translations[node_index]
            old_length = vector_length(old_rest)
            new_length = vector_length(new_rest)
            ratio = new_length / old_length if old_length > 1e-6 else 1.0
            converted = [
                tuple(new_rest[axis] + (float(row[axis]) - old_rest[axis]) * ratio for axis in range(3))
                for row in values
            ]
            payload = struct.pack(f"<{len(converted) * 3}f", *(value for row in converted for value in row))
            sampler["output"] = append_accessor(document, binary, payload, 5126, "VEC3", len(converted))


def retarget_animation_rotations(
    document: dict,
    binary: bytearray,
    old_translations: dict[int, tuple[float, float, float]],
    new_translations: dict[int, tuple[float, float, float]],
) -> None:
    node_by_name = {node.get("name"): index for index, node in enumerate(document["nodes"])}
    corrections: dict[int, tuple[float, float, float, float]] = {}
    parents: dict[int, int] = {}
    for parent, node in enumerate(document["nodes"]):
        for child in node.get("children", []):
            parents[child] = parent

    for chain in LIMB_RETARGET_CHAINS:
        for joint_name, child_name in zip(chain, chain[1:]):
            joint_index = node_by_name[joint_name]
            child_index = node_by_name[child_name]
            old_direction = old_translations[child_index]
            new_direction = new_translations[child_index]
            corrections[joint_index] = quat_from_to(new_direction, old_direction)
        corrections[node_by_name[chain[-1]]] = (0.0, 0.0, 0.0, 1.0)

    identity = (0.0, 0.0, 0.0, 1.0)
    for animation in document.get("animations", []):
        if animation.get("name") in PROCEDURAL_ANIMATIONS:
            continue
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            node_index = target.get("node")
            if target.get("path") != "rotation" or node_index not in corrections:
                continue
            sampler = animation["samplers"][channel["sampler"]]
            if sampler.get("interpolation", "LINEAR") != "LINEAR":
                raise ValueError("Limb rotation retargeting currently requires LINEAR interpolation")
            parent_correction = corrections.get(parents.get(node_index), identity)
            correction = corrections[node_index]
            values = read_accessor(document, binary, sampler["output"])
            converted = [
                quat_normalize(
                    quat_multiply(
                        quat_multiply(quat_inverse(parent_correction), tuple(float(value) for value in row)),
                        correction,
                    )
                )
                for row in values
            ]
            payload = struct.pack(f"<{len(converted) * 4}f", *(value for row in converted for value in row))
            sampler["output"] = append_accessor(document, binary, payload, 5126, "VEC4", len(converted))


def stabilize_lower_body_animations(document: dict, binary: bytearray) -> None:
    nodes = document["nodes"]
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    parents = {child: parent for parent, node in enumerate(nodes) for child in node.get("children", [])}
    rest_rotations = [matrix_quaternion(local_matrix(node)) for node in nodes]
    rest_world_rotations = [matrix_quaternion(matrix) for matrix in world_matrices(document)]
    lower_body_indices = {node_by_name[name] for name in LOWER_BODY_NODES}
    base_index = node_by_name["base"]
    idle_animation = next(animation for animation in document.get("animations", []) if animation.get("name") == "Commando_Idle")
    idle_values = {}
    for channel in idle_animation.get("channels", []):
        target = channel.get("target", {})
        sampler = idle_animation["samplers"][channel["sampler"]]
        values = read_accessor(document, binary, sampler["output"])
        idle_values[(target.get("node"), target.get("path"))] = tuple(float(value) for value in values[0])

    for animation in document.get("animations", []):
        name = animation.get("name")
        if name in RIGID_LOWER_BODY_ANIMATIONS:
            animation["channels"] = [
                channel
                for channel in animation.get("channels", [])
                if channel.get("target", {}).get("node") not in lower_body_indices
                and not (
                    name in ROOT_HEIGHT_LOCKED_ANIMATIONS
                    and channel.get("target", {}).get("node") == base_index
                    and channel.get("target", {}).get("path") == "translation"
                )
            ]
            continue
        if name not in GROUND_LOCOMOTION_ANIMATIONS:
            continue

        locomotion_profile = ground_locomotion_profile(name)
        animation.setdefault("extras", {}).update(
            {
                "motionRetarget": "scaled-commando-locomotion-v2",
                "sourceMotion": "original-commando-lower-body",
                "kneeBendAxis": "negative-local-x",
                "thighMotionBlend": locomotion_profile["thigh_blend"],
                "calfMotionBlend": locomotion_profile["calf_blend"],
                "baseMotionBlend": locomotion_profile["base_blend"],
                "durationScale": locomotion_profile["time_scale"],
                "footWorldMotionBlend": locomotion_profile["foot_world_blend"],
                "footWorldMotionMode": "rest-aligned-pitch-only",
                "footLocalRotationLimitDeg": locomotion_profile["foot_local_limit_deg"],
            }
        )
        if "thigh_negative_scale" in locomotion_profile:
            animation["extras"].update(
                {
                    "motionRetarget": "scaled-commando-locomotion-v4",
                    "forwardStrideNegativeLimitDeg": locomotion_profile["thigh_negative_limit_deg"],
                    "forwardStridePositiveLimitDeg": locomotion_profile["thigh_positive_limit_deg"],
                    "forwardStrideScaleTransitionDeg": locomotion_profile["thigh_scale_transition_deg"],
                    "forwardStrideSmoothingPasses": locomotion_profile["thigh_smoothing_passes"],
                }
            )

        source_tracks: dict[tuple[int, str], tuple[list[float], list[tuple[float, ...]]]] = {}
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            node_index = target.get("node")
            path = target.get("path")
            sampler = animation["samplers"][channel["sampler"]]
            if sampler.get("interpolation", "LINEAR") != "LINEAR":
                raise ValueError("Ground locomotion stabilization requires LINEAR interpolation")
            source_tracks[(node_index, path)] = (
                [float(row[0]) for row in read_accessor(document, binary, sampler["input"])],
                [tuple(float(value) for value in row) for row in read_accessor(document, binary, sampler["output"])],
            )

        converted_tracks: dict[tuple[int, str], tuple[list[float], list[tuple[float, ...]]]] = {}
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            node_index = target.get("node")
            path = target.get("path")
            node_name = nodes[node_index].get("name", "")
            times, values = source_tracks[(node_index, path)]
            if node_index not in lower_body_indices:
                reference = idle_values.get((node_index, path))
                if reference is None:
                    raise ValueError(f"Idle animation is missing {node_name}.{path}")
                if node_index == base_index and path == "translation":
                    reference = tuple(float(value) for value in nodes[node_index].get("translation", [0.0, 0.0, 0.0]))
                    mean_height = sum(float(row[1]) for row in values) / len(values)
                    converted = [
                        (
                            reference[0],
                            reference[1]
                            + max(
                                locomotion_profile["bob_min"],
                                min(
                                    locomotion_profile["bob_max"],
                                    (float(row[1]) - mean_height) * locomotion_profile["bob_scale"],
                                ),
                            ),
                            reference[2],
                        )
                        for row in values
                    ]
                elif path == "rotation":
                    blend = ground_upper_body_blend(node_name, name)
                    converted = [
                        quat_slerp(reference, tuple(float(value) for value in row), blend)
                        for row in values
                    ]
                elif path == "scale":
                    converted = [reference] * len(times)
                else:
                    blend = ground_upper_body_blend(node_name, name)
                    converted = [
                        tuple(expected + (float(value) - expected) * blend for value, expected in zip(row, reference))
                        for row in values
                    ]
                converted_tracks[(node_index, path)] = (times, converted)
                continue
            if node_name == "pelvis" and path == "rotation":
                reference = idle_values[(node_index, path)]
                converted = [
                    quat_slerp(reference, tuple(float(value) for value in row), ground_upper_body_blend(node_name, name))
                    for row in values
                ]
                converted_tracks[(node_index, path)] = (times, continuous_quaternions(converted))
                continue
            if path != "rotation" or node_name not in GROUND_GAIT_NODES:
                continue
            if node_name.startswith("foot."):
                continue
            if node_name.startswith("thigh."):
                if "thigh_negative_scale" in locomotion_profile:
                    converted = smooth_asymmetric_x_hinge_rotations(
                        rest_rotations[node_index],
                        values,
                        locomotion_profile["thigh_negative_scale"],
                        locomotion_profile["thigh_positive_scale"],
                        locomotion_profile["thigh_negative_limit_deg"],
                        locomotion_profile["thigh_positive_limit_deg"],
                        locomotion_profile["thigh_scale_transition_deg"],
                        int(locomotion_profile["thigh_smoothing_passes"]),
                    )
                else:
                    converted = [
                        limit_rotation_delta(
                            rest_rotations[node_index],
                            quat_slerp(
                                rest_rotations[node_index],
                                row,
                                locomotion_profile["thigh_blend"],
                            ),
                            locomotion_profile["thigh_rotation_limit_deg"],
                        )
                        for row in values
                    ]
            else:
                converted = [
                    negative_x_hinge_rotation(rest_rotations[node_index], row, locomotion_profile["calf_blend"])
                    for row in values
                ]
            converted_tracks[(node_index, path)] = (times, smooth_closed_quaternions(converted, 2))

        for side in ("l", "r"):
            foot_index = node_by_name[f"foot.{side}"]
            foot_key = (foot_index, "rotation")
            if foot_key not in source_tracks:
                raise ValueError(f"{name} is missing {nodes[foot_index].get('name')}.rotation")
            times, _values = source_tracks[foot_key]
            parent_index = parents[foot_index]
            converted = []
            for time in times:
                parent_world = animated_world_rotation(
                    parent_index,
                    time,
                    converted_tracks,
                    rest_rotations,
                    parents,
                )
                original_foot_world = animated_world_rotation(
                    foot_index,
                    time,
                    source_tracks,
                    rest_rotations,
                    parents,
                )
                desired_foot_world = pitch_only_world_rotation(
                    rest_world_rotations[foot_index],
                    original_foot_world,
                    locomotion_profile["foot_world_blend"],
                )
                foot_local = quat_normalize(quat_multiply(quat_inverse(parent_world), desired_foot_world))
                converted.append(
                    limit_rotation_delta(
                        rest_rotations[foot_index],
                        foot_local,
                        locomotion_profile["foot_local_limit_deg"],
                    )
                )
            # Each local value is solved from a pitch-only world target. A
            # second quaternion smoothing pass in local space reintroduces
            # yaw/roll as the calf parent changes, which reads as toe-out.
            converted_tracks[foot_key] = (times, continuous_quaternions(converted))

        stabilized_channels = []
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            key = (target.get("node"), target.get("path"))
            if key not in converted_tracks:
                continue
            times, converted = converted_tracks[key]
            width = len(converted[0])
            payload = struct.pack(f"<{len(converted) * width}f", *(value for row in converted for value in row))
            output_type = "VEC4" if width == 4 else "VEC3"
            sampler = animation["samplers"][channel["sampler"]]
            sampler["output"] = append_accessor(document, binary, payload, 5126, output_type, len(converted))
            stabilized_channels.append(channel)
        animation["channels"] = stabilized_channels


def retime_ground_locomotion_animations(document: dict, binary: bytearray) -> None:
    for animation in document.get("animations", []):
        name = animation.get("name")
        if name not in GROUND_LOCOMOTION_ANIMATIONS:
            continue
        time_scale = ground_locomotion_profile(name)["time_scale"]
        if abs(time_scale - 1.0) <= 1e-8:
            continue
        remapped: dict[int, int] = {}
        for sampler in animation.get("samplers", []):
            source_accessor = sampler["input"]
            target_accessor = remapped.get(source_accessor)
            if target_accessor is None:
                times = [float(row[0]) * time_scale for row in read_accessor(document, binary, source_accessor)]
                payload = struct.pack(f"<{len(times)}f", *times)
                target_accessor = append_accessor(document, binary, payload, 5126, "SCALAR", len(times))
                document["accessors"][target_accessor]["min"] = [min(times)]
                document["accessors"][target_accessor]["max"] = [max(times)]
                remapped[source_accessor] = target_accessor
            sampler["input"] = target_accessor


def warp_weight_samples(
    points: list[Vec3],
    weights: list[tuple[tuple[int, int, int, int], tuple[float, float, float, float]]],
    old_inverse_binds: list[Matrix],
    new_joint_world: list[Matrix],
) -> list[Vec3]:
    warped = []
    joint_matrices = [matrix_multiply(new_joint_world[index], old_inverse_binds[index]) for index in range(len(new_joint_world))]
    for point, (joints, joint_weights) in zip(points, weights):
        result = [0.0, 0.0, 0.0]
        for joint, weight in zip(joints, joint_weights):
            if weight <= 0.0:
                continue
            transformed = matrix_point(joint_matrices[joint], point)
            for axis in range(3):
                result[axis] += transformed[axis] * weight
        warped.append(tuple(result))
    return warped


def import_hunyuan_materials(document: dict, binary: bytearray, source_document: dict, source_binary: bytearray) -> int:
    sampler_base = len(document.setdefault("samplers", []))
    document["samplers"].extend(deepcopy(source_document.get("samplers", [])))

    image_base = len(document.setdefault("images", []))
    for source_image in source_document.get("images", []):
        image = deepcopy(source_image)
        if "bufferView" in image:
            view = source_document["bufferViews"][image["bufferView"]]
            start = view.get("byteOffset", 0)
            data = bytes(source_binary[start : start + view["byteLength"]])
            image["bufferView"] = append_buffer_view(document, binary, data)
        document["images"].append(image)

    texture_base = len(document.setdefault("textures", []))
    for source_texture in source_document.get("textures", []):
        texture = deepcopy(source_texture)
        if "sampler" in texture:
            texture["sampler"] += sampler_base
        if "source" in texture:
            texture["source"] += image_base
        document["textures"].append(texture)

    def remap_texture_info(value: dict | None) -> None:
        if isinstance(value, dict) and "index" in value:
            value["index"] += texture_base

    material_base = len(document.setdefault("materials", []))
    for source_material in source_document.get("materials", []):
        material = deepcopy(source_material)
        pbr = material.get("pbrMetallicRoughness", {})
        remap_texture_info(pbr.get("baseColorTexture"))
        remap_texture_info(pbr.get("metallicRoughnessTexture"))
        for key in ("normalTexture", "occlusionTexture", "emissiveTexture"):
            remap_texture_info(material.get(key))
        document["materials"].append(material)

    extensions = list(document.get("extensionsUsed", []))
    for extension in source_document.get("extensionsUsed", []):
        if extension not in extensions:
            extensions.append(extension)
    if extensions:
        document["extensionsUsed"] = extensions
    return material_base


def append_body_mesh(
    document: dict,
    binary: bytearray,
    mesh: dict,
    transferred: list[tuple[tuple[int, int, int, int], tuple[float, float, float, float]]],
    material_index: int,
) -> None:
    positions = mesh["positions"]
    normals = mesh["normals"]
    texcoords = mesh["texcoords"]
    indices = mesh["indices"]
    position_bytes = struct.pack(f"<{len(positions) * 3}f", *(value for row in positions for value in row))
    normal_bytes = struct.pack(f"<{len(normals) * 3}f", *(value for row in normals for value in row))
    texcoord_bytes = struct.pack(f"<{len(texcoords) * 2}f", *(value for row in texcoords for value in row))
    joint_bytes = struct.pack(f"<{len(transferred) * 4}B", *(value for row, _ in transferred for value in row))
    weight_bytes = struct.pack(f"<{len(transferred) * 4}f", *(value for _, row in transferred for value in row))
    index_component = 5123 if max(indices) < 65536 else 5125
    index_format = "H" if index_component == 5123 else "I"
    index_bytes = struct.pack(f"<{len(indices)}{index_format}", *indices)

    position_accessor = append_accessor(document, binary, position_bytes, 5126, "VEC3", len(positions), target=34962)
    document["accessors"][position_accessor]["min"] = [min(row[axis] for row in positions) for axis in range(3)]
    document["accessors"][position_accessor]["max"] = [max(row[axis] for row in positions) for axis in range(3)]
    normal_accessor = append_accessor(document, binary, normal_bytes, 5126, "VEC3", len(normals), target=34962)
    texcoord_accessor = append_accessor(document, binary, texcoord_bytes, 5126, "VEC2", len(texcoords), target=34962)
    joint_accessor = append_accessor(document, binary, joint_bytes, 5121, "VEC4", len(transferred), target=34962)
    weight_accessor = append_accessor(document, binary, weight_bytes, 5126, "VEC4", len(transferred), target=34962)
    index_accessor = append_accessor(document, binary, index_bytes, index_component, "SCALAR", len(indices), target=34963)
    document["meshes"][0] = {
        "name": "GeneratedCharacterMesh",
        "primitives": [
            {
                "attributes": {
                    "POSITION": position_accessor,
                    "NORMAL": normal_accessor,
                    "TEXCOORD_0": texcoord_accessor,
                    "JOINTS_0": joint_accessor,
                    "WEIGHTS_0": weight_accessor,
                },
                "indices": index_accessor,
                "material": material_index,
            }
        ],
    }


def update_inverse_bind_matrices(document: dict, binary: bytearray, body_node_index: int) -> None:
    world = world_matrices(document)
    mesh_world = world[body_node_index]
    matrices = [matrix_multiply(matrix_inverse(world[index]), mesh_world) for index in document["skins"][0]["joints"]]
    accessor = append_accessor(document, binary, pack_matrices(matrices), 5126, "MAT4", len(matrices))
    document["skins"][0]["inverseBindMatrices"] = accessor


def build_rigged_model(source: Path, template: Path, profile_path: Path, destination: Path) -> dict[str, int | float]:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    source_document, source_binary = read_glb(source)
    mesh = transformed_hunyuan_mesh(
        source_document,
        source_binary,
        float(profile["targetHeight"]),
        float(profile.get("depthOffset", 0.0)),
        float(profile.get("bodyYawDeg", 0.0)),
    )

    template_document, template_binary = read_glb(template)
    document = deepcopy(template_document)
    binary = bytearray(template_binary)
    document["animations"] = [
        animation for animation in document.get("animations", []) if animation.get("name") not in PROCEDURAL_ANIMATIONS
    ]
    old_world = world_matrices(document)
    sample_points, sample_weights, body_node_index = source_weight_samples(document, binary)
    inverse_rows = read_accessor(document, binary, document["skins"][0]["inverseBindMatrices"])
    old_inverse_binds = [column_major_matrix(row) for row in inverse_rows]

    old_translations, new_translations = fit_skeleton(document, profile["jointWorldPositions"])
    retarget_animation_translations(document, binary, old_translations, new_translations)
    retarget_animation_rotations(document, binary, old_translations, new_translations)
    stabilize_lower_body_animations(document, binary)
    retime_ground_locomotion_animations(document, binary)
    new_world = world_matrices(document)
    joint_world = [new_world[index] for index in document["skins"][0]["joints"]]
    warped_points = warp_weight_samples(sample_points, sample_weights, old_inverse_binds, joint_world)
    tree = NearestWeightTree(warped_points, sample_weights)
    joint_names = [document["nodes"][index].get("name", "") for index in document["skins"][0]["joints"]]
    joint_by_name = {name: slot for slot, name in enumerate(joint_names)}
    component_categories = mesh_component_categories(mesh)
    close_rigid_hands(mesh, component_categories, profile["jointWorldPositions"])
    surface_limb_weights = surface_limb_blends(mesh)

    transferred = []
    total = len(mesh["positions"])
    for index, (point, limb_blends, component_category) in enumerate(
        zip(mesh["positions"], surface_limb_weights, component_categories),
        1,
    ):
        nearest = interpolated_weight(tree.nearest(point))
        transferred.append(
            stabilized_character_weight(
                point,
                nearest,
                limb_blends,
                profile["jointWorldPositions"],
                joint_by_name,
                joint_names,
                component_category,
            )
        )
        if index % 25000 == 0 or index == total:
            print(f"Transferred weights: {index}/{total}")

    material_base = import_hunyuan_materials(document, binary, source_document, source_binary)
    append_body_mesh(document, binary, mesh, transferred, material_base + int(mesh["material"]))
    for index, node in enumerate(document["nodes"]):
        if index == body_node_index:
            node.update(
                {
                    "name": "GeneratedCharacter",
                    "mesh": 0,
                    "skin": 0,
                    "translation": [0.0, 0.0, 0.0],
                    "rotation": [0.0, 0.0, 0.0, 1.0],
                    "scale": [1.0, 1.0, 1.0],
                }
            )
        elif "mesh" in node and node.get("name") not in WEAPON_NODE_NAMES:
            node.pop("mesh", None)

    update_inverse_bind_matrices(document, binary, body_node_index)
    weapon_detail = "original" if len(mesh["indices"]) // 3 > 200_000 else "low"
    node_by_name = {node.get("name"): index for index, node in enumerate(document["nodes"])}
    for _parent_index, name, builder in build_weapon_attachments(document, detail=weapon_detail):
        mesh_index, _vertices, _triangles = append_mesh(document, binary, f"{name}Mesh", builder)
        node = document["nodes"][node_by_name[name]]
        node["mesh"] = mesh_index
        node["scale"] = [0.0, 0.0, 0.0]
        node.pop("translation", None)
        node.pop("rotation", None)
    weapon_nodes = {
        node["name"]: index
        for index, node in enumerate(document["nodes"])
        if node.get("name") in WEAPON_NODE_NAMES
        and node.get("name") not in {"SpaceCommandoGunL", "SpaceCommandoGunR"}
    }
    add_weapon_animations(document, binary, weapon_nodes)

    document.setdefault("asset", {})["generator"] = "game-model-wiki Hunyuan V3.1 proportional rig transfer"
    document["asset"]["extras"] = {
        "rig": {
            "source": "space-commando-original-game-rig",
            "originalGameRig": True,
            "jointCount": len(document["skins"][0]["joints"]),
            "animations": len(document.get("animations", [])),
            "skeletonFitted": True,
            "profile": profile_path.name,
        },
        "imageToModel": {
            "generator": "tencent/Hunyuan3D-3.1",
            "source": source.name,
            "weightTransfer": "four-nearest inverse-distance over proportion-warped template",
            "handWeighting": "rigid hand with collapsed finger influences and compact closed-grip bind geometry",
            "handGeometry": "compact-closed-grip-v1",
            "limbWeighting": "connected-component garment, leg, and shoe classification; pelvis-anchored hip transition; rigid thigh, calf, and shoe segments with narrow knee and ankle blends",
            "meshSegmentation": "connected-components-with-geometric-fallback-v3",
            "lowerBodyAnimation": "scaled original Commando locomotion with one-way knee flex, root bob, and pitch-only world-space foot orientation compensation; forward run uses a 0.9 duration scale, full root forward lean, three-pass cyclic thigh smoothing, continuous asymmetric thigh swing, and a full forward stride; backward run and sprint keep the source swing; rigid lower body for idle, stationary attacks, jump, rolls, and slide",
            "groundUpperBodyAnimation": "layered Commando torso, arm, and head motion; the root lean (-16 deg, -32.5 deg sprint) is preserved and the head follows the source counter-rotation so the gaze stays level",
            "targetHeight": float(profile["targetHeight"]),
            "sourceOrientation": profile["sourceOrientation"],
            "bodyYawDeg": float(profile.get("bodyYawDeg", 0.0)),
            "independentWeapons": sorted(WEAPON_NODE_NAMES),
        },
    }
    document["skins"][0]["name"] = "GeneratedCharacterFittedSpaceCommandoRig"
    document["skins"][0].setdefault("extras", {}).update(
        {"weightTransfer": "proportion-warped-template-vertices", "skeletonFitted": True}
    )
    write_glb(destination, document, binary)
    return {
        "vertices": len(mesh["positions"]),
        "triangles": len(mesh["indices"]) // 3,
        "joints": len(document["skins"][0]["joints"]),
        "animations": len(document.get("animations", [])),
        "targetHeight": float(profile["targetHeight"]),
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--template",
        type=Path,
        default=repo_root / "generated" / "templates" / "space-commando-original.glb",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=repo_root / "references" / "popbot-hunyuan-v31-rig-profile.json",
    )
    args = parser.parse_args()
    stats = build_rigged_model(args.source, args.template, args.profile, args.destination)
    print(
        f"Built {args.destination}: {stats['triangles']} triangles, {stats['vertices']} vertices, "
        f"{stats['joints']} joints, {stats['animations']} animations"
    )


if __name__ == "__main__":
    main()
