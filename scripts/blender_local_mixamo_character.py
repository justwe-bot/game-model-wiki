"""Run the Mixamo character finalizer in a local Blender process."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import types


class _ModalBuilder:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: self


class _ModalApp(_ModalBuilder):
    def __init__(self, *_args, **_kwargs):
        pass

    def function(self, *_args, **_kwargs):
        return lambda function: function

    def local_entrypoint(self, *_args, **_kwargs):
        return lambda function: function


def load_finalizer(repo_root: Path):
    module = types.ModuleType("modal")
    module.App = _ModalApp
    module.Image = _ModalBuilder()
    module.Volume = _ModalBuilder()
    sys.modules["modal"] = module
    path = repo_root / "cloud" / "modal_mixamo_character.py"
    spec = importlib.util.spec_from_file_location("local_mixamo_character", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load finalizer: {path}")
    finalizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(finalizer)
    return finalizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-fbx", type=Path, required=True)
    parser.add_argument("--actions-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-fbx", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--target-height", type=float, default=1.9)
    parser.add_argument("--force-opaque-materials", action="store_true")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    finalizer = load_finalizer(repo_root)
    result = finalizer.build_character(
        args.base_fbx.resolve(),
        args.actions_zip.resolve(),
        args.output.resolve(),
        args.output_fbx.resolve(),
        target_height=args.target_height,
        force_opaque=args.force_opaque_materials,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
