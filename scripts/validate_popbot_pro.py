"""Validate POPBOT Pro's mobile budget, inherited rig, and attachments."""

from __future__ import annotations

import argparse
from pathlib import Path

from add_ror2_bandit_rig import read_accessor, read_glb
from validate_ror2_commando_game_rig import ESSENTIAL_JOINTS, EXPECTED_ANIMATIONS


MAX_TRIANGLES = 10_000
EXPECTED_CARRY_ANIMATIONS = {
    "Popbot_Idle",
    "Popbot_RunForward",
    "Popbot_RunBackward",
    "Popbot_RunLeft",
    "Popbot_RunRight",
    "Popbot_SprintForward",
}
EXPECTED_POPBOT_ANIMATIONS = EXPECTED_ANIMATIONS | {"Popbot_HeavyFire"} | EXPECTED_CARRY_ANIMATIONS
EXPECTED_MATERIALS = {
    "PopbotInnerSuit",
    "PopbotBlue",
    "PopbotDarkBlue",
    "PopbotMagenta",
    "PopbotYellow",
    "PopbotWhite",
    "PopbotGray",
    "PopbotSkin",
    "PopbotHair",
    "PopbotHairShadow",
    "PopbotFaceDecal",
}
EXPECTED_ATTACHMENTS = {
    "PopbotHead": "head",
    "PopbotFaceDecal": "head",
    "PopbotBackpack": "chest",
    "PopbotHeavyWeapon": "hand.r",
    "PopbotShoeL": "foot.l",
    "PopbotShoeR": "foot.r",
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
    design = extras.get("popbotPro", {})
    if rig.get("source") != "original-game-rig" or rig.get("originalGameRig") is not True:
        raise ValueError(f"{path}: missing inherited Commando rig metadata")
    if (
        design.get("sourceCharacter") != "commando"
        or design.get("mobileTriangleLimit") != MAX_TRIANGLES
        or design.get("commandoBaseMesh") is not False
        or design.get("customVisibleMesh") is not True
    ):
        raise ValueError(f"{path}: missing POPBOT design provenance or triangle limit")

    materials = {material.get("name") for material in document.get("materials", [])}
    if materials != EXPECTED_MATERIALS:
        raise ValueError(f"{path}: unexpected materials: {sorted(materials)}")
    skins = document.get("skins", [])
    if len(skins) != 1 or len(skins[0].get("joints", [])) != 78:
        raise ValueError(f"{path}: expected one 78-joint skin")
    joint_names = {document["nodes"][index].get("name") for index in skins[0]["joints"]}
    if not ESSENTIAL_JOINTS.issubset(joint_names):
        raise ValueError(f"{path}: inherited rig is missing essential joints")

    animations = document.get("animations", [])
    if {animation.get("name") for animation in animations} != EXPECTED_POPBOT_ANIMATIONS:
        raise ValueError(f"{path}: unexpected animation set")
    signatures = animation_signature(document, binary)
    template_signatures = animation_signature(template_document, template_binary)
    for name, signature in template_signatures.items():
        if signatures.get(name) != signature:
            raise ValueError(f"{path}: inherited animation curve changed: {name}")
    heavy_animation = next(animation for animation in animations if animation.get("name") == "Popbot_HeavyFire")
    if heavy_animation.get("extras", {}).get("proceduralTwoHandedWeapon") is not True:
        raise ValueError(f"{path}: missing POPBOT two-handed weapon animation metadata")
    for animation in animations:
        if animation.get("name") in EXPECTED_CARRY_ANIMATIONS and animation.get("extras", {}).get("proceduralHeavyCarry") is not True:
            raise ValueError(f"{path}: missing POPBOT heavy-carry animation metadata")

    nodes = document["nodes"]
    parents = {}
    for parent, node in enumerate(nodes):
        for child in node.get("children", []):
            parents[child] = parent
    body = next((node for node in nodes if node.get("name") == "PopbotBaseMesh"), None)
    if body is None or body.get("skin") != 0 or "mesh" not in body:
        raise ValueError(f"{path}: POPBOT base mesh is not bound to skin 0")
    for attachment_name, parent_name in EXPECTED_ATTACHMENTS.items():
        index = next((index for index, node in enumerate(nodes) if node.get("name") == attachment_name), None)
        if index is None or nodes[parents.get(index, -1)].get("name") != parent_name:
            raise ValueError(f"{path}: {attachment_name} is not attached to {parent_name}")
    if any(node.get("name") in {"GunMesh", "GunMesh.001"} and "mesh" in node for node in nodes):
        raise ValueError(f"{path}: inherited pistol mesh is still visible")

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
            joints = read_accessor(document, binary, attributes["JOINTS_0"])
            weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
            for joint_row, weight_row in zip(joints, weights):
                if max(joint_row) >= 78:
                    raise ValueError(f"{path}: joint index exceeds inherited skin")
                max_weight_error = max(max_weight_error, abs(sum(weight_row) - 1.0))
    if triangles > MAX_TRIANGLES:
        raise ValueError(f"{path}: {triangles} triangles exceeds mobile limit {MAX_TRIANGLES}")
    if triangles < 3_000:
        raise ValueError(f"{path}: generated model is unexpectedly sparse: {triangles}")
    if max_weight_error > 1e-5:
        raise ValueError(f"{path}: skin weights are not normalized: {max_weight_error}")
    return {
        "vertices": vertices,
        "triangles": triangles,
        "joints": 78,
        "animations": 25,
        "weightError": max_weight_error,
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        result = validate(path, args.template)
        print(
            f"{path}: {result['triangles']} triangles, {result['vertices']} vertices, "
            f"{result['joints']} joints, {result['animations']} animations, "
            f"weightError={result['weightError']:.2e}"
        )


if __name__ == "__main__":
    main()
