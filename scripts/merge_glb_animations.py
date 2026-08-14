"""Merge animations from compatible GLBs into one base GLB."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    magic, version, total_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total_length != len(data):
        raise ValueError(f"Invalid GLB: {path}")
    offset = 12
    chunks: dict[int, bytes] = {}
    while offset < len(data):
        length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunks[chunk_type] = data[offset : offset + length]
        offset += length
    document = json.loads(chunks[JSON_CHUNK].decode("utf-8"))
    return document, chunks.get(BIN_CHUNK, b"")


def pad(data: bytes, alignment: int, value: bytes = b"\x00") -> bytes:
    remainder = len(data) % alignment
    return data if remainder == 0 else data + value * (alignment - remainder)


def write_glb(path: Path, document: dict, binary: bytes) -> None:
    binary = pad(binary, 4)
    document.setdefault("buffers", [{}])[0]["byteLength"] = len(binary)
    json_bytes = pad(json.dumps(document, separators=(",", ":")).encode("utf-8"), 4, b" ")
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    payload = bytearray(struct.pack("<4sII", b"glTF", 2, total))
    payload.extend(struct.pack("<II", len(json_bytes), JSON_CHUNK))
    payload.extend(json_bytes)
    payload.extend(struct.pack("<II", len(binary), BIN_CHUNK))
    payload.extend(binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def node_paths(document: dict) -> dict[int, str]:
    parents = {
        child: parent
        for parent, node in enumerate(document.get("nodes", []))
        for child in node.get("children", [])
    }
    paths = {}
    for index, node in enumerate(document.get("nodes", [])):
        parts = [node.get("name", f"node-{index}")]
        parent = parents.get(index)
        while parent is not None:
            parts.append(document["nodes"][parent].get("name", f"node-{parent}"))
            parent = parents.get(parent)
        paths[index] = "/".join(reversed(parts))
    return paths


def node_mapping(base: dict, source: dict, referenced_nodes: set[int]) -> dict[int, int]:
    base_paths = {path: index for index, path in node_paths(base).items()}
    source_paths = node_paths(source)
    source_parents = {
        child: parent
        for parent, node in enumerate(source.get("nodes", []))
        for child in node.get("children", [])
    }
    mapping = {}
    for index in referenced_nodes:
        path = source_paths[index]
        if path not in base_paths:
            parent = source_parents.get(index)
            if parent is None or source_paths[parent] not in base_paths:
                raise ValueError(f"Cannot attach animated node {path!r}")
            clone = {
                key: value
                for key, value in source["nodes"][index].items()
                if key not in {"mesh", "skin", "camera", "children"}
            }
            base.setdefault("nodes", []).append(clone)
            base_index = len(base["nodes"]) - 1
            base_parent = base_paths[source_paths[parent]]
            base["nodes"][base_parent].setdefault("children", []).append(base_index)
            base_paths[path] = base_index
        mapping[index] = base_paths[path]
    return mapping


def append_accessor(
    base: dict,
    binary: bytearray,
    source: dict,
    source_binary: bytes,
    accessor_index: int,
) -> int:
    accessor = dict(source["accessors"][accessor_index])
    if "sparse" in accessor:
        raise ValueError("Sparse animation accessors are not supported")
    source_view = source["bufferViews"][accessor["bufferView"]]
    start = source_view.get("byteOffset", 0)
    end = start + source_view["byteLength"]
    while len(binary) % 4:
        binary.append(0)
    destination_offset = len(binary)
    binary.extend(source_binary[start:end])
    view = dict(source_view)
    view["buffer"] = 0
    view["byteOffset"] = destination_offset
    base.setdefault("bufferViews", []).append(view)
    accessor["bufferView"] = len(base["bufferViews"]) - 1
    base.setdefault("accessors", []).append(accessor)
    return len(base["accessors"]) - 1


def glb_stats(document: dict) -> dict:
    vertices = 0
    triangles = 0
    primitives = 0
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            primitives += 1
            position = primitive.get("attributes", {}).get("POSITION")
            if position is not None:
                vertices += document["accessors"][position]["count"]
            indices = primitive.get("indices")
            if indices is not None:
                triangles += document["accessors"][indices]["count"] // 3
            elif position is not None:
                triangles += document["accessors"][position]["count"] // 3
    return {
        "rendererCount": primitives,
        "vertices": vertices,
        "triangles": triangles,
        "jointCount": max((len(skin.get("joints", [])) for skin in document.get("skins", [])), default=0),
    }


def strip_texture_references(document: dict) -> None:
    for material in document.get("materials", []):
        pbr = material.get("pbrMetallicRoughness", {})
        pbr.pop("baseColorTexture", None)
        pbr.pop("metallicRoughnessTexture", None)
        material.pop("normalTexture", None)
        material.pop("occlusionTexture", None)
        material.pop("emissiveTexture", None)
    document.pop("textures", None)
    document.pop("images", None)
    document.pop("samplers", None)


def merge(base_path: Path, actions: list[tuple[str, str, Path]], output: Path) -> dict:
    base, base_binary = read_glb(base_path)
    strip_texture_references(base)
    binary = bytearray(base_binary)
    base["animations"] = []
    merged = []
    for display_name, clip_name, action_path in actions:
        source, source_binary = read_glb(action_path)
        animations = source.get("animations", [])
        if len(animations) != 1:
            raise ValueError(f"Expected one animation in {action_path}, found {len(animations)}")
        source_animation = animations[0]
        referenced_nodes = {channel["target"]["node"] for channel in source_animation["channels"]}
        mapping = node_mapping(base, source, referenced_nodes)
        animation = {"name": clip_name, "samplers": [], "channels": []}
        for sampler in source_animation["samplers"]:
            animation["samplers"].append(
                {
                    **sampler,
                    "input": append_accessor(base, binary, source, source_binary, sampler["input"]),
                    "output": append_accessor(base, binary, source, source_binary, sampler["output"]),
                }
            )
        for channel in source_animation["channels"]:
            target = dict(channel["target"])
            target["node"] = mapping[target["node"]]
            animation["channels"].append({**channel, "target": target})
        base["animations"].append(animation)
        merged.append(
            {
                "clip": clip_name,
                "name": display_name,
                "channels": len(animation["channels"]),
                "samplers": len(animation["samplers"]),
            }
        )
    write_glb(output, base, bytes(binary))
    return {
        **glb_stats(base),
        "animations": merged,
        "animationCount": len(merged),
        "sizeBytes": output.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    action_specs = json.loads(args.actions.read_text(encoding="utf-8"))
    report = merge(
        args.base,
        [(item["name"], item["clip"], Path(item["path"])) for item in action_specs],
        args.output,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
