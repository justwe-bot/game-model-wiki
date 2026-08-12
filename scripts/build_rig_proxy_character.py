"""Build a cached proxy-to-final rigged character pipeline."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from prepare_hunyuan_multiview import crop_views


STAGES = (
    "prepare-views",
    "generate-proxy",
    "proxy-skeleton",
    "validate-proxy",
    "skin-proxy",
    "rig-final",
    "validate-final",
    "promote",
    "all",
)


def resolve(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def run(command: list[str], repo_root: Path, dry_run: bool) -> None:
    print("+", shlex.join(command))
    if not dry_run:
        subprocess.run(command, cwd=repo_root, check=True)


def check_sources(paths: list[Path], dry_run: bool) -> None:
    if dry_run:
        return
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)


def cached_output(path: Path, force: bool, dry_run: bool) -> bool:
    if not path.exists():
        return False
    if force:
        print(f"Will replace: {path}")
        return False
    print(f"Using cached output: {path}")
    return True


def ensure_output(path: Path, dry_run: bool) -> None:
    if not dry_run and not path.is_file():
        raise RuntimeError(f"Stage did not produce its declared output: {path}")


def prepare_views(repo_root: Path, manifest: dict, force: bool, dry_run: bool) -> None:
    config = manifest["references"]
    source = resolve(repo_root, config["turnaround"])
    output_dir = resolve(repo_root, config["viewsDir"])
    outputs = [output_dir / f"{name}.png" for name in ("front", "left", "back")]
    check_sources([source], dry_run)
    existing = [path for path in outputs if path.exists()]
    if existing and not force:
        if len(existing) == len(outputs):
            for path in outputs:
                print(f"Using cached output: {path}")
            return
        raise FileExistsError(
            f"Only part of the prepared view set exists; pass --force to rebuild: {output_dir}"
        )
    print(f"Prepare views: {source} -> {output_dir}")
    if dry_run:
        return
    background = config.get("background", [238, 238, 238, 255])
    crop_views(
        source,
        output_dir,
        crop_boxes=config["cropBoxes"],
        canvas_size=int(config["canvasSize"]),
        suffix="",
        background=tuple(int(value) for value in background),
    )
    for path in outputs:
        ensure_output(path, False)


def generate_proxy(repo_root: Path, manifest: dict, force: bool, dry_run: bool) -> None:
    references = manifest["references"]
    config = manifest["reconstruction"]
    views_dir = resolve(repo_root, references["viewsDir"])
    sources = {name: views_dir / f"{name}.png" for name in ("front", "left", "back")}
    output = resolve(repo_root, config["output"])
    if cached_output(output, force, dry_run):
        return
    check_sources(list(sources.values()), dry_run)
    command = [
        sys.executable,
        "-m",
        "modal",
        "run",
        str(resolve(repo_root, config["script"])),
        "--front",
        str(sources["front"]),
        "--left",
        str(sources["left"]),
        "--back",
        str(sources["back"]),
        "--output",
        str(output),
        "--seed",
        str(config["seed"]),
        "--steps",
        str(config["steps"]),
        "--octree-resolution",
        str(config["octreeResolution"]),
        "--num-chunks",
        str(config["numChunks"]),
    ]
    run(command, repo_root, dry_run)
    ensure_output(output, dry_run)


def modal_unirig_command(
    repo_root: Path,
    manifest: dict,
    *,
    stage: str,
    output: Path,
) -> list[str]:
    config = manifest["proxyRig"]
    return [
        sys.executable,
        "-m",
        "modal",
        "run",
        str(resolve(repo_root, config["script"])),
        "--input",
        str(resolve(repo_root, manifest["reconstruction"]["output"])),
        "--output",
        str(output),
        "--stage",
        stage,
        "--seed",
        str(config["seed"]),
        "--faces-target-count",
        str(config["facesTargetCount"]),
    ]


def proxy_skeleton(repo_root: Path, manifest: dict, force: bool, dry_run: bool) -> None:
    config = manifest["proxyRig"]
    source = resolve(repo_root, manifest["reconstruction"]["output"])
    skeleton = resolve(repo_root, config["skeletonOutput"])
    inspection = resolve(repo_root, config["inspectionOutput"])
    check_sources([source], dry_run)
    if not cached_output(skeleton, force, dry_run):
        run(
            modal_unirig_command(repo_root, manifest, stage="skeleton", output=skeleton),
            repo_root,
            dry_run,
        )
        ensure_output(skeleton, dry_run)
    if cached_output(inspection, force, dry_run):
        return
    command = [
        sys.executable,
        "-m",
        "modal",
        "run",
        str(resolve(repo_root, config["script"])),
        "--input",
        str(skeleton),
        "--output",
        str(inspection),
        "--stage",
        "inspect",
    ]
    run(command, repo_root, dry_run)
    ensure_output(inspection, dry_run)


def skin_proxy(repo_root: Path, manifest: dict, force: bool, dry_run: bool) -> None:
    config = manifest["proxyRig"]
    source = resolve(repo_root, manifest["reconstruction"]["output"])
    output = resolve(repo_root, config["riggedOutput"])
    if cached_output(output, force, dry_run):
        return
    check_sources([source], dry_run)
    run(
        modal_unirig_command(repo_root, manifest, stage="full", output=output),
        repo_root,
        dry_run,
    )
    ensure_output(output, dry_run)


def validate_proxy(repo_root: Path, manifest: dict, force: bool, dry_run: bool) -> None:
    config = manifest["proxyRig"]
    skeleton = resolve(repo_root, config["inspectionOutput"])
    mesh = resolve(repo_root, manifest["reconstruction"]["output"])
    output = resolve(repo_root, config["validationOutput"])
    validator = resolve(repo_root, config["validationScript"])
    check_sources([skeleton, mesh, validator], dry_run)
    if output.exists() and not force:
        newest_input = max(skeleton.stat().st_mtime, mesh.stat().st_mtime) if not dry_run else 0
        if dry_run or output.stat().st_mtime >= newest_input:
            print(f"Using cached output: {output}")
            if not dry_run:
                report = json.loads(output.read_text(encoding="utf-8"))
                if report.get("passed") is not True:
                    raise ValueError(f"Cached proxy validation did not pass: {output}")
            return
    run(
        [
            sys.executable,
            str(validator),
            "--skeleton",
            str(skeleton),
            "--mesh",
            str(mesh),
            "--output",
            str(output),
        ],
        repo_root,
        dry_run,
    )
    ensure_output(output, dry_run)


def rig_final(
    repo_root: Path,
    manifest: dict,
    force: bool,
    dry_run: bool,
    approve_proxy: bool,
) -> None:
    proxy_report = resolve(repo_root, manifest["proxyRig"]["validationOutput"])
    check_sources([proxy_report], dry_run)
    if not dry_run:
        report = json.loads(proxy_report.read_text(encoding="utf-8"))
        if report.get("passed") is not True:
            raise ValueError(f"Proxy skeleton validation did not pass: {proxy_report}")
        if not approve_proxy:
            raise ValueError(
                "rig-final requires --approve-proxy after reviewing the skeleton overlay"
            )
    config = manifest["finalTransfer"]
    template = resolve(repo_root, config["template"])
    profile = resolve(repo_root, config["profile"])
    rig_script = resolve(repo_root, config["script"])
    check_sources([template, profile, rig_script], dry_run)
    for variant in ("original", "low"):
        source = resolve(repo_root, config["sources"][variant])
        output = resolve(repo_root, config["outputs"][variant])
        if cached_output(output, force, dry_run):
            continue
        check_sources([source], dry_run)
        run(
            [
                sys.executable,
                str(rig_script),
                str(source),
                str(output),
                "--template",
                str(template),
                "--profile",
                str(profile),
            ],
            repo_root,
            dry_run,
        )
        ensure_output(output, dry_run)


def validate_final(repo_root: Path, manifest: dict, dry_run: bool) -> None:
    config = manifest["finalTransfer"]
    outputs = [resolve(repo_root, config["outputs"][variant]) for variant in ("original", "low")]
    check_sources(outputs, dry_run)
    run(
        [
            sys.executable,
            str(resolve(repo_root, manifest["validation"]["script"])),
            *(str(path) for path in outputs),
        ],
        repo_root,
        dry_run,
    )


def load_validation_stats(repo_root: Path, manifest: dict) -> dict[str, dict[str, int | float]]:
    from validate_hunyuan_character import validate

    outputs = manifest["finalTransfer"]["outputs"]
    return {
        variant: validate(resolve(repo_root, outputs[variant]))
        for variant in ("original", "low")
    }


def register_catalog_entry(
    repo_root: Path,
    manifest: dict,
    stats: dict[str, dict[str, int | float]],
    replace_entry: bool,
) -> None:
    config = manifest["wiki"]
    catalog_path = resolve(repo_root, config["catalogSource"])
    entries = json.loads(catalog_path.read_text(encoding="utf-8"))
    template = next(
        (entry for entry in entries if entry.get("slug") == config["templateEntry"]),
        None,
    )
    if template is None:
        raise ValueError(f"Missing catalog template entry: {config['templateEntry']}")
    entry = deepcopy(template)
    entry.update(
        {
            "slug": config["slug"],
            "name": config["name"],
            "nameEn": config["nameEn"],
            "bundle": config["bundle"],
            "status": config["status"],
            "summary": config["summary"],
            "bakedTriangles": int(stats["original"]["totalTriangles"]),
            "sourceTriangles": int(stats["original"]["totalTriangles"]),
            "lowTriangles": int(stats["low"]["totalTriangles"]),
        }
    )
    models_dir = repo_root / "models" / "survivors"
    entry["sourceSizeKB"] = round((models_dir / f"{config['slug']}-original.glb").stat().st_size / 1024)
    entry["lowSizeKB"] = round((models_dir / f"{config['slug']}-low.glb").stat().st_size / 1024)
    matches = [index for index, item in enumerate(entries) if item.get("slug") == config["slug"]]
    if matches and not replace_entry:
        raise ValueError(f"Catalog entry already exists for {config['slug']}; pass --replace-entry")
    if matches:
        entries[matches[0]] = entry
    else:
        entries.append(entry)
    catalog_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run(
        [sys.executable, str(repo_root / "scripts" / "merge_ror2_survivors.py")],
        repo_root,
        False,
    )


def promote(
    repo_root: Path,
    manifest: dict,
    *,
    force: bool,
    dry_run: bool,
    approve_visual: bool,
    replace_entry: bool,
) -> None:
    if not approve_visual:
        raise ValueError("promote requires --approve-visual after browser deformation review")
    outputs = manifest["finalTransfer"]["outputs"]
    wiki = manifest["wiki"]
    sources = {variant: resolve(repo_root, outputs[variant]) for variant in ("original", "low")}
    destinations = {
        variant: repo_root / "models" / "survivors" / f"{wiki['slug']}-{variant}.glb"
        for variant in ("original", "low")
    }
    check_sources(list(sources.values()), dry_run)
    for variant, destination in destinations.items():
        if destination.exists() and not force:
            raise FileExistsError(f"Refusing to replace promoted model without --force: {destination}")
        print(f"Promote {variant}: {sources[variant]} -> {destination}")
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sources[variant], destination)
    if dry_run:
        print(f"Would register Wiki slug: {wiki['slug']}")
        return
    stats = load_validation_stats(repo_root, manifest)
    register_catalog_entry(repo_root, manifest, stats, replace_entry)
    print(f"Wiki URL: http://127.0.0.1:8765/?game=risk-of-rain-2&monster={wiki['slug']}")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "references" / "popbot-rig-proxy-pipeline-v1.json",
    )
    parser.add_argument("--stage", choices=STAGES, default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--approve-proxy", action="store_true")
    parser.add_argument("--approve-visual", action="store_true")
    parser.add_argument("--replace-entry", action="store_true")
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"Pipeline: {manifest['name']}")
    print(f"Manifest: {manifest_path}")

    if args.stage in {"prepare-views", "all"}:
        prepare_views(repo_root, manifest, args.force, args.dry_run)
    if args.stage == "generate-proxy" or (args.stage == "all" and not args.skip_generation):
        generate_proxy(repo_root, manifest, args.force, args.dry_run)
    if args.stage in {"proxy-skeleton", "all"}:
        proxy_skeleton(repo_root, manifest, args.force, args.dry_run)
    if args.stage in {"validate-proxy", "all"}:
        validate_proxy(repo_root, manifest, args.force, args.dry_run)
    if args.stage == "skin-proxy":
        skin_proxy(repo_root, manifest, args.force, args.dry_run)
    if args.stage in {"rig-final", "all"}:
        rig_final(repo_root, manifest, args.force, args.dry_run, args.approve_proxy)
    if args.stage in {"validate-final", "all"}:
        validate_final(repo_root, manifest, args.dry_run)
    if args.stage == "promote":
        promote(
            repo_root,
            manifest,
            force=args.force,
            dry_run=args.dry_run,
            approve_visual=args.approve_visual,
            replace_entry=args.replace_entry,
        )


if __name__ == "__main__":
    main()
