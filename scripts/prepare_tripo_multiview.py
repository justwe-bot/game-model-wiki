"""Crop and upscale the doujie Tripo turnaround into aligned 4K views."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageFilter


VIEW_BOXES = {
    "front": (0, 0, 742, 928),
    "left": (744, 0, 952, 928),
    "back": (953, 0, 1695, 928),
}


def edge_extended_canvas(image: Image.Image, size: int) -> tuple[Image.Image, tuple[int, int]]:
    """Center an image and extend its boundary pixels into the padding."""
    canvas = Image.new("RGB", (size, size))
    offset_x = (size - image.width) // 2
    offset_y = (size - image.height) // 2
    right = offset_x + image.width
    bottom = offset_y + image.height

    canvas.paste(image, (offset_x, offset_y))
    if offset_x:
        left_edge = image.crop((0, 0, 1, image.height)).resize((offset_x, image.height))
        canvas.paste(left_edge, (0, offset_y))
    if right < size:
        right_edge = image.crop((image.width - 1, 0, image.width, image.height)).resize(
            (size - right, image.height)
        )
        canvas.paste(right_edge, (right, offset_y))

    middle = canvas.crop((0, offset_y, size, bottom))
    if offset_y:
        top_edge = middle.crop((0, 0, size, 1)).resize((size, offset_y))
        canvas.paste(top_edge, (0, 0))
    if bottom < size:
        bottom_edge = middle.crop((0, middle.height - 1, size, middle.height)).resize(
            (size, size - bottom)
        )
        canvas.paste(bottom_edge, (0, bottom))

    canvas = canvas.filter(ImageFilter.GaussianBlur(radius=max(24, size // 32)))
    canvas.paste(image, (offset_x, offset_y))
    return canvas, (offset_x, offset_y)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--size", type=int, default=4096)
    args = parser.parse_args()

    source = Image.open(args.source).convert("RGB")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "source": str(args.source),
        "sourceSize": list(source.size),
        "canvasSize": args.size,
        "cropBoxes": {},
        "outputs": {},
    }

    for name, box in VIEW_BOXES.items():
        crop = source.crop(box)
        scale = min((args.size * 0.92) / crop.width, (args.size * 0.94) / crop.height)
        resized = crop.resize(
            (round(crop.width * scale), round(crop.height * scale)),
            Image.Resampling.LANCZOS,
        )
        canvas, offset = edge_extended_canvas(resized, args.size)
        destination = args.output_dir / f"{name}.png"
        canvas.save(destination, optimize=True)
        report["cropBoxes"][name] = list(box)
        report["outputs"][name] = {
            "path": str(destination),
            "contentSize": list(resized.size),
            "offset": list(offset),
        }

    (args.output_dir / "crop-spec.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
