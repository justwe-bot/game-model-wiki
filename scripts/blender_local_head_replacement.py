"""Run the Modal head-replacement implementation in a local Blender process."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import types


class _ModalBuilder:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: self

    def commit(self) -> None:
        return None


class _ModalApp(_ModalBuilder):
    def __init__(self, *_args, **_kwargs):
        pass

    def function(self, *_args, **_kwargs):
        return lambda function: function

    def local_entrypoint(self, *_args, **_kwargs):
        return lambda function: function


def _install_modal_stub() -> None:
    module = types.ModuleType("modal")
    module.App = _ModalApp
    module.Image = _ModalBuilder()
    module.Volume = _ModalBuilder()
    sys.modules["modal"] = module


def _load_head_replacement(repo_root: Path):
    _install_modal_stub()
    path = repo_root / "cloud" / "modal_character_head_replacement.py"
    spec = importlib.util.spec_from_file_location("local_head_replacement", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load head replacement implementation: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--body-cut-ratio", type=float, default=0.865)
    parser.add_argument("--head-cut-ratio", type=float, default=0.12)
    parser.add_argument("--head-fit-height", type=float, default=0.0)
    parser.add_argument("--neck-overlap", type=float, default=0.018)
    parser.add_argument("--head-offset", default="0,0,0")
    parser.add_argument("--head-rotation", default="0,0,0")
    parser.add_argument("--voxel-size", type=float, default=0.0045)
    parser.add_argument("--texture-size", type=int, default=2048)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    implementation = _load_head_replacement(repo_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    implementation.replace_head(
        args.body.resolve(),
        args.head.resolve(),
        args.output_dir.resolve(),
        body_cut_ratio=args.body_cut_ratio,
        head_cut_ratio=args.head_cut_ratio,
        head_fit_height=args.head_fit_height,
        neck_overlap=args.neck_overlap,
        head_offset=implementation.parse_vector(args.head_offset, label="head_offset"),
        head_rotation_degrees=implementation.parse_vector(
            args.head_rotation, label="head_rotation"
        ),
        voxel_size=args.voxel_size,
        texture_size=args.texture_size,
    )


if __name__ == "__main__":
    main()
