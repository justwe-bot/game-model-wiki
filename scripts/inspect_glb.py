"""Print compact structural statistics for a GLB file."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def read_glb(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, total_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total_length != len(data):
        raise ValueError(f"Invalid GLB: {path}")
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if json_type != 0x4E4F534A:
        raise ValueError(f"Missing JSON chunk: {path}")
    return json.loads(data[20 : 20 + json_length].decode("utf-8"))


def main() -> None:
    for value in sys.argv[1:]:
        path = Path(value)
        gltf = read_glb(path)
        node_names = [node.get("name") for node in gltf.get("nodes", []) if node.get("name")]
        print(
            json.dumps(
                {
                    "path": str(path),
                    "nodes": len(gltf.get("nodes", [])),
                    "meshes": len(gltf.get("meshes", [])),
                    "skins": len(gltf.get("skins", [])),
                    "materials": len(gltf.get("materials", [])),
                    "animations": [item.get("name") for item in gltf.get("animations", [])],
                    "jointCounts": [len(skin.get("joints", [])) for skin in gltf.get("skins", [])],
                    "nodeNames": node_names,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
