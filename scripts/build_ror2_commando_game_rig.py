"""Build Commando GLBs with the original Unity skeleton, weights, and clips."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

import numpy as np
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

from add_ror2_bandit_rig import append_accessor, read_glb, write_glb
from extract_ror2_survivors import (
    bind_matrix,
    component,
    find_model_root,
    iter_transforms,
    renderer_mesh,
    transform_key,
)
from ror2_bandit_animation_curves import COMMANDO_CLIPS as CLIPS
from ror2_bandit_animation_curves import parse_rotation_curves, quat_normalize


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
REFLECTION = np.diag([1.0, 1.0, -1.0, 1.0])


def converted_trs(transform: object) -> tuple[list[float], list[float], list[float]]:
    position = transform.m_LocalPosition
    rotation = transform.m_LocalRotation
    scale = transform.m_LocalScale
    return (
        [position.x, position.y, -position.z],
        [-rotation.x, -rotation.y, rotation.z, rotation.w],
        [scale.x, scale.y, scale.z],
    )


def append_blob(document: dict, binary: bytearray, data: bytes) -> int:
    binary.extend(b"\x00" * ((-len(binary)) % 4))
    offset = len(binary)
    binary.extend(data)
    index = len(document.setdefault("bufferViews", []))
    document["bufferViews"].append(
        {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
    )
    return index


def float_accessor(
    document: dict,
    binary: bytearray,
    rows: list[tuple[float, ...]],
    accessor_type: str,
    *,
    target: int | None = None,
    bounds: bool = False,
) -> int:
    width = len(rows[0])
    data = struct.pack(f"<{len(rows) * width}f", *(value for row in rows for value in row))
    index = append_accessor(document, binary, data, 5126, accessor_type, len(rows), target=target)
    if bounds:
        document["accessors"][index]["min"] = [min(row[axis] for row in rows) for axis in range(width)]
        document["accessors"][index]["max"] = [max(row[axis] for row in rows) for axis in range(width)]
    return index


def mesh_primitive(
    document: dict,
    binary: bytearray,
    mesh: object,
    *,
    skinned: bool,
) -> tuple[dict, int]:
    handler = MeshHandler(mesh)
    handler.process()
    positions = [(x, y, -z) for x, y, z in handler.m_Vertices or []]
    normals = [(x, y, -z) for x, y, z in handler.m_Normals or [(0.0, 1.0, 0.0)] * len(positions)]
    uvs = [(u, 1.0 - v) for u, v, *_ in handler.m_UV0 or [(0.0, 0.0)] * len(positions)]
    source_indices = [int(value) for value in handler.m_IndexBuffer or []]
    indices = [
        value
        for offset in range(0, len(source_indices) - 2, 3)
        for value in (source_indices[offset], source_indices[offset + 2], source_indices[offset + 1])
    ]
    attributes = {
        "POSITION": float_accessor(document, binary, positions, "VEC3", target=34962, bounds=True),
        "NORMAL": float_accessor(document, binary, normals, "VEC3", target=34962),
        "TEXCOORD_0": float_accessor(document, binary, uvs, "VEC2", target=34962),
    }
    if skinned:
        bone_indices = handler.m_BoneIndices or []
        bone_weights = handler.m_BoneWeights or []
        if not bone_indices or not bone_weights:
            raise ValueError(f"{mesh.m_Name}: missing original bone weights")
        joint_rows = [tuple(int(value) for value in row[:4]) + (0,) * (4 - len(row[:4])) for row in bone_indices]
        weight_rows = [tuple(float(value) for value in row[:4]) + (0.0,) * (4 - len(row[:4])) for row in bone_weights]
        joints_data = struct.pack(
            f"<{len(joint_rows) * 4}B",
            *(value for row in joint_rows for value in row),
        )
        attributes["JOINTS_0"] = append_accessor(
            document, binary, joints_data, 5121, "VEC4", len(joint_rows), target=34962
        )
        attributes["WEIGHTS_0"] = float_accessor(
            document, binary, weight_rows, "VEC4", target=34962
        )
    index_data = struct.pack(f"<{len(indices)}H", *indices)
    index_accessor = append_accessor(
        document, binary, index_data, 5123, "SCALAR", len(indices), target=34963
    )
    return {"attributes": attributes, "indices": index_accessor, "material": 0}, len(indices) // 3


def parse_vector_curves(
    path: Path,
    section_name: str,
) -> dict[str, list[tuple[float, tuple[float, float, float]]]]:
    import re

    text = path.read_text(encoding="utf-8")
    marker = f"  {section_name}:"
    start = text.find(marker)
    if start < 0 or text.startswith(" []", start + len(marker)):
        return {}
    next_section = re.search(r"\n  m_[A-Za-z0-9_]+:", text[start + len(marker) :])
    end = start + len(marker) + next_section.start() if next_section else len(text)
    key_pattern = re.compile(
        rf"time:\s*({NUMBER})\s*\r?\n\s*value:\s*\{{x:\s*({NUMBER}),\s*y:\s*({NUMBER}),\s*z:\s*({NUMBER})\}}"
    )
    curves = {}
    for block in re.split(r"\n  - curve:\s*\n", text[start:end])[1:]:
        path_match = re.search(r"\n    path:\s*(.+?)\s*\r?$", block, re.MULTILINE)
        if not path_match:
            continue
        keys = [
            (float(match.group(1)), tuple(float(match.group(index)) for index in range(2, 5)))
            for match in key_pattern.finditer(block)
        ]
        if keys:
            curves[path_match.group(1)] = keys
    return curves


def animation_accessor(
    document: dict,
    binary: bytearray,
    keys: list[tuple[float, tuple[float, ...]]],
    accessor_type: str,
) -> tuple[int, int]:
    times = [(time,) for time, _ in keys]
    values = [value for _, value in keys]
    input_index = float_accessor(document, binary, times, "SCALAR", bounds=True)
    output_index = float_accessor(document, binary, values, accessor_type)
    return input_index, output_index


def add_idle_base(
    curves: dict[str, list[tuple[float, tuple[float, ...]]]],
    base_curves: dict[str, list[tuple[float, tuple[float, ...]]]],
    duration: float,
) -> None:
    for path, keys in base_curves.items():
        if path in curves or not keys:
            continue
        value = keys[0][1]
        curves[path] = [(0.0, value), (duration, value)]


def add_game_animations(
    document: dict,
    binary: bytearray,
    path_to_node: dict[str, int],
    animation_root: Path,
) -> None:
    animations = []
    for spec in CLIPS:
        source_path = animation_root / spec.file_name
        rotations = parse_rotation_curves(source_path)
        positions = parse_vector_curves(source_path, "m_PositionCurves")
        scales = parse_vector_curves(source_path, "m_ScaleCurves")
        if spec.base_file_name:
            duration = max(
                keys[-1][0]
                for curves in (rotations, positions, scales)
                for keys in curves.values()
                if keys
            )
            base_path = animation_root / spec.base_file_name
            add_idle_base(rotations, parse_rotation_curves(base_path), duration)
            add_idle_base(positions, parse_vector_curves(base_path, "m_PositionCurves"), duration)
            add_idle_base(scales, parse_vector_curves(base_path, "m_ScaleCurves"), duration)
        samplers = []
        channels = []

        for path, keys in rotations.items():
            node_index = path_to_node.get(path)
            if node_index is None:
                continue
            converted = []
            previous = None
            for time, quaternion in keys:
                value = quat_normalize((-quaternion[0], -quaternion[1], quaternion[2], quaternion[3]))
                if previous is not None and sum(left * right for left, right in zip(previous, value)) < 0.0:
                    value = tuple(-component for component in value)
                previous = value
                converted.append((time, value))
            input_index, output_index = animation_accessor(document, binary, converted, "VEC4")
            sampler_index = len(samplers)
            samplers.append({"input": input_index, "output": output_index, "interpolation": "LINEAR"})
            channels.append({"sampler": sampler_index, "target": {"node": node_index, "path": "rotation"}})

        for path, keys in positions.items():
            node_index = path_to_node.get(path)
            if node_index is None:
                continue
            converted = [(time, (value[0], value[1], -value[2])) for time, value in keys]
            input_index, output_index = animation_accessor(document, binary, converted, "VEC3")
            sampler_index = len(samplers)
            samplers.append({"input": input_index, "output": output_index, "interpolation": "LINEAR"})
            channels.append({"sampler": sampler_index, "target": {"node": node_index, "path": "translation"}})

        for path, keys in scales.items():
            node_index = path_to_node.get(path)
            if node_index is None:
                continue
            input_index, output_index = animation_accessor(document, binary, keys, "VEC3")
            sampler_index = len(samplers)
            samplers.append({"input": input_index, "output": output_index, "interpolation": "LINEAR"})
            channels.append({"sampler": sampler_index, "target": {"node": node_index, "path": "scale"}})

        if not channels:
            raise ValueError(f"{source_path}: no curves matched the original Commando hierarchy")
        animations.append(
            {
                "name": spec.name,
                "samplers": samplers,
                "channels": channels,
                "extras": {"source": spec.file_name, "originalGameRig": True},
            }
        )
    document["animations"] = animations


def build_original(
    bundle_paths: list[Path],
    texture_path: Path,
    animation_root: Path,
    destination: Path,
) -> dict[str, int]:
    environment = UnityPy.load(*[str(path) for path in bundle_paths])
    root = find_model_root(environment, ["CommandoMesh"], "mdlCommandoDualies")
    transforms = list(iter_transforms(root))
    key_to_node = {transform_key(transform): index for index, (transform, _, _) in enumerate(transforms)}
    document = {
        "asset": {
            "version": "2.0",
            "generator": "ror2-wiki Commando original-rig builder",
            "extras": {
                "rig": {
                    "source": "original-game-rig",
                    "originalGameRig": True,
                    "jointCount": 78,
                    "animations": len(CLIPS),
                }
            },
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [],
        "meshes": [],
        "buffers": [{"byteLength": 0}],
        "bufferViews": [],
        "accessors": [],
    }
    binary = bytearray()
    path_to_node = {}
    path_by_key = {}

    for index, (transform, game_object, _) in enumerate(transforms):
        translation, rotation, scale = converted_trs(transform)
        node = {
            "name": game_object.m_Name,
            "translation": translation,
            "rotation": rotation,
            "scale": scale,
        }
        children = [key_to_node[transform_key(pointer.read())] for pointer in transform.m_Children]
        if children:
            node["children"] = children
        document["nodes"].append(node)
        if transform is root:
            path = ""
        else:
            parent_path = path_by_key[transform_key(transform.m_Father.read())]
            path = f"{parent_path}/{game_object.m_Name}" if parent_path else game_object.m_Name
        path_by_key[transform_key(transform)] = path
        if path:
            path_to_node[path] = index

    image_view = append_blob(document, binary, texture_path.read_bytes())
    document["images"] = [{"name": "texCommandoPaletteDiffuse", "bufferView": image_view, "mimeType": "image/png"}]
    document["samplers"] = [{"magFilter": 9728, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
    document["textures"] = [{"sampler": 0, "source": 0}]
    document["materials"] = [
        {
            "name": "CommandoOriginalMaterial",
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0},
                "metallicFactor": 0.0,
                "roughnessFactor": 0.78,
            },
        }
    ]

    skinned_renderers = []
    static_renderers = []
    excluded = set()
    for transform, game_object, active in transforms:
        if not active or game_object.m_Name in excluded:
            continue
        rendered = renderer_mesh(game_object)
        if rendered is None:
            continue
        if rendered[0] == "skinned":
            skinned_renderers.append((transform, game_object, rendered[1], rendered[2]))
        elif rendered[1].m_Name:
            static_renderers.append((transform, game_object, rendered[1]))

    if not skinned_renderers:
        raise ValueError("Commando prefab has no skinned renderers")
    first_renderer = skinned_renderers[0][3]
    joint_keys = [transform_key(pointer.read()) for pointer in first_renderer.m_Bones]
    joint_nodes = [key_to_node[key] for key in joint_keys]
    inverse_bind_data = bytearray()
    for value in skinned_renderers[0][2].m_BindPose:
        converted = REFLECTION @ bind_matrix(value) @ REFLECTION
        inverse_bind_data.extend(struct.pack("<16f", *converted.T.flatten()))
    inverse_bind_accessor = append_accessor(
        document, binary, bytes(inverse_bind_data), 5126, "MAT4", len(joint_nodes)
    )
    root_bone = first_renderer.m_RootBone.read()
    document["skins"] = [
        {
            "name": "CommandoOriginalGameRig",
            "inverseBindMatrices": inverse_bind_accessor,
            "skeleton": key_to_node[transform_key(root_bone)],
            "joints": joint_nodes,
            "extras": {"originalGameRig": True},
        }
    ]

    triangle_count = 0
    for transform, game_object, mesh, renderer in skinned_renderers:
        renderer_keys = [transform_key(pointer.read()) for pointer in renderer.m_Bones]
        if renderer_keys != joint_keys:
            raise ValueError(f"{game_object.m_Name}: bone ordering differs from Commando body")
        primitive, triangles = mesh_primitive(document, binary, mesh, skinned=True)
        mesh_index = len(document["meshes"])
        document["meshes"].append({"name": mesh.m_Name, "primitives": [primitive]})
        node = document["nodes"][key_to_node[transform_key(transform)]]
        node["mesh"] = mesh_index
        node["skin"] = 0
        triangle_count += triangles

    for transform, _game_object, mesh in static_renderers:
        handler = MeshHandler(mesh)
        handler.process()
        if not handler.m_IndexBuffer:
            continue
        primitive, triangles = mesh_primitive(document, binary, mesh, skinned=False)
        mesh_index = len(document["meshes"])
        document["meshes"].append({"name": mesh.m_Name, "primitives": [primitive]})
        document["nodes"][key_to_node[transform_key(transform)]]["mesh"] = mesh_index
        triangle_count += triangles

    add_game_animations(document, binary, path_to_node, animation_root)
    write_glb(destination, document, binary)
    return {
        "sourceTriangles": triangle_count,
        "jointCount": len(joint_nodes),
        "animationCount": len(CLIPS),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundles", nargs="+", type=Path, required=True)
    parser.add_argument("--texture", type=Path, required=True)
    parser.add_argument("--animation-root", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--low", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--low-script", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    args = parser.parse_args()

    stats = build_original(args.bundles, args.texture, args.animation_root, args.original)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temporary:
        low_stats_path = Path(temporary.name)
    try:
        subprocess.run(
            [
                str(args.blender),
                "--background",
                "--factory-startup",
                "--python",
                str(args.low_script),
                "--",
                "--source",
                str(args.original),
                "--destination",
                str(args.low),
                "--stats",
                str(low_stats_path),
            ],
            check=True,
        )
        low_stats = json.loads(low_stats_path.read_text(encoding="utf-8"))
    finally:
        low_stats_path.unlink(missing_ok=True)
    low_document, low_binary = read_glb(args.low)
    low_document.setdefault("asset", {}).setdefault("extras", {})["rig"] = {
        "source": "original-game-rig",
        "originalGameRig": True,
        "jointCount": stats["jointCount"],
        "animations": stats["animationCount"],
    }
    low_document["skins"][0].setdefault("extras", {})["originalGameRig"] = True
    low_triangles = sum(
        low_document["accessors"][primitive["indices"]]["count"] // 3
        for mesh in low_document.get("meshes", [])
        for primitive in mesh.get("primitives", [])
    )
    write_glb(args.low, low_document, low_binary)
    result = {
        **stats,
        "lowTriangles": low_triangles,
        "sourceSizeKB": round(args.original.stat().st_size / 1024),
        "lowSizeKB": round(args.low.stat().st_size / 1024),
    }
    args.stats.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"Built original Commando rig: {result['sourceTriangles']} -> {result['lowTriangles']} triangles, "
        f"{result['jointCount']} joints, {result['animationCount']} animations"
    )


if __name__ == "__main__":
    main()
