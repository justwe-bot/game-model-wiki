"""Map a UniRig-skinned humanoid mesh onto a reusable animated template rig."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import struct

from add_ror2_bandit_rig import append_accessor, read_accessor, read_glb, write_glb
from build_space_commando import (
    local_matrix,
    matrix_inverse,
    matrix_multiply,
    matrix_quaternion,
    quat_inverse,
    quat_multiply,
    quat_normalize,
    world_matrices,
)
from rig_hunyuan_character import (
    animated_world_rotation,
    append_body_mesh,
    column_major_matrix,
    continuous_quaternions,
    fit_skeleton,
    import_hunyuan_materials,
    limit_rotation_delta,
    matrix_point,
    pitch_only_world_rotation,
    quat_slerp,
    retarget_animation_rotations,
    retarget_animation_translations,
    rotation_delta_degrees,
    source_weight_samples,
    update_inverse_bind_matrices,
)
from unirig_humanoid_mapping import semantic_weight_mapping
from stabilize_glb_upper_body_weights import stabilize_upper_body_weights
from reshape_glb_shoulder_armor import reshape_shoulder_armor_positions


HAND_POSES = {"source", "relaxed"}
FOOT_MODES = {"articulated", "rigid-shoe"}
RIGID_SHOE_FOOT_PROFILES = {
    "Commando_RunForward": {"world_pitch_blend": 0.30, "local_limit_deg": 38.0},
    "Commando_RunBackward": {"world_pitch_blend": 0.30, "local_limit_deg": 34.0},
    "Commando_RunLeft": {"world_pitch_blend": 0.30, "local_limit_deg": 34.0},
    "Commando_RunRight": {"world_pitch_blend": 0.30, "local_limit_deg": 34.0},
    "Commando_SprintForward": {"world_pitch_blend": 0.30, "local_limit_deg": 42.0},
}
LOCOMOTION_DESPIKE_NODES = {
    "thigh.l": {"threshold_deg": 2.0, "strength": 0.75, "passes": 1},
    "thigh.r": {"threshold_deg": 2.0, "strength": 0.75, "passes": 1},
    "calf.l": {"threshold_deg": 4.0, "strength": 0.85, "passes": 2},
    "calf.r": {"threshold_deg": 4.0, "strength": 0.85, "passes": 2},
    "foot.l": {"threshold_deg": 2.0, "strength": 0.75, "passes": 1},
    "foot.r": {"threshold_deg": 2.0, "strength": 0.75, "passes": 1},
}


def quaternion_multiply(left, right):
    lx, ly, lz, lw = (float(value) for value in left)
    rx, ry, rz, rw = (float(value) for value in right)
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def x_rotation(degrees: float):
    radians = math.radians(degrees) * 0.5
    return (math.sin(radians), 0.0, 0.0, math.cos(radians))


def apply_relaxed_hand_pose(document: dict, joint_names: list[str]) -> tuple[dict, str]:
    if len(joint_names) != 52:
        return deepcopy(document), f"source-fallback-{len(joint_names)}-bone"
    posed = deepcopy(document)
    node_by_name = {
        node.get("name", ""): index for index, node in enumerate(posed.get("nodes", []))
    }
    finger_chains = (
        (10, 11, 12),
        (13, 14, 15),
        (16, 17, 18),
        (19, 20, 21),
        (22, 23, 24),
        (29, 30, 31),
        (32, 33, 34),
        (35, 36, 37),
        (38, 39, 40),
        (41, 42, 43),
    )
    thumb_chains = {(10, 11, 12), (29, 30, 31)}
    for chain in finger_chains:
        angles = (
            (-26.0, -38.0, -30.0)
            if chain in thumb_chains
            else (-45.0, -65.0, -40.0)
        )
        for bone_index, angle in zip(chain, angles):
            name = f"bone_{bone_index}"
            node = posed["nodes"][node_by_name[name]]
            rest = tuple(node.get("rotation", (0.0, 0.0, 0.0, 1.0)))
            node["rotation"] = list(quaternion_multiply(rest, x_rotation(angle)))
    return posed, "relaxed"


def stabilize_rigid_shoe_animations(document: dict, binary: bytearray) -> None:
    nodes = document["nodes"]
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    parents = {
        child: parent
        for parent, node in enumerate(nodes)
        for child in node.get("children", [])
    }
    rest_rotations = [matrix_quaternion(local_matrix(node)) for node in nodes]
    rest_world_rotations = [matrix_quaternion(matrix) for matrix in world_matrices(document)]

    for animation in document.get("animations", []):
        profile = RIGID_SHOE_FOOT_PROFILES.get(animation.get("name"))
        if profile is None:
            continue
        tracks = {}
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            if target.get("path") != "rotation":
                continue
            sampler = animation["samplers"][channel["sampler"]]
            if sampler.get("interpolation", "LINEAR") != "LINEAR":
                raise ValueError("Rigid-shoe stabilization requires LINEAR rotation tracks")
            tracks[(target.get("node"), "rotation")] = (
                [float(row[0]) for row in read_accessor(document, binary, sampler["input"])],
                [
                    tuple(float(value) for value in row)
                    for row in read_accessor(document, binary, sampler["output"])
                ],
            )

        for side in ("l", "r"):
            foot_index = node_by_name[f"foot.{side}"]
            foot_key = (foot_index, "rotation")
            if foot_key not in tracks:
                raise ValueError(
                    f"{animation.get('name')} is missing {nodes[foot_index].get('name')}.rotation"
                )
            times, _values = tracks[foot_key]
            parent_index = parents[foot_index]
            converted = []
            for time in times:
                parent_world = animated_world_rotation(
                    parent_index,
                    time,
                    tracks,
                    rest_rotations,
                    parents,
                )
                source_world = animated_world_rotation(
                    foot_index,
                    time,
                    tracks,
                    rest_rotations,
                    parents,
                )
                desired_world = pitch_only_world_rotation(
                    rest_world_rotations[foot_index],
                    source_world,
                    profile["world_pitch_blend"],
                )
                desired_local = quat_normalize(
                    quat_multiply(quat_inverse(parent_world), desired_world)
                )
                converted.append(
                    limit_rotation_delta(
                        rest_rotations[foot_index],
                        desired_local,
                        profile["local_limit_deg"],
                    )
                )
            converted = continuous_quaternions(converted)
            payload = struct.pack(
                f"<{len(converted) * 4}f",
                *(value for row in converted for value in row),
            )
            channel = next(
                channel
                for channel in animation["channels"]
                if channel.get("target", {}).get("node") == foot_index
                and channel.get("target", {}).get("path") == "rotation"
            )
            sampler = animation["samplers"][channel["sampler"]]
            sampler["output"] = append_accessor(
                document,
                binary,
                payload,
                5126,
                "VEC4",
                len(converted),
            )
            tracks[foot_key] = (times, converted)

        animation.setdefault("extras", {}).update(
            {
                "footRetarget": "rigid-shoe-world-pitch-v1",
                "footWorldPitchBlend": profile["world_pitch_blend"],
                "footLocalRotationLimitDeg": profile["local_limit_deg"],
            }
        )


def despike_quaternion_track(
    times: list[float],
    values: list[tuple[float, ...]],
    *,
    threshold_deg: float,
    strength: float,
    passes: int,
) -> list[tuple[float, float, float, float]]:
    if len(values) < 3:
        return [quat_normalize(tuple(value)) for value in values]
    converted = [quat_normalize(tuple(value)) for value in values]
    looped = rotation_delta_degrees(converted[0], converted[-1]) < 0.01
    for _ in range(passes):
        source = converted
        converted = list(source)
        for index in range(1, len(source) - 1):
            amount = (times[index] - times[index - 1]) / max(
                times[index + 1] - times[index - 1],
                1e-8,
            )
            expected = quat_slerp(source[index - 1], source[index + 1], amount)
            error = rotation_delta_degrees(expected, source[index])
            if error <= threshold_deg:
                continue
            correction = min(
                strength,
                (error - threshold_deg * 0.5) / max(error, 1e-8),
            )
            converted[index] = quat_slerp(source[index], expected, correction)
        if looped:
            converted[-1] = converted[0]
    return continuous_quaternions(converted)


def stabilize_locomotion_rotation_spikes(document: dict, binary: bytearray) -> None:
    nodes = document["nodes"]
    for animation in document.get("animations", []):
        if animation.get("name") not in RIGID_SHOE_FOOT_PROFILES:
            continue
        smoothed_nodes = []
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            if target.get("path") != "rotation":
                continue
            node_name = nodes[target.get("node")].get("name", "")
            profile = LOCOMOTION_DESPIKE_NODES.get(node_name)
            if profile is None:
                continue
            sampler = animation["samplers"][channel["sampler"]]
            if sampler.get("interpolation", "LINEAR") != "LINEAR":
                raise ValueError("Locomotion despiking requires LINEAR rotation tracks")
            times = [
                float(row[0])
                for row in read_accessor(document, binary, sampler["input"])
            ]
            values = [
                tuple(float(value) for value in row)
                for row in read_accessor(document, binary, sampler["output"])
            ]
            converted = despike_quaternion_track(
                times,
                values,
                threshold_deg=profile["threshold_deg"],
                strength=profile["strength"],
                passes=int(profile["passes"]),
            )
            payload = struct.pack(
                f"<{len(converted) * 4}f",
                *(value for row in converted for value in row),
            )
            sampler["output"] = append_accessor(
                document,
                binary,
                payload,
                5126,
                "VEC4",
                len(converted),
            )
            smoothed_nodes.append(node_name)
        animation.setdefault("extras", {}).update(
            {
                "locomotionSmoothing": "isolated-key-despike-v1",
                "locomotionSmoothedNodes": sorted(smoothed_nodes),
            }
        )


def pose_skinned_vertices(
    document: dict,
    binary: bytearray,
    *,
    mesh_node_index: int,
    positions,
    normals,
    joints,
    weights,
    joint_names: list[str],
    hand_pose: str,
):
    if hand_pose == "source":
        return positions, normals, "source"
    if hand_pose not in HAND_POSES:
        raise ValueError(f"Unsupported hand pose: {hand_pose}")
    posed, effective_hand_pose = apply_relaxed_hand_pose(document, joint_names)
    if effective_hand_pose != "relaxed":
        return positions, normals, effective_hand_pose
    skin = document["skins"][document["nodes"][mesh_node_index]["skin"]]
    inverse_binds = [
        column_major_matrix(row)
        for row in read_accessor(document, binary, skin["inverseBindMatrices"])
    ]
    posed_world = world_matrices(posed)
    mesh_world = posed_world[mesh_node_index]
    inverse_mesh_world = matrix_inverse(mesh_world)
    joint_matrices = [
        matrix_multiply(
            matrix_multiply(inverse_mesh_world, posed_world[node_index]),
            inverse_bind,
        )
        for node_index, inverse_bind in zip(skin["joints"], inverse_binds)
    ]
    posed_positions = []
    posed_normals = []
    for position, normal, joint_row, weight_row in zip(
        positions, normals, joints, weights
    ):
        point = [0.0, 0.0, 0.0]
        direction = [0.0, 0.0, 0.0]
        for joint, weight in zip(joint_row, weight_row):
            weight = float(weight)
            if weight <= 0.0:
                continue
            matrix = joint_matrices[int(joint)]
            transformed = matrix_point(matrix, tuple(float(value) for value in position))
            transformed_normal = vector_transform(matrix, normal)
            for axis in range(3):
                point[axis] += transformed[axis] * weight
                direction[axis] += transformed_normal[axis] * weight
        posed_positions.append(tuple(point))
        posed_normals.append(normalize(direction))
    return posed_positions, posed_normals, effective_hand_pose


def vector_transform(matrix, value):
    return tuple(
        sum(float(matrix[row][column]) * float(value[column]) for column in range(3))
        for row in range(3)
    )


def normalize(value):
    length = math.sqrt(sum(component * component for component in value))
    if length < 1e-8:
        return (0.0, 1.0, 0.0)
    return tuple(component / length for component in value)


def reverse_triangle_winding(indices: list[int]) -> list[int]:
    if len(indices) % 3:
        raise ValueError("Triangle index count must be divisible by three")
    reversed_indices = list(indices)
    for offset in range(0, len(reversed_indices), 3):
        reversed_indices[offset + 1], reversed_indices[offset + 2] = (
            reversed_indices[offset + 2],
            reversed_indices[offset + 1],
        )
    return reversed_indices


def source_mesh(
    document: dict,
    binary: bytearray,
    *,
    target_height: float,
    depth_offset: float,
    hand_pose: str,
) -> tuple[dict, list[tuple[tuple[int, ...], tuple[float, ...]]], list[str]]:
    mesh_nodes = [
        (index, node)
        for index, node in enumerate(document.get("nodes", []))
        if "mesh" in node and "skin" in node
    ]
    if len(mesh_nodes) != 1:
        raise ValueError(f"Expected one UniRig skinned mesh node, found {len(mesh_nodes)}")
    node_index, node = mesh_nodes[0]
    primitives = document["meshes"][node["mesh"]].get("primitives", [])
    if len(primitives) != 1:
        raise ValueError(f"Expected one UniRig mesh primitive, found {len(primitives)}")
    primitive = primitives[0]
    attributes = primitive["attributes"]
    required = {"POSITION", "NORMAL", "TEXCOORD_0", "JOINTS_0", "WEIGHTS_0"}
    if not required.issubset(attributes) or "indices" not in primitive:
        raise ValueError(f"UniRig primitive is missing {sorted(required - set(attributes))} or indices")

    positions = read_accessor(document, binary, attributes["POSITION"])
    normals = read_accessor(document, binary, attributes["NORMAL"])
    texcoords = read_accessor(document, binary, attributes["TEXCOORD_0"])
    joints = read_accessor(document, binary, attributes["JOINTS_0"])
    weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
    indices = [int(row[0]) for row in read_accessor(document, binary, primitive["indices"])]
    if len({len(positions), len(normals), len(texcoords), len(joints), len(weights)}) != 1:
        raise ValueError("UniRig vertex attribute counts differ")

    skin = document["skins"][node["skin"]]
    source_joint_names = [document["nodes"][index].get("name", "") for index in skin["joints"]]
    positions, normals, effective_hand_pose = pose_skinned_vertices(
        document,
        binary,
        mesh_node_index=node_index,
        positions=positions,
        normals=normals,
        joints=joints,
        weights=weights,
        joint_names=source_joint_names,
        hand_pose=hand_pose,
    )
    matrix = world_matrices(document)[node_index]
    world_positions = [matrix_point(matrix, tuple(float(value) for value in row)) for row in positions]
    world_normals = [normalize(vector_transform(matrix, row)) for row in normals]
    minimum = [min(point[axis] for point in world_positions) for axis in range(3)]
    maximum = [max(point[axis] for point in world_positions) for axis in range(3)]
    source_height = maximum[1] - minimum[1]
    if source_height < 1e-5:
        raise ValueError("UniRig mesh has no usable Y-up height")
    scale = target_height / source_height
    center_x = (minimum[0] + maximum[0]) * 0.5
    converted_positions = [
        (
            (point[0] - center_x) * scale,
            (point[1] - minimum[1]) * scale,
            depth_offset - point[2] * scale,
        )
        for point in world_positions
    ]
    converted_normals = [normalize((normal[0], normal[1], -normal[2])) for normal in world_normals]
    converted_indices = reverse_triangle_winding(indices)
    rows = [
        (
            tuple(int(value) for value in joint_row),
            tuple(float(value) for value in weight_row),
        )
        for joint_row, weight_row in zip(joints, weights)
    ]
    return (
        {
            "positions": converted_positions,
            "normals": converted_normals,
            "texcoords": [tuple(float(value) for value in row) for row in texcoords],
            "indices": converted_indices,
            "material": int(primitive.get("material", 0)),
            "sourceBounds": {"min": minimum, "max": maximum},
            "scale": scale,
            "handPose": effective_hand_pose,
        },
        rows,
        source_joint_names,
    )


def remap_weights(
    rows: list[tuple[tuple[int, ...], tuple[float, ...]]],
    source_joint_names: list[str],
    target_joint_by_name: dict[str, int],
    *,
    finger_mode: str,
    foot_mode: str,
) -> tuple[list[tuple[tuple[int, int, int, int], tuple[float, float, float, float]]], dict[str, str]]:
    mapping = semantic_weight_mapping(len(source_joint_names), finger_mode=finger_mode)
    if foot_mode not in FOOT_MODES:
        raise ValueError(f"Unsupported foot mode: {foot_mode}")
    if foot_mode == "rigid-shoe":
        mapping = {
            source_name: (
                "foot.l"
                if target_name == "toe.l"
                else "foot.r"
                if target_name == "toe.r"
                else target_name
            )
            for source_name, target_name in mapping.items()
        }
    missing_targets = sorted(set(mapping.values()) - set(target_joint_by_name))
    if missing_targets:
        raise ValueError(f"Template rig is missing mapped joints: {missing_targets}")
    output = []
    for source_slots, source_weights in rows:
        merged: dict[int, float] = {}
        for source_slot, weight in zip(source_slots, source_weights):
            if weight <= 0.0:
                continue
            source_name = source_joint_names[source_slot]
            target_name = mapping.get(source_name)
            if target_name is None:
                continue
            target_slot = target_joint_by_name[target_name]
            merged[target_slot] = merged.get(target_slot, 0.0) + weight
        if not merged:
            merged[target_joint_by_name["pelvis"]] = 1.0
        selected = sorted(merged.items(), key=lambda item: item[1], reverse=True)[:4]
        total = sum(weight for _, weight in selected)
        joints = [slot for slot, _ in selected]
        weights = [weight / total for _, weight in selected]
        while len(joints) < 4:
            joints.append(0)
            weights.append(0.0)
        output.append((tuple(joints), tuple(weights)))
    return output, mapping


def build_rigged_model(
    source: Path,
    template: Path,
    profile_path: Path,
    destination: Path,
    *,
    finger_mode: str = "rigid",
    hand_pose: str = "source",
    foot_mode: str = "rigid-shoe",
    upper_body_mode: str = "none",
    shoulder_mode: str = "none",
) -> dict[str, int | float]:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    source_document, source_binary = read_glb(source)
    mesh, source_rows, source_joint_names = source_mesh(
        source_document,
        source_binary,
        target_height=float(profile["targetHeight"]),
        depth_offset=float(profile.get("depthOffset", 0.08)),
        hand_pose=hand_pose,
    )
    shoulder_changed = 0
    shoulder_centers = None
    if shoulder_mode == "armored-slab":
        mesh["positions"], shoulder_changed, shoulder_centers = reshape_shoulder_armor_positions(
            mesh["positions"]
        )
    elif shoulder_mode != "none":
        raise ValueError(f"Unsupported shoulder mode: {shoulder_mode}")

    template_document, template_binary = read_glb(template)
    document = deepcopy(template_document)
    binary = bytearray(template_binary)
    _points, _weights, body_node_index = source_weight_samples(document, binary)
    old_translations, new_translations = fit_skeleton(document, profile["jointWorldPositions"])
    retarget_animation_translations(document, binary, old_translations, new_translations)
    retarget_animation_rotations(document, binary, old_translations, new_translations)
    if foot_mode == "rigid-shoe":
        stabilize_rigid_shoe_animations(document, binary)
        stabilize_locomotion_rotation_spikes(document, binary)

    template_joint_names = [
        document["nodes"][index].get("name", "") for index in document["skins"][0]["joints"]
    ]
    target_joint_by_name = {name: slot for slot, name in enumerate(template_joint_names)}
    transferred, mapping = remap_weights(
        source_rows,
        source_joint_names,
        target_joint_by_name,
        finger_mode=finger_mode,
        foot_mode=foot_mode,
    )
    upper_body_changed = 0
    if upper_body_mode == "armored":
        transferred, upper_body_changed = stabilize_upper_body_weights(
            mesh["positions"],
            transferred,
            chest_slot=target_joint_by_name["chest"],
        )
    elif upper_body_mode != "none":
        raise ValueError(f"Unsupported upper body mode: {upper_body_mode}")
    material_base = import_hunyuan_materials(
        document, binary, source_document, source_binary
    )
    append_body_mesh(
        document,
        binary,
        mesh,
        transferred,
        material_base + int(mesh["material"]),
    )
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
        elif "mesh" in node:
            node.pop("mesh", None)
    update_inverse_bind_matrices(document, binary, body_node_index)

    document.setdefault("asset", {})["generator"] = "game-model-wiki UniRig semantic template transfer"
    document["asset"]["extras"] = {
        "rig": {
            "source": template.name,
            "weightSource": "unirig-semantic-map",
            "jointCount": len(document["skins"][0]["joints"]),
            "animations": len(document.get("animations", [])),
            "profile": profile_path.name,
            "fingerMode": finger_mode,
            "handPose": mesh["handPose"],
            "requestedHandPose": hand_pose,
            "footMode": foot_mode,
            "upperBodyMode": upper_body_mode,
            "upperBodyChangedVertices": upper_body_changed,
            "shoulderMode": shoulder_mode,
            "shoulderChangedVertices": shoulder_changed,
            "shoulderCenters": shoulder_centers,
        },
        "imageToModel": {
            "generator": "VAST-AI/UniRig",
            "source": source.name,
            "weightTransfer": "semantic anonymous-bone remap",
            "mappedBoneCount": len(mapping),
            "sourceBoneCount": len(source_joint_names),
            "targetHeight": float(profile["targetHeight"]),
        },
    }
    document["skins"][0]["name"] = "GeneratedCharacterUniRigTemplateRig"
    document["skins"][0].setdefault("extras", {}).update(
        {"weightTransfer": "unirig-semantic-map", "skeletonFitted": True}
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
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--finger-mode", choices=("rigid", "mapped"), default="rigid")
    parser.add_argument("--hand-pose", choices=sorted(HAND_POSES), default="source")
    parser.add_argument("--foot-mode", choices=sorted(FOOT_MODES), default="rigid-shoe")
    parser.add_argument("--upper-body-mode", choices=("none", "armored"), default="none")
    parser.add_argument("--shoulder-mode", choices=("none", "armored-slab"), default="none")
    args = parser.parse_args()
    stats = build_rigged_model(
        args.source.resolve(),
        args.template.resolve(),
        args.profile.resolve(),
        args.destination.resolve(),
        finger_mode=args.finger_mode,
        hand_pose=args.hand_pose,
        foot_mode=args.foot_mode,
        upper_body_mode=args.upper_body_mode,
        shoulder_mode=args.shoulder_mode,
    )
    print(
        f"Rigged {args.destination}: {stats['triangles']} triangles, "
        f"{stats['joints']} joints, {stats['animations']} animations"
    )


if __name__ == "__main__":
    main()
