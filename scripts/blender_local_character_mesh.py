"""Run the Modal character-mesh implementation in a local Blender process."""

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


def _load_mesh_pipeline(repo_root: Path):
    _install_modal_stub()
    path = repo_root / "cloud" / "modal_character_mesh.py"
    spec = importlib.util.spec_from_file_location("local_character_mesh", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load mesh pipeline: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--qremeshify-root", type=Path, required=True)
    parser.add_argument("--target-faces", type=int, default=15000)
    parser.add_argument("--target-height", type=float, default=1.9)
    parser.add_argument("--texture-size", type=int, default=2048)
    parser.add_argument("--symmetry-x", action="store_true")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    qremeshify_root = args.qremeshify_root.resolve()
    if not (qremeshify_root / "QRemeshify" / "__init__.py").is_file():
        raise FileNotFoundError(qremeshify_root / "QRemeshify" / "__init__.py")
    sys.path.insert(0, str(qremeshify_root))
    pipeline = _load_mesh_pipeline(repo_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    pipeline.prepare_character(
        args.input.resolve(),
        args.output_dir.resolve(),
        target_faces=args.target_faces,
        target_height=args.target_height,
        texture_size=args.texture_size,
        symmetry_x=args.symmetry_x,
    )


if __name__ == "__main__":
    main()
