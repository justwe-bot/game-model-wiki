"""Build a lightweight geometry-only Mixamo auto-rig proxy from an OBJ archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile
import zipfile


def safe_asset_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not name:
        raise ValueError("Asset name must contain a letter or number")
    return name


def build_proxy(
    source_archive: Path,
    destination: Path,
    *,
    face_count: int,
    asset_name: str,
) -> dict[str, int | str]:
    try:
        import trimesh
    except ImportError as error:
        raise RuntimeError(
            "build_mixamo_proxy.py requires trimesh and fast-simplification"
        ) from error

    asset_name = safe_asset_name(asset_name)
    with zipfile.ZipFile(source_archive) as archive:
        obj_names = [name for name in archive.namelist() if name.lower().endswith(".obj")]
        if len(obj_names) != 1:
            raise ValueError(f"Expected one OBJ in {source_archive}, found {len(obj_names)}")
        with tempfile.TemporaryDirectory() as directory:
            source_obj = Path(directory) / Path(obj_names[0]).name
            source_obj.write_bytes(archive.read(obj_names[0]))
            loaded = trimesh.load(source_obj, force="mesh", process=False)

    if not isinstance(loaded, trimesh.Trimesh):
        raise ValueError("Mixamo proxy source did not load as one triangle mesh")
    original_faces = len(loaded.faces)
    if original_faces <= face_count:
        simplified = loaded.copy()
    else:
        simplified = loaded.simplify_quadric_decimation(face_count=face_count)
    simplified.remove_unreferenced_vertices()
    obj_payload = trimesh.exchange.obj.export_obj(
        simplified,
        include_normals=True,
        include_color=False,
        include_texture=False,
    )
    metadata = {
        "source": source_archive.name,
        "purpose": "Mixamo auto-rig proxy; transfer animation back to final textured mesh",
        "originalFaces": original_faces,
        "proxyFaces": len(simplified.faces),
        "proxyVertices": len(simplified.vertices),
        "assetName": asset_name,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        archive.writestr(f"{asset_name}.obj", obj_payload)
        archive.writestr(
            f"{asset_name}-proxy.json",
            json.dumps(metadata, indent=2) + "\n",
        )
    metadata["sizeBytes"] = destination.stat().st_size
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--face-count", type=int, default=80000)
    parser.add_argument("--asset-name", default="popbot_mixamo_proxy")
    args = parser.parse_args()
    result = build_proxy(
        args.source.resolve(),
        args.destination.resolve(),
        face_count=args.face_count,
        asset_name=args.asset_name,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
