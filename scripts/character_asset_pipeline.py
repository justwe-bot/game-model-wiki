"""Manifest-driven character image-to-rig-to-Wiki production pipeline."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from prepare_hunyuan_multiview import crop_views


SCHEMA_VERSION = 2
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RUN_STAGES = (
    "prepare-views",
    "reconstruct",
    "proxy-skeleton",
    "validate-proxy",
    "skin-proxy",
    "generate-final-mesh",
    "skin-final-mesh",
    "generate-rig-profile",
    "rig-final",
    "validate-final",
    "publish",
    "all",
)
ALL_STAGES = (
    "prepare-views",
    "reconstruct",
    "proxy-skeleton",
    "validate-proxy",
    "generate-final-mesh",
    "skin-final-mesh",
    "generate-rig-profile",
    "rig-final",
    "validate-final",
)
CLOUD_GENERATION_STAGES = {
    "reconstruct",
    "proxy-skeleton",
    "skin-proxy",
    "generate-final-mesh",
    "skin-final-mesh",
}
RECONSTRUCTION_ADAPTERS = {"existing-glb", "hunyuan-multiview", "triposr-single"}
PROXY_ADAPTERS = {"none", "unirig"}
FINAL_RIG_ADAPTERS = {
    "hunyuan-component-aware",
    "nearest-template",
    "none",
    "unirig-template-transfer",
}
FINAL_GEOMETRY_ADAPTERS = {
    "existing-variants",
    "hunyuan-multiview-variants",
    "reuse-reconstruction",
}
CATALOG_MODES = {"direct", "source-template"}
FINAL_SKIN_ADAPTERS = {"existing-variants", "none", "unirig-shared"}
RIG_PROFILE_ADAPTERS = {"existing", "unirig-auto"}


class ManifestError(ValueError):
    """Raised when a character pipeline manifest is incomplete or inconsistent."""


def resolve(repo_root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def relative_to_repo(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ManifestError(f"Manifest root must be an object: {path}")
    return payload


def require_object(payload: dict[str, Any], key: str, errors: list[str]) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        errors.append(f"{key} must be an object")
        return {}
    return value


def require_string(payload: dict[str, Any], key: str, prefix: str, errors: list[str]) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{prefix}.{key} must be a non-empty string")
        return ""
    return value


def reference_view_paths(repo_root: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    references = manifest["references"]
    direct = references.get("views")
    if isinstance(direct, dict):
        return {name: resolve(repo_root, direct[name]) for name in ("front", "left", "back")}
    views_dir = resolve(repo_root, references["viewsDir"])
    return {name: views_dir / f"{name}.png" for name in ("front", "left", "back")}


def validate_manifest(
    manifest: dict[str, Any],
    repo_root: Path,
    *,
    check_files: bool = False,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"schemaVersion must be {SCHEMA_VERSION}")

    pipeline = require_object(manifest, "pipeline", errors)
    require_string(pipeline, "name", "pipeline", errors)

    identity = require_object(manifest, "identity", errors)
    slug = require_string(identity, "slug", "identity", errors)
    if slug and not SLUG_PATTERN.fullmatch(slug):
        errors.append("identity.slug must contain lowercase letters, digits, and single hyphens only")
    for key in ("name", "nameEn", "game", "kind", "tier"):
        require_string(identity, key, "identity", errors)

    references = require_object(manifest, "references", errors)
    reference_mode = references.get("mode")
    if reference_mode not in {"single-image", "multiview", "turnaround", "existing-glb"}:
        errors.append("references.mode must be single-image, multiview, turnaround, or existing-glb")
    if reference_mode == "single-image":
        require_string(references, "singleImage", "references", errors)
    if reference_mode == "multiview":
        views = require_object(references, "views", errors)
        for key in ("front", "left", "back"):
            require_string(views, key, "references.views", errors)
    if reference_mode == "turnaround":
        require_string(references, "turnaround", "references", errors)
        require_string(references, "viewsDir", "references", errors)
        crop_boxes = references.get("cropBoxes")
        if not isinstance(crop_boxes, dict):
            errors.append("references.cropBoxes must be an object for turnaround mode")
        else:
            for name in ("front", "left", "back"):
                box = crop_boxes.get(name)
                if not isinstance(box, list) or len(box) != 4 or not all(
                    isinstance(value, int) for value in box
                ):
                    errors.append(f"references.cropBoxes.{name} must contain four integers")

    reconstruction = require_object(manifest, "reconstruction", errors)
    reconstruction_adapter = reconstruction.get("adapter")
    if reconstruction_adapter not in RECONSTRUCTION_ADAPTERS:
        errors.append(f"reconstruction.adapter must be one of {sorted(RECONSTRUCTION_ADAPTERS)}")
    require_string(reconstruction, "output", "reconstruction", errors)
    if reconstruction_adapter in {"hunyuan-multiview", "triposr-single"}:
        require_string(reconstruction, "script", "reconstruction", errors)
    if reconstruction_adapter == "existing-glb":
        require_string(reconstruction, "source", "reconstruction", errors)
    if reconstruction_adapter == "hunyuan-multiview" and reference_mode not in {
        "multiview",
        "turnaround",
    }:
        errors.append("hunyuan-multiview requires multiview or turnaround references")
    if reconstruction_adapter == "triposr-single" and reference_mode != "single-image":
        errors.append("triposr-single requires references.mode=single-image")

    proxy = require_object(manifest, "proxyRig", errors)
    proxy_adapter = proxy.get("adapter")
    if proxy_adapter not in PROXY_ADAPTERS:
        errors.append(f"proxyRig.adapter must be one of {sorted(PROXY_ADAPTERS)}")
    if proxy_adapter == "unirig":
        for key in ("script", "skeletonOutput", "inspectionOutput", "riggedOutput"):
            require_string(proxy, key, "proxyRig", errors)
        proxy_validation = require_object(proxy, "validation", errors)
        for key in ("script", "output"):
            require_string(proxy_validation, key, "proxyRig.validation", errors)

    final_geometry = require_object(manifest, "finalGeometry", errors)
    final_geometry_adapter = final_geometry.get("adapter")
    if final_geometry_adapter not in FINAL_GEOMETRY_ADAPTERS:
        errors.append(
            f"finalGeometry.adapter must be one of {sorted(FINAL_GEOMETRY_ADAPTERS)}"
        )
    geometry_outputs = require_object(final_geometry, "outputs", errors)
    for variant in ("original", "low"):
        require_string(geometry_outputs, variant, "finalGeometry.outputs", errors)
    if final_geometry_adapter == "hunyuan-multiview-variants":
        require_string(final_geometry, "script", "finalGeometry", errors)
        geometry_views = require_object(final_geometry, "views", errors)
        for key in ("front", "left", "back"):
            require_string(geometry_views, key, "finalGeometry.views", errors)

    final_skin = require_object(manifest, "finalSkin", errors)
    final_skin_adapter = final_skin.get("adapter")
    if final_skin_adapter not in FINAL_SKIN_ADAPTERS:
        errors.append(f"finalSkin.adapter must be one of {sorted(FINAL_SKIN_ADAPTERS)}")
    if final_skin_adapter in {"existing-variants", "unirig-shared"}:
        skin_outputs = require_object(final_skin, "outputs", errors)
        for variant in ("original", "low"):
            require_string(skin_outputs, variant, "finalSkin.outputs", errors)
    if final_skin_adapter == "unirig-shared":
        require_string(final_skin, "script", "finalSkin", errors)
        require_string(final_skin, "rawOutput", "finalSkin", errors)

    rig_profile = require_object(manifest, "rigProfile", errors)
    rig_profile_adapter = rig_profile.get("adapter")
    if rig_profile_adapter not in RIG_PROFILE_ADAPTERS:
        errors.append(
            f"rigProfile.adapter must be one of {sorted(RIG_PROFILE_ADAPTERS)}"
        )
    require_string(rig_profile, "output", "rigProfile", errors)
    if rig_profile_adapter == "unirig-auto":
        require_string(rig_profile, "script", "rigProfile", errors)

    final_rig = require_object(manifest, "finalRig", errors)
    final_adapter = final_rig.get("adapter")
    if final_adapter not in FINAL_RIG_ADAPTERS:
        errors.append(f"finalRig.adapter must be one of {sorted(FINAL_RIG_ADAPTERS)}")
    outputs = require_object(final_rig, "outputs", errors)
    for variant in ("original", "low"):
        require_string(outputs, variant, "finalRig.outputs", errors)
    if final_adapter != "none":
        for key in ("script", "template"):
            require_string(final_rig, key, "finalRig", errors)

    validation = require_object(manifest, "validation", errors)
    final_validation = require_object(validation, "final", errors)
    require_string(final_validation, "script", "validation.final", errors)
    visual_clips = validation.get("visualClips")
    if not isinstance(visual_clips, list) or not all(isinstance(item, str) for item in visual_clips):
        errors.append("validation.visualClips must be an array of strings")
    reject = validation.get("reject")
    if not isinstance(reject, list) or not all(isinstance(item, str) for item in reject):
        errors.append("validation.reject must be an array of strings")

    publish = require_object(manifest, "publish", errors)
    require_string(publish, "modelsDir", "publish", errors)
    require_string(publish, "provenanceOutput", "publish", errors)
    catalog = require_object(publish, "catalog", errors)
    if catalog.get("mode") not in CATALOG_MODES:
        errors.append(f"publish.catalog.mode must be one of {sorted(CATALOG_MODES)}")
    require_string(catalog, "path", "publish.catalog", errors)
    if catalog.get("mode") == "source-template":
        require_string(catalog, "templateEntry", "publish.catalog", errors)
    entry = require_object(publish, "entry", errors)
    for key in ("bundle", "status", "summary"):
        require_string(entry, key, "publish.entry", errors)

    approvals = require_object(manifest, "approvals", errors)
    for key in ("requireProxyVisual", "requireFinalVisual"):
        if not isinstance(approvals.get(key), bool):
            errors.append(f"approvals.{key} must be a boolean")

    rights = require_object(manifest, "rights", errors)
    require_string(rights, "usage", "rights", errors)

    if check_files and not errors:
        required_paths: list[tuple[str, Path]] = []
        if reference_mode == "single-image":
            required_paths.append(("references.singleImage", resolve(repo_root, references["singleImage"])))
        elif reference_mode == "multiview":
            required_paths.extend(
                (f"references.views.{name}", path)
                for name, path in reference_view_paths(repo_root, manifest).items()
            )
        elif reference_mode == "turnaround":
            required_paths.append(("references.turnaround", resolve(repo_root, references["turnaround"])))
        if reconstruction_adapter in {"hunyuan-multiview", "triposr-single"}:
            required_paths.append(("reconstruction.script", resolve(repo_root, reconstruction["script"])))
        elif reconstruction_adapter == "existing-glb":
            required_paths.append(("reconstruction.source", resolve(repo_root, reconstruction["source"])))
        if proxy_adapter == "unirig":
            required_paths.extend(
                (
                    ("proxyRig.script", resolve(repo_root, proxy["script"])),
                    (
                        "proxyRig.validation.script",
                        resolve(repo_root, proxy["validation"]["script"]),
                    ),
                )
            )
        if final_geometry_adapter == "existing-variants":
            required_paths.extend(
                (f"finalGeometry.outputs.{variant}", resolve(repo_root, geometry_outputs[variant]))
                for variant in ("original", "low")
            )
        elif final_geometry_adapter == "hunyuan-multiview-variants":
            required_paths.append(
                ("finalGeometry.script", resolve(repo_root, final_geometry["script"]))
            )
            required_paths.extend(
                (f"finalGeometry.views.{name}", resolve(repo_root, final_geometry["views"][name]))
                for name in ("front", "left", "back")
            )
        if final_skin_adapter == "existing-variants":
            required_paths.extend(
                (f"finalSkin.outputs.{variant}", resolve(repo_root, final_skin["outputs"][variant]))
                for variant in ("original", "low")
            )
        elif final_skin_adapter == "unirig-shared":
            required_paths.append(("finalSkin.script", resolve(repo_root, final_skin["script"])))
        if rig_profile_adapter == "existing":
            required_paths.append(("rigProfile.output", resolve(repo_root, rig_profile["output"])))
        else:
            required_paths.append(("rigProfile.script", resolve(repo_root, rig_profile["script"])))
        if final_adapter != "none":
            required_paths.extend(
                (
                    ("finalRig.script", resolve(repo_root, final_rig["script"])),
                    ("finalRig.template", resolve(repo_root, final_rig["template"])),
                )
            )
        required_paths.append(
            ("validation.final.script", resolve(repo_root, final_validation["script"]))
        )
        for label, path in required_paths:
            if not path.is_file():
                errors.append(f"{label} does not exist: {path}")

    return errors


def assert_valid_manifest(
    manifest: dict[str, Any], repo_root: Path, *, check_files: bool = False
) -> None:
    errors = validate_manifest(manifest, repo_root, check_files=check_files)
    if errors:
        raise ManifestError("Manifest validation failed:\n- " + "\n- ".join(errors))


def command_text(command: list[str]) -> str:
    return shlex.join(command)


def selected_run_stages(stage: str, *, skip_generation: bool) -> list[str]:
    stages = list(ALL_STAGES) if stage == "all" else [stage]
    if skip_generation:
        stages = [item for item in stages if item not in CLOUD_GENERATION_STAGES]
    return stages


def run_command(command: list[str], repo_root: Path) -> None:
    print("+", command_text(command))
    subprocess.run(command, cwd=repo_root, check=True)


def cached_output(path: Path, force: bool) -> bool:
    if not path.exists():
        return False
    if force:
        print(f"Will replace: {path}")
        return False
    print(f"Using cached output: {path}")
    return True


def ensure_files(paths: list[Path]) -> None:
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)


def reconstruction_command(repo_root: Path, manifest: dict[str, Any]) -> list[str] | None:
    config = manifest["reconstruction"]
    output = resolve(repo_root, config["output"])
    options = config.get("options", {})
    if config["adapter"] == "existing-glb":
        return None
    if config["adapter"] == "triposr-single":
        return [
            sys.executable,
            "-m",
            "modal",
            "run",
            str(resolve(repo_root, config["script"])),
            "--input",
            str(resolve(repo_root, manifest["references"]["singleImage"])),
            "--output",
            str(output),
            "--resolution",
            str(options.get("resolution", 192)),
        ]
    views = reference_view_paths(repo_root, manifest)
    return [
        sys.executable,
        "-m",
        "modal",
        "run",
        str(resolve(repo_root, config["script"])),
        "--front",
        str(views["front"]),
        "--left",
        str(views["left"]),
        "--back",
        str(views["back"]),
        "--output",
        str(output),
        "--seed",
        str(options.get("seed", 12345)),
        "--steps",
        str(options.get("steps", 50)),
        "--octree-resolution",
        str(options.get("octreeResolution", 380)),
        "--num-chunks",
        str(options.get("numChunks", 20000)),
    ]


def unirig_command(
    repo_root: Path,
    manifest: dict[str, Any],
    *,
    stage: str,
    input_path: Path,
    output_path: Path,
) -> list[str]:
    config = manifest["proxyRig"]
    command = [
        sys.executable,
        "-m",
        "modal",
        "run",
        str(resolve(repo_root, config["script"])),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--stage",
        stage,
    ]
    if stage != "inspect":
        options = config.get("options", {})
        command.extend(
            [
                "--seed",
                str(options.get("seed", 12345)),
                "--faces-target-count",
                str(options.get("facesTargetCount", 50000)),
            ]
        )
    return command


def final_geometry_sources(repo_root: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    config = manifest["finalGeometry"]
    if config["adapter"] == "reuse-reconstruction":
        source = resolve(repo_root, manifest["reconstruction"]["output"])
        return {"original": source, "low": source}
    return {
        variant: resolve(repo_root, config["outputs"][variant])
        for variant in ("original", "low")
    }


def final_sources(repo_root: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    config = manifest["finalSkin"]
    if config["adapter"] == "none":
        return final_geometry_sources(repo_root, manifest)
    return {
        variant: resolve(repo_root, config["outputs"][variant])
        for variant in ("original", "low")
    }


def rig_profile_path(repo_root: Path, manifest: dict[str, Any]) -> Path:
    return resolve(repo_root, manifest["rigProfile"]["output"])


def final_skin_commands(repo_root: Path, manifest: dict[str, Any]) -> list[tuple[str, list[str]]]:
    config = manifest["finalSkin"]
    if config["adapter"] != "unirig-shared":
        return []
    options = config.get("options", {})
    source_variant = str(options.get("sourceVariant", "original"))
    source = final_geometry_sources(repo_root, manifest)[source_variant]
    raw = resolve(repo_root, config["rawOutput"])
    script = resolve(repo_root, config["script"])
    commands: list[tuple[str, list[str]]] = [
        (
            "full",
            [
                sys.executable,
                "-m",
                "modal",
                "run",
                str(script),
                "--input",
                str(source),
                "--output",
                str(raw),
                "--stage",
                "full",
                "--seed",
                str(options.get("seed", 12345)),
                "--faces-target-count",
                str(options.get("facesTargetCount", 50000)),
            ],
        )
    ]
    targets = options.get("targetFaces", {"original": 500000, "low": 120000})
    for variant in ("original", "low"):
        commands.append(
            (
                variant,
                [
                    sys.executable,
                    "-m",
                    "modal",
                    "run",
                    str(script),
                    "--input",
                    str(raw),
                    "--output",
                    str(resolve(repo_root, config["outputs"][variant])),
                    "--stage",
                    "simplify",
                    "--target-faces",
                    str(int(targets[variant])),
                ],
            )
        )
    return commands


def rig_profile_command(repo_root: Path, manifest: dict[str, Any]) -> list[str] | None:
    config = manifest["rigProfile"]
    if config["adapter"] == "existing":
        return None
    options = config.get("options", {})
    source_variant = str(options.get("sourceVariant", "original"))
    command = [
        sys.executable,
        str(resolve(repo_root, config["script"])),
        "--rigged",
        str(final_sources(repo_root, manifest)[source_variant]),
        "--output",
        str(rig_profile_path(repo_root, manifest)),
        "--name",
        str(options.get("name", manifest["identity"]["nameEn"])),
        "--target-height",
        str(options.get("targetHeight", 1.9)),
        "--depth-offset",
        str(options.get("depthOffset", 0.08)),
        "--body-yaw-deg",
        str(options.get("bodyYawDeg", 0.0)),
    ]
    proxy = manifest["proxyRig"]
    if proxy["adapter"] != "none" and options.get("compareProxy", True):
        command.extend(
            ["--proxy-skeleton", str(resolve(repo_root, proxy["inspectionOutput"]))]
        )
    if options.get("strict") is True:
        command.append("--strict")
    return command


def final_geometry_commands(
    repo_root: Path, manifest: dict[str, Any]
) -> dict[str, list[str] | None]:
    config = manifest["finalGeometry"]
    if config["adapter"] != "hunyuan-multiview-variants":
        return {"original": None, "low": None}
    options = config.get("options", {})
    variants = options.get("variants", {})
    views = {
        name: resolve(repo_root, config["views"][name])
        for name in ("front", "left", "back")
    }
    commands: dict[str, list[str] | None] = {}
    for variant in ("original", "low"):
        variant_options = variants.get(variant, {})
        commands[variant] = [
            sys.executable,
            "-m",
            "modal",
            "run",
            str(resolve(repo_root, config["script"])),
            "--front",
            str(views["front"]),
            "--left",
            str(views["left"]),
            "--back",
            str(views["back"]),
            "--output",
            str(resolve(repo_root, config["outputs"][variant])),
            "--seed",
            str(variant_options.get("seed", options.get("seed", 12345))),
            "--steps",
            str(variant_options.get("steps", options.get("steps", 50))),
            "--octree-resolution",
            str(
                variant_options.get(
                    "octreeResolution",
                    options.get("octreeResolution", 380 if variant == "original" else 256),
                )
            ),
            "--num-chunks",
            str(variant_options.get("numChunks", options.get("numChunks", 20000))),
        ]
    return commands


def final_outputs(repo_root: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    return {
        variant: resolve(repo_root, manifest["finalRig"]["outputs"][variant])
        for variant in ("original", "low")
    }


def final_rig_command(
    repo_root: Path,
    manifest: dict[str, Any],
    source: Path,
    output: Path,
) -> list[str] | None:
    config = manifest["finalRig"]
    adapter = config["adapter"]
    if adapter == "none":
        return None
    command = [
        sys.executable,
        str(resolve(repo_root, config["script"])),
        str(source),
        str(output),
        "--template",
        str(resolve(repo_root, config["template"])),
    ]
    options = config.get("options", {})
    if adapter in {"hunyuan-component-aware", "unirig-template-transfer"}:
        command.extend(["--profile", str(rig_profile_path(repo_root, manifest))])
        if adapter == "unirig-template-transfer":
            command.extend(["--finger-mode", str(options.get("fingerMode", "rigid"))])
            command.extend(["--hand-pose", str(options.get("handPose", "source"))])
            command.extend(["--foot-mode", str(options.get("footMode", "rigid-shoe"))])
            command.extend(["--upper-body-mode", str(options.get("upperBodyMode", "none"))])
            command.extend(["--shoulder-mode", str(options.get("shoulderMode", "none"))])
    elif adapter == "nearest-template":
        command.extend(
            [
                "--target-height",
                str(options.get("targetHeight", 1.9)),
                "--rigid-prop-hand",
                str(options.get("rigidPropHand", "none")),
            ]
        )
        if options.get("mirrorX") is True:
            command.append("--mirror-x")
    return command


def plan_actions(repo_root: Path, manifest: dict[str, Any]) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    references = manifest["references"]
    if references["mode"] == "turnaround":
        actions.append(
            (
                "prepare-views",
                f"crop {resolve(repo_root, references['turnaround'])} -> "
                f"{resolve(repo_root, references['viewsDir'])}/{{front,left,back}}.png",
            )
        )
    else:
        actions.append(("prepare-views", "use declared reference images"))

    reconstruction = reconstruction_command(repo_root, manifest)
    if reconstruction is None:
        config = manifest["reconstruction"]
        actions.append(
            (
                "reconstruct",
                f"copy {resolve(repo_root, config['source'])} -> {resolve(repo_root, config['output'])}",
            )
        )
    else:
        actions.append(("reconstruct", command_text(reconstruction)))

    proxy = manifest["proxyRig"]
    if proxy["adapter"] == "none":
        actions.extend(
            (("proxy-skeleton", "disabled"), ("validate-proxy", "disabled"))
        )
    else:
        mesh = resolve(repo_root, manifest["reconstruction"]["output"])
        skeleton = resolve(repo_root, proxy["skeletonOutput"])
        inspection = resolve(repo_root, proxy["inspectionOutput"])
        actions.append(
            ("proxy-skeleton", command_text(unirig_command(repo_root, manifest, stage="skeleton", input_path=mesh, output_path=skeleton)))
        )
        actions.append(
            ("proxy-skeleton", command_text(unirig_command(repo_root, manifest, stage="inspect", input_path=skeleton, output_path=inspection)))
        )
        validation = proxy["validation"]
        actions.append(
            (
                "validate-proxy",
                command_text(
                    [
                        sys.executable,
                        str(resolve(repo_root, validation["script"])),
                        "--skeleton",
                        str(inspection),
                        "--mesh",
                        str(mesh),
                        "--output",
                        str(resolve(repo_root, validation["output"])),
                    ]
                ),
            )
        )

    geometry = manifest["finalGeometry"]
    geometry_commands = final_geometry_commands(repo_root, manifest)
    if geometry["adapter"] == "reuse-reconstruction":
        actions.append(("generate-final-mesh", "reuse reconstruction output for both variants"))
    elif geometry["adapter"] == "existing-variants":
        actions.append(("generate-final-mesh", "use declared original and low GLB variants"))
    else:
        for variant in ("original", "low"):
            assert geometry_commands[variant] is not None
            actions.append(
                ("generate-final-mesh", command_text(geometry_commands[variant]))
            )

    skin = manifest["finalSkin"]
    if skin["adapter"] == "none":
        actions.append(("skin-final-mesh", "disabled; final geometry already supplies weights"))
    elif skin["adapter"] == "existing-variants":
        actions.append(("skin-final-mesh", "use declared UniRig-skinned variants"))
    else:
        for label, command in final_skin_commands(repo_root, manifest):
            actions.append(("skin-final-mesh", f"{label}: {command_text(command)}"))

    profile_command = rig_profile_command(repo_root, manifest)
    actions.append(
        (
            "generate-rig-profile",
            command_text(profile_command)
            if profile_command
            else f"use existing profile {rig_profile_path(repo_root, manifest)}",
        )
    )

    sources = final_sources(repo_root, manifest)
    outputs = final_outputs(repo_root, manifest)
    for variant in ("original", "low"):
        command = final_rig_command(repo_root, manifest, sources[variant], outputs[variant])
        description = command_text(command) if command else f"copy {sources[variant]} -> {outputs[variant]}"
        actions.append(("rig-final", description))
    actions.append(
        (
            "validate-final",
            command_text(
                [
                    sys.executable,
                    str(resolve(repo_root, manifest["validation"]["final"]["script"])),
                    str(outputs["original"]),
                    str(outputs["low"]),
                ]
            ),
        )
    )
    models_dir = resolve(repo_root, manifest["publish"]["modelsDir"])
    slug = manifest["identity"]["slug"]
    actions.append(
        (
            "publish",
            f"copy validated GLBs to {models_dir / f'{slug}-original.glb'} and "
            f"{models_dir / f'{slug}-low.glb'}, then update catalog and provenance",
        )
    )
    return actions


def prepare_views_stage(repo_root: Path, manifest: dict[str, Any], force: bool) -> None:
    references = manifest["references"]
    if references["mode"] in {"single-image", "existing-glb"}:
        print("No multiview preparation required")
        return
    if references["mode"] != "turnaround":
        ensure_files(list(reference_view_paths(repo_root, manifest).values()))
        print("Using declared multiview references")
        return
    source = resolve(repo_root, references["turnaround"])
    output_dir = resolve(repo_root, references["viewsDir"])
    outputs = [output_dir / f"{name}.png" for name in ("front", "left", "back")]
    ensure_files([source])
    existing = [path for path in outputs if path.exists()]
    if existing and not force:
        if len(existing) == len(outputs):
            for path in outputs:
                print(f"Using cached output: {path}")
            return
        raise FileExistsError(f"Only part of the view set exists; pass --force: {output_dir}")
    background = tuple(int(value) for value in references.get("background", [238, 238, 238, 255]))
    print(f"Prepare views: {source} -> {output_dir}")
    crop_views(
        source,
        output_dir,
        crop_boxes=references["cropBoxes"],
        canvas_size=int(references.get("canvasSize", 1024)),
        suffix="",
        background=background,
    )
    ensure_files(outputs)


def reconstruct_stage(repo_root: Path, manifest: dict[str, Any], force: bool) -> None:
    config = manifest["reconstruction"]
    output = resolve(repo_root, config["output"])
    if cached_output(output, force):
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    command = reconstruction_command(repo_root, manifest)
    if command is None:
        source = resolve(repo_root, config["source"])
        ensure_files([source])
        if source != output:
            shutil.copy2(source, output)
    else:
        if config["adapter"] == "triposr-single":
            ensure_files([resolve(repo_root, manifest["references"]["singleImage"])])
        else:
            ensure_files(list(reference_view_paths(repo_root, manifest).values()))
        run_command(command, repo_root)
    ensure_files([output])


def proxy_skeleton_stage(repo_root: Path, manifest: dict[str, Any], force: bool) -> None:
    config = manifest["proxyRig"]
    if config["adapter"] == "none":
        print("Proxy skeleton stage disabled")
        return
    mesh = resolve(repo_root, manifest["reconstruction"]["output"])
    skeleton = resolve(repo_root, config["skeletonOutput"])
    inspection = resolve(repo_root, config["inspectionOutput"])
    ensure_files([mesh])
    if not cached_output(skeleton, force):
        skeleton.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            unirig_command(
                repo_root,
                manifest,
                stage="skeleton",
                input_path=mesh,
                output_path=skeleton,
            ),
            repo_root,
        )
        ensure_files([skeleton])
    if not cached_output(inspection, force):
        inspection.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            unirig_command(
                repo_root,
                manifest,
                stage="inspect",
                input_path=skeleton,
                output_path=inspection,
            ),
            repo_root,
        )
        ensure_files([inspection])


def validate_proxy_stage(repo_root: Path, manifest: dict[str, Any], force: bool) -> None:
    config = manifest["proxyRig"]
    if config["adapter"] == "none":
        print("Proxy validation stage disabled")
        return
    validation = config["validation"]
    skeleton = resolve(repo_root, config["inspectionOutput"])
    mesh = resolve(repo_root, manifest["reconstruction"]["output"])
    output = resolve(repo_root, validation["output"])
    ensure_files([skeleton, mesh, resolve(repo_root, validation["script"])])
    if output.exists() and not force:
        report = json.loads(output.read_text(encoding="utf-8"))
        if report.get("passed") is not True:
            raise ValueError(f"Cached proxy validation did not pass: {output}")
        if output.stat().st_mtime >= max(skeleton.stat().st_mtime, mesh.stat().st_mtime):
            print(f"Using cached output: {output}")
            return
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            sys.executable,
            str(resolve(repo_root, validation["script"])),
            "--skeleton",
            str(skeleton),
            "--mesh",
            str(mesh),
            "--output",
            str(output),
        ],
        repo_root,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    if report.get("passed") is not True:
        raise ValueError(f"Proxy validation did not pass: {output}")


def skin_proxy_stage(repo_root: Path, manifest: dict[str, Any], force: bool) -> None:
    config = manifest["proxyRig"]
    if config["adapter"] == "none":
        print("Proxy skinning stage disabled")
        return
    mesh = resolve(repo_root, manifest["reconstruction"]["output"])
    output = resolve(repo_root, config["riggedOutput"])
    if cached_output(output, force):
        return
    ensure_files([mesh])
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        unirig_command(repo_root, manifest, stage="full", input_path=mesh, output_path=output),
        repo_root,
    )
    ensure_files([output])


def generate_final_mesh_stage(
    repo_root: Path, manifest: dict[str, Any], force: bool
) -> None:
    config = manifest["finalGeometry"]
    adapter = config["adapter"]
    if adapter == "reuse-reconstruction":
        ensure_files([resolve(repo_root, manifest["reconstruction"]["output"])])
        print("Using reconstruction output as final geometry for both variants")
        return
    outputs = {
        variant: resolve(repo_root, config["outputs"][variant])
        for variant in ("original", "low")
    }
    if adapter == "existing-variants":
        ensure_files(list(outputs.values()))
        for path in outputs.values():
            print(f"Using declared final geometry: {path}")
        return
    views = [resolve(repo_root, config["views"][name]) for name in ("front", "left", "back")]
    ensure_files([resolve(repo_root, config["script"]), *views])
    commands = final_geometry_commands(repo_root, manifest)
    for variant in ("original", "low"):
        output = outputs[variant]
        if cached_output(output, force):
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        command = commands[variant]
        assert command is not None
        run_command(command, repo_root)
        ensure_files([output])


def skin_final_mesh_stage(
    repo_root: Path, manifest: dict[str, Any], force: bool
) -> None:
    config = manifest["finalSkin"]
    if config["adapter"] == "none":
        ensure_files(list(final_geometry_sources(repo_root, manifest).values()))
        print("Final geometry already supplies the skinning input")
        return
    outputs = {
        variant: resolve(repo_root, config["outputs"][variant])
        for variant in ("original", "low")
    }
    if config["adapter"] == "existing-variants":
        ensure_files(list(outputs.values()))
        for path in outputs.values():
            print(f"Using declared final skin: {path}")
        return
    commands = final_skin_commands(repo_root, manifest)
    raw = resolve(repo_root, config["rawOutput"])
    full_command = commands[0][1]
    if not cached_output(raw, force):
        ensure_files([final_geometry_sources(repo_root, manifest)[str(config.get("options", {}).get("sourceVariant", "original"))]])
        raw.parent.mkdir(parents=True, exist_ok=True)
        run_command(full_command, repo_root)
        ensure_files([raw])
    for label, command in commands[1:]:
        output = outputs[label]
        if cached_output(output, force):
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        run_command(command, repo_root)
        ensure_files([output])


def generate_rig_profile_stage(
    repo_root: Path, manifest: dict[str, Any], force: bool
) -> None:
    output = rig_profile_path(repo_root, manifest)
    command = rig_profile_command(repo_root, manifest)
    if command is None:
        ensure_files([output])
        print(f"Using existing rig profile: {output}")
        return
    if cached_output(output, force):
        return
    ensure_files([final_sources(repo_root, manifest)[str(manifest["rigProfile"].get("options", {}).get("sourceVariant", "original"))]])
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(command, repo_root)
    ensure_files([output])


def require_proxy_approval(repo_root: Path, manifest: dict[str, Any], approved: bool) -> None:
    proxy = manifest["proxyRig"]
    if proxy["adapter"] == "none":
        return
    report_path = resolve(repo_root, proxy["validation"]["output"])
    ensure_files([report_path])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("passed") is not True:
        raise ValueError(f"Proxy skeleton validation did not pass: {report_path}")
    if manifest["approvals"]["requireProxyVisual"] and not approved:
        raise ValueError("rig-final requires --approve-proxy after reviewing the skeleton overlay")


def rig_final_stage(
    repo_root: Path,
    manifest: dict[str, Any],
    force: bool,
    approve_proxy: bool,
) -> None:
    require_proxy_approval(repo_root, manifest, approve_proxy)
    sources = final_sources(repo_root, manifest)
    outputs = final_outputs(repo_root, manifest)
    ensure_files(list(sources.values()))
    for variant in ("original", "low"):
        output = outputs[variant]
        if cached_output(output, force):
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        command = final_rig_command(repo_root, manifest, sources[variant], output)
        if command is None:
            shutil.copy2(sources[variant], output)
        else:
            run_command(command, repo_root)
        ensure_files([output])


def validate_final_stage(repo_root: Path, manifest: dict[str, Any]) -> None:
    outputs = final_outputs(repo_root, manifest)
    validator = resolve(repo_root, manifest["validation"]["final"]["script"])
    ensure_files([validator, *outputs.values()])
    run_command(
        [sys.executable, str(validator), str(outputs["original"]), str(outputs["low"])],
        repo_root,
    )


def load_validator(path: Path):
    module_name = f"character_pipeline_validator_{hashlib.sha1(str(path).encode()).hexdigest()}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load validator: {path}")
    module = importlib.util.module_from_spec(spec)
    scripts_dir = str(path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec.loader.exec_module(module)
    if not hasattr(module, "validate"):
        raise AttributeError(f"Validator must expose validate(path): {path}")
    return module.validate


def validation_stats(repo_root: Path, manifest: dict[str, Any]) -> dict[str, dict[str, int | float]]:
    validator_path = resolve(repo_root, manifest["validation"]["final"]["script"])
    validate = load_validator(validator_path)
    stats: dict[str, dict[str, int | float]] = {}
    for variant, path in final_outputs(repo_root, manifest).items():
        result = validate(path)
        triangles = result.get("totalTriangles", result.get("triangles"))
        if triangles is None:
            raise ValueError(f"Validator did not report triangles for {path}")
        stats[variant] = {**result, "triangles": int(triangles)}
    return stats


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(repo_root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": relative_to_repo(repo_root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def catalog_preflight(
    repo_root: Path,
    manifest: dict[str, Any],
    *,
    replace_entry: bool,
) -> tuple[Path, list[dict[str, Any]]]:
    catalog = manifest["publish"]["catalog"]
    path = resolve(repo_root, catalog["path"])
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if not isinstance(entries, list):
        raise ValueError(f"Catalog source must contain an array: {path}")
    slug = manifest["identity"]["slug"]
    if any(entry.get("slug") == slug for entry in entries) and not replace_entry:
        raise ValueError(f"Catalog entry already exists for {slug}; pass --replace-entry")
    if catalog["mode"] == "source-template" and not any(
        entry.get("slug") == catalog["templateEntry"] for entry in entries
    ):
        raise ValueError(f"Missing catalog template entry: {catalog['templateEntry']}")
    return path, entries


def build_catalog_entry(
    repo_root: Path,
    manifest: dict[str, Any],
    entries: list[dict[str, Any]],
    stats: dict[str, dict[str, int | float]],
    destinations: dict[str, Path],
) -> dict[str, Any]:
    identity = manifest["identity"]
    publish = manifest["publish"]
    catalog = publish["catalog"]
    if catalog["mode"] == "source-template":
        entry = deepcopy(
            next(item for item in entries if item.get("slug") == catalog["templateEntry"])
        )
    else:
        entry = {}
    entry.update(deepcopy(publish["entry"]))
    entry.update(
        {
            "slug": identity["slug"],
            "name": identity["name"],
            "nameEn": identity["nameEn"],
            "bakedTriangles": int(stats["original"]["triangles"]),
            "sourceTriangles": int(stats["original"]["triangles"]),
            "lowTriangles": int(stats["low"]["triangles"]),
            "sourceSizeKB": round(destinations["original"].stat().st_size / 1024),
            "lowSizeKB": round(destinations["low"].stat().st_size / 1024),
        }
    )
    if catalog["mode"] == "direct":
        entry.setdefault("kind", identity["kind"])
        entry.setdefault("tier", identity["tier"])
        entry["models"] = {
            variant: relative_to_repo(repo_root, path) for variant, path in destinations.items()
        }
        entry.setdefault("textures", {"original": None, "low": None})
        entry.setdefault("preserveMaterials", True)
        if "animations" in stats["original"]:
            entry.setdefault("animationCount", int(stats["original"]["animations"]))
        if "joints" in stats["original"]:
            entry.setdefault("jointCount", int(stats["original"]["joints"]))
            entry.setdefault("rigSource", "template-transfer")
    return entry


def render_command_template(command: list[str], repo_root: Path, manifest: dict[str, Any]) -> list[str]:
    identity = manifest["identity"]
    replacements = {
        "{repo}": str(repo_root),
        "{slug}": identity["slug"],
        "{game}": identity["game"],
        "{kind}": identity["kind"],
    }
    rendered = []
    for value in command:
        for source, replacement in replacements.items():
            value = value.replace(source, replacement)
        rendered.append(value)
    return rendered


def write_provenance(
    repo_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    destinations: dict[str, Path],
    stats: dict[str, dict[str, int | float]],
) -> Path:
    output = resolve(repo_root, manifest["publish"]["provenanceOutput"])
    reference_paths: list[Path] = []
    references = manifest["references"]
    if references["mode"] == "single-image":
        reference_paths.append(resolve(repo_root, references["singleImage"]))
    elif references["mode"] in {"multiview", "turnaround"}:
        if references["mode"] == "turnaround":
            reference_paths.append(resolve(repo_root, references["turnaround"]))
        reference_paths.extend(reference_view_paths(repo_root, manifest).values())
    support_paths = [rig_profile_path(repo_root, manifest)]
    if manifest["finalRig"].get("template"):
        support_paths.append(resolve(repo_root, manifest["finalRig"]["template"]))
    intermediate_paths = [resolve(repo_root, manifest["reconstruction"]["output"])]
    proxy = manifest["proxyRig"]
    if proxy["adapter"] != "none":
        intermediate_paths.extend(
            resolve(repo_root, proxy[key])
            for key in ("skeletonOutput", "inspectionOutput")
        )
        intermediate_paths.append(resolve(repo_root, proxy["validation"]["output"]))
    intermediate_paths.extend(final_sources(repo_root, manifest).values())
    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "identity": manifest["identity"],
        "pipelineManifest": artifact_record(repo_root, manifest_path),
        "references": [artifact_record(repo_root, path) for path in reference_paths if path.is_file()],
        "supportAssets": [artifact_record(repo_root, path) for path in support_paths if path.is_file()],
        "intermediates": [
            artifact_record(repo_root, path)
            for path in dict.fromkeys(intermediate_paths)
            if path.is_file()
        ],
        "outputs": {
            variant: {**artifact_record(repo_root, path), "validation": stats[variant]}
            for variant, path in destinations.items()
        },
        "adapters": {
            "reconstruction": manifest["reconstruction"]["adapter"],
            "proxyRig": manifest["proxyRig"]["adapter"],
            "finalGeometry": manifest["finalGeometry"]["adapter"],
            "finalSkin": manifest["finalSkin"]["adapter"],
            "rigProfile": manifest["rigProfile"]["adapter"],
            "finalRig": manifest["finalRig"]["adapter"],
        },
        "rights": manifest["rights"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def publish_stage(
    repo_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    force: bool,
    approve_visual: bool,
    replace_entry: bool,
) -> None:
    if manifest["approvals"]["requireFinalVisual"] and not approve_visual:
        raise ValueError("publish requires --approve-visual after browser deformation review")
    catalog_path, entries = catalog_preflight(
        repo_root, manifest, replace_entry=replace_entry
    )
    outputs = final_outputs(repo_root, manifest)
    ensure_files(list(outputs.values()))
    models_dir = resolve(repo_root, manifest["publish"]["modelsDir"])
    slug = manifest["identity"]["slug"]
    destinations = {
        variant: models_dir / f"{slug}-{variant}.glb" for variant in ("original", "low")
    }
    for path in destinations.values():
        if path.exists() and not force:
            raise FileExistsError(f"Refusing to replace promoted model without --force: {path}")
    stats = validation_stats(repo_root, manifest)
    models_dir.mkdir(parents=True, exist_ok=True)
    for variant, destination in destinations.items():
        print(f"Publish {variant}: {outputs[variant]} -> {destination}")
        shutil.copy2(outputs[variant], destination)

    entry = build_catalog_entry(repo_root, manifest, entries, stats, destinations)
    matching = [index for index, item in enumerate(entries) if item.get("slug") == slug]
    if matching:
        entries[matching[0]] = entry
    else:
        entries.append(entry)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    merge_command = manifest["publish"]["catalog"].get("mergeCommand")
    if isinstance(merge_command, list) and merge_command:
        run_command(render_command_template(merge_command, repo_root, manifest), repo_root)
    provenance = write_provenance(
        repo_root, manifest_path, manifest, destinations, stats
    )
    print(f"Provenance: {provenance}")
    viewer_url = manifest["publish"]["catalog"].get("viewerUrl")
    if isinstance(viewer_url, str) and viewer_url:
        print(
            "Wiki URL:",
            viewer_url.format(
                slug=manifest["identity"]["slug"],
                game=manifest["identity"]["game"],
                kind=manifest["identity"]["kind"],
            ),
        )


def run_stage(
    stage: str,
    repo_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    if stage == "prepare-views":
        prepare_views_stage(repo_root, manifest, args.force)
    elif stage == "reconstruct":
        reconstruct_stage(repo_root, manifest, args.force)
    elif stage == "proxy-skeleton":
        proxy_skeleton_stage(repo_root, manifest, args.force)
    elif stage == "validate-proxy":
        validate_proxy_stage(repo_root, manifest, args.force)
    elif stage == "skin-proxy":
        skin_proxy_stage(repo_root, manifest, args.force)
    elif stage == "generate-final-mesh":
        generate_final_mesh_stage(repo_root, manifest, args.force)
    elif stage == "skin-final-mesh":
        skin_final_mesh_stage(repo_root, manifest, args.force)
    elif stage == "generate-rig-profile":
        generate_rig_profile_stage(repo_root, manifest, args.force)
    elif stage == "rig-final":
        rig_final_stage(repo_root, manifest, args.force, args.approve_proxy)
    elif stage == "validate-final":
        validate_final_stage(repo_root, manifest)
    elif stage == "publish":
        publish_stage(
            repo_root,
            manifest_path,
            manifest,
            force=args.force,
            approve_visual=args.approve_visual,
            replace_entry=args.replace_entry,
        )
    else:
        raise ValueError(f"Unsupported stage: {stage}")


def replace_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: replace_placeholders(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_placeholders(item, replacements) for item in value]
    if isinstance(value, str):
        for source, replacement in replacements.items():
            value = value.replace(source, replacement)
    return value


def initialize_manifest(
    template: dict[str, Any],
    *,
    slug: str,
    name: str,
    name_en: str,
    game: str,
    kind: str,
    tier: str,
) -> dict[str, Any]:
    replacements = {
        "__SLUG__": slug,
        "__NAME__": name,
        "__NAME_EN__": name_en,
        "__GAME__": game,
        "__KIND__": kind,
        "__TIER__": tier,
    }
    manifest = replace_placeholders(deepcopy(template), replacements)
    if game != "risk-of-rain-2" or kind != "hero":
        catalog_path = "catalog.json" if game == "risk-of-rain-2" else f"games/{game}/catalog.json"
        models_dir = "models/generated" if game == "risk-of-rain-2" else f"models/{game}/characters"
        manifest["publish"]["modelsDir"] = models_dir
        manifest["publish"]["catalog"] = {
            "mode": "direct",
            "path": catalog_path,
            "viewerUrl": "http://127.0.0.1:8765/?game={game}&monster={slug}",
        }
    return manifest


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser(repo_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan and run a manifest-driven image-to-rigged-Wiki character pipeline."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a new character pipeline manifest")
    init_parser.add_argument("--slug", required=True)
    init_parser.add_argument("--name", required=True)
    init_parser.add_argument("--name-en", required=True)
    init_parser.add_argument("--game", default="risk-of-rain-2")
    init_parser.add_argument("--kind", default="hero")
    init_parser.add_argument("--tier", default="英雄")
    init_parser.add_argument(
        "--template",
        type=Path,
        default=repo_root / "references" / "templates" / "character-pipeline.template.json",
    )
    init_parser.add_argument("--output", type=Path)
    init_parser.add_argument("--force", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="Validate a pipeline manifest")
    validate_parser.add_argument("--manifest", type=Path, required=True)
    validate_parser.add_argument("--check-files", action="store_true")

    plan_parser = subparsers.add_parser("plan", help="Print all local and GPU actions")
    plan_parser.add_argument("--manifest", type=Path, required=True)

    run_parser = subparsers.add_parser("run", help="Run one stage or all pre-publish stages")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--stage", choices=RUN_STAGES, default="all")
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Use cached reconstruction and UniRig outputs without launching cloud generation stages",
    )
    run_parser.add_argument("--approve-proxy", action="store_true")
    run_parser.add_argument("--approve-visual", action="store_true")
    run_parser.add_argument("--replace-entry", action="store_true")
    return parser


def main() -> None:
    repo_root = default_repo_root()
    parser = build_parser(repo_root)
    args = parser.parse_args()

    if args.command == "init":
        if not SLUG_PATTERN.fullmatch(args.slug):
            raise ValueError("--slug must contain lowercase letters, digits, and single hyphens only")
        template_path = args.template.expanduser().resolve()
        template = load_manifest(template_path)
        manifest = initialize_manifest(
            template,
            slug=args.slug,
            name=args.name,
            name_en=args.name_en,
            game=args.game,
            kind=args.kind,
            tier=args.tier,
        )
        output = (
            args.output.expanduser().resolve()
            if args.output
            else repo_root / "references" / "characters" / f"{args.slug}.json"
        )
        if output.exists() and not args.force:
            raise FileExistsError(f"Refusing to replace manifest without --force: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(output)
        return

    manifest_path = args.manifest.expanduser().resolve()
    manifest = load_manifest(manifest_path)
    assert_valid_manifest(
        manifest,
        repo_root,
        check_files=args.command == "validate" and args.check_files,
    )

    if args.command == "validate":
        print(f"Valid manifest: {manifest_path}")
        return
    if args.command == "plan":
        print(f"Pipeline: {manifest['pipeline']['name']}")
        print(f"Manifest: {manifest_path}")
        print("Publish is intentionally excluded from run --stage all.")
        for stage, action in plan_actions(repo_root, manifest):
            gate = ""
            if stage == "rig-final" and manifest["approvals"]["requireProxyVisual"]:
                gate = " [requires --approve-proxy]"
            elif stage == "publish" and manifest["approvals"]["requireFinalVisual"]:
                gate = " [requires --approve-visual]"
            print(f"{stage}{gate}: {action}")
        return

    print(f"Pipeline: {manifest['pipeline']['name']}")
    print(f"Manifest: {manifest_path}")
    stages = selected_run_stages(args.stage, skip_generation=args.skip_generation)
    for stage in stages:
        run_stage(stage, repo_root, manifest_path, manifest, args)


if __name__ == "__main__":
    main()
