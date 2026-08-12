"""Export a mesh-only Hunyuan GLB as a textured Mixamo OBJ archive."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import zipfile

from add_ror2_bandit_rig import read_accessor, read_glb


def embedded_image(document: dict, binary: bytearray, image_index: int) -> tuple[str, bytes]:
    image = document["images"][image_index]
    view = document["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    payload = bytes(binary[start : start + view["byteLength"]])
    mime_type = image.get("mimeType", "image/png")
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
        mime_type,
        ".bin",
    )
    return extension, payload


def export_archive(
    source: Path,
    destination: Path,
    target_height: float,
    yaw_degrees: float,
    asset_name: str | None,
    source_up: str = "z",
) -> None:
    document, binary = read_glb(source)
    if document.get("skins") or document.get("animations"):
        raise ValueError("Mixamo source must be a mesh-only GLB without skins or animations")

    mesh_nodes = [node for node in document.get("nodes", []) if "mesh" in node]
    if len(mesh_nodes) != 1:
        raise ValueError(f"Expected one mesh node, found {len(mesh_nodes)}")
    primitives = document["meshes"][mesh_nodes[0]["mesh"]].get("primitives", [])
    if len(primitives) != 1:
        raise ValueError(f"Expected one mesh primitive, found {len(primitives)}")

    primitive = primitives[0]
    attributes = primitive["attributes"]
    if "POSITION" not in attributes or "indices" not in primitive:
        raise ValueError("Mixamo export requires positions and indices")

    positions = read_accessor(document, binary, attributes["POSITION"])
    normals = (
        read_accessor(document, binary, attributes["NORMAL"])
        if "NORMAL" in attributes
        else None
    )
    texcoords = (
        read_accessor(document, binary, attributes["TEXCOORD_0"])
        if "TEXCOORD_0" in attributes
        else None
    )
    indices = [int(row[0]) for row in read_accessor(document, binary, primitive["indices"])]
    if len(indices) % 3:
        raise ValueError("Mesh index count is not triangular")
    if normals is not None and len(positions) != len(normals):
        raise ValueError("Position and normal counts differ")
    if texcoords is not None and len(positions) != len(texcoords):
        raise ValueError("Vertex attribute counts differ")

    if source_up == "y":
        height_axis, depth_axis = 1, 2
    elif source_up == "z":
        height_axis, depth_axis = 2, 1
    else:
        raise ValueError(f"Unsupported source up axis: {source_up}")

    minimum_height = min(point[height_axis] for point in positions)
    maximum_height = max(point[height_axis] for point in positions)
    source_height = maximum_height - minimum_height
    if source_height <= 1e-6:
        raise ValueError("Mesh has no usable height")
    scale = target_height / source_height
    center_x = (min(point[0] for point in positions) + max(point[0] for point in positions)) * 0.5
    center_depth = (
        min(point[depth_axis] for point in positions)
        + max(point[depth_axis] for point in positions)
    ) * 0.5
    yaw = math.radians(yaw_degrees)
    cosine, sine = math.cos(yaw), math.sin(yaw)

    asset_name = re.sub(r"[^A-Za-z0-9_-]+", "_", asset_name or destination.stem).strip("_")
    if not asset_name:
        raise ValueError("Asset name must contain a letter or number")
    obj_lines = [
        f"# Mixamo upload asset: {asset_name}",
        f"mtllib {asset_name}.mtl",
        f"o {asset_name}",
    ]
    for point in positions:
        local_x = (float(point[0]) - center_x) * scale
        local_z = (float(point[depth_axis]) - center_depth) * scale
        x = cosine * local_x + sine * local_z
        if source_up == "y":
            y = (float(point[height_axis]) - minimum_height) * scale
        else:
            y = (maximum_height - float(point[height_axis])) * scale
        z = -sine * local_x + cosine * local_z
        obj_lines.append(f"v {x:.7f} {y:.7f} {z:.7f}")

    if texcoords is not None:
        for texcoord in texcoords:
            obj_lines.append(f"vt {float(texcoord[0]):.7f} {1.0 - float(texcoord[1]):.7f}")

    if normals is not None:
        for normal in normals:
            local_x = float(normal[0])
            if source_up == "y":
                local_y = float(normal[1])
                local_z = float(normal[2])
            else:
                local_y = -float(normal[2])
                local_z = float(normal[1])
            x = cosine * local_x + sine * local_z
            z = -sine * local_x + cosine * local_z
            length = max(math.sqrt(x * x + local_y * local_y + z * z), 1e-8)
            obj_lines.append(f"vn {x / length:.7f} {local_y / length:.7f} {z / length:.7f}")

    obj_lines.extend(["usemtl POPBOT_PBR", "s 1"])
    for offset in range(0, len(indices), 3):
        triangle = [indices[offset + axis] + 1 for axis in range(3)]
        if normals is not None and texcoords is not None:
            face = " ".join(f"{index}/{index}/{index}" for index in triangle)
        elif normals is not None:
            face = " ".join(f"{index}//{index}" for index in triangle)
        elif texcoords is not None:
            face = " ".join(f"{index}/{index}" for index in triangle)
        else:
            face = " ".join(str(index) for index in triangle)
        obj_lines.append(f"f {face}")

    materials = document.get("materials", [])
    material = materials[primitive.get("material", 0)] if materials else {}
    pbr = material.get("pbrMetallicRoughness", {})
    texture_files: dict[str, bytes] = {}
    mtl_lines = [
        "newmtl POPBOT_PBR",
        "Ka 1.000000 1.000000 1.000000",
        "Kd 1.000000 1.000000 1.000000",
        "Ks 0.000000 0.000000 0.000000",
        "d 1.0",
        "illum 1",
    ]

    base_color = pbr.get("baseColorTexture")
    if base_color is not None:
        image_index = document["textures"][base_color["index"]]["source"]
        extension, payload = embedded_image(document, binary, image_index)
        name = f"{asset_name}_basecolor{extension}"
        texture_files[name] = payload
        mtl_lines.append(f"map_Kd {name}")

    normal_texture = material.get("normalTexture")
    if normal_texture is not None:
        image_index = document["textures"][normal_texture["index"]]["source"]
        extension, payload = embedded_image(document, binary, image_index)
        name = f"{asset_name}_normal{extension}"
        texture_files[name] = payload
        mtl_lines.append(f"map_Bump {name}")

    metallic_roughness = pbr.get("metallicRoughnessTexture")
    if metallic_roughness is not None:
        image_index = document["textures"][metallic_roughness["index"]]["source"]
        extension, payload = embedded_image(document, binary, image_index)
        texture_files[f"{asset_name}_metallic_roughness{extension}"] = payload

    metadata = {
        "source": source.name,
        "vertexCount": len(positions),
        "triangleCount": len(indices) // 3,
        "targetHeightMeters": target_height,
        "yawDegrees": yaw_degrees,
        "sourceUp": source_up,
        "assetName": asset_name,
        "purpose": "Adobe Mixamo auto-rig baseline",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(f"{asset_name}.obj", "\n".join(obj_lines) + "\n")
        archive.writestr(f"{asset_name}.mtl", "\n".join(mtl_lines) + "\n")
        archive.writestr(f"{asset_name}-export.json", json.dumps(metadata, indent=2) + "\n")
        for name, payload in texture_files.items():
            archive.writestr(name, payload)
    print(json.dumps({**metadata, "output": str(destination), "sizeBytes": destination.stat().st_size}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--target-height", type=float, default=1.9)
    parser.add_argument(
        "--yaw-degrees",
        type=float,
        default=0.0,
        help="Rotate around Mixamo's Y axis before upload; use 180 only when the source faces backward",
    )
    parser.add_argument("--source-up", choices=("y", "z"), default="z")
    parser.add_argument("--asset-name")
    args = parser.parse_args()
    export_archive(
        args.input.resolve(),
        args.output.resolve(),
        args.target_height,
        args.yaw_degrees,
        args.asset_name,
        args.source_up,
    )


if __name__ == "__main__":
    main()
