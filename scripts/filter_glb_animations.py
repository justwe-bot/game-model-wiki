"""Keep selected animation name prefixes in a GLB file."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile

from add_ror2_bandit_rig import read_glb, write_glb


def filter_document_animations(document: dict, prefixes: tuple[str, ...]) -> tuple[list[str], list[str]]:
    animations = document.get("animations", [])
    kept = [animation for animation in animations if animation.get("name", "").startswith(prefixes)]
    removed = [animation for animation in animations if animation not in kept]
    if not kept:
        raise ValueError(f"No animations matched prefixes: {', '.join(prefixes)}")

    document["animations"] = kept
    extras = document.setdefault("asset", {}).setdefault("extras", {})
    rig = extras.get("rig")
    if isinstance(rig, dict):
        rig["animations"] = len(kept)

    retarget = extras.get("mixamoRetarget")
    if isinstance(retarget, dict) and isinstance(retarget.get("animations"), list):
        kept_names = {animation.get("name") for animation in kept}
        retarget["animations"] = [
            animation
            for animation in retarget["animations"]
            if animation.get("name") in kept_names
        ]

    return (
        [animation.get("name", "<unnamed>") for animation in kept],
        [animation.get("name", "<unnamed>") for animation in removed],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--keep-prefix", action="append", required=True)
    args = parser.parse_args()

    document, binary = read_glb(args.input.resolve())
    kept, removed = filter_document_animations(document, tuple(args.keep_prefix))

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".glb", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        write_glb(temporary, document, binary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

    print(
        {
            "output": str(output),
            "kept": kept,
            "removed": removed,
            "totalAnimations": len(kept),
        }
    )


if __name__ == "__main__":
    main()
