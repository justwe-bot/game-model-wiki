"""Build a Space Commando skin on the original Commando mesh and game rig.

The source mesh, UV layout, skin, gun attachments, and animations stay intact.
A new palette plus procedural astronaut armor create the alternate character.
"""

from __future__ import annotations

import argparse
import binascii
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import struct
import zlib

from add_ror2_bandit_rig import append_accessor, read_accessor, read_glb, write_glb


Vec3 = tuple[float, float, float]
Vec4 = tuple[float, float, float, float]
Quat = tuple[float, float, float, float]


MIRROR_Z_PLANE = 0.012856721878051758
HELMET_CENTER_DESIGN = (0.0, 1.705, 0.035)
HELMET_RADII = (0.19, 0.195, 0.205)


MATERIALS = [
    {
        "name": "SpaceCommandoBasePalette",
        "pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0},
            "metallicFactor": 0.08,
            "roughnessFactor": 0.7,
        },
    },
    {
        "name": "SpaceCommandoCeramic",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.82, 0.84, 0.82, 1.0],
            "metallicFactor": 0.14,
            "roughnessFactor": 0.68,
        },
    },
    {
        "name": "SpaceCommandoPressureSuit",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.035, 0.045, 0.05, 1.0],
            "metallicFactor": 0.04,
            "roughnessFactor": 0.9,
        },
    },
    {
        "name": "SpaceCommandoTealArmor",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.12, 0.24, 0.26, 1.0],
            "metallicFactor": 0.28,
            "roughnessFactor": 0.5,
        },
    },
    {
        "name": "SpaceCommandoMirrorHelmet",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.015, 0.025, 0.03, 1.0],
            "metallicFactor": 0.78,
            "roughnessFactor": 0.2,
        },
    },
    {
        "name": "SpaceCommandoAmberLight",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.95, 0.2, 0.02, 1.0],
            "metallicFactor": 0.08,
            "roughnessFactor": 0.36,
        },
        "emissiveFactor": [1.0, 0.11, 0.01],
    },
    {
        "name": "SpaceCommandoTitanium",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.28, 0.31, 0.32, 1.0],
            "metallicFactor": 0.72,
            "roughnessFactor": 0.38,
        },
    },
    {
        "name": "SpaceCommandoSecondaryArmor",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.48, 0.48, 0.45, 1.0],
            "metallicFactor": 0.12,
            "roughnessFactor": 0.72,
        },
    },
    {
        "name": "SpaceCommandoGlassHelmet",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.12, 0.42, 0.52, 0.2],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.1,
        },
        "alphaMode": "BLEND",
        "extensions": {
            "KHR_materials_transmission": {"transmissionFactor": 0.88},
            "KHR_materials_ior": {"ior": 1.52},
            "KHR_materials_specular": {
                "specularFactor": 0.35,
                "specularColorFactor": [0.15, 0.55, 0.68],
            },
        },
    },
]

BASE = 0
CERAMIC = 1
SUIT = 2
TEAL = 3
MIRROR_HELMET = 4
AMBER = 5
METAL = 6
SECONDARY = 7
GLASS_HELMET = 8


SPACE_PALETTE = (
    [(24, 29, 31, 255)] * 9
    + [(43, 51, 54, 255)] * 9
    + [(82, 92, 94, 255)] * 9
    + [(184, 187, 182, 255)] * 10
    + [(230, 230, 222, 255)] * 9
    + [(242, 241, 233, 255)] * 9
    + [(49, 78, 82, 255)] * 9
)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def palette_png() -> bytes:
    width, height = 64, 8
    row = bytes(channel for color in SPACE_PALETTE for channel in color)
    scanlines = b"".join(b"\x00" + row for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(scanlines, 9))
        + png_chunk(b"IEND", b"")
    )


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(value: Vec3, amount: float) -> Vec3:
    return (value[0] * amount, value[1] * amount, value[2] * amount)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(value: Vec3) -> float:
    return math.sqrt(dot(value, value))


def normalize(value: Vec3) -> Vec3:
    magnitude = length(value)
    if magnitude <= 1e-8:
        return (0.0, 1.0, 0.0)
    return scale(value, 1.0 / magnitude)


def lerp(a: Vec3, b: Vec3, amount: float) -> Vec3:
    return tuple(a[index] + (b[index] - a[index]) * amount for index in range(3))  # type: ignore[return-value]


def quat_normalize(value: Quat) -> Quat:
    magnitude = math.sqrt(sum(component * component for component in value))
    if magnitude <= 1e-8:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(component / magnitude for component in value)  # type: ignore[return-value]


def quat_multiply(left: Quat, right: Quat) -> Quat:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def quat_inverse(value: Quat) -> Quat:
    return (-value[0], -value[1], -value[2], value[3])


def quat_rotate(value: Quat, vector: Vec3) -> Vec3:
    rotated = quat_multiply(
        quat_multiply(value, (vector[0], vector[1], vector[2], 0.0)),
        quat_inverse(value),
    )
    return (rotated[0], rotated[1], rotated[2])


def quat_from_to(source: Vec3, target: Vec3) -> Quat:
    source = normalize(source)
    target = normalize(target)
    alignment = max(-1.0, min(1.0, dot(source, target)))
    if alignment > 0.999999:
        return (0.0, 0.0, 0.0, 1.0)
    if alignment < -0.999999:
        reference = (1.0, 0.0, 0.0) if abs(source[0]) < 0.9 else (0.0, 1.0, 0.0)
        axis = normalize(cross(source, reference))
        return (axis[0], axis[1], axis[2], 0.0)
    axis = cross(source, target)
    return quat_normalize((axis[0], axis[1], axis[2], 1.0 + alignment))


def matrix_quaternion(matrix: list[list[float]]) -> Quat:
    rotation = [[0.0] * 3 for _ in range(3)]
    for column in range(3):
        column_length = math.sqrt(sum(matrix[row][column] ** 2 for row in range(3)))
        for row in range(3):
            rotation[row][column] = matrix[row][column] / max(column_length, 1e-8)
    trace = rotation[0][0] + rotation[1][1] + rotation[2][2]
    if trace > 0.0:
        amount = math.sqrt(trace + 1.0) * 2.0
        value = (
            (rotation[2][1] - rotation[1][2]) / amount,
            (rotation[0][2] - rotation[2][0]) / amount,
            (rotation[1][0] - rotation[0][1]) / amount,
            amount * 0.25,
        )
    elif rotation[0][0] > rotation[1][1] and rotation[0][0] > rotation[2][2]:
        amount = math.sqrt(1.0 + rotation[0][0] - rotation[1][1] - rotation[2][2]) * 2.0
        value = (
            amount * 0.25,
            (rotation[0][1] + rotation[1][0]) / amount,
            (rotation[0][2] + rotation[2][0]) / amount,
            (rotation[2][1] - rotation[1][2]) / amount,
        )
    elif rotation[1][1] > rotation[2][2]:
        amount = math.sqrt(1.0 + rotation[1][1] - rotation[0][0] - rotation[2][2]) * 2.0
        value = (
            (rotation[0][1] + rotation[1][0]) / amount,
            amount * 0.25,
            (rotation[1][2] + rotation[2][1]) / amount,
            (rotation[0][2] - rotation[2][0]) / amount,
        )
    else:
        amount = math.sqrt(1.0 + rotation[2][2] - rotation[0][0] - rotation[1][1]) * 2.0
        value = (
            (rotation[0][2] + rotation[2][0]) / amount,
            (rotation[1][2] + rotation[2][1]) / amount,
            amount * 0.25,
            (rotation[1][0] - rotation[0][1]) / amount,
        )
    return quat_normalize(value)


def rotate_xyz(value: Vec3, rotation: Vec3) -> Vec3:
    x, y, z = value
    rx, ry, rz = rotation
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    y, z = y * cx - z * sx, y * sx + z * cx
    x, z = x * cy + z * sy, -x * sy + z * cy
    x, y = x * cz - y * sz, x * sz + y * cz
    return (x, y, z)


def matrix_multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [sum(left[row][axis] * right[axis][column] for axis in range(4)) for column in range(4)]
        for row in range(4)
    ]


def matrix_inverse(matrix: list[list[float]]) -> list[list[float]]:
    augmented = [
        list(matrix[row]) + [1.0 if row == column else 0.0 for column in range(4)]
        for row in range(4)
    ]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            raise ValueError("Cannot invert a singular node matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(4):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(8)
            ]
    return [row[4:] for row in augmented]


def local_matrix(node: dict) -> list[list[float]]:
    if "matrix" in node:
        values = node["matrix"]
        return [[values[column * 4 + row] for column in range(4)] for row in range(4)]
    x, y, z, w = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    sx, sy, sz = node.get("scale", [1.0, 1.0, 1.0])
    tx, ty, tz = node.get("translation", [0.0, 0.0, 0.0])
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    matrix = [
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy), tx],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx), ty],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy), tz],
        [0.0, 0.0, 0.0, 1.0],
    ]
    for row in range(3):
        matrix[row][0] *= sx
        matrix[row][1] *= sy
        matrix[row][2] *= sz
    return matrix


def world_matrices(document: dict) -> list[list[list[float]]]:
    nodes = document["nodes"]
    parents: dict[int, int] = {}
    for parent, node in enumerate(nodes):
        for child in node.get("children", []):
            parents[child] = parent
    cache: dict[int, list[list[float]]] = {}

    def resolve(index: int) -> list[list[float]]:
        if index not in cache:
            matrix = local_matrix(nodes[index])
            if index in parents:
                matrix = matrix_multiply(resolve(parents[index]), matrix)
            cache[index] = matrix
        return cache[index]

    return [resolve(index) for index in range(len(nodes))]


def matrix_position(matrix: list[list[float]]) -> Vec3:
    return (matrix[0][3], matrix[1][3], matrix[2][3])


def weapon_pose_layout(document: dict) -> dict[str, Vec3 | float]:
    nodes = document["nodes"]
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    matrices = world_matrices(document)
    chest = matrix_position(matrices[node_by_name["chest"]])
    shoulder_l = matrix_position(matrices[node_by_name["upper_arm.l"]])
    shoulder_r = matrix_position(matrices[node_by_name["upper_arm.r"]])
    reach_l = length(tuple(nodes[node_by_name["lower_arm.l"]]["translation"])) + length(
        tuple(nodes[node_by_name["hand.l"]]["translation"])
    )
    reach_r = length(tuple(nodes[node_by_name["lower_arm.r"]]["translation"])) + length(
        tuple(nodes[node_by_name["hand.r"]]["translation"])
    )
    reach = (reach_l + reach_r) * 0.5
    weapon_scale = max(0.72, min(1.0, reach / 0.72))

    bow_grip = (
        shoulder_l[0] + abs(shoulder_l[0]) * 0.36,
        chest[1] + 0.30,
        shoulder_l[2] - reach_l * 0.90,
    )
    bow_draw = (
        shoulder_r[0] - abs(shoulder_r[0]) * 0.56,
        chest[1] + 0.34,
        shoulder_r[2] - min(0.18, reach_r * 0.36),
    )
    bow_release = lerp(bow_draw, bow_grip, 0.45)
    bow_half_height = min(0.36, reach * 0.65)

    heavy_trigger = (
        shoulder_r[0] - 0.03,
        chest[1] + 0.14,
        shoulder_r[2] - min(0.25, reach_r * 0.52),
    )
    heavy_foregrip = (
        shoulder_l[0] + 0.06,
        chest[1] + 0.14,
        heavy_trigger[2] - min(0.22, reach_l * 0.46),
    )
    heavy_recoil_trigger = add(heavy_trigger, (0.0, 0.0, 0.07))
    heavy_recoil_foregrip = add(heavy_foregrip, (0.0, 0.0, 0.07))
    return {
        "bowGrip": bow_grip,
        "bowDraw": bow_draw,
        "bowRelease": bow_release,
        "bowHalfHeight": bow_half_height,
        "heavyTrigger": heavy_trigger,
        "heavyForegrip": heavy_foregrip,
        "heavyRecoilTrigger": heavy_recoil_trigger,
        "heavyRecoilForegrip": heavy_recoil_foregrip,
        "weaponScale": weapon_scale,
        "armReach": reach,
    }


def solve_arm_ik(
    document: dict,
    side: str,
    target: Vec3,
    pole: Vec3,
) -> dict[str, Quat]:
    nodes = document["nodes"]
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    parents = {
        child: parent
        for parent, node in enumerate(nodes)
        for child in node.get("children", [])
    }
    matrices = world_matrices(document)
    upper_index = node_by_name[f"upper_arm.{side}"]
    lower_index = node_by_name[f"lower_arm.{side}"]
    hand_index = node_by_name[f"hand.{side}"]
    shoulder = matrix_position(matrices[upper_index])
    upper_length = length(tuple(nodes[lower_index]["translation"]))
    lower_length = length(tuple(nodes[hand_index]["translation"]))
    shoulder_to_target = sub(target, shoulder)
    target_distance = min(length(shoulder_to_target), upper_length + lower_length - 1e-5)
    direction = normalize(shoulder_to_target)
    along = (
        upper_length * upper_length
        - lower_length * lower_length
        + target_distance * target_distance
    ) / (2.0 * target_distance)
    height = math.sqrt(max(upper_length * upper_length - along * along, 0.0))
    pole_perpendicular = sub(pole, scale(direction, dot(pole, direction)))
    if length(pole_perpendicular) < 1e-5:
        pole_perpendicular = cross(direction, (0.0, 1.0, 0.0))
    elbow = add(
        add(shoulder, scale(direction, along)),
        scale(normalize(pole_perpendicular), height),
    )
    upper_direction = normalize(sub(elbow, shoulder))
    lower_direction = normalize(sub(target, elbow))

    upper_bind_world = matrix_quaternion(matrices[upper_index])
    parent_world = matrix_quaternion(matrices[parents[upper_index]])
    upper_bind_axis = normalize(tuple(nodes[lower_index]["translation"]))
    upper_world = quat_normalize(
        quat_multiply(
            quat_from_to(quat_rotate(upper_bind_world, upper_bind_axis), upper_direction),
            upper_bind_world,
        )
    )
    upper_local = quat_normalize(quat_multiply(quat_inverse(parent_world), upper_world))

    lower_bind_local = tuple(nodes[lower_index].get("rotation", [0.0, 0.0, 0.0, 1.0]))
    lower_bind_world = quat_normalize(quat_multiply(upper_world, lower_bind_local))
    lower_bind_axis = normalize(tuple(nodes[hand_index]["translation"]))
    lower_world = quat_normalize(
        quat_multiply(
            quat_from_to(quat_rotate(lower_bind_world, lower_bind_axis), lower_direction),
            lower_bind_world,
        )
    )
    lower_local = quat_normalize(quat_multiply(quat_inverse(upper_world), lower_world))
    return {
        f"upper_arm.{side}": upper_local,
        f"lower_arm.{side}": lower_local,
    }


def animation_duration(document: dict, binary: bytearray, animation: dict) -> float:
    return max(
        max(row[0] for row in read_accessor(document, binary, sampler["input"]))
        for sampler in animation["samplers"]
    )


def sample_animation_value(
    document: dict,
    binary: bytearray,
    animation: dict,
    node_index: int,
    path: str,
    time: float,
) -> tuple[float, ...]:
    for channel in animation["channels"]:
        if channel["target"] != {"node": node_index, "path": path}:
            continue
        sampler = animation["samplers"][channel["sampler"]]
        times = read_accessor(document, binary, sampler["input"])
        values = read_accessor(document, binary, sampler["output"])
        if time <= times[0][0]:
            return values[0]
        if time >= times[-1][0]:
            return values[-1]
        for index in range(len(times) - 1):
            left_time = times[index][0]
            right_time = times[index + 1][0]
            if left_time <= time <= right_time:
                amount = (time - left_time) / max(right_time - left_time, 1e-8)
                if path == "rotation":
                    left = values[index]
                    right = values[index + 1]
                    if sum(a * b for a, b in zip(left, right)) < 0.0:
                        right = tuple(-value for value in right)
                    return quat_normalize(
                        tuple(a + (b - a) * amount for a, b in zip(left, right))  # type: ignore[arg-type]
                    )
                return tuple(
                    left + (right - left) * amount
                    for left, right in zip(values[index], values[index + 1])
                )
    node = document["nodes"][node_index]
    defaults = {
        "rotation": (0.0, 0.0, 0.0, 1.0),
        "translation": (0.0, 0.0, 0.0),
        "scale": (1.0, 1.0, 1.0),
    }
    return tuple(node.get(path, defaults[path]))


def append_animation_channel(
    document: dict,
    binary: bytearray,
    animation: dict,
    node_index: int,
    path: str,
    times: list[float],
    values: list[tuple[float, ...]],
) -> None:
    animation["channels"] = [
        channel
        for channel in animation["channels"]
        if channel["target"] != {"node": node_index, "path": path}
    ]
    time_bytes = struct.pack(f"<{len(times)}f", *times)
    input_accessor = append_accessor(
        document,
        binary,
        time_bytes,
        5126,
        "SCALAR",
        len(times),
    )
    document["accessors"][input_accessor]["min"] = [min(times)]
    document["accessors"][input_accessor]["max"] = [max(times)]
    component_count = len(values[0])
    output_bytes = struct.pack(
        f"<{len(values) * component_count}f",
        *(component for value in values for component in value),
    )
    output_accessor = append_accessor(
        document,
        binary,
        output_bytes,
        5126,
        "VEC4" if component_count == 4 else "VEC3",
        len(values),
    )
    sampler_index = len(animation["samplers"])
    animation["samplers"].append(
        {
            "input": input_accessor,
            "output": output_accessor,
            "interpolation": "LINEAR",
        }
    )
    animation["channels"].append(
        {
            "sampler": sampler_index,
            "target": {"node": node_index, "path": path},
        }
    )


@dataclass
class PrimitiveData:
    positions: list[Vec3] = field(default_factory=list)
    normals: list[Vec3] = field(default_factory=list)
    joints: list[tuple[int, int, int, int]] = field(default_factory=list)
    weights: list[Vec4] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)


class MeshBuilder:
    def __init__(self, *, skinned: bool) -> None:
        self.skinned = skinned
        self.primitives: dict[int, PrimitiveData] = defaultdict(PrimitiveData)

    def vertex(self, material: int, position: Vec3, normal: Vec3, joint: int | None) -> int:
        primitive = self.primitives[material]
        index = len(primitive.positions)
        primitive.positions.append(position)
        primitive.normals.append(normalize(normal))
        if self.skinned:
            if joint is None:
                raise ValueError("Skinned geometry requires a joint")
            primitive.joints.append((joint, 0, 0, 0))
            primitive.weights.append((1.0, 0.0, 0.0, 0.0))
        return index

    def triangle(self, material: int, left: int, middle: int, right: int) -> None:
        self.primitives[material].indices.extend((left, middle, right))

    def mirror_z(self, pivot: float) -> None:
        for primitive in self.primitives.values():
            primitive.positions = [
                (position[0], position[1], pivot * 2.0 - position[2])
                for position in primitive.positions
            ]
            primitive.normals = [
                (normal[0], normal[1], -normal[2])
                for normal in primitive.normals
            ]
            for offset in range(0, len(primitive.indices), 3):
                primitive.indices[offset + 1], primitive.indices[offset + 2] = (
                    primitive.indices[offset + 2],
                    primitive.indices[offset + 1],
                )

    def transform(self, matrix: list[list[float]]) -> None:
        for primitive in self.primitives.values():
            primitive.positions = [
                (
                    sum(matrix[0][axis] * position[axis] for axis in range(3)) + matrix[0][3],
                    sum(matrix[1][axis] * position[axis] for axis in range(3)) + matrix[1][3],
                    sum(matrix[2][axis] * position[axis] for axis in range(3)) + matrix[2][3],
                )
                for position in primitive.positions
            ]
            primitive.normals = [
                normalize(
                    (
                        sum(matrix[0][axis] * normal[axis] for axis in range(3)),
                        sum(matrix[1][axis] * normal[axis] for axis in range(3)),
                        sum(matrix[2][axis] * normal[axis] for axis in range(3)),
                    )
                )
                for normal in primitive.normals
            ]

    def panel(
        self,
        center: Vec3,
        size: tuple[float, float],
        depth: float,
        bevel: float,
        material: int,
        joint: int | None,
    ) -> None:
        """Add a convex chamfered armor plate authored in the XY plane."""
        half_x, half_y = size[0] * 0.5, size[1] * 0.5
        bevel = min(bevel, half_x * 0.45, half_y * 0.45)
        outline = [
            (-half_x + bevel, -half_y),
            (half_x - bevel, -half_y),
            (half_x, -half_y + bevel),
            (half_x, half_y - bevel),
            (half_x - bevel, half_y),
            (-half_x + bevel, half_y),
            (-half_x, half_y - bevel),
            (-half_x, -half_y + bevel),
        ]
        front_z = center[2] + depth * 0.5
        back_z = center[2] - depth * 0.5
        front = [
            self.vertex(material, (center[0] + x, center[1] + y, front_z), (0.0, 0.0, 1.0), joint)
            for x, y in outline
        ]
        back = [
            self.vertex(material, (center[0] + x, center[1] + y, back_z), (0.0, 0.0, -1.0), joint)
            for x, y in outline
        ]
        for index in range(1, len(outline) - 1):
            self.triangle(material, front[0], front[index], front[index + 1])
            self.triangle(material, back[0], back[index + 1], back[index])
        for point, next_point in zip(outline, outline[1:] + outline[:1]):
            edge = (next_point[0] - point[0], next_point[1] - point[1], 0.0)
            normal = normalize((edge[1], -edge[0], 0.0))
            side = [
                self.vertex(material, (center[0] + point[0], center[1] + point[1], back_z), normal, joint),
                self.vertex(material, (center[0] + next_point[0], center[1] + next_point[1], back_z), normal, joint),
                self.vertex(material, (center[0] + next_point[0], center[1] + next_point[1], front_z), normal, joint),
                self.vertex(material, (center[0] + point[0], center[1] + point[1], front_z), normal, joint),
            ]
            self.triangle(material, side[0], side[1], side[2])
            self.triangle(material, side[0], side[2], side[3])

    def box(
        self,
        center: Vec3,
        size: Vec3,
        material: int,
        joint: int | None,
        rotation: Vec3 = (0.0, 0.0, 0.0),
    ) -> None:
        hx, hy, hz = (size[0] * 0.5, size[1] * 0.5, size[2] * 0.5)
        faces = [
            ((1.0, 0.0, 0.0), [(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)]),
            ((-1.0, 0.0, 0.0), [(-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz), (-hx, -hy, -hz)]),
            ((0.0, 1.0, 0.0), [(-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz)]),
            ((0.0, -1.0, 0.0), [(-hx, -hy, hz), (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz)]),
            ((0.0, 0.0, 1.0), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
            ((0.0, 0.0, -1.0), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)]),
        ]
        for normal, corners in faces:
            rotated_normal = rotate_xyz(normal, rotation)
            vertices = [
                self.vertex(material, add(center, rotate_xyz(corner, rotation)), rotated_normal, joint)
                for corner in corners
            ]
            self.triangle(material, vertices[0], vertices[1], vertices[2])
            self.triangle(material, vertices[0], vertices[2], vertices[3])

    def oriented_box_between(
        self,
        start: Vec3,
        end: Vec3,
        width: float,
        depth: float,
        offset: float,
        material: int,
        joint: int | None,
        outward_hint: Vec3 = (0.0, 0.0, 1.0),
    ) -> None:
        """Add a limb plate whose long axis follows the bind-pose bone segment."""
        axis = normalize(sub(end, start))
        outward = sub(outward_hint, scale(axis, dot(outward_hint, axis)))
        if length(outward) < 1e-4:
            outward_hint = (0.0, 1.0, 0.0)
            outward = sub(outward_hint, scale(axis, dot(outward_hint, axis)))
        outward = normalize(outward)
        right = normalize(cross(axis, outward))
        outward = normalize(cross(right, axis))
        center = add(scale(add(start, end), 0.5), scale(outward, offset))
        half_width = width * 0.5
        half_length = length(sub(end, start)) * 0.5
        half_depth = depth * 0.5

        def point(local: Vec3) -> Vec3:
            return add(
                center,
                add(
                    scale(right, local[0]),
                    add(scale(axis, local[1]), scale(outward, local[2])),
                ),
            )

        faces = [
            (right, [(half_width, -half_length, -half_depth), (half_width, half_length, -half_depth), (half_width, half_length, half_depth), (half_width, -half_length, half_depth)]),
            (scale(right, -1.0), [(-half_width, -half_length, half_depth), (-half_width, half_length, half_depth), (-half_width, half_length, -half_depth), (-half_width, -half_length, -half_depth)]),
            (axis, [(-half_width, half_length, -half_depth), (-half_width, half_length, half_depth), (half_width, half_length, half_depth), (half_width, half_length, -half_depth)]),
            (scale(axis, -1.0), [(-half_width, -half_length, half_depth), (-half_width, -half_length, -half_depth), (half_width, -half_length, -half_depth), (half_width, -half_length, half_depth)]),
            (outward, [(-half_width, -half_length, half_depth), (half_width, -half_length, half_depth), (half_width, half_length, half_depth), (-half_width, half_length, half_depth)]),
            (scale(outward, -1.0), [(half_width, -half_length, -half_depth), (-half_width, -half_length, -half_depth), (-half_width, half_length, -half_depth), (half_width, half_length, -half_depth)]),
        ]
        for normal, corners in faces:
            vertices = [self.vertex(material, point(corner), normal, joint) for corner in corners]
            self.triangle(material, vertices[0], vertices[1], vertices[2])
            self.triangle(material, vertices[0], vertices[2], vertices[3])

    def ellipsoid(
        self,
        center: Vec3,
        radii: Vec3,
        material: int,
        joint: int | None,
        segments: int,
        rings: int,
    ) -> None:
        primitive = self.primitives[material]
        top = self.vertex(material, (center[0], center[1] + radii[1], center[2]), (0.0, 1.0, 0.0), joint)
        rows: list[list[int]] = []
        for ring in range(1, rings):
            phi = math.pi * ring / rings
            sin_phi = math.sin(phi)
            cos_phi = math.cos(phi)
            row = []
            for segment in range(segments):
                theta = math.tau * segment / segments
                cos_theta = math.cos(theta)
                sin_theta = math.sin(theta)
                offset = (
                    radii[0] * sin_phi * cos_theta,
                    radii[1] * cos_phi,
                    radii[2] * sin_phi * sin_theta,
                )
                normal = (
                    offset[0] / max(radii[0] * radii[0], 1e-8),
                    offset[1] / max(radii[1] * radii[1], 1e-8),
                    offset[2] / max(radii[2] * radii[2], 1e-8),
                )
                row.append(self.vertex(material, add(center, offset), normal, joint))
            rows.append(row)
        bottom = self.vertex(material, (center[0], center[1] - radii[1], center[2]), (0.0, -1.0, 0.0), joint)
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            self.triangle(material, top, rows[0][next_segment], rows[0][segment])
        for row_index in range(len(rows) - 1):
            upper = rows[row_index]
            lower = rows[row_index + 1]
            for segment in range(segments):
                next_segment = (segment + 1) % segments
                self.triangle(material, upper[segment], upper[next_segment], lower[next_segment])
                self.triangle(material, upper[segment], lower[next_segment], lower[segment])
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            self.triangle(material, bottom, rows[-1][segment], rows[-1][next_segment])
        if len(primitive.positions) >= 65535:
            raise ValueError("A material primitive exceeded the UInt16 vertex limit")

    def cylinder(
        self,
        start: Vec3,
        end: Vec3,
        radius_start: float,
        radius_end: float,
        material: int,
        joint: int | None,
        sides: int,
        *,
        capped: bool = True,
    ) -> None:
        axis = normalize(sub(end, start))
        reference = (0.0, 1.0, 0.0) if abs(axis[1]) < 0.88 else (1.0, 0.0, 0.0)
        right = normalize(cross(axis, reference))
        forward = normalize(cross(right, axis))
        start_ring: list[int] = []
        end_ring: list[int] = []
        for side in range(sides):
            angle = math.tau * side / sides
            radial = add(scale(right, math.cos(angle)), scale(forward, math.sin(angle)))
            start_ring.append(self.vertex(material, add(start, scale(radial, radius_start)), radial, joint))
            end_ring.append(self.vertex(material, add(end, scale(radial, radius_end)), radial, joint))
        for side in range(sides):
            next_side = (side + 1) % sides
            self.triangle(material, start_ring[side], start_ring[next_side], end_ring[next_side])
            self.triangle(material, start_ring[side], end_ring[next_side], end_ring[side])
        if not capped:
            return
        for center, ring, normal, reverse in (
            (start, start_ring, scale(axis, -1.0), True),
            (end, end_ring, axis, False),
        ):
            center_index = self.vertex(material, center, normal, joint)
            cap_ring = []
            radius = radius_start if center == start else radius_end
            for side in range(sides):
                angle = math.tau * side / sides
                radial = add(scale(right, math.cos(angle)), scale(forward, math.sin(angle)))
                cap_ring.append(self.vertex(material, add(center, scale(radial, radius)), normal, joint))
            for side in range(sides):
                next_side = (side + 1) % sides
                if reverse:
                    self.triangle(material, center_index, cap_ring[next_side], cap_ring[side])
                else:
                    self.triangle(material, center_index, cap_ring[side], cap_ring[next_side])


def append_buffer_view(document: dict, binary: bytearray, data: bytes) -> int:
    binary.extend(b"\x00" * ((-len(binary)) % 4))
    byte_offset = len(binary)
    binary.extend(data)
    index = len(document.setdefault("bufferViews", []))
    document["bufferViews"].append(
        {"buffer": 0, "byteOffset": byte_offset, "byteLength": len(data)}
    )
    return index


def prepare_commando_template(document: dict, binary: bytearray) -> tuple[dict, bytearray]:
    result = deepcopy(document)
    result_binary = bytearray(binary)
    result["materials"] = deepcopy(MATERIALS)
    palette_view = append_buffer_view(result, result_binary, palette_png())
    result["images"] = [
        {
            "name": "texSpaceCommandoPalette",
            "bufferView": palette_view,
            "mimeType": "image/png",
        }
    ]
    result["textures"] = [{"sampler": 0, "source": 0}]
    result["samplers"] = [
        {"magFilter": 9728, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}
    ]
    extensions_used = result.setdefault("extensionsUsed", [])
    for extension in (
        "KHR_materials_transmission",
        "KHR_materials_ior",
        "KHR_materials_specular",
    ):
        if extension not in extensions_used:
            extensions_used.append(extension)
    return result, result_binary


def tuck_in_inherited_head(document: dict, binary: bytearray, mesh_index: int) -> None:
    nodes = document["nodes"]
    skin = document["skins"][0]
    joint_by_name = {
        nodes[node_index].get("name"): slot
        for slot, node_index in enumerate(skin["joints"])
    }
    head_joint = joint_by_name["head"]
    helmet_center = (
        HELMET_CENTER_DESIGN[0],
        HELMET_CENTER_DESIGN[1],
        2.0 * MIRROR_Z_PLANE - HELMET_CENTER_DESIGN[2],
    )

    for primitive in document["meshes"][mesh_index]["primitives"]:
        attributes = primitive["attributes"]
        if not {"POSITION", "JOINTS_0", "WEIGHTS_0"}.issubset(attributes):
            continue
        positions = read_accessor(document, binary, attributes["POSITION"])
        joints = read_accessor(document, binary, attributes["JOINTS_0"])
        weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
        tucked_positions = []
        for position, vertex_joints, vertex_weights in zip(positions, joints, weights):
            head_weight = sum(
                float(weight)
                for joint, weight in zip(vertex_joints, vertex_weights)
                if int(joint) == head_joint
            )
            if head_weight > 0.5:
                position = tuple(
                    helmet_center[axis] + (position[axis] - helmet_center[axis]) * 0.7
                    for axis in range(3)
                )
            tucked_positions.append(position)

        position_bytes = struct.pack(
            f"<{len(tucked_positions) * 3}f",
            *(value for position in tucked_positions for value in position),
        )
        position_accessor = append_accessor(
            document,
            binary,
            position_bytes,
            5126,
            "VEC3",
            len(tucked_positions),
            target=34962,
        )
        document["accessors"][position_accessor]["min"] = [
            min(position[axis] for position in tucked_positions) for axis in range(3)
        ]
        document["accessors"][position_accessor]["max"] = [
            max(position[axis] for position in tucked_positions) for axis in range(3)
        ]
        attributes["POSITION"] = position_accessor


def append_primitive(document: dict, binary: bytearray, data: PrimitiveData, material: int, *, skinned: bool) -> dict:
    if not data.positions or not data.indices:
        raise ValueError(f"Material {material} produced no geometry")
    position_bytes = struct.pack(f"<{len(data.positions) * 3}f", *(value for row in data.positions for value in row))
    normal_bytes = struct.pack(f"<{len(data.normals) * 3}f", *(value for row in data.normals for value in row))
    index_bytes = struct.pack(f"<{len(data.indices)}H", *data.indices)
    position_accessor = append_accessor(document, binary, position_bytes, 5126, "VEC3", len(data.positions), target=34962)
    document["accessors"][position_accessor]["min"] = [min(row[axis] for row in data.positions) for axis in range(3)]
    document["accessors"][position_accessor]["max"] = [max(row[axis] for row in data.positions) for axis in range(3)]
    normal_accessor = append_accessor(document, binary, normal_bytes, 5126, "VEC3", len(data.normals), target=34962)
    attributes = {"POSITION": position_accessor, "NORMAL": normal_accessor}
    if skinned:
        joint_bytes = struct.pack(f"<{len(data.joints) * 4}B", *(value for row in data.joints for value in row))
        weight_bytes = struct.pack(f"<{len(data.weights) * 4}f", *(value for row in data.weights for value in row))
        attributes["JOINTS_0"] = append_accessor(document, binary, joint_bytes, 5121, "VEC4", len(data.joints), target=34962)
        attributes["WEIGHTS_0"] = append_accessor(document, binary, weight_bytes, 5126, "VEC4", len(data.weights), target=34962)
    index_accessor = append_accessor(document, binary, index_bytes, 5123, "SCALAR", len(data.indices), target=34963)
    return {"attributes": attributes, "indices": index_accessor, "material": material}


def append_mesh(document: dict, binary: bytearray, name: str, builder: MeshBuilder) -> tuple[int, int, int]:
    primitives, vertex_count, triangle_count = append_builder_primitives(document, binary, builder)
    mesh_index = len(document["meshes"])
    document["meshes"].append({"name": name, "primitives": primitives})
    return mesh_index, vertex_count, triangle_count


def append_builder_primitives(document: dict, binary: bytearray, builder: MeshBuilder) -> tuple[list[dict], int, int]:
    primitives = [
        append_primitive(document, binary, builder.primitives[material], material, skinned=builder.skinned)
        for material in sorted(builder.primitives)
        if builder.primitives[material].indices
    ]
    vertex_count = sum(len(data.positions) for data in builder.primitives.values())
    triangle_count = sum(len(data.indices) // 3 for data in builder.primitives.values())
    return primitives, vertex_count, triangle_count


def mesh_stats(document: dict, mesh_index: int) -> tuple[int, int]:
    vertices = 0
    triangles = 0
    for primitive in document["meshes"][mesh_index]["primitives"]:
        vertices += document["accessors"][primitive["attributes"]["POSITION"]]["count"]
        triangles += document["accessors"][primitive["indices"]]["count"] // 3
    return vertices, triangles


def build_body(document: dict, *, detail: str) -> MeshBuilder:
    nodes = document["nodes"]
    matrices = world_matrices(document)
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    skin = document["skins"][0]
    joint_by_name = {nodes[node_index].get("name"): slot for slot, node_index in enumerate(skin["joints"])}
    position = {name: matrix_position(matrices[node_by_name[name]]) for name in joint_by_name}
    joint = joint_by_name.__getitem__
    segments = 24 if detail == "original" else 12
    rings = 16 if detail == "original" else 8
    sides = 16 if detail == "original" else 8
    builder = MeshBuilder(skinned=True)

    # The recolored Commando mesh supplies the detailed suit, segmented limbs,
    # gloves, boots, and pouches. Added pieces stay close to that silhouette.
    builder.panel((0.0, 1.34, 0.16), (0.38, 0.25), 0.065, 0.045, CERAMIC, joint("chest"))
    builder.panel((0.0, 1.34, 0.198), (0.22, 0.13), 0.018, 0.025, TEAL, joint("chest"))
    builder.panel((0.0, 1.405, 0.211), (0.095, 0.025), 0.012, 0.006, AMBER, joint("chest"))
    builder.panel((-0.145, 1.30, 0.204), (0.045, 0.105), 0.012, 0.01, SECONDARY, joint("chest"))
    builder.panel((0.145, 1.30, 0.204), (0.045, 0.105), 0.012, 0.01, SECONDARY, joint("chest"))
    builder.panel((0.0, 1.145, 0.145), (0.255, 0.105), 0.05, 0.025, CERAMIC, joint("stomach"))
    builder.panel((0.0, 1.145, 0.174), (0.095, 0.032), 0.012, 0.008, TEAL, joint("stomach"))
    for x in (-0.145, 0.145):
        for y in (1.255, 1.425):
            builder.cylinder((x, y, 0.198), (x, y, 0.222), 0.011, 0.011, METAL, joint("chest"), 8)

    # A transparent glass shell surrounds a slightly smaller black-chrome
    # inner visor. The double layer catches bright highlights without exposing
    # the inherited head mesh inside.
    head_center = (position["head"][0], HELMET_CENTER_DESIGN[1], HELMET_CENTER_DESIGN[2])
    inner_radii = tuple(radius * 0.91 for radius in HELMET_RADII)
    builder.ellipsoid(head_center, inner_radii, MIRROR_HELMET, joint("head"), segments, rings)
    builder.ellipsoid(head_center, HELMET_RADII, GLASS_HELMET, joint("head"), segments, rings)
    builder.cylinder((0.0, 1.515, 0.055), (0.0, 1.555, 0.055), 0.16, 0.16, METAL, joint("head"), sides)
    builder.cylinder((-0.185, 1.705, 0.055), (-0.218, 1.705, 0.055), 0.044, 0.038, METAL, joint("head"), sides)
    builder.cylinder((0.185, 1.705, 0.055), (0.218, 1.705, 0.055), 0.044, 0.038, METAL, joint("head"), sides)

    # Close-fitting life support pack and twin vectoring thrusters.
    builder.panel((0.0, 1.33, -0.205), (0.35, 0.31), 0.12, 0.045, METAL, joint("chest"))
    builder.panel((0.0, 1.36, -0.271), (0.18, 0.19), 0.025, 0.03, TEAL, joint("chest"))
    builder.panel((0.0, 1.36, -0.288), (0.055, 0.085), 0.009, 0.01, AMBER, joint("chest"))
    for x in (-0.13, 0.13):
        builder.panel((x, 1.36, -0.276), (0.052, 0.16), 0.014, 0.012, SECONDARY, joint("chest"))
    for side in (-1.0, 1.0):
        x = side * 0.12
        builder.cylinder((x, 1.30, -0.315), (x, 1.12, -0.315), 0.05, 0.058, CERAMIC, joint("chest"), sides)
        builder.cylinder((x, 1.12, -0.315), (x, 1.045, -0.315), 0.058, 0.078, METAL, joint("chest"), sides)
        builder.cylinder((x, 1.05, -0.315), (x, 1.025, -0.315), 0.047, 0.047, AMBER, joint("chest"), sides)

    # Unity Commando faces -Z in the bind pose. The design coordinates above
    # are authored with +Z as front, so mirror only the newly generated armor
    # around the original mesh plane. The inherited body and gun meshes retain
    # their untouched game orientation.
    builder.mirror_z(MIRROR_Z_PLANE)
    return builder


def build_limb_attachments(document: dict) -> list[tuple[int, str, MeshBuilder]]:
    nodes = document["nodes"]
    matrices = world_matrices(document)
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    position = {
        name: matrix_position(matrices[index])
        for name, index in node_by_name.items()
        if name
    }
    attachments: list[tuple[int, str, MeshBuilder]] = []
    for suffix, label in (("l", "L"), ("r", "R")):
        for kind, parent_name, next_name, start_amount, end_amount, width, depth, offset, material in (
            ("Forearm", f"lower_arm.{suffix}", f"hand.{suffix}", 0.18, 0.72, 0.09, 0.026, 0.074, TEAL),
            ("Shin", f"calf.{suffix}", f"foot.{suffix}", 0.16, 0.68, 0.10, 0.03, 0.07, SECONDARY),
        ):
            parent_index = node_by_name[parent_name]
            start = position[parent_name]
            end = position[next_name]
            builder = MeshBuilder(skinned=False)
            builder.oriented_box_between(
                lerp(start, end, start_amount),
                lerp(start, end, end_amount),
                width,
                depth,
                offset,
                material,
                None,
                outward_hint=(0.0, 0.0, -1.0),
            )
            builder.transform(matrix_inverse(matrices[parent_index]))
            attachments.append((parent_index, f"SpaceCommando{kind}{label}", builder))
    return attachments


def build_weapon_attachments(document: dict, *, detail: str) -> list[tuple[int, str, MeshBuilder]]:
    nodes = document["nodes"]
    matrices = world_matrices(document)
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    chest_index = node_by_name["chest"]
    chest_inverse = matrix_inverse(matrices[chest_index])
    sides = 12 if detail == "original" else 8
    attachments: list[tuple[int, str, MeshBuilder]] = []
    layout = weapon_pose_layout(document)
    bow_grip = layout["bowGrip"]
    bow_draw = layout["bowDraw"]
    bow_half_height = float(layout["bowHalfHeight"])
    weapon_scale = float(layout["weaponScale"])
    reach = float(layout["armReach"])

    bow = MeshBuilder(skinned=False)
    bow_curve = 0.07 * weapon_scale
    bow_points = [
        (bow_grip[0], bow_grip[1] + bow_half_height, bow_grip[2] + bow_curve),
        (bow_grip[0], bow_grip[1] + bow_half_height * 0.58, bow_grip[2] - bow_curve * 0.28),
        bow_grip,
        (bow_grip[0], bow_grip[1] - bow_half_height * 0.58, bow_grip[2] - bow_curve * 0.28),
        (bow_grip[0], bow_grip[1] - bow_half_height, bow_grip[2] + bow_curve),
    ]
    for start, end in zip(bow_points, bow_points[1:]):
        bow.cylinder(start, end, 0.016 * weapon_scale, 0.016 * weapon_scale, TEAL, None, sides)
    bow.cylinder(
        add(bow_grip, (0.0, -0.07 * weapon_scale, 0.0)),
        add(bow_grip, (0.0, 0.07 * weapon_scale, 0.0)),
        0.026 * weapon_scale,
        0.026 * weapon_scale,
        METAL,
        None,
        sides,
    )
    bow.transform(chest_inverse)
    attachments.append((chest_index, "SpaceCommandoBow", bow))

    bow_string_drawn = MeshBuilder(skinned=False)
    bow_string_drawn.cylinder(bow_points[0], bow_draw, 0.004, 0.004, CERAMIC, None, 6)
    bow_string_drawn.cylinder(bow_draw, bow_points[-1], 0.004, 0.004, CERAMIC, None, 6)
    bow_string_drawn.transform(chest_inverse)
    attachments.append((chest_index, "SpaceCommandoBowStringDrawn", bow_string_drawn))

    bow_string_rest = MeshBuilder(skinned=False)
    bow_string_rest.cylinder(bow_points[0], bow_points[-1], 0.004, 0.004, CERAMIC, None, 6)
    bow_string_rest.transform(chest_inverse)
    attachments.append((chest_index, "SpaceCommandoBowStringRest", bow_string_rest))

    arrow = MeshBuilder(skinned=False)
    arrow_end = (bow_grip[0], bow_grip[1], bow_draw[2] - max(0.55, reach * 0.90))
    arrow_tip = add(arrow_end, scale(normalize(sub(arrow_end, bow_draw)), 0.08))
    arrow.cylinder(bow_draw, arrow_end, 0.008 * weapon_scale, 0.008 * weapon_scale, SECONDARY, None, 8)
    arrow.cylinder(arrow_end, arrow_tip, 0.022 * weapon_scale, 0.0, METAL, None, 8)
    arrow.panel(lerp(bow_draw, arrow_end, 0.08), (0.07 * weapon_scale, 0.035 * weapon_scale), 0.006, 0.004, TEAL, None)
    arrow.transform(chest_inverse)
    attachments.append((chest_index, "SpaceCommandoArrow", arrow))

    heavy = MeshBuilder(skinned=False)
    heavy_trigger = layout["heavyTrigger"]
    heavy_foregrip = layout["heavyForegrip"]
    heavy_center = (
        (heavy_trigger[0] + heavy_foregrip[0]) * 0.5,
        heavy_trigger[1] + 0.05 * weapon_scale,
        (heavy_trigger[2] + heavy_foregrip[2]) * 0.5 - 0.04 * weapon_scale,
    )
    heavy.box(heavy_center, (0.24 * weapon_scale, 0.19 * weapon_scale, 0.50 * weapon_scale), METAL, None)
    heavy.box(heavy_center, (0.17 * weapon_scale, 0.12 * weapon_scale, 0.54 * weapon_scale), TEAL, None)
    heavy.box(add(heavy_trigger, (-0.03, 0.0, 0.14 * weapon_scale)), (0.16 * weapon_scale, 0.16 * weapon_scale, 0.22 * weapon_scale), SECONDARY, None)
    heavy.box(add(heavy_trigger, (0.0, -0.10 * weapon_scale, 0.02)), (0.075 * weapon_scale, 0.17 * weapon_scale, 0.09 * weapon_scale), SUIT, None, rotation=(0.28, 0.0, 0.0))
    heavy.box(add(heavy_foregrip, (0.0, -0.06 * weapon_scale, 0.0)), (0.07 * weapon_scale, 0.15 * weapon_scale, 0.08 * weapon_scale), SUIT, None, rotation=(-0.22, 0.0, 0.0))
    barrel_start = add(heavy_center, (0.0, 0.02 * weapon_scale, -0.23 * weapon_scale))
    barrel_end = add(barrel_start, (0.0, 0.0, -0.27 * weapon_scale))
    muzzle_end = add(barrel_end, (0.0, 0.0, -0.08 * weapon_scale))
    heavy.cylinder(barrel_start, barrel_end, 0.052 * weapon_scale, 0.042 * weapon_scale, METAL, None, sides)
    heavy.cylinder(barrel_end, muzzle_end, 0.06 * weapon_scale, 0.06 * weapon_scale, SECONDARY, None, sides)
    heavy.panel(add(heavy_center, (0.0, 0.10 * weapon_scale, 0.03 * weapon_scale)), (0.11 * weapon_scale, 0.05 * weapon_scale), 0.015, 0.008, AMBER, None)
    heavy.transform(chest_inverse)
    attachments.append((chest_index, "SpaceCommandoHeavyWeapon", heavy))

    muzzle_flash = MeshBuilder(skinned=False)
    muzzle_flash.cylinder(muzzle_end, add(muzzle_end, (0.0, 0.0, -0.19 * weapon_scale)), 0.09 * weapon_scale, 0.0, AMBER, None, sides)
    muzzle_flash.transform(chest_inverse)
    attachments.append((chest_index, "SpaceCommandoHeavyMuzzleFlash", muzzle_flash))
    return attachments


def add_weapon_animations(
    document: dict,
    binary: bytearray,
    weapon_nodes: dict[str, int],
) -> None:
    nodes = document["nodes"]
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    animation_by_name = {
        animation.get("name"): animation
        for animation in document.get("animations", [])
    }
    layout = weapon_pose_layout(document)
    pistol_nodes = [node_by_name["SpaceCommandoGunL"], node_by_name["SpaceCommandoGunR"]]

    def make_action(
        name: str,
        source_name: str,
        times: list[float],
        poses: list[dict[str, Quat] | None],
        visible_nodes: set[str],
    ) -> dict:
        source = animation_by_name[source_name]
        source_duration = animation_duration(document, binary, source)
        action = deepcopy(source)
        action["name"] = name
        action["extras"] = {
            "source": source_name,
            "originalGameRig": True,
            "proceduralUpperBody": True,
        }
        arm_names = (
            "upper_arm.l",
            "lower_arm.l",
            "upper_arm.r",
            "lower_arm.r",
        )
        arm_nodes = {node_by_name[name] for name in arm_names}
        stabilized_nodes = {
            node_by_name[name]
            for name in (
                "base",
                "stomach",
                "chest",
                "pelvis",
                "thigh.l",
                "calf.l",
                "foot.l",
                "toe.l",
                "thigh.r",
                "calf.r",
                "foot.r",
                "toe.r",
            )
        }
        stabilized_nodes.update(
            index
            for index, node in enumerate(nodes)
            if node.get("name") in {"hand.l", "hand.r"}
            or node.get("name", "").startswith(("finger", "thumb"))
        )
        action["channels"] = [
            channel
            for channel in action.get("channels", [])
            if not (
                (
                    channel.get("target", {}).get("node") in arm_nodes
                    and channel.get("target", {}).get("path") == "rotation"
                )
                or channel.get("target", {}).get("node") in stabilized_nodes
            )
        ]
        for arm_name in arm_names:
            node_index = node_by_name[arm_name]
            start = sample_animation_value(document, binary, source, node_index, "rotation", 0.0)
            end = sample_animation_value(
                document,
                binary,
                source,
                node_index,
                "rotation",
                source_duration,
            )
            values = []
            for index, pose in enumerate(poses):
                if pose is None:
                    values.append(start if index == 0 else end)
                else:
                    values.append(pose[arm_name])
            append_animation_channel(document, binary, action, node_index, "rotation", times, values)

        duration = times[-1]
        for pistol_node in pistol_nodes:
            append_animation_channel(
                document,
                binary,
                action,
                pistol_node,
                "scale",
                [0.0, duration],
                [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)],
            )
        for weapon_name, node_index in weapon_nodes.items():
            visible = weapon_name in visible_nodes
            scale_value = (1.0, 1.0, 1.0) if visible else (0.0, 0.0, 0.0)
            append_animation_channel(
                document,
                binary,
                action,
                node_index,
                "scale",
                [0.0, duration],
                [scale_value, scale_value],
            )
        return action

    bow_draw_pose = {
        **solve_arm_ik(document, "l", layout["bowGrip"], (-1.0, -0.25, 0.1)),
        **solve_arm_ik(document, "r", layout["bowDraw"], (1.0, -0.25, 0.2)),
    }
    bow_release_pose = {
        **solve_arm_ik(document, "l", layout["bowGrip"], (-1.0, -0.25, 0.1)),
        **solve_arm_ik(document, "r", layout["bowRelease"], (1.0, -0.25, 0.2)),
    }
    bow_times = [0.0, 0.16, 0.42, 0.53, 0.68, 0.9]
    bow_action = make_action(
        "SpaceCommando_BowShot",
        "Commando_Idle",
        bow_times,
        [None, bow_draw_pose, bow_draw_pose, bow_release_pose, bow_release_pose, None],
        {"SpaceCommandoBow", "SpaceCommandoBowStringDrawn", "SpaceCommandoArrow"},
    )
    append_animation_channel(
        document,
        binary,
        bow_action,
        weapon_nodes["SpaceCommandoBowStringDrawn"],
        "scale",
        [0.0, 0.50, 0.54, 0.9],
        [(1.0, 1.0, 1.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)],
    )
    append_animation_channel(
        document,
        binary,
        bow_action,
        weapon_nodes["SpaceCommandoBowStringRest"],
        "scale",
        [0.0, 0.50, 0.54, 0.9],
        [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (1.0, 1.0, 1.0)],
    )
    append_animation_channel(
        document,
        binary,
        bow_action,
        weapon_nodes["SpaceCommandoArrow"],
        "translation",
        [0.0, 0.50, 0.68, 0.82, 0.9],
        [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, -0.95), (0.0, 0.0, -1.8), (0.0, 0.0, -1.8)],
    )
    append_animation_channel(
        document,
        binary,
        bow_action,
        weapon_nodes["SpaceCommandoArrow"],
        "scale",
        [0.0, 0.76, 0.82, 0.9],
        [(1.0, 1.0, 1.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)],
    )

    heavy_ready_pose = {
        **solve_arm_ik(document, "l", layout["heavyForegrip"], (-1.0, -0.4, 0.1)),
        **solve_arm_ik(document, "r", layout["heavyTrigger"], (1.0, -0.35, 0.15)),
    }
    heavy_recoil_pose = {
        **solve_arm_ik(document, "l", layout["heavyRecoilForegrip"], (-1.0, -0.4, 0.1)),
        **solve_arm_ik(document, "r", layout["heavyRecoilTrigger"], (1.0, -0.35, 0.15)),
    }
    heavy_times = [0.0, 0.18, 0.42, 0.50, 0.66, 0.74, 1.18, 1.45]
    heavy_action = make_action(
        "SpaceCommando_HeavyFire",
        "Commando_Idle",
        heavy_times,
        [None, heavy_ready_pose, heavy_ready_pose, heavy_recoil_pose, heavy_ready_pose, heavy_recoil_pose, heavy_ready_pose, None],
        {"SpaceCommandoHeavyWeapon"},
    )
    append_animation_channel(
        document,
        binary,
        heavy_action,
        weapon_nodes["SpaceCommandoHeavyMuzzleFlash"],
        "scale",
        [0.0, 0.46, 0.50, 0.55, 0.70, 0.74, 0.79, 1.45],
        [
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (1.3, 1.3, 1.3),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (1.3, 1.3, 1.3),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        ],
    )
    document["animations"].extend((bow_action, heavy_action))


def build_variant(template: Path, destination: Path, *, detail: str) -> dict[str, int]:
    source_document, source_binary = read_glb(template)
    body_node_index = next(index for index, node in enumerate(source_document["nodes"]) if node.get("name") == "CommandoMesh")
    left_gun_index = next(index for index, node in enumerate(source_document["nodes"]) if node.get("name") == "GunMesh.001")
    right_gun_index = next(index for index, node in enumerate(source_document["nodes"]) if node.get("name") == "GunMesh")
    source_body_mesh = source_document["nodes"][body_node_index]["mesh"]
    source_left_mesh = source_document["nodes"][left_gun_index]["mesh"]
    source_right_mesh = source_document["nodes"][right_gun_index]["mesh"]
    document, binary = prepare_commando_template(source_document, source_binary)
    document.setdefault("asset", {}).update(
        {
            "generator": "ror2-wiki Space Commando procedural armor builder",
            "extras": {
                "rig": {
                    "source": "original-game-rig",
                    "originalGameRig": True,
                    "jointCount": 78,
                    "animations": 20,
                },
                "spaceCommando": {
                    "commandoBaseMesh": True,
                    "originalArmor": True,
                    "sourceCharacter": "commando",
                    "design": "sealed astronaut armor with compact twin-thruster pack",
                    "variant": detail,
                },
            },
        }
    )
    document["skins"][0]["name"] = "SpaceCommandoOriginalGameRig"
    document["skins"][0].setdefault("extras", {})["originalGameRig"] = True

    body_mesh = document["meshes"][source_body_mesh]
    body_mesh["name"] = "SpaceCommandoMesh"
    for primitive in body_mesh["primitives"]:
        primitive["material"] = BASE
    tuck_in_inherited_head(document, binary, source_body_mesh)
    base_body_vertices, base_body_triangles = mesh_stats(document, source_body_mesh)
    armor_primitives, armor_vertices, armor_triangles = append_builder_primitives(
        document,
        binary,
        build_body(document, detail=detail),
    )
    body_mesh["primitives"].extend(armor_primitives)

    left_mesh = document["meshes"][source_left_mesh]
    left_mesh["name"] = "SpaceCommandoGunL"
    for primitive in left_mesh["primitives"]:
        primitive["material"] = BASE
    right_mesh = document["meshes"][source_right_mesh]
    right_mesh["name"] = "SpaceCommandoGunR"
    for primitive in right_mesh["primitives"]:
        primitive["material"] = BASE
    left_vertices, left_triangles = mesh_stats(document, source_left_mesh)
    right_vertices, right_triangles = mesh_stats(document, source_right_mesh)

    body_node = document["nodes"][body_node_index]
    body_node["name"] = "SpaceCommandoMesh"
    body_node["mesh"] = source_body_mesh
    body_node["skin"] = 0
    left_node = document["nodes"][left_gun_index]
    left_node["name"] = "SpaceCommandoGunL"
    left_node["mesh"] = source_left_mesh
    right_node = document["nodes"][right_gun_index]
    right_node["name"] = "SpaceCommandoGunR"
    right_node["mesh"] = source_right_mesh

    attachment_vertices = 0
    attachment_triangles = 0
    for parent_index, name, builder in build_limb_attachments(document):
        mesh_index, vertices, triangles = append_mesh(document, binary, f"{name}Mesh", builder)
        node_index = len(document["nodes"])
        document["nodes"].append({"name": name, "mesh": mesh_index})
        document["nodes"][parent_index].setdefault("children", []).append(node_index)
        attachment_vertices += vertices
        attachment_triangles += triangles

    weapon_nodes: dict[str, int] = {}
    for parent_index, name, builder in build_weapon_attachments(document, detail=detail):
        mesh_index, vertices, triangles = append_mesh(document, binary, f"{name}Mesh", builder)
        node_index = len(document["nodes"])
        document["nodes"].append(
            {
                "name": name,
                "mesh": mesh_index,
                "scale": [0.0, 0.0, 0.0],
            }
        )
        document["nodes"][parent_index].setdefault("children", []).append(node_index)
        weapon_nodes[name] = node_index
        attachment_vertices += vertices
        attachment_triangles += triangles

    add_weapon_animations(document, binary, weapon_nodes)

    write_glb(destination, document, binary)
    return {
        "vertices": base_body_vertices + armor_vertices + left_vertices + right_vertices + attachment_vertices,
        "triangles": base_body_triangles + armor_triangles + left_triangles + right_triangles + attachment_triangles,
        "bodyTriangles": base_body_triangles + armor_triangles,
        "gunTriangles": left_triangles + right_triangles,
        "attachmentTriangles": attachment_triangles,
        "animationCount": len(document["animations"]),
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-original", type=Path, default=repo_root / "models/survivors/commando-original.glb")
    parser.add_argument("--original", type=Path, default=repo_root / "generated/templates/space-commando-original.glb")
    parser.add_argument("--low", type=Path, default=repo_root / "generated/templates/space-commando-low.glb")
    parser.add_argument("--stats", type=Path, default=repo_root / "generated/templates/space-commando.stats.json")
    args = parser.parse_args()

    original = build_variant(args.template_original, args.original, detail="original")
    # Both variants retain the same Unity-space rig. The checked-in Commando
    # low template has Blender-exported bone axes, which are valid for its own
    # mesh but unsuitable as a bind template for newly generated geometry.
    low = build_variant(args.template_original, args.low, detail="low")
    stats = {
        "sourceTriangles": original["triangles"],
        "lowTriangles": low["triangles"],
        "sourceVertices": original["vertices"],
        "lowVertices": low["vertices"],
        "sourceSizeKB": round(args.original.stat().st_size / 1024),
        "lowSizeKB": round(args.low.stat().st_size / 1024),
        "jointCount": 78,
        "animationCount": original["animationCount"],
    }
    args.stats.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(
        f"Built Space Commando: {stats['sourceTriangles']} -> {stats['lowTriangles']} triangles, "
        f"{stats['jointCount']} joints, {stats['animationCount']} animations"
    )


if __name__ == "__main__":
    main()
