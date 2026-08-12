"""Validate Space Commando's original mesh and inherited Commando rig."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from add_ror2_bandit_rig import read_accessor, read_glb
from validate_ror2_commando_game_rig import ESSENTIAL_JOINTS, EXPECTED_ANIMATIONS


EXPECTED_MATERIALS = {
    "SpaceCommandoBasePalette",
    "SpaceCommandoCeramic",
    "SpaceCommandoPressureSuit",
    "SpaceCommandoTealArmor",
    "SpaceCommandoMirrorHelmet",
    "SpaceCommandoAmberLight",
    "SpaceCommandoTitanium",
    "SpaceCommandoSecondaryArmor",
    "SpaceCommandoGlassHelmet",
}

EXPECTED_ATTACHMENTS = {
    "SpaceCommandoForearmL": "lower_arm.l",
    "SpaceCommandoForearmR": "lower_arm.r",
    "SpaceCommandoShinL": "calf.l",
    "SpaceCommandoShinR": "calf.r",
}

EXPECTED_WEAPONS = {
    "SpaceCommandoBow": "chest",
    "SpaceCommandoBowStringDrawn": "chest",
    "SpaceCommandoBowStringRest": "chest",
    "SpaceCommandoArrow": "chest",
    "SpaceCommandoHeavyWeapon": "chest",
    "SpaceCommandoHeavyMuzzleFlash": "chest",
}

EXPECTED_CUSTOM_ANIMATIONS = {
    "SpaceCommando_BowShot",
    "SpaceCommando_HeavyFire",
}


def animation_signature(document: dict, binary: bytearray) -> dict[str, tuple]:
    signatures = {}
    for animation in document.get("animations", []):
        channels = []
        for channel in animation.get("channels", []):
            sampler = animation["samplers"][channel["sampler"]]
            target = channel["target"]
            channels.append(
                (
                    document["nodes"][target["node"]].get("name"),
                    target["path"],
                    sampler.get("interpolation", "LINEAR"),
                    tuple(read_accessor(document, binary, sampler["input"])),
                    tuple(read_accessor(document, binary, sampler["output"])),
                )
            )
        signatures[animation.get("name")] = tuple(channels)
    return signatures


def validate(path: Path, template: Path) -> dict[str, int | float]:
    document, binary = read_glb(path)
    template_document, template_binary = read_glb(template)
    extras = document.get("asset", {}).get("extras", {})
    rig = extras.get("rig", {})
    design = extras.get("spaceCommando", {})
    if rig.get("source") != "original-game-rig" or rig.get("originalGameRig") is not True:
        raise ValueError(f"{path}: missing inherited Commando original-game-rig metadata")
    if design.get("commandoBaseMesh") is not True or design.get("originalArmor") is not True or design.get("sourceCharacter") != "commando":
        raise ValueError(f"{path}: missing Space Commando mesh provenance")
    images = document.get("images", [])
    textures = document.get("textures", [])
    if len(images) != 1 or images[0].get("name") != "texSpaceCommandoPalette" or len(textures) != 1:
        raise ValueError(f"{path}: expected one custom Space Commando palette texture")
    materials = {material.get("name") for material in document.get("materials", [])}
    if materials != EXPECTED_MATERIALS:
        raise ValueError(f"{path}: unexpected material set: {sorted(materials)}")
    glass = next(
        material
        for material in document["materials"]
        if material.get("name") == "SpaceCommandoGlassHelmet"
    )
    glass_extensions = glass.get("extensions", {})
    required_glass_extensions = {
        "KHR_materials_transmission",
        "KHR_materials_ior",
        "KHR_materials_specular",
    }
    if glass.get("alphaMode") != "BLEND" or not required_glass_extensions.issubset(glass_extensions):
        raise ValueError(f"{path}: glass helmet is missing transparent PBR extensions")
    if not required_glass_extensions.issubset(document.get("extensionsUsed", [])):
        raise ValueError(f"{path}: glass helmet extensions are not declared")

    skins = document.get("skins", [])
    if len(skins) != 1 or len(skins[0].get("joints", [])) != 78:
        raise ValueError(f"{path}: expected one 78-joint skin")
    joint_names = {document["nodes"][index].get("name") for index in skins[0]["joints"]}
    if not ESSENTIAL_JOINTS.issubset(joint_names):
        raise ValueError(f"{path}: inherited rig is missing essential joints")

    animations = document.get("animations", [])
    animation_names = {animation.get("name") for animation in animations}
    if animation_names != EXPECTED_ANIMATIONS | EXPECTED_CUSTOM_ANIMATIONS:
        raise ValueError(f"{path}: unexpected animation set: {sorted(animation_names)}")
    signatures = animation_signature(document, binary)
    template_signatures = animation_signature(template_document, template_binary)
    for animation_name in EXPECTED_ANIMATIONS:
        if signatures.get(animation_name) != template_signatures.get(animation_name):
            raise ValueError(f"{path}: {animation_name} differs from the Commando template")

    nodes = document["nodes"]
    parents = {}
    for parent, node in enumerate(nodes):
        for child in node.get("children", []):
            parents[child] = parent
    body = next((node for node in nodes if node.get("name") == "SpaceCommandoMesh"), None)
    if body is None or body.get("skin") != 0 or "mesh" not in body:
        raise ValueError(f"{path}: SpaceCommandoMesh is not bound to skin 0")
    for gun_name, parent_name in (("SpaceCommandoGunL", "gun.l"), ("SpaceCommandoGunR", "gun.r")):
        index = next((index for index, node in enumerate(nodes) if node.get("name") == gun_name), None)
        if index is None or nodes[parents.get(index, -1)].get("name") != parent_name:
            raise ValueError(f"{path}: {gun_name} is not attached to {parent_name}")
    for attachment_name, parent_name in EXPECTED_ATTACHMENTS.items():
        index = next((index for index, node in enumerate(nodes) if node.get("name") == attachment_name), None)
        if index is None or nodes[parents.get(index, -1)].get("name") != parent_name:
            raise ValueError(f"{path}: {attachment_name} is not attached to {parent_name}")
    for weapon_name, parent_name in EXPECTED_WEAPONS.items():
        index = next((index for index, node in enumerate(nodes) if node.get("name") == weapon_name), None)
        if index is None or nodes[parents.get(index, -1)].get("name") != parent_name:
            raise ValueError(f"{path}: {weapon_name} is not attached to {parent_name}")
        if nodes[index].get("scale") != [0.0, 0.0, 0.0]:
            raise ValueError(f"{path}: {weapon_name} must be hidden outside its action")

    vertices = 0
    triangles = 0
    max_weight_error = 0.0
    for node in nodes:
        if "mesh" not in node:
            continue
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            positions = read_accessor(document, binary, primitive["attributes"]["POSITION"])
            indices = read_accessor(document, binary, primitive["indices"])
            vertices += len(positions)
            triangles += len(indices) // 3
            if "skin" not in node:
                continue
            attributes = primitive["attributes"]
            if "JOINTS_0" not in attributes or "WEIGHTS_0" not in attributes:
                raise ValueError(f"{path}: a body primitive is missing skin attributes")
            joints = read_accessor(document, binary, attributes["JOINTS_0"])
            weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
            if len(joints) != len(positions) or len(weights) != len(positions):
                raise ValueError(f"{path}: body skin attribute counts differ")
            for joint_row, weight_row in zip(joints, weights):
                if max(joint_row) >= 78:
                    raise ValueError(f"{path}: joint index exceeds inherited skin")
                max_weight_error = max(max_weight_error, abs(sum(weight_row) - 1.0))
    if max_weight_error > 1e-5:
        raise ValueError(f"{path}: skin weights are not normalized: {max_weight_error}")
    if triangles < 2500:
        raise ValueError(f"{path}: generated model is unexpectedly sparse: {triangles} triangles")
    return {
        "vertices": vertices,
        "triangles": triangles,
        "joints": 78,
        "animations": 20,
        "weightError": max_weight_error,
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--template",
        type=Path,
        default=repo_root / "models/survivors/commando-original.glb",
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.paths:
        try:
            result = validate(path, args.template)
            print(
                f"{path}: {result['triangles']} triangles, {result['vertices']} vertices, "
                f"{result['joints']} joints, {result['animations']} animations, "
                f"weightError={result['weightError']:.2e}"
            )
        except Exception as error:
            failed = True
            print(error, file=sys.stderr)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
