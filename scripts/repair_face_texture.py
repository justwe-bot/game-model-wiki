"""Repair small baked facial artifacts using exported UV-region triangles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mask-output", type=Path)
    return parser.parse_args()


def _pixel(uv, width: int, height: int):
    return (round(uv[0] * (width - 1)), round((1.0 - uv[1]) * (height - 1)))


def _median_color(image: Image.Image, mask: Image.Image):
    pixels = image.load()
    mask_pixels = mask.load()
    width, height = image.size
    samples = []
    step = max(1, min(width, height) // 1024)
    for y in range(0, height, step):
        for x in range(0, width, step):
            if mask_pixels[x, y] > 8:
                samples.append(pixels[x, y][:3])
    if not samples:
        return (166, 128, 118)
    channels = [sorted(color[index] for color in samples) for index in range(3)]
    return tuple(channel[len(channel) // 2] for channel in channels)


def main() -> None:
    args = parse_args()
    image = Image.open(args.input).convert("RGBA")
    payload = json.loads(args.regions.read_text(encoding="utf-8"))
    width, height = image.size
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    for region_name in (
        "nose_bottom",
        "upper_lip_skin",
        "lower_lip_shadow",
        "neck",
    ):
        for triangle in payload["regions"].get(region_name, []):
            draw.polygon([_pixel(uv, width, height) for uv in triangle], fill=255)

    mask = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(3.0))
    local_skin = image.filter(ImageFilter.MedianFilter(11)).filter(ImageFilter.GaussianBlur(2.0))
    neutral = Image.new("RGBA", image.size, _median_color(image, mask) + (255,))
    repaired = Image.composite(local_skin, image, mask)
    soft_mask = mask.point(lambda value: round(value * 0.12))
    repaired = Image.composite(neutral, repaired, soft_mask)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    repaired.save(args.output)
    if args.mask_output:
        args.mask_output.parent.mkdir(parents=True, exist_ok=True)
        mask.save(args.mask_output)
    print({"output": str(args.output.resolve()), "mask": str(args.mask_output.resolve()) if args.mask_output else None})


if __name__ == "__main__":
    main()
