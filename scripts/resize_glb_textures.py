"""Resize embedded GLB textures and compact the binary buffer."""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image

from add_ror2_bandit_rig import read_glb, write_glb


def resize_image(
    payload: bytes,
    max_size: int,
    shadow_gamma: float,
) -> tuple[bytes, str]:
    image = Image.open(BytesIO(payload))
    if max(image.size) < 256:
        return payload, Image.MIME.get(image.format, "image/png")
    changed = False
    if max(image.size) > max_size:
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        changed = True
    if shadow_gamma != 1.0:
        has_alpha = "A" in image.getbands()
        alpha = image.getchannel("A") if has_alpha else None
        rgb = image.convert("RGB")
        lut = [round(255 * ((value / 255) ** shadow_gamma)) for value in range(256)]
        rgb = rgb.point(lut * 3)
        image = Image.merge("RGBA", (*rgb.split(), alpha)) if alpha is not None else rgb
        changed = True
    if not changed:
        return payload, Image.MIME.get(image.format, "image/png")
    output = BytesIO()
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    image.save(output, format="PNG", optimize=True)
    return output.getvalue(), "image/png"


def resize_textures(
    source: Path,
    destination: Path,
    max_size: int,
    shadow_gamma: float = 1.0,
) -> dict[str, int]:
    document, binary = read_glb(source)
    replacements: dict[int, bytes] = {}
    resized = 0
    for image in document.get("images", []):
        view_index = image.get("bufferView")
        if view_index is None:
            continue
        view = document["bufferViews"][view_index]
        start = view.get("byteOffset", 0)
        payload = bytes(binary[start : start + view["byteLength"]])
        resized_payload, mime_type = resize_image(payload, max_size, shadow_gamma)
        if resized_payload != payload:
            replacements[view_index] = resized_payload
            image["mimeType"] = mime_type
            resized += 1

    compact = bytearray()
    for view_index, view in enumerate(document.get("bufferViews", [])):
        compact.extend(b"\x00" * ((-len(compact)) % 4))
        start = view.get("byteOffset", 0)
        payload = replacements.get(
            view_index,
            bytes(binary[start : start + view["byteLength"]]),
        )
        view["byteOffset"] = len(compact)
        view["byteLength"] = len(payload)
        compact.extend(payload)

    destination.parent.mkdir(parents=True, exist_ok=True)
    write_glb(destination, document, compact)
    return {
        "resizedTextures": resized,
        "sourceBytes": source.stat().st_size,
        "outputBytes": destination.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--max-size", type=int, default=1024)
    parser.add_argument("--shadow-gamma", type=float, default=1.0)
    args = parser.parse_args()
    if args.max_size < 64:
        raise ValueError("--max-size must be at least 64")
    if not 0.5 <= args.shadow_gamma <= 1.0:
        raise ValueError("--shadow-gamma must be between 0.5 and 1.0")
    print(
        resize_textures(
            args.source,
            args.destination,
            args.max_size,
            shadow_gamma=args.shadow_gamma,
        )
    )


if __name__ == "__main__":
    main()
