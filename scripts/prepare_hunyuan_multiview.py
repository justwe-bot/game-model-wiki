"""Prepare aligned transparent front/left/back inputs for Hunyuan3D-2mv."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageColor


VIEW_NAMES = ("front", "left", "back")


def load_crop_boxes(path: Path) -> dict[str, list[int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    boxes = payload.get("cropBoxes", payload)
    if not isinstance(boxes, dict):
        raise ValueError(f"Crop spec must contain an object: {path}")
    return boxes


def crop_views(
    source: Path,
    output_dir: Path,
    *,
    crop_boxes: dict[str, list[int]] | None = None,
    canvas_size: int | None = None,
    suffix: str = "-green",
    background: tuple[int, int, int, int] = (238, 238, 238, 255),
) -> list[Path]:
    image = Image.open(source).convert("RGBA")
    width, height = image.size
    if crop_boxes is None:
        boundaries = [round(width * index / 3) for index in range(4)]
        crop_boxes = {
            name: [boundaries[index], 0, boundaries[index + 1], height]
            for index, name in enumerate(VIEW_NAMES)
        }
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    for name in VIEW_NAMES:
        values = crop_boxes.get(name)
        if not isinstance(values, list) or len(values) != 4:
            raise ValueError(f"Missing four-value crop box for {name}")
        box = tuple(int(value) for value in values)
        left, top, right, bottom = box
        if left < 0 or top < 0 or right > width or bottom > height:
            raise ValueError(f"Crop box for {name} is outside {width}x{height}: {box}")
        if right <= left or bottom <= top:
            raise ValueError(f"Crop box for {name} has no area: {box}")
        crop = image.crop(box)
        if canvas_size is not None:
            if canvas_size < max(crop.size):
                raise ValueError(
                    f"Canvas {canvas_size}px is smaller than the {name} crop {crop.size}"
                )
            canvas = Image.new("RGBA", (canvas_size, canvas_size), background)
            offset = (
                (canvas_size - crop.width) // 2,
                (canvas_size - crop.height) // 2,
            )
            canvas.alpha_composite(crop, offset)
            crop = canvas
        destination = output_dir / f"{name}{suffix}.png"
        crop.save(destination)
        outputs.append(destination)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--crop-spec",
        type=Path,
        help="JSON object containing front/left/back pixel crop boxes",
    )
    parser.add_argument("--canvas-size", type=int)
    parser.add_argument("--suffix", default="-green")
    parser.add_argument("--background", default="#eeeeee")
    args = parser.parse_args()

    if not args.source.is_file():
        raise FileNotFoundError(args.source)
    crop_boxes = load_crop_boxes(args.crop_spec) if args.crop_spec else None
    red, green, blue = ImageColor.getrgb(args.background)
    for output in crop_views(
        args.source,
        args.output_dir,
        crop_boxes=crop_boxes,
        canvas_size=args.canvas_size,
        suffix=args.suffix,
        background=(red, green, blue, 255),
    ):
        print(output)


if __name__ == "__main__":
    main()
