"""Orchestrate the Hunyuan -> clean mesh -> Mixamo -> animated model workflow."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print(shlex.join(command))
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def modal_command(script: str) -> list[str]:
    return [sys.executable, "-m", "modal", "run", str(REPO_ROOT / script)]


def prepare_command(args: argparse.Namespace) -> list[str]:
    command = [
        *modal_command("cloud/modal_character_mesh.py"),
        "--input",
        str(args.input.resolve()),
        "--output-dir",
        str(args.output_dir.resolve()),
        "--target-faces",
        str(args.target_faces),
        "--target-height",
        str(args.target_height),
        "--texture-size",
        str(args.texture_size),
    ]
    if args.symmetry_x:
        command.append("--symmetry-x")
    if args.force:
        command.append("--force")
    return command


def replace_head_command(args: argparse.Namespace) -> list[str]:
    command = [
        *modal_command("cloud/modal_character_head_replacement.py"),
        "--body", str(args.body.resolve()),
        "--head", str(args.head.resolve()),
        "--output-dir", str(args.output_dir.resolve()),
        "--body-cut-ratio", str(args.body_cut_ratio),
        "--head-cut-ratio", str(args.head_cut_ratio),
        "--head-fit-height", str(args.head_fit_height),
        "--neck-overlap", str(args.neck_overlap),
        "--head-offset", args.head_offset,
        "--head-rotation", args.head_rotation,
        "--voxel-size", str(args.voxel_size),
        "--texture-size", str(args.texture_size),
    ]
    if args.force:
        command.append("--force")
    return command


def make_action_archive(directory: Path) -> Path:
    sources = sorted(path for path in directory.iterdir() if path.suffix.lower() == ".fbx")
    if not sources:
        raise FileNotFoundError(f"No action FBXs found in {directory}")
    temporary = Path(tempfile.mkdtemp(prefix="mixamo-actions-")) / "actions.zip"
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            archive.write(source, source.name)
    return temporary


def finalize_command(args: argparse.Namespace, actions: Path) -> list[str]:
    command = [
        *modal_command("cloud/modal_mixamo_character.py"),
        "--base-fbx",
        str(args.base_fbx.resolve()),
        "--actions-zip",
        str(actions.resolve()),
        "--output",
        str(args.output.resolve()),
        "--output-fbx",
        str(args.output_fbx.resolve()),
        "--report",
        str(args.report.resolve()),
        "--target-height",
        str(args.target_height),
    ]
    if getattr(args, "stabilize_helmet_weights", False):
        command.extend(
            [
                "--stabilize-helmet-weights",
                "--helmet-rigid-threshold",
                str(args.helmet_rigid_threshold),
                "--helmet-minimum-head-influence",
                str(args.helmet_minimum_head_influence),
            ]
        )
    if getattr(args, "force_opaque_materials", False):
        command.append("--force-opaque-materials")
    if args.force:
        command.append("--force")
    return command


def attach_command(args: argparse.Namespace) -> list[str]:
    command = [
        *modal_command("cloud/modal_character_attachment.py"),
        "--character",
        str(args.character.resolve()),
        "--part",
        str(args.part.resolve()),
        "--bone",
        args.bone,
        "--part-name",
        args.part_name,
        "--part-anchor",
        args.part_anchor,
        "--bone-anchor",
        args.bone_anchor,
        "--offset",
        args.offset,
        "--rotation",
        args.rotation,
        "--scale",
        str(args.scale),
        "--fit-height",
        str(args.fit_height),
        "--output",
        str(args.output.resolve()),
        "--output-fbx",
        str(args.output_fbx.resolve()),
        "--report",
        str(args.report.resolve()),
    ]
    if args.force_opaque_materials:
        command.append("--force-opaque-materials")
    if args.force:
        command.append("--force")
    return command


def add_prepare_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "prepare",
        help="Turn a Hunyuan high-resolution GLB into a Mixamo upload package",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-faces", type=int, default=15_000)
    parser.add_argument("--target-height", type=float, default=1.9)
    parser.add_argument("--texture-size", type=int, default=2048)
    parser.add_argument("--symmetry-x", action="store_true")
    parser.add_argument("--force", action="store_true")


def add_replace_head_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "replace-head",
        help="Replace an integrated biological head before Mixamo skinning",
    )
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
    parser.add_argument("--force", action="store_true")


def add_finalize_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "finalize",
        help="Combine a Mixamo With Skin FBX and action FBXs into final GLB/FBX files",
    )
    parser.add_argument("--base-fbx", type=Path, required=True)
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--actions-zip", type=Path)
    action_group.add_argument("--actions-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-fbx", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--target-height", type=float, default=1.9)
    parser.add_argument("--stabilize-helmet-weights", action="store_true")
    parser.add_argument("--helmet-rigid-threshold", type=float, default=0.35)
    parser.add_argument("--helmet-minimum-head-influence", type=float, default=0.02)
    parser.add_argument("--force-opaque-materials", action="store_true")
    parser.add_argument("--force", action="store_true")


def add_validate_parser(subparsers) -> None:
    parser = subparsers.add_parser("validate", help="Structurally validate final animated GLBs")
    parser.add_argument("models", nargs="+", type=Path)


def add_attach_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "attach",
        help="Rigidly attach a separate GLB part to an animated character bone",
    )
    parser.add_argument("--character", type=Path, required=True)
    parser.add_argument("--part", type=Path, required=True)
    parser.add_argument("--bone", required=True)
    parser.add_argument("--part-name", default="Attachment")
    parser.add_argument(
        "--part-anchor",
        choices=("origin", "bottom", "center", "top"),
        default="bottom",
    )
    parser.add_argument(
        "--bone-anchor",
        choices=("head", "center", "tail"),
        default="head",
    )
    parser.add_argument("--offset", default="0,0,0")
    parser.add_argument("--rotation", default="0,0,0")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--fit-height", type=float, default=0.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-fbx", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--force-opaque-materials", action="store_true")
    parser.add_argument("--force", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_prepare_parser(subparsers)
    add_replace_head_parser(subparsers)
    add_finalize_parser(subparsers)
    add_attach_parser(subparsers)
    add_validate_parser(subparsers)
    args = parser.parse_args()

    if args.command == "prepare":
        run(prepare_command(args))
        return
    if args.command == "replace-head":
        run(replace_head_command(args))
        return
    if args.command == "validate":
        run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "validate_mixamo_character.py"),
                *(str(path.resolve()) for path in args.models),
            ]
        )
        return

    if args.command == "attach":
        args.output_fbx = args.output_fbx or args.output.with_suffix(".fbx")
        args.report = args.report or args.output.with_suffix(".attachment-report.json")
        run(attach_command(args))
        run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "validate_mixamo_character.py"),
                str(args.output.resolve()),
            ]
        )
        return

    args.output_fbx = args.output_fbx or args.output.with_suffix(".fbx")
    args.report = args.report or args.output.with_suffix(".mixamo-report.json")
    actions = args.actions_zip or make_action_archive(args.actions_dir.resolve())
    run(finalize_command(args, actions))
    run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_mixamo_character.py"),
            str(args.output.resolve()),
        ]
    )


if __name__ == "__main__":
    main()
