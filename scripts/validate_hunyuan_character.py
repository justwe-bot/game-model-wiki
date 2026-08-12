"""Validate a Hunyuan character fitted to the animated Space Commando rig."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from add_ror2_bandit_rig import read_accessor, read_glb
from build_space_commando import animation_duration, matrix_quaternion, sample_animation_value, world_matrices
from validate_ror2_commando_game_rig import EXPECTED_ANIMATIONS


CUSTOM_ANIMATIONS = {"SpaceCommando_BowShot", "SpaceCommando_HeavyFire"}
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
GROUND_LOCOMOTION_ROTATION_LIMITS_DEG = {
    "thigh.l": 54.05,
    "calf.l": 78.05,
    "foot.l": 42.05,
    "thigh.r": 54.05,
    "calf.r": 78.05,
    "foot.r": 42.05,
}
FORWARD_RUN_THIGH_ROTATION_LIMIT_DEG = 72.05
BACKWARD_RUN_THIGH_ROTATION_LIMIT_DEG = 72.05
SPRINT_THIGH_ROTATION_LIMIT_DEG = 84.05
GROUND_KNEE_PEAK_DEG = {
    "default": 38.0,
    "Commando_SprintForward": 56.5,
}
GROUND_FOOT_LOCAL_LIMIT_DEG = {
    "default": 34.0,
    "Commando_RunForward": 38.0,
    "Commando_RunBackward": 34.0,
    "Commando_SprintForward": 42.0,
}
GROUND_FOOT_WORLD_LIMIT_DEG = 18.05
GROUND_FOOT_CROSS_AXIS_LIMITS = {
    "default": 0.015,
    "Commando_RunBackward": 0.035,
}
GROUND_ROOT_BOB_LIMITS = {
    "default": 0.032,
    "Commando_RunForward": 0.052,
    "Commando_RunBackward": 0.034,
    "Commando_SprintForward": 0.040,
}
GROUND_DURATION_SCALES = {"default": 1.0, "Commando_RunForward": 0.90}
FORWARD_RUN_THIGH_SIGNED_LIMITS_DEG = (-72.05, 30.05)
FORWARD_RUN_MIN_STRIDE_RANGE_DEG = 63.0
FORWARD_RUN_MAX_THIGH_ACCEL_DEG_S2 = 18000.0
# The source clips keep a constant -16 deg root lean (sprint -32.5 deg).
# The fitted skeleton must preserve that pose to read as a forward charge.
GROUND_BASE_ROTATION_LIMITS_DEG = {
    "default": 18.05,
    "Commando_SprintForward": 34.05,
}
GROUND_PELVIS_ROTATION_LIMIT_DEG = 7.0
# The source counter-rotates the head up to ~+56 deg (strafe) to hold the
# gaze level under the root lean; the fitted big head follows that motion.
HEAD_ROTATION_LIMIT_DEG = 58.05
WEAPON_PARENTS = {
    "SpaceCommandoGunL": "gun.l",
    "SpaceCommandoGunR": "gun.r",
    "SpaceCommandoBow": "chest",
    "SpaceCommandoBowStringDrawn": "chest",
    "SpaceCommandoBowStringRest": "chest",
    "SpaceCommandoArrow": "chest",
    "SpaceCommandoHeavyWeapon": "chest",
    "SpaceCommandoHeavyMuzzleFlash": "chest",
}
HIDDEN_WEAPONS = set(WEAPON_PARENTS) - {"SpaceCommandoGunL", "SpaceCommandoGunR"}
CUSTOM_STABLE_NODES = {
    "base",
    "stomach",
    "chest",
    "pelvis",
    "hand.l",
    "hand.r",
    "thigh.l",
    "calf.l",
    "foot.l",
    "toe.l",
    "thigh.r",
    "calf.r",
    "foot.r",
    "toe.r",
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


def quat_multiply(first, second):
    ax, ay, az, aw = first
    bx, by, bz, bw = second
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_normalize(value):
    length = math.sqrt(sum(component * component for component in value))
    return tuple(component / max(length, 1e-8) for component in value)


def relative_rotation(reference, value):
    inverse = (-reference[0], -reference[1], -reference[2], reference[3])
    relative = quat_multiply(inverse, value)
    if relative[3] < 0.0:
        relative = tuple(-component for component in relative)
    return relative


def rotation_delta_degrees(reference, value):
    relative = relative_rotation(reference, value)
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, relative[3]))))


def animated_world_rotation(document, binary, animation, node_index, time, parents):
    chain = []
    current = node_index
    while True:
        chain.append(current)
        if current not in parents:
            break
        current = parents[current]
    world = (0.0, 0.0, 0.0, 1.0)
    for index in reversed(chain):
        local = sample_animation_value(document, binary, animation, index, "rotation", time)
        world = quat_normalize(quat_multiply(world, local))
    return world


def validate(path: Path) -> dict[str, int | float]:
    document, binary = read_glb(path)
    nodes = document.get("nodes", [])
    extras = document.get("asset", {}).get("extras", {})
    rig = extras.get("rig", {})
    generation = extras.get("imageToModel", {})
    if rig.get("source") != "space-commando-original-game-rig" or rig.get("originalGameRig") is not True:
        raise ValueError(f"{path}: missing fitted Space Commando rig provenance")
    if generation.get("generator") != "tencent/Hunyuan3D-3.1":
        raise ValueError(f"{path}: missing Hunyuan3D V3.1 provenance")
    if generation.get("meshSegmentation") != "connected-components-with-geometric-fallback-v3":
        raise ValueError(f"{path}: missing connected-component mesh segmentation")
    if generation.get("handGeometry") != "compact-closed-grip-v1":
        raise ValueError(f"{path}: missing compact closed-grip hand geometry")

    skins = document.get("skins", [])
    if len(skins) != 1 or len(skins[0].get("joints", [])) != 78:
        raise ValueError(f"{path}: expected one 78-joint skin")
    animation_names = {animation.get("name") for animation in document.get("animations", [])}
    expected_animations = EXPECTED_ANIMATIONS | CUSTOM_ANIMATIONS
    if animation_names != expected_animations:
        raise ValueError(f"{path}: unexpected animation set: {sorted(animation_names)}")
    idle_animation = next(animation for animation in document["animations"] if animation.get("name") == "Commando_Idle")
    idle_values = {}
    for channel in idle_animation.get("channels", []):
        target = channel.get("target", {})
        sampler = idle_animation["samplers"][channel["sampler"]]
        values = read_accessor(document, binary, sampler["output"])
        idle_values[(target.get("node"), target.get("path"))] = tuple(float(value) for value in values[0])
    parents = {child: parent for parent, node in enumerate(nodes) for child in node.get("children", [])}
    rest_world_rotations = [matrix_quaternion(matrix) for matrix in world_matrices(document)]
    for animation in document.get("animations", []):
        name = animation.get("name")
        ground_locomotion_targets = set()
        ground_base_translation_seen = False
        ground_knee_ranges = {}
        ground_thigh_signed_ranges = {}
        ground_thigh_acceleration_peaks = {}
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            node_index = target.get("node", -1)
            node_name = nodes[node_index].get("name", "")
            target_path = target.get("path")
            if name in RIGID_LOWER_BODY_ANIMATIONS and node_name in LOWER_BODY_NODES:
                raise ValueError(f"{path}: {name} animates stabilized lower-body node {node_name}")
            if name in ROOT_HEIGHT_LOCKED_ANIMATIONS and node_name == "base" and target_path == "translation":
                raise ValueError(f"{path}: {name} animates stabilized root height")
            if name not in GROUND_LOCOMOTION_ANIMATIONS:
                continue
            if node_name == "base" and target_path == "translation":
                reference = tuple(float(value) for value in nodes[node_index].get("translation", [0.0, 0.0, 0.0]))
                sampler = animation["samplers"][channel["sampler"]]
                for row in read_accessor(document, binary, sampler["output"]):
                    if abs(float(row[0]) - reference[0]) > 1e-5 or abs(float(row[2]) - reference[2]) > 1e-5:
                        raise ValueError(f"{path}: {name} moves the stabilized root horizontally")
                    bob_limit = GROUND_ROOT_BOB_LIMITS.get(name, GROUND_ROOT_BOB_LIMITS["default"])
                    if abs(float(row[1]) - reference[1]) > bob_limit:
                        raise ValueError(f"{path}: {name} exceeds the root bob limit")
                ground_base_translation_seen = True
                continue
            if node_name not in LOWER_BODY_NODES:
                reference = idle_values.get((node_index, target_path))
                if reference is None:
                    raise ValueError(f"{path}: idle animation is missing {node_name}.{target_path}")
                sampler = animation["samplers"][channel["sampler"]]
                values = read_accessor(document, binary, sampler["output"])
                if target_path == "scale" or node_name.startswith(("finger", "thumb")):
                    for row in values:
                        if max(abs(float(value) - expected) for value, expected in zip(row, reference)) > 1e-5:
                            raise ValueError(f"{path}: {name} does not stabilize {node_name}.{target_path}")
                if node_name in {"neck", "head"} and target_path == "rotation":
                    # The head follows the source counter-rotation (keeps the
                    # gaze level under the root lean); bound its range instead
                    # of requiring a fully frozen head.
                    for row in values:
                        relative = relative_rotation(reference, tuple(float(value) for value in row))
                        head_delta = math.degrees(2.0 * math.acos(max(-1.0, min(1.0, relative[3]))))
                        if head_delta > HEAD_ROTATION_LIMIT_DEG + 0.05:
                            raise ValueError(f"{path}: {name} tilts {node_name} too far: {head_delta:.2f}")
                if node_name == "base" and target_path == "rotation":
                    base_lean_limit = GROUND_BASE_ROTATION_LIMITS_DEG.get(name, GROUND_BASE_ROTATION_LIMITS_DEG["default"])
                    for row in values:
                        dot = abs(sum(expected * float(value) for expected, value in zip(reference, row)))
                        delta_deg = math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot))))
                        if delta_deg > base_lean_limit + 0.05:
                            raise ValueError(f"{path}: {name} exceeds the root lean limit: {delta_deg:.2f}")
                continue
            if node_name == "pelvis":
                if target_path != "rotation":
                    raise ValueError(f"{path}: {name} has unsupported pelvis track {target_path}")
                reference = tuple(float(value) for value in nodes[node_index].get("rotation", [0.0, 0.0, 0.0, 1.0]))
                sampler = animation["samplers"][channel["sampler"]]
                for row in read_accessor(document, binary, sampler["output"]):
                    dot = abs(sum(expected * float(value) for expected, value in zip(reference, row)))
                    delta_deg = math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot))))
                    if delta_deg > GROUND_PELVIS_ROTATION_LIMIT_DEG + 0.05:
                        raise ValueError(f"{path}: {name} exceeds the pelvis rotation limit: {delta_deg:.2f}")
                continue
            if node_name not in GROUND_LOCOMOTION_ROTATION_LIMITS_DEG:
                raise ValueError(f"{path}: {name} animates unsupported ground locomotion node {node_name}")
            if target_path != "rotation":
                raise ValueError(f"{path}: {name} has non-rotation lower-body track {node_name}.{target_path}")
            ground_locomotion_targets.add(node_name)
            rest = tuple(float(value) for value in nodes[node_index].get("rotation", [0.0, 0.0, 0.0, 1.0]))
            sampler = animation["samplers"][channel["sampler"]]
            angles = []
            signed_x_angles = []
            for row in read_accessor(document, binary, sampler["output"]):
                dot = abs(sum(a * float(b) for a, b in zip(rest, row)))
                delta_deg = math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot))))
                angles.append(delta_deg)
                if node_name.startswith("thigh."):
                    relative = relative_rotation(rest, tuple(float(value) for value in row))
                    signed_x_angles.append(math.degrees(2.0 * math.atan2(relative[0], relative[3])))
                if node_name.startswith("calf."):
                    relative = relative_rotation(rest, tuple(float(value) for value in row))
                    if relative[0] > 1e-5:
                        raise ValueError(f"{path}: {name} bends {node_name} in the wrong direction")
                rotation_limit = (
                    FORWARD_RUN_THIGH_ROTATION_LIMIT_DEG
                    if name == "Commando_RunForward" and node_name.startswith("thigh.")
                    else BACKWARD_RUN_THIGH_ROTATION_LIMIT_DEG
                    if name == "Commando_RunBackward" and node_name.startswith("thigh.")
                    else SPRINT_THIGH_ROTATION_LIMIT_DEG
                    if name == "Commando_SprintForward" and node_name.startswith("thigh.")
                    else GROUND_LOCOMOTION_ROTATION_LIMITS_DEG[node_name]
                )
                if delta_deg > rotation_limit + 0.05:
                    raise ValueError(f"{path}: {name} exceeds {node_name} rotation limit: {delta_deg:.2f}")
            if node_name.startswith("calf."):
                ground_knee_ranges[node_name] = (min(angles), max(angles))
            if node_name.startswith("thigh."):
                ground_thigh_signed_ranges[node_name] = (min(signed_x_angles), max(signed_x_angles))
                times = [float(row[0]) for row in read_accessor(document, binary, sampler["input"])]
                velocities = [
                    (signed_x_angles[index + 1] - signed_x_angles[index])
                    / (times[index + 1] - times[index])
                    for index in range(len(signed_x_angles) - 1)
                ]
                accelerations = [
                    abs(
                        (velocities[index + 1] - velocities[index])
                        / ((times[index + 2] - times[index]) * 0.5)
                    )
                    for index in range(len(velocities) - 1)
                ]
                ground_thigh_acceleration_peaks[node_name] = max(accelerations)
            if node_name.startswith("foot."):
                expected_limit = GROUND_FOOT_LOCAL_LIMIT_DEG.get(name, GROUND_FOOT_LOCAL_LIMIT_DEG["default"])
                if max(angles) > expected_limit + 0.05:
                    raise ValueError(f"{path}: {name} exceeds {node_name} local ankle limit: {max(angles):.2f}")
                times = read_accessor(document, binary, sampler["input"])
                world_rotations = [
                    animated_world_rotation(document, binary, animation, node_index, float(row[0]), parents)
                    for row in times
                ]
                world_angles = [
                    rotation_delta_degrees(rest_world_rotations[node_index], value)
                    for value in world_rotations
                ]
                if max(world_angles) > GROUND_FOOT_WORLD_LIMIT_DEG:
                    raise ValueError(f"{path}: {name} tilts {node_name} too far in world space: {max(world_angles):.2f}")
                cross_axis = max(
                    max(abs(component) for component in relative_rotation(rest_world_rotations[node_index], value)[1:3])
                    for value in world_rotations
                )
                cross_limit = GROUND_FOOT_CROSS_AXIS_LIMITS.get(name, GROUND_FOOT_CROSS_AXIS_LIMITS["default"])
                if cross_axis > cross_limit:
                    raise ValueError(f"{path}: {name} turns {node_name} outward or sideways: {cross_axis:.4f}")
        if name in GROUND_LOCOMOTION_ANIMATIONS and ground_locomotion_targets != set(
            GROUND_LOCOMOTION_ROTATION_LIMITS_DEG
        ):
            raise ValueError(f"{path}: {name} has incomplete retargeted locomotion targets: {sorted(ground_locomotion_targets)}")
        if name in GROUND_LOCOMOTION_ANIMATIONS and not ground_base_translation_seen:
            raise ValueError(f"{path}: {name} is missing stabilized root bob")
        if name in GROUND_LOCOMOTION_ANIMATIONS:
            extras = animation.get("extras", {})
            expected_retarget = (
                "scaled-commando-locomotion-v4"
                if name == "Commando_RunForward"
                else "scaled-commando-locomotion-v2"
            )
            if extras.get("motionRetarget") != expected_retarget:
                raise ValueError(f"{path}: {name} is missing Commando locomotion retarget metadata")
            if extras.get("sourceMotion") != "original-commando-lower-body":
                raise ValueError(f"{path}: {name} is missing original lower-body motion provenance")
            if extras.get("kneeBendAxis") != "negative-local-x":
                raise ValueError(f"{path}: {name} is missing the knee bend direction")
            if extras.get("footWorldMotionMode") != "rest-aligned-pitch-only":
                raise ValueError(f"{path}: {name} is missing straight-foot world motion metadata")
            expected_duration_scale = GROUND_DURATION_SCALES.get(name, GROUND_DURATION_SCALES["default"])
            if abs(float(extras.get("durationScale", -1.0)) - expected_duration_scale) > 1e-6:
                raise ValueError(f"{path}: {name} has incorrect cadence metadata")
            if abs(animation_duration(document, binary, animation) - expected_duration_scale) > 1e-5:
                raise ValueError(f"{path}: {name} has incorrect retimed duration")
            expected_foot_limit = GROUND_FOOT_LOCAL_LIMIT_DEG.get(name, GROUND_FOOT_LOCAL_LIMIT_DEG["default"])
            if abs(float(extras.get("footLocalRotationLimitDeg", -1.0)) - expected_foot_limit) > 1e-6:
                raise ValueError(f"{path}: {name} has incorrect foot rotation limit metadata")
            required_peak = GROUND_KNEE_PEAK_DEG.get(name, GROUND_KNEE_PEAK_DEG["default"])
            for node_name in ("calf.l", "calf.r"):
                _observed_minimum, observed_peak = ground_knee_ranges[node_name]
                if observed_peak < required_peak:
                    raise ValueError(f"{path}: {name} under-flexes {node_name}: {observed_peak:.2f}")
            if name == "Commando_RunForward":
                expected_negative, expected_positive = FORWARD_RUN_THIGH_SIGNED_LIMITS_DEG
                for node_name in ("thigh.l", "thigh.r"):
                    observed_negative, observed_positive = ground_thigh_signed_ranges[node_name]
                    if observed_negative < expected_negative or observed_positive > expected_positive:
                        raise ValueError(
                            f"{path}: {name} exceeds {node_name} signed stride limits: "
                            f"{observed_negative:.2f}..{observed_positive:.2f}"
                        )
                    if observed_positive - observed_negative < FORWARD_RUN_MIN_STRIDE_RANGE_DEG:
                        raise ValueError(
                            f"{path}: {name} under-strides {node_name}: "
                            f"{observed_negative:.2f}..{observed_positive:.2f}"
                        )
                    observed_acceleration = ground_thigh_acceleration_peaks[node_name]
                    if observed_acceleration > FORWARD_RUN_MAX_THIGH_ACCEL_DEG_S2:
                        raise ValueError(
                            f"{path}: {name} has a {node_name} motion spike: "
                            f"{observed_acceleration:.1f} deg/s^2"
                        )
    for name in CUSTOM_ANIMATIONS:
        animation = next(item for item in document["animations"] if item.get("name") == name)
        if animation.get("extras", {}).get("proceduralUpperBody") is not True:
            raise ValueError(f"{path}: {name} is missing procedural action metadata")
        if animation.get("extras", {}).get("source") != "Commando_Idle":
            raise ValueError(f"{path}: {name} must use the stable idle pose as its base")
        targets = set()
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            node_name = nodes[target.get("node", -1)].get("name", "")
            key = (target.get("node"), target.get("path"))
            if key in targets:
                raise ValueError(f"{path}: {name} has duplicate target {node_name}.{target.get('path')}")
            targets.add(key)
            if node_name in CUSTOM_STABLE_NODES or node_name.startswith(("finger", "thumb")):
                raise ValueError(f"{path}: {name} animates stabilized node {node_name}")

    body_index = next((index for index, node in enumerate(nodes) if node.get("name") == "GeneratedCharacter"), None)
    if body_index is None or nodes[body_index].get("skin") != 0 or "mesh" not in nodes[body_index]:
        raise ValueError(f"{path}: GeneratedCharacter is not bound to skin 0")
    for name, parent_name in WEAPON_PARENTS.items():
        index = next((index for index, node in enumerate(nodes) if node.get("name") == name), None)
        if index is None or nodes[parents.get(index, -1)].get("name") != parent_name:
            raise ValueError(f"{path}: {name} is not attached to {parent_name}")
        if name in HIDDEN_WEAPONS and nodes[index].get("scale") != [0.0, 0.0, 0.0]:
            raise ValueError(f"{path}: {name} must be hidden outside its action")

    body_mesh = document["meshes"][nodes[body_index]["mesh"]]
    joint_names = [nodes[index].get("name", "") for index in skins[0]["joints"]]
    vertices = 0
    body_triangles = 0
    max_weight_error = 0.0
    for primitive in body_mesh.get("primitives", []):
        attributes = primitive.get("attributes", {})
        required = {"POSITION", "NORMAL", "TEXCOORD_0", "JOINTS_0", "WEIGHTS_0"}
        if not required.issubset(attributes):
            raise ValueError(f"{path}: body primitive is missing {sorted(required - set(attributes))}")
        positions = read_accessor(document, binary, attributes["POSITION"])
        joints = read_accessor(document, binary, attributes["JOINTS_0"])
        weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
        if len(positions) != len(joints) or len(positions) != len(weights):
            raise ValueError(f"{path}: body vertex attribute counts differ")
        vertices += len(positions)
        body_triangles += document["accessors"][primitive["indices"]]["count"] // 3
        for joint_row, weight_row in zip(joints, weights):
            if max(joint_row) >= 78:
                raise ValueError(f"{path}: joint index exceeds inherited skin")
            if any(
                weight > 1e-6 and joint_names[int(joint)].startswith("toe.")
                for joint, weight in zip(joint_row, weight_row)
            ):
                raise ValueError(f"{path}: shoe vertices must not use toe joints")
            max_weight_error = max(max_weight_error, abs(sum(weight_row) - 1.0))
        for position, joint_row, weight_row in zip(positions, joints, weights):
            if float(position[1]) > 0.215 or abs(float(position[0])) <= 0.02:
                continue
            side = "l" if float(position[0]) < 0.0 else "r"
            used_names = {
                joint_names[int(joint)]
                for joint, weight in zip(joint_row, weight_row)
                if float(weight) > 1e-6
            }
            if used_names != {f"foot.{side}"}:
                raise ValueError(f"{path}: {side} shoe vertex is not rigidly assigned to foot.{side}: {sorted(used_names)}")
    if max_weight_error > 1e-5:
        raise ValueError(f"{path}: skin weights are not normalized: {max_weight_error}")
    if len(document.get("images", [])) < 3 or len(document.get("textures", [])) < 3:
        raise ValueError(f"{path}: embedded PBR texture set is incomplete")

    total_triangles = 0
    for node in nodes:
        if "mesh" not in node:
            continue
        total_triangles += sum(
            document["accessors"][primitive["indices"]]["count"] // 3
            for primitive in document["meshes"][node["mesh"]].get("primitives", [])
        )
    return {
        "vertices": vertices,
        "bodyTriangles": body_triangles,
        "totalTriangles": total_triangles,
        "joints": 78,
        "animations": 20,
        "weightError": max_weight_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        result = validate(path)
        print(
            f"{path}: {result['bodyTriangles']} body triangles, "
            f"{result['totalTriangles']} total triangles, {result['vertices']} vertices, "
            f"{result['joints']} joints, {result['animations']} animations, "
            f"weightError={result['weightError']:.2e}"
        )


if __name__ == "__main__":
    main()
