"""Build the purchased Sci-Fi character and weapon packs for Ultimate Pack."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import re
import shutil
import struct
from collections import defaultdict
from pathlib import Path

import numpy as np

from add_ror2_bandit_rig import append_accessor, read_accessor
from build_ultimate_pack import (
    LOW_POLY_PERSON_ACTIONS,
    PACKS,
    ROOT,
    TEXTURES_ROOT,
    MODELS_ROOT,
    animation_kind,
    append_generic_animations,
    apply_unity_materials,
    base_entry,
    convert_fbx,
    load_prefab_materials,
    load_unity_materials,
    load_unity_textures,
    report_for_glb,
    slugify,
    write_catalog,
)
from merge_glb_animations import read_glb, write_glb
from retarget_mixamo_animation import (
    Transform,
    animation_tracks,
    compose,
    hierarchy,
    inverse_transform_point,
    node_transform,
    packed_floats,
    quat_inverse,
    quat_multiply,
    quat_normalize,
    require_finite_rows,
    retarget_animation,
    sample_track,
    skeleton_scale,
    vec_add,
    vec_scale,
    vec_subtract,
    world_transforms,
)


CATALOG_PATH = ROOT / "games" / "ultimate-pack" / "catalog.json"
WARNINGS_PATH = CATALOG_PATH.parent / "import-warnings.json"
ACTION_SOURCE = ROOT / "models" / "ultimate-pack" / "low-poly-10" / "m" / "people-man-casual.glb"
MIXAMO_ACTION_ROOT = ROOT / "generated" / "sci-fi-civilians-mixamo"
DEFAULT_CONVERTER = Path(
    r"C:\Users\xd199\Documents\Codex\2026-07-28\steam-wiki-d\work\tools\fbx2gltf"
    r"\FBX2glTF-windows-x86_64\FBX2glTF-windows-x86_64.exe"
)

SOURCE_TO_SIDEKICK = (
    ("Hips", "pelvis"),
    ("Spine", "spine_01"),
    ("Spine1", "spine_02"),
    ("Spine2", "spine_03"),
    ("Neck", "neck_01"),
    ("Head", "head"),
    ("LeftShoulder", "clavicle_l"),
    ("LeftArm", "upperarm_l"),
    ("LeftForeArm", "lowerarm_l"),
    ("LeftHand", "hand_l"),
    ("RightShoulder", "clavicle_r"),
    ("RightArm", "upperarm_r"),
    ("RightForeArm", "lowerarm_r"),
    ("RightHand", "hand_r"),
    ("LeftUpLeg", "thigh_l"),
    ("LeftLeg", "calf_l"),
    ("LeftFoot", "foot_l"),
    ("LeftToeBase", "ball_l"),
    ("RightUpLeg", "thigh_r"),
    ("RightLeg", "calf_r"),
    ("RightFoot", "foot_r"),
    ("RightToeBase", "ball_r"),
)

MIXAMO_TO_SIDEKICK = (
    ("mixamorig:Hips", "pelvis"),
    ("mixamorig:Spine", "spine_01"),
    ("mixamorig:Spine1", "spine_02"),
    ("mixamorig:Spine2", "spine_03"),
    ("mixamorig:Neck", "neck_01"),
    ("mixamorig:Head", "head"),
    ("mixamorig:LeftShoulder", "clavicle_l"),
    ("mixamorig:LeftArm", "upperarm_l"),
    ("mixamorig:LeftForeArm", "lowerarm_l"),
    ("mixamorig:LeftHand", "hand_l"),
    ("mixamorig:RightShoulder", "clavicle_r"),
    ("mixamorig:RightArm", "upperarm_r"),
    ("mixamorig:RightForeArm", "lowerarm_r"),
    ("mixamorig:RightHand", "hand_r"),
    ("mixamorig:LeftUpLeg", "thigh_l"),
    ("mixamorig:LeftLeg", "calf_l"),
    ("mixamorig:LeftFoot", "foot_l"),
    ("mixamorig:LeftToeBase", "ball_l"),
    ("mixamorig:RightUpLeg", "thigh_r"),
    ("mixamorig:RightLeg", "calf_r"),
    ("mixamorig:RightFoot", "foot_r"),
    ("mixamorig:RightToeBase", "ball_r"),
)

MIXAMO_ACTIONS = (
    ("Mixamo_RifleFireStanding", MIXAMO_ACTION_ROOT / "rifle-fire-standing.glb"),
    ("Mixamo_RifleRunFire", MIXAMO_ACTION_ROOT / "rifle-run-fire-inplace.glb"),
)

MIXAMO_ACTION_LABELS = {
    "Mixamo_RifleReady": "步枪持枪待机",
    "Mixamo_RifleFireStanding": "步枪站立射击",
    "Mixamo_RifleRunFire": "步枪跑动射击",
}

RIFLE_EQUIPMENT_TRANSFORMS = {
    "Mixamo_RifleReady": {
        "position": [-0.1018, -0.0042, 0.0798],
        "rotationDeg": [186.663, -61.199, -84.155],
        "scale": [1.0, 1.0, 1.0],
    },
    "Mixamo_RifleFireStanding": {
        "position": [-0.1018, -0.0042, 0.0798],
        "rotationDeg": [186.663, -61.199, -84.155],
        "scale": [1.0, 1.0, 1.0],
    },
    "Mixamo_RifleRunFire": {
        "position": [-0.0820, -0.0084, 0.0870],
        "rotationDeg": [189.551, -47.649, -82.912],
        "scale": [1.0, 1.0, 1.0],
    },
}

RIFLE_GRIP_CURL_DEGREES = {
    **{
        f"{finger}_{segment}_{side}": angle
        for side in ("l", "r")
        for finger in ("middle", "ring", "pinky")
        for segment, angle in (("01", 48.0), ("02", 62.0), ("03", 42.0))
    },
    **{
        f"index_{segment}_l": angle
        for segment, angle in (("01", 38.0), ("02", 50.0), ("03", 32.0))
    },
    **{
        f"index_{segment}_r": angle
        for segment, angle in (("01", 8.0), ("02", 16.0), ("03", 10.0))
    },
    **{
        f"thumb_{segment}_{side}": angle
        for side in ("l", "r")
        for segment, angle in (("01", 18.0), ("02", 28.0), ("03", 20.0))
    },
}

COLLISION_PREFIXES = ("UCX_", "UBX_", "USP_", "UCP_")

EQUIPMENT_OPTIONS = [
    ("sci-fi-battle-weapons-scifirifle01-1", "科幻步枪 01", "rifle"),
    ("sci-fi-battle-weapons-scifipistol01-1", "科幻手枪 01", "pistol"),
    ("sci-fi-battle-weapons-scifismg01-1", "科幻冲锋枪 01", "rifle"),
    ("sci-fi-battle-weapons-scifishotgun01-1", "科幻霰弹枪 01", "rifle"),
    ("sci-fi-battle-weapons-scifisniperrifle01-1", "科幻狙击枪 01", "rifle"),
    ("sci-fi-battle-weapons-scifigrenadelauncher01-1", "科幻榴弹发射器 01", "heavy"),
    ("sci-fi-battle-weapons-scifirocketlauncher01-1", "科幻火箭筒 01", "heavy"),
]


def equipment_options() -> list[dict]:
    options = []
    for slug, name, family in EQUIPMENT_OPTIONS:
        transform = RIFLE_EQUIPMENT_TRANSFORMS["Mixamo_RifleReady"] if slug == EQUIPMENT_OPTIONS[0][0] else {
            "position": [0.0, 0.0, 0.0],
            "rotationDeg": [0.0, 90.0, 180.0],
            "scale": [1.0, 1.0, 1.0],
        }
        options.append({
            "slug": slug,
            "name": name,
            "family": family,
            **deepcopy(transform),
        })
    return options


def yaml_documents(text: str):
    pattern = re.compile(r"^--- !u!(\d+) &(-?\d+)\r?\n(.*?)(?=^--- !u!|\Z)", re.MULTILINE | re.DOTALL)
    yield from ((int(match.group(1)), match.group(2), match.group(3)) for match in pattern.finditer(text))


def parse_vector(body: str, key: str, defaults: tuple[float, ...]) -> list[float]:
    match = re.search(rf"^  {re.escape(key)}: \{{([^}}]+)\}}$", body, re.MULTILINE)
    if not match:
        return list(defaults)
    values = {name: float(value) for name, value in re.findall(r"([xyzw]): ([-+0-9.eE]+)", match.group(1))}
    names = "xyzw"[: len(defaults)]
    return [values.get(name, defaults[index]) for index, name in enumerate(names)]


def unity_to_gltf_translation(value: list[float]) -> list[float]:
    return [-value[0], value[1], value[2]]


def unity_to_gltf_rotation(value: list[float]) -> list[float]:
    return [value[0], -value[1], -value[2], value[3]]


def quaternion_matrix(value: list[float]) -> np.ndarray:
    x, y, z, w = value
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 1e-12:
        x = y = z = 0.0
        w = 1.0
    else:
        x, y, z, w = (component / length for component in (x, y, z, w))
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def local_matrix(node: dict) -> np.ndarray:
    matrix = np.identity(4, dtype=np.float64)
    scale = np.array(node.get("scale", [1.0, 1.0, 1.0]), dtype=np.float64)
    matrix[:3, :3] = quaternion_matrix(node.get("rotation", [0.0, 0.0, 0.0, 1.0])) @ np.diag(scale)
    matrix[:3, 3] = node.get("translation", [0.0, 0.0, 0.0])
    return matrix


def prefab_rig(prefab_path: Path) -> dict:
    text = prefab_path.read_text(encoding="utf-8", errors="ignore")
    documents = list(yaml_documents(text))
    game_objects = {}
    transforms = {}
    renderer = None
    transform_by_object = {}
    order = []
    for class_id, file_id, body in documents:
        if class_id == 1:
            name = re.search(r"^  m_Name: (.+)$", body, re.MULTILINE)
            if name:
                game_objects[file_id] = name.group(1).strip()
        elif class_id == 4:
            object_match = re.search(r"^  m_GameObject: \{fileID: (-?\d+)\}$", body, re.MULTILINE)
            father_match = re.search(r"^  m_Father: \{fileID: (-?\d+)\}$", body, re.MULTILINE)
            if not object_match or not father_match:
                continue
            transforms[file_id] = {
                "object": object_match.group(1),
                "father": father_match.group(1),
                "translation": unity_to_gltf_translation(parse_vector(body, "m_LocalPosition", (0.0, 0.0, 0.0))),
                "rotation": unity_to_gltf_rotation(parse_vector(body, "m_LocalRotation", (0.0, 0.0, 0.0, 1.0))),
                "scale": parse_vector(body, "m_LocalScale", (1.0, 1.0, 1.0)),
            }
            transform_by_object[object_match.group(1)] = file_id
            order.append(file_id)
        elif class_id == 137:
            renderer = body
    if renderer is None:
        raise ValueError(f"No SkinnedMeshRenderer in {prefab_path}")

    renderer_object = re.search(r"^  m_GameObject: \{fileID: (-?\d+)\}$", renderer, re.MULTILINE)
    root_bone = re.search(r"^  m_RootBone: \{fileID: (-?\d+)\}$", renderer, re.MULTILINE)
    if not renderer_object or not root_bone:
        raise ValueError(f"Incomplete SkinnedMeshRenderer in {prefab_path}")
    bones_block = renderer.split("  m_Bones:\n", 1)[1].split("  m_BlendShapeWeights:", 1)[0]
    bone_ids = re.findall(r"^  - \{fileID: (-?\d+)\}$", bones_block, re.MULTILINE)
    unique_bones = list(dict.fromkeys(bone_ids))
    bone_to_joint = {file_id: index for index, file_id in enumerate(unique_bones)}
    original_joint_map = [bone_to_joint[file_id] for file_id in bone_ids]

    node_index = {file_id: index for index, file_id in enumerate(order)}
    nodes = []
    for file_id in order:
        transform = transforms[file_id]
        node = {
            "name": game_objects.get(transform["object"], f"Transform_{file_id}"),
            "translation": transform["translation"],
            "rotation": transform["rotation"],
            "scale": transform["scale"],
        }
        children = [node_index[child] for child in order if transforms[child]["father"] == file_id]
        if children:
            node["children"] = children
        nodes.append(node)

    mesh_transform = transform_by_object[renderer_object.group(1)]
    mesh_node = node_index[mesh_transform]
    nodes[mesh_node]["mesh"] = 0
    nodes[mesh_node]["skin"] = 0
    roots = [node_index[file_id] for file_id in order if transforms[file_id]["father"] == "0"]

    world = [None] * len(nodes)

    def resolve_world(index: int) -> np.ndarray:
        if world[index] is not None:
            return world[index]
        file_id = order[index]
        father = transforms[file_id]["father"]
        local = local_matrix(nodes[index])
        world[index] = local if father == "0" else resolve_world(node_index[father]) @ local
        return world[index]

    for index in range(len(nodes)):
        resolve_world(index)
    mesh_world = world[mesh_node]
    inverse_bind_matrices = []
    for bone_id in unique_bones:
        matrix = np.linalg.inv(world[node_index[bone_id]]) @ mesh_world
        inverse_bind_matrices.append(tuple(float(value) for value in matrix.flatten(order="F")))

    return {
        "nodes": nodes,
        "roots": roots,
        "meshNode": mesh_node,
        "joints": [node_index[file_id] for file_id in unique_bones],
        "skeleton": node_index[root_bone.group(1)],
        "jointMap": original_joint_map,
        "inverseBindMatrices": inverse_bind_matrices,
    }


def parse_mesh_asset(path: Path, joint_map: list[int]) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    vertex_count = int(re.search(r"^    m_VertexCount: (\d+)$", text, re.MULTILINE).group(1))
    index_format = int(re.search(r"^  m_IndexFormat: (\d+)$", text, re.MULTILINE).group(1))
    index_hex = re.search(r"^  m_IndexBuffer: ([0-9a-f]+)$", text, re.MULTILINE).group(1)
    data_hex = re.search(r"^    _typelessdata: ([0-9a-f]+)$", text, re.MULTILINE).group(1)
    submesh = re.search(
        r"^  - serializedVersion: 2\r?\n"
        r"    firstByte: (\d+)\r?\n"
        r"    indexCount: (\d+)\r?\n"
        r"    topology: (\d+)\r?\n"
        r"    baseVertex: (-?\d+)\r?\n"
        r"    firstVertex: (\d+)\r?\n"
        r"    vertexCount: (\d+)$",
        text,
        re.MULTILINE,
    )
    if not submesh or int(submesh.group(3)) != 0:
        raise ValueError(f"Expected one triangle submesh in {path}")

    channel_block = text.split("    m_Channels:\n", 1)[1].split("    m_DataSize:", 1)[0]
    channels = [
        tuple(int(value) for value in match.groups())
        for match in re.finditer(
            r"    - stream: (\d+)\r?\n      offset: (\d+)\r?\n      format: (\d+)\r?\n      dimension: (\d+)",
            channel_block,
        )
    ]
    if len(channels) < 14:
        raise ValueError(f"Incomplete vertex channels in {path}")
    format_sizes = {0: 4, 10: 4}
    strides = defaultdict(int)
    for stream, offset, data_format, dimension in channels:
        if dimension:
            strides[stream] = max(strides[stream], offset + format_sizes[data_format] * dimension)
    stream_offsets = {}
    cursor = 0
    for stream in sorted(strides):
        cursor += (-cursor) % 16
        stream_offsets[stream] = cursor
        cursor += strides[stream] * vertex_count
    data = bytes.fromhex(data_hex)
    if cursor > len(data):
        raise ValueError(f"Vertex data is truncated in {path}: need {cursor}, found {len(data)}")

    def read_channel(channel_index: int, fmt: str):
        stream, offset, _data_format, _dimension = channels[channel_index]
        return [
            struct.unpack_from(fmt, data, stream_offsets[stream] + row * strides[stream] + offset)
            for row in range(vertex_count)
        ]

    positions = [(-row[0], row[1], row[2]) for row in read_channel(0, "<3f")]
    normals = [(-row[0], row[1], row[2]) for row in read_channel(1, "<3f")]
    tangents = [(-row[0], row[1], row[2], -row[3]) for row in read_channel(2, "<4f")]
    texcoords = read_channel(4, "<2f")
    weights = []
    for row in read_channel(12, "<4f"):
        total = sum(row)
        weights.append(tuple(value / total for value in row) if total > 1e-8 else (1.0, 0.0, 0.0, 0.0))
    joints = []
    for row in read_channel(13, "<4I"):
        if max(row) >= len(joint_map):
            raise ValueError(f"Joint index {max(row)} exceeds {len(joint_map)} bones in {path}")
        joints.append(tuple(joint_map[index] for index in row))

    component = "H" if index_format == 0 else "I"
    component_size = 2 if index_format == 0 else 4
    index_data = bytes.fromhex(index_hex)
    first_byte, index_count = int(submesh.group(1)), int(submesh.group(2))
    indices = list(struct.unpack_from(f"<{index_count}{component}", index_data, first_byte))
    for offset in range(0, len(indices), 3):
        indices[offset + 1], indices[offset + 2] = indices[offset + 2], indices[offset + 1]
    return {
        "positions": positions,
        "normals": normals,
        "tangents": tangents,
        "texcoords": texcoords,
        "weights": weights,
        "joints": joints,
        "indices": indices,
        "indexComponentType": 5123 if component_size == 2 else 5125,
    }


def pack_rows(rows, fmt: str) -> bytes:
    return b"".join(struct.pack(fmt, *row) for row in rows)


def build_character_glb(mesh_path: Path, prefab_path: Path, output: Path) -> None:
    rig = prefab_rig(prefab_path)
    mesh = parse_mesh_asset(mesh_path, rig["jointMap"])
    document = {
        "asset": {"version": "2.0", "generator": "ror2-wiki Unity Sidekick importer"},
        "scene": 0,
        "scenes": [{"nodes": rig["roots"]}],
        "nodes": rig["nodes"],
        "meshes": [{"name": mesh_path.stem, "primitives": [{}]}],
        "materials": [{
            "name": f"{mesh_path.stem}_Material",
            "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": 0.78},
            "doubleSided": False,
        }],
        "skins": [{"joints": rig["joints"], "skeleton": rig["skeleton"]}],
        "buffers": [{"byteLength": 0}],
    }
    binary = bytearray()
    attributes = {}
    for name, rows, component_type, accessor_type, fmt in (
        ("POSITION", mesh["positions"], 5126, "VEC3", "<3f"),
        ("NORMAL", mesh["normals"], 5126, "VEC3", "<3f"),
        ("TANGENT", mesh["tangents"], 5126, "VEC4", "<4f"),
        ("TEXCOORD_0", mesh["texcoords"], 5126, "VEC2", "<2f"),
        ("WEIGHTS_0", mesh["weights"], 5126, "VEC4", "<4f"),
        ("JOINTS_0", mesh["joints"], 5123, "VEC4", "<4H"),
    ):
        attributes[name] = append_accessor(
            document, binary, pack_rows(rows, fmt), component_type, accessor_type, len(rows), target=34962
        )
    position_accessor = document["accessors"][attributes["POSITION"]]
    position_accessor["min"] = [min(row[index] for row in mesh["positions"]) for index in range(3)]
    position_accessor["max"] = [max(row[index] for row in mesh["positions"]) for index in range(3)]
    index_fmt = "<" + ("H" if mesh["indexComponentType"] == 5123 else "I") * len(mesh["indices"])
    indices = append_accessor(
        document,
        binary,
        struct.pack(index_fmt, *mesh["indices"]),
        mesh["indexComponentType"],
        "SCALAR",
        len(mesh["indices"]),
        target=34963,
    )
    ibm = append_accessor(
        document,
        binary,
        pack_rows(rig["inverseBindMatrices"], "<16f"),
        5126,
        "MAT4",
        len(rig["inverseBindMatrices"]),
    )
    document["skins"][0]["inverseBindMatrices"] = ibm
    document["meshes"][0]["primitives"][0] = {
        "attributes": attributes,
        "indices": indices,
        "material": 0,
        "mode": 4,
    }
    write_glb(output, document, bytes(binary))


def retarget_low_poly_actions(path: Path) -> None:
    target_document, target_binary_bytes = read_glb(path)
    target_binary = bytearray(target_binary_bytes)
    source_document, source_binary = read_glb(ACTION_SOURCE)
    source_names = {node.get("name", ""): index for index, node in enumerate(source_document.get("nodes", []))}
    target_names = {node.get("name", ""): index for index, node in enumerate(target_document.get("nodes", []))}
    missing_source = [name for name, _ in SOURCE_TO_SIDEKICK if name not in source_names]
    missing_target = [name for _, name in SOURCE_TO_SIDEKICK if name not in target_names]
    if missing_source or missing_target:
        raise ValueError(f"Missing retarget bones: source={missing_source}, target={missing_target}")

    source_parents, source_order = hierarchy(source_document)
    target_parents, target_order = hierarchy(target_document)
    source_rest_local = [node_transform(node) for node in source_document["nodes"]]
    target_rest_local = [node_transform(node) for node in target_document["nodes"]]
    source_rest_world = world_transforms(source_rest_local, source_parents, source_order)
    target_rest_world = world_transforms(target_rest_local, target_parents, target_order)
    motion_scale = skeleton_scale(target_rest_world, target_names, "pelvis", ("foot_l", "foot_r")) / max(
        skeleton_scale(source_rest_world, source_names, "Hips", ("LeftFoot", "RightFoot")), 1e-8
    )
    source_for_target = {
        target_names[target_name]: source_names[source_name]
        for source_name, target_name in SOURCE_TO_SIDEKICK
    }
    pelvis_index = target_names["pelvis"]
    source_animations = {animation.get("name"): animation for animation in source_document.get("animations", [])}
    target_document["animations"] = []

    for clip_name in LOW_POLY_PERSON_ACTIONS.values():
        source_animation = source_animations[clip_name]
        tracks = animation_tracks(source_document, source_binary, source_animation)
        if not tracks:
            target_document["animations"].append({"name": clip_name, "samplers": [], "channels": []})
            continue
        times = next(iter(tracks.values()))[0]
        rotation_rows = {target_index: [] for target_index in source_for_target}
        pelvis_translation_rows = []
        for time in times:
            source_local = deepcopy(source_rest_local)
            for (node_index, channel_path), (track_times, values, interpolation) in tracks.items():
                sampled = sample_track(track_times, values, time, channel_path, interpolation)
                current = source_local[node_index]
                if channel_path == "translation":
                    source_local[node_index] = Transform(sampled, current.rotation, current.scale)
                elif channel_path == "rotation":
                    source_local[node_index] = Transform(current.translation, quat_normalize(sampled), current.scale)
                elif channel_path == "scale":
                    source_local[node_index] = Transform(current.translation, current.rotation, sampled)
            source_world = world_transforms(source_local, source_parents, source_order)
            target_world = [None] * len(target_rest_local)
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
                    desired_world_rotation = quat_multiply(source_delta, target_rest_world[target_index].rotation)
                    local_rotation = quat_multiply(
                        quat_inverse(parent_world.rotation if parent_world else (0.0, 0.0, 0.0, 1.0)),
                        desired_world_rotation,
                    )
                    previous = rotation_rows[target_index][-1] if rotation_rows[target_index] else None
                    if previous and sum(previous[index] * local_rotation[index] for index in range(4)) < 0:
                        local_rotation = tuple(-component for component in local_rotation)
                    rotation_rows[target_index].append(local_rotation)
                    if target_index == pelvis_index:
                        delta = vec_subtract(
                            source_world[source_index].translation,
                            source_rest_world[source_index].translation,
                        )
                        desired = vec_add(target_rest_world[target_index].translation, vec_scale(delta, motion_scale))
                        local_translation = inverse_transform_point(parent_world, desired)
                        pelvis_translation_rows.append(local_translation)
                target_world[target_index] = compose(
                    parent_world,
                    Transform(local_translation, local_rotation, rest_local.scale),
                )

        time_accessor = append_accessor(
            target_document, target_binary, packed_floats([(time,) for time in times]), 5126, "SCALAR", len(times)
        )
        target_document["accessors"][time_accessor]["min"] = [min(times)]
        target_document["accessors"][time_accessor]["max"] = [max(times)]
        animation = {"name": clip_name, "samplers": [], "channels": [], "extras": {"retarget": "low-poly-to-sidekick-v1"}}
        for target_index in target_order:
            rows = rotation_rows.get(target_index)
            if not rows:
                continue
            require_finite_rows(f"{clip_name}:{target_document['nodes'][target_index].get('name')} rotation", rows)
            output = append_accessor(target_document, target_binary, packed_floats(rows), 5126, "VEC4", len(rows))
            sampler = len(animation["samplers"])
            animation["samplers"].append({"input": time_accessor, "output": output, "interpolation": "LINEAR"})
            animation["channels"].append({"sampler": sampler, "target": {"node": target_index, "path": "rotation"}})
        require_finite_rows(f"{clip_name}:pelvis translation", pelvis_translation_rows)
        output = append_accessor(
            target_document, target_binary, packed_floats(pelvis_translation_rows), 5126, "VEC3", len(pelvis_translation_rows)
        )
        sampler = len(animation["samplers"])
        animation["samplers"].append({"input": time_accessor, "output": output, "interpolation": "LINEAR"})
        animation["channels"].append({"sampler": sampler, "target": {"node": pelvis_index, "path": "translation"}})
        target_document["animations"].append(animation)
    write_glb(path, target_document, bytes(target_binary))
    append_generic_animations(path)


def append_rifle_grip_channels(document: dict, binary: bytearray, animation_names: tuple[str, ...]) -> None:
    node_names = {node.get("name", ""): index for index, node in enumerate(document.get("nodes", []))}
    missing = sorted(set(RIFLE_GRIP_CURL_DEGREES) - set(node_names))
    if missing:
        raise ValueError(f"Missing Sidekick finger bones: {missing}")
    animations = {animation.get("name"): animation for animation in document.get("animations", [])}
    for animation_name in animation_names:
        animation = animations[animation_name]
        duration = max(
            float(read_accessor(document, binary, sampler["input"])[-1][0])
            for sampler in animation.get("samplers", [])
        )
        time_accessor = append_accessor(
            document,
            binary,
            packed_floats([(0.0,), (duration,)]),
            5126,
            "SCALAR",
            2,
        )
        for bone_name, degrees in RIFLE_GRIP_CURL_DEGREES.items():
            bone_index = node_names[bone_name]
            rest_rotation = tuple(document["nodes"][bone_index].get("rotation", [0.0, 0.0, 0.0, 1.0]))
            half_angle = math.radians(degrees) * 0.5
            grip_rotation = quat_multiply(
                rest_rotation,
                (0.0, 0.0, math.sin(half_angle), math.cos(half_angle)),
            )
            output_accessor = append_accessor(
                document,
                binary,
                packed_floats([grip_rotation, grip_rotation]),
                5126,
                "VEC4",
                2,
            )
            sampler_index = len(animation["samplers"])
            animation["samplers"].append({
                "input": time_accessor,
                "output": output_accessor,
                "interpolation": "LINEAR",
            })
            animation["channels"].append({
                "sampler": sampler_index,
                "target": {"node": bone_index, "path": "rotation"},
            })


def clone_animation_as_pose(
    document: dict,
    binary: bytearray,
    source_name: str,
    target_name: str,
    duration: float = 1.0,
) -> dict:
    source = next(animation for animation in document.get("animations", []) if animation.get("name") == source_name)
    time_accessor = append_accessor(
        document,
        binary,
        packed_floats([(0.0,), (duration,)]),
        5126,
        "SCALAR",
        2,
    )
    pose = {"name": target_name, "samplers": [], "channels": []}
    for channel in source.get("channels", []):
        sampler = source["samplers"][channel["sampler"]]
        values = read_accessor(document, binary, sampler["output"])
        first_value = tuple(float(value) for value in values[0])
        output_type = document["accessors"][sampler["output"]]["type"]
        output_accessor = append_accessor(
            document,
            binary,
            packed_floats([first_value, first_value]),
            5126,
            output_type,
            2,
        )
        sampler_index = len(pose["samplers"])
        pose["samplers"].append({
            "input": time_accessor,
            "output": output_accessor,
            "interpolation": "LINEAR",
        })
        pose["channels"].append({"sampler": sampler_index, "target": deepcopy(channel["target"])})
    document.setdefault("animations", []).append(pose)
    return {"name": target_name, "source": source_name, "frames": 2, "channels": len(pose["channels"])}


def append_mixamo_actions(path: Path) -> list[dict]:
    missing = [source for _name, source in MIXAMO_ACTIONS if not source.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing Mixamo action sources: {missing}")
    document, binary_bytes = read_glb(path)
    binary = bytearray(binary_bytes)
    results = [
        retarget_animation(
            document,
            binary,
            source,
            name,
            bone_mapping=MIXAMO_TO_SIDEKICK,
            target_hips="pelvis",
            target_feet=("foot_l", "foot_r"),
        )
        for name, source in MIXAMO_ACTIONS
    ]
    append_rifle_grip_channels(
        document,
        binary,
        ("Mixamo_RifleFireStanding", "Mixamo_RifleRunFire"),
    )
    pose_report = clone_animation_as_pose(
        document,
        binary,
        "Mixamo_RifleFireStanding",
        "Mixamo_RifleReady",
    )
    extras = document.setdefault("asset", {}).setdefault("extras", {})
    extras["mixamoRetarget"] = {"version": 1, "animations": results}
    extras["rifleGripPose"] = {
        "version": 1,
        "fingerChannels": len(RIFLE_GRIP_CURL_DEGREES),
        "poseAnimation": pose_report,
    }
    rig = extras.get("rig")
    if isinstance(rig, dict):
        rig["animations"] = len(document.get("animations", []))
    write_glb(path, document, bytes(binary))
    return results


def prune_collision_geometry(path: Path) -> int:
    document, binary = read_glb(path)
    removed = {
        index for index, node in enumerate(document.get("nodes", []))
        if node.get("name", "").upper().startswith(COLLISION_PREFIXES)
    }
    if not removed:
        return 0
    keep = [index for index in range(len(document["nodes"])) if index not in removed]
    mapping = {old: new for new, old in enumerate(keep)}
    nodes = []
    for old in keep:
        node = dict(document["nodes"][old])
        children = [mapping[child] for child in node.get("children", []) if child in mapping]
        if children:
            node["children"] = children
        else:
            node.pop("children", None)
        nodes.append(node)
    document["nodes"] = nodes
    for scene in document.get("scenes", []):
        scene["nodes"] = [mapping[index] for index in scene.get("nodes", []) if index in mapping]
    used_meshes = sorted({node["mesh"] for node in nodes if "mesh" in node})
    mesh_mapping = {old: new for new, old in enumerate(used_meshes)}
    document["meshes"] = [document["meshes"][index] for index in used_meshes]
    for node in nodes:
        if "mesh" in node:
            node["mesh"] = mesh_mapping[node["mesh"]]
    used_materials = sorted({
        primitive["material"]
        for mesh in document.get("meshes", [])
        for primitive in mesh.get("primitives", [])
        if "material" in primitive
    })
    material_mapping = {old: new for new, old in enumerate(used_materials)}
    document["materials"] = [document["materials"][index] for index in used_materials]
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            if "material" in primitive:
                primitive["material"] = material_mapping[primitive["material"]]
    write_glb(path, document, binary)
    return len(removed)


def copy_texture(guid: str, textures: dict[str, Path], package_slug: str, copied: dict[str, str]) -> str | None:
    source = textures.get(guid)
    if not source:
        return None
    if guid in copied:
        return copied[guid]
    target = TEXTURES_ROOT / package_slug / f"{slugify(source.stem)}{source.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    copied[guid] = target.relative_to(ROOT).as_posix()
    return copied[guid]


def build_characters() -> list[dict]:
    package_slug = "sci-fi-civilians"
    package = PACKS[package_slug]
    characters_root = package["source"] / "Characters" / "ScifiCivilians"
    entries = []
    for index in range(1, 7):
        stem = f"ScifiCivilians_{index:02d}"
        directory = characters_root / stem
        output = MODELS_ROOT / package_slug / f"{slugify(stem)}.glb"
        print(f"[{package_slug}] {index}/6 {stem}", flush=True)
        build_character_glb(directory / "Meshes" / f"{stem}.asset", directory / f"{stem}.prefab", output)
        retarget_low_poly_actions(output)
        append_mixamo_actions(output)
        texture_source = directory / "Textures" / f"T_{stem}ColorMap.png"
        texture_target = TEXTURES_ROOT / package_slug / f"{slugify(stem)}-color-map.png"
        texture_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(texture_source, texture_target)
        report = report_for_glb(output)
        entry = base_entry(package_slug, package, slugify(stem), f"Sci-Fi Civilian {index:02d}", report, output.relative_to(ROOT).as_posix())
        for animation in entry["animations"]:
            if animation["clip"].startswith("Mixamo_Rifle"):
                animation["name"] = MIXAMO_ACTION_LABELS[animation["clip"]]
                animation["equipment"] = EQUIPMENT_OPTIONS[0][0]
                animation["equipmentTransform"] = deepcopy(RIFLE_EQUIPMENT_TRANSFORMS[animation["clip"]])
                animation["gripPose"] = "rifle"
                animation["loop"] = animation["clip"] in {"Mixamo_RifleReady", "Mixamo_RifleRunFire"}
                animation["kind"] = "idle" if animation["clip"] == "Mixamo_RifleReady" else "attack"
        entry.update({
            "name": f"科幻平民 {index:02d}",
            "category": "人物",
            "tier": "人物",
            "status": f"{report['animationCount']} 个动作",
            "summary": f"Sidekick 科幻平民角色 {index:02d}，使用 Unity 完整组合网格、原始骨骼与颜色贴图。",
            "modelSource": package["name"],
            "prefabSource": f"{stem}.prefab / {stem}.asset",
            "rigSource": "original-unity-sidekick-prefab",
            "textures": {"original": texture_target.relative_to(ROOT).as_posix(), "low": texture_target.relative_to(ROOT).as_posix()},
            "paletteTexture": True,
            "textureFlipY": True,
            "defaultClip": "Mixamo_RifleReady",
            "motionAnchorBone": "pelvis",
            "motionAnchorAxes": ["x", "y", "z"],
            "equipmentBone": "hand_r",
            "defaultEquipment": EQUIPMENT_OPTIONS[0][0],
            "equipmentOptions": equipment_options(),
            "sharedPackageActions": [animation.get("name") for animation in report["animations"]],
            "mixamoActionSource": {
                "provider": "Adobe Mixamo",
                "motion": "Firing Rifle",
                "skin": "Without Skin",
                "fps": 30,
                "keyframeReduction": "none",
                "runInPlace": True,
            },
            "cameraTargetY": 1.05,
        })
        entries.append(entry)
    return entries


def build_weapons(converter: Path) -> tuple[list[dict], list[dict]]:
    package_slug = "sci-fi-battle-weapons"
    package = PACKS[package_slug]
    models_root = package["source"] / "Models"
    sources = sorted([path for path in models_root.iterdir() if path.suffix.lower() == ".fbx"])
    library = load_unity_materials(package["source"])
    assignments = load_prefab_materials(package["source"])
    textures = load_unity_textures(package["source"])
    copied = {}
    entries = []
    warnings = []
    for index, source in enumerate(sources, 1):
        stem_slug = slugify(source.stem)
        output = MODELS_ROOT / package_slug / stem_slug[0] / f"{stem_slug}.glb"
        print(f"[{package_slug}] {index}/{len(sources)} {source.stem}", flush=True)
        try:
            convert_fbx(converter, source, output.with_suffix(""))
            removed = prune_collision_geometry(output)
            material_result = apply_unity_materials(output, source, library, assignments)
        except Exception as error:
            warnings.append({"source": str(source), "error": str(error)})
            continue
        report = report_for_glb(output)
        entry = base_entry(package_slug, package, stem_slug, source.stem, report, output.relative_to(ROOT).as_posix())
        base_maps = material_result["baseTextureMaterials"]
        emissive_maps = material_result["emissiveTextureMaterials"]
        normal_maps = material_result["normalTextureMaterials"]
        base_guid = next(iter(base_maps), None)
        emissive_guid = next(iter(emissive_maps), None)
        normal_guid = next(iter(normal_maps), None)
        base_texture = copy_texture(base_guid, textures, package_slug, copied) if base_guid else None
        emissive_texture = copy_texture(emissive_guid, textures, package_slug, copied) if emissive_guid else None
        normal_texture = copy_texture(normal_guid, textures, package_slug, copied) if normal_guid else None
        entry.update({
            "name": source.stem,
            "category": "科幻武器",
            "tier": "科幻武器",
            "status": "静态武器",
            "summary": f"Sci-Fi Battle Weapons 中的 {source.stem}，已移除 {removed} 个碰撞网格并恢复 Unity 材质贴图。",
            "modelSource": package["name"],
            "prefabSource": f"{source.stem}.FBX / 对应 Unity Prefab",
            "textures": {"original": base_texture, "low": base_texture},
            "emissiveTextures": {"original": emissive_texture, "low": emissive_texture},
            "normalTextures": {"original": normal_texture, "low": normal_texture},
            "cameraTargetY": 0.65,
            "framingScaleByVariant": {"original": 0.92, "low": 0.92},
            "mobileFramingScaleByVariant": {"original": 0.86, "low": 0.86},
            "embeddedMaterialMetalness": 0.45,
        })
        if base_maps:
            names = sorted(set(next(iter(base_maps.values()))))
            entry["textureMaterialNamesByVariant"] = {"original": names, "low": names}
        entries.append(entry)
    return entries, warnings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--converter", type=Path, default=DEFAULT_CONVERTER)
    parser.add_argument("--characters-only", action="store_true")
    args = parser.parse_args()
    if not args.converter.is_file():
        parser.error(f"FBX2glTF converter not found: {args.converter}")
    existing = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    existing = [
        entry for entry in existing
        if entry.get("packageSlug") != "sci-fi-civilians"
        and (args.characters_only or entry.get("packageSlug") != "sci-fi-battle-weapons")
    ]
    characters = build_characters()
    if args.characters_only:
        warnings = json.loads(WARNINGS_PATH.read_text(encoding="utf-8")) if WARNINGS_PATH.is_file() else {}
        warnings["sci-fi-civilians"] = []
        WARNINGS_PATH.write_text(json.dumps(warnings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_catalog([*existing, *characters])
        print(json.dumps({
            "characters": len(characters),
            "characterActions": sum(entry["animationCount"] for entry in characters),
        }, ensure_ascii=False, indent=2))
        return
    weapons, weapon_warnings = build_weapons(args.converter)
    warnings = json.loads(WARNINGS_PATH.read_text(encoding="utf-8")) if WARNINGS_PATH.is_file() else {}
    warnings["sci-fi-civilians"] = []
    warnings["sci-fi-battle-weapons"] = weapon_warnings
    WARNINGS_PATH.write_text(json.dumps(warnings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_catalog([*existing, *characters, *weapons])
    print(json.dumps({
        "characters": len(characters),
        "characterActions": sum(entry["animationCount"] for entry in characters),
        "weapons": len(weapons),
        "weaponWarnings": len(weapon_warnings),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
