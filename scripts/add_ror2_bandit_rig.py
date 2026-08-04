"""Add a reconstructed humanoid rig to the baked Risk of Rain 2 Bandit GLB.

The original survivor pipeline deliberately bakes Unity skinning to OBJ, so
the repository GLBs no longer contain the game's joints or weights. This tool
adds a documented modeling rig to the existing bind pose without claiming to
reproduce the original runtime skeleton.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import struct
import tempfile


JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

COMPONENT_FORMAT = {
    5120: "b",
    5121: "B",
    5122: "h",
    5123: "H",
    5125: "I",
    5126: "f",
}
COMPONENT_COUNT = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT4": 16,
}


@dataclass(frozen=True)
class Joint:
    name: str
    parent: int | None
    world_position: tuple[float, float, float]


# Positions are measured from the checked-in Bandit SelectPoseIdle mesh in the
# viewer's Y-up space. The arm and leg chains follow the displayed bent pose.
JOINTS = [
    Joint("RigRoot", None, (0.0, 0.0, 0.0)),
    Joint("Hips", 0, (0.10, 0.96, -0.05)),
    Joint("Spine", 1, (0.10, 1.13, -0.04)),
    Joint("Chest", 2, (0.10, 1.39, -0.02)),
    Joint("Neck", 3, (0.10, 1.58, 0.02)),
    Joint("Head", 4, (0.10, 1.71, 0.05)),
    Joint("UpperArm.L", 3, (-0.16, 1.39, -0.02)),
    Joint("Forearm.L", 6, (-0.32, 1.12, -0.02)),
    Joint("Hand.L", 7, (-0.35, 0.81, -0.01)),
    Joint("UpperArm.R", 3, (0.29, 1.39, 0.03)),
    Joint("Forearm.R", 9, (0.47, 1.20, 0.06)),
    Joint("Hand.R", 10, (0.48, 1.39, 0.23)),
    Joint("UpperLeg.L", 1, (0.00, 0.92, 0.01)),
    Joint("LowerLeg.L", 12, (-0.18, 0.52, 0.06)),
    Joint("Foot.L", 13, (-0.29, 0.12, 0.12)),
    Joint("Toe.L", 14, (-0.34, 0.02, 0.25)),
    Joint("UpperLeg.R", 1, (0.20, 0.92, -0.06)),
    Joint("LowerLeg.R", 16, (0.30, 0.52, -0.04)),
    Joint("Foot.R", 17, (0.42, 0.12, -0.02)),
    Joint("Toe.R", 18, (0.50, 0.02, 0.07)),
    Joint("CapeRoot", 3, (0.10, 1.40, -0.20)),
    Joint("CapeMid", 20, (0.10, 0.95, -0.30)),
    Joint("CapeTip.L", 21, (-0.14, 0.40, -0.34)),
    Joint("CapeTip.R", 21, (0.34, 0.40, -0.34)),
]


CONTROL_SEGMENTS = {
    1: (JOINTS[1].world_position, JOINTS[2].world_position),
    2: (JOINTS[2].world_position, JOINTS[3].world_position),
    3: (JOINTS[3].world_position, JOINTS[4].world_position),
    4: (JOINTS[4].world_position, JOINTS[5].world_position),
    5: (JOINTS[5].world_position, (0.10, 1.92, 0.05)),
    6: (JOINTS[6].world_position, JOINTS[7].world_position),
    7: (JOINTS[7].world_position, JOINTS[8].world_position),
    8: (JOINTS[8].world_position, (-0.35, 0.70, 0.00)),
    9: (JOINTS[9].world_position, JOINTS[10].world_position),
    10: (JOINTS[10].world_position, JOINTS[11].world_position),
    11: (JOINTS[11].world_position, (0.48, 1.49, 0.28)),
    12: (JOINTS[12].world_position, JOINTS[13].world_position),
    13: (JOINTS[13].world_position, JOINTS[14].world_position),
    14: (JOINTS[14].world_position, JOINTS[15].world_position),
    15: (JOINTS[15].world_position, (-0.35, 0.01, 0.33)),
    16: (JOINTS[16].world_position, JOINTS[17].world_position),
    17: (JOINTS[17].world_position, JOINTS[18].world_position),
    18: (JOINTS[18].world_position, JOINTS[19].world_position),
    19: (JOINTS[19].world_position, (0.57, 0.01, 0.12)),
    20: (JOINTS[20].world_position, JOINTS[21].world_position),
    21: (JOINTS[21].world_position, (0.10, 0.58, -0.34)),
    22: (JOINTS[22].world_position, (-0.20, 0.28, -0.36)),
    23: (JOINTS[23].world_position, (0.42, 0.28, -0.36)),
}


CANDIDATES = {
    "head": [4, 5],
    "arm_l": [6, 7, 8],
    "arm_r": [9, 10, 11],
    "leg_l": [12, 13, 14, 15],
    "leg_r": [16, 17, 18, 19],
    "lower_body": [1, 12, 13, 16, 17, 20, 21, 22, 23],
    "body": [1, 2, 3, 4, 5, 20, 21, 22, 23],
}


def read_glb(path: Path) -> tuple[dict, bytearray]:
    data = path.read_bytes()
    if len(data) < 20 or struct.unpack_from("<I", data, 0)[0] != 0x46546C67:
        raise ValueError(f"Not a GLB file: {path}")
    document = None
    binary = bytearray()
    offset = 12
    while offset < len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8 : offset + 8 + chunk_length]
        offset += 8 + chunk_length
        if chunk_type == JSON_CHUNK:
            document = json.loads(chunk.rstrip(b"\x00 "))
        elif chunk_type == BIN_CHUNK:
            binary.extend(chunk)
    if document is None:
        raise ValueError(f"GLB has no JSON chunk: {path}")
    return document, binary


def write_glb(path: Path, document: dict, binary: bytearray) -> None:
    document["buffers"][0]["byteLength"] = len(binary)
    json_data = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_data += b" " * ((-len(json_data)) % 4)
    bin_data = bytes(binary) + b"\x00" * ((-len(binary)) % 4)
    total_length = 12 + 8 + len(json_data) + 8 + len(bin_data)
    payload = bytearray(struct.pack("<III", 0x46546C67, 2, total_length))
    payload.extend(struct.pack("<II", len(json_data), JSON_CHUNK))
    payload.extend(json_data)
    payload.extend(struct.pack("<II", len(bin_data), BIN_CHUNK))
    payload.extend(bin_data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def read_accessor(document: dict, binary: bytearray, index: int) -> list[tuple[float, ...]]:
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    component_format = COMPONENT_FORMAT[accessor["componentType"]]
    component_count = COMPONENT_COUNT[accessor["type"]]
    fmt = "<" + component_format * component_count
    element_size = struct.calcsize(fmt)
    stride = view.get("byteStride", element_size)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    return [struct.unpack_from(fmt, binary, start + row * stride) for row in range(accessor["count"])]


def append_accessor(
    document: dict,
    binary: bytearray,
    data: bytes,
    component_type: int,
    accessor_type: str,
    count: int,
    *,
    target: int | None = None,
) -> int:
    binary.extend(b"\x00" * ((-len(binary)) % 4))
    byte_offset = len(binary)
    binary.extend(data)
    view = {"buffer": 0, "byteOffset": byte_offset, "byteLength": len(data)}
    if target is not None:
        view["target"] = target
    view_index = len(document.setdefault("bufferViews", []))
    document["bufferViews"].append(view)
    accessor_index = len(document.setdefault("accessors", []))
    document["accessors"].append(
        {
            "bufferView": view_index,
            "componentType": component_type,
            "count": count,
            "type": accessor_type,
        }
    )
    return accessor_index


def quat_rotate(quaternion: tuple[float, float, float, float], value: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z, w = quaternion
    vx, vy, vz = value
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + y * tz - z * ty,
        vy + w * ty + z * tx - x * tz,
        vz + w * tz + x * ty - y * tx,
    )


def transform_point(node: dict, value: tuple[float, float, float]) -> tuple[float, float, float]:
    if "matrix" in node:
        m = node["matrix"]
        x, y, z = value
        return (
            m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14],
        )
    scale = tuple(node.get("scale", [1.0, 1.0, 1.0]))
    rotation = tuple(node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
    translation = tuple(node.get("translation", [0.0, 0.0, 0.0]))
    scaled = (value[0] * scale[0], value[1] * scale[1], value[2] * scale[2])
    rotated = quat_rotate(rotation, scaled)
    return tuple(rotated[i] + translation[i] for i in range(3))


def inverse_transform_point(node: dict, value: tuple[float, float, float]) -> tuple[float, float, float]:
    if "matrix" in node:
        raise ValueError("Bandit rigging expects a TRS mesh node, not a matrix node")
    translation = tuple(node.get("translation", [0.0, 0.0, 0.0]))
    rotation = tuple(node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
    scale = tuple(node.get("scale", [1.0, 1.0, 1.0]))
    shifted = tuple(value[i] - translation[i] for i in range(3))
    inverse_rotation = (-rotation[0], -rotation[1], -rotation[2], rotation[3])
    unrotated = quat_rotate(inverse_rotation, shifted)
    return tuple(unrotated[i] / scale[i] for i in range(3))


def distance_to_segment(
    point: tuple[float, float, float],
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> float:
    delta = tuple(end[i] - start[i] for i in range(3))
    offset = tuple(point[i] - start[i] for i in range(3))
    length_squared = sum(value * value for value in delta)
    factor = 0.0 if not length_squared else max(0.0, min(1.0, sum(offset[i] * delta[i] for i in range(3)) / length_squared))
    closest = tuple(start[i] + factor * delta[i] for i in range(3))
    return math.sqrt(sum((point[i] - closest[i]) ** 2 for i in range(3)))


def component_categories(
    positions: list[tuple[float, float, float]],
    indices: list[tuple[float, ...]],
) -> list[str]:
    parent = list(range(len(positions)))
    sizes = [1] * len(positions)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if sizes[left_root] < sizes[right_root]:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        sizes[left_root] += sizes[right_root]

    flat_indices = [int(value[0]) for value in indices]
    for offset in range(0, len(flat_indices) - 2, 3):
        first, second, third = flat_indices[offset : offset + 3]
        union(first, second)
        union(second, third)

    members: dict[int, list[int]] = {}
    for vertex in range(len(positions)):
        members.setdefault(find(vertex), []).append(vertex)

    categories = ["body"] * len(positions)
    for vertices in members.values():
        points = [positions[index] for index in vertices]
        minimum = tuple(min(point[axis] for point in points) for axis in range(3))
        maximum = tuple(max(point[axis] for point in points) for axis in range(3))
        mean_x = sum(point[0] for point in points) / len(points)
        if minimum[1] > 1.53:
            category = "head"
        elif maximum[1] < 0.68:
            category = "leg_l" if mean_x < 0.08 else "leg_r"
        elif maximum[0] < -0.08 and minimum[1] > 0.64 and maximum[1] < 1.56:
            category = "arm_l"
        elif minimum[0] > 0.23 and minimum[1] > 0.88 and maximum[1] < 1.56:
            category = "arm_r"
        elif maximum[1] < 1.18:
            category = "lower_body"
        else:
            category = "body"
        for vertex in vertices:
            categories[vertex] = category
    return categories


def vertex_weights(
    point: tuple[float, float, float],
    category: str,
) -> tuple[tuple[int, int, int, int], tuple[float, float, float, float]]:
    candidates = list(CANDIDATES[category])
    if category == "body" and not (point[2] < -0.12 and point[1] < 1.50):
        candidates = [1, 2, 3, 4, 5]
    if category == "lower_body" and point[2] > -0.12:
        candidates = [1, 12, 13, 16, 17]
    scored = []
    for joint_index in candidates:
        start, end = CONTROL_SEGMENTS[joint_index]
        distance = distance_to_segment(point, start, end)
        scored.append((1.0 / (distance * distance + 0.0025), joint_index))
    selected = sorted(scored, reverse=True)[:4]
    total = sum(score for score, _ in selected)
    joint_values = [joint for _, joint in selected]
    weight_values = [score / total for score, _ in selected]
    while len(joint_values) < 4:
        joint_values.append(0)
        weight_values.append(0.0)
    return tuple(joint_values), tuple(weight_values)


def rig_document(document: dict, binary: bytearray) -> dict[str, int]:
    if document.get("skins") or any("skin" in node for node in document.get("nodes", [])):
        raise ValueError("Input GLB is already skinned; refusing to stack another rig")
    mesh_nodes = [(index, node) for index, node in enumerate(document.get("nodes", [])) if "mesh" in node]
    if len(mesh_nodes) != 1:
        raise ValueError(f"Expected one Bandit mesh node, found {len(mesh_nodes)}")
    _mesh_node_index, mesh_node = mesh_nodes[0]
    mesh = document["meshes"][mesh_node["mesh"]]
    vertex_total = 0

    for primitive in mesh["primitives"]:
        attributes = primitive["attributes"]
        positions_local = [tuple(value) for value in read_accessor(document, binary, attributes["POSITION"])]
        positions_world = [transform_point(mesh_node, value) for value in positions_local]
        indices = read_accessor(document, binary, primitive["indices"])
        categories = component_categories(positions_world, indices)
        joints_data = bytearray()
        weights_data = bytearray()
        for point, category in zip(positions_world, categories):
            joint_values, weight_values = vertex_weights(point, category)
            joints_data.extend(struct.pack("<4B", *joint_values))
            weights_data.extend(struct.pack("<4f", *weight_values))
        attributes["JOINTS_0"] = append_accessor(
            document,
            binary,
            bytes(joints_data),
            5121,
            "VEC4",
            len(positions_local),
            target=34962,
        )
        attributes["WEIGHTS_0"] = append_accessor(
            document,
            binary,
            bytes(weights_data),
            5126,
            "VEC4",
            len(positions_local),
            target=34962,
        )
        vertex_total += len(positions_local)

    absolute_local_positions = [inverse_transform_point(mesh_node, joint.world_position) for joint in JOINTS]
    joint_node_indices = []
    for joint_index, joint in enumerate(JOINTS):
        absolute = absolute_local_positions[joint_index]
        if joint.parent is None:
            translation = absolute
        else:
            parent_position = absolute_local_positions[joint.parent]
            translation = tuple(absolute[axis] - parent_position[axis] for axis in range(3))
        node = {
            "name": joint.name,
            "translation": [round(value, 8) for value in translation],
        }
        joint_node_indices.append(len(document["nodes"]))
        document["nodes"].append(node)

    for joint_index, joint in enumerate(JOINTS):
        children = [
            joint_node_indices[index]
            for index, candidate in enumerate(JOINTS)
            if candidate.parent == joint_index
        ]
        if children:
            document["nodes"][joint_node_indices[joint_index]]["children"] = children

    inverse_bind_data = bytearray()
    for x, y, z in absolute_local_positions:
        inverse_bind_data.extend(
            struct.pack(
                "<16f",
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                -x, -y, -z, 1.0,
            )
        )
    inverse_bind_accessor = append_accessor(
        document,
        binary,
        bytes(inverse_bind_data),
        5126,
        "MAT4",
        len(JOINTS),
    )
    document["skins"] = [
        {
            "name": "BanditReconstructedRig",
            "inverseBindMatrices": inverse_bind_accessor,
            "skeleton": joint_node_indices[0],
            "joints": joint_node_indices,
            "extras": {
                "rigSource": "reconstructed-humanoid-v1",
                "originalGameRig": False,
            },
        }
    ]
    mesh_node["skin"] = 0
    mesh_node.setdefault("children", []).append(joint_node_indices[0])
    document.setdefault("asset", {}).setdefault("extras", {})["rig"] = {
        "source": "reconstructed-humanoid-v1",
        "jointCount": len(JOINTS),
        "animations": 0,
        "note": "Modeling rig reconstructed from the baked Bandit SelectPoseIdle mesh; not the original game rig.",
    }
    return {"vertices": vertex_total, "joints": len(JOINTS)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    document, binary = read_glb(args.source)
    stats = rig_document(document, binary)
    if args.source.resolve() == args.destination.resolve():
        with tempfile.NamedTemporaryFile(
            prefix=f".{args.destination.stem}-",
            suffix=".glb",
            dir=args.destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            write_glb(temporary_path, document, binary)
            temporary_path.replace(args.destination)
        finally:
            temporary_path.unlink(missing_ok=True)
    else:
        write_glb(args.destination, document, binary)
    print(
        f"Rigged {args.destination}: {stats['vertices']} vertices, "
        f"{stats['joints']} reconstructed joints"
    )


if __name__ == "__main__":
    main()
