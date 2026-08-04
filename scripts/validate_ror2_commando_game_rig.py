"""Validate Commando's original Unity rig, weights, and animation clips."""

from __future__ import annotations

import argparse
from pathlib import Path

from add_ror2_bandit_rig import read_accessor, read_glb


EXPECTED_ANIMATIONS = {
    "Commando_Idle",
    "Commando_RunForward",
    "Commando_RunBackward",
    "Commando_RunLeft",
    "Commando_RunRight",
    "Commando_SprintForward",
    "Commando_Jump",
    "Commando_RollForward",
    "Commando_RollBackward",
    "Commando_RollLeft",
    "Commando_RollRight",
    "Commando_SlideForward",
    "Commando_FirePistolLeft",
    "Commando_FirePistolRight",
    "Commando_ReloadPistols",
    "Commando_FireFMJ",
    "Commando_FireBarrage",
    "Commando_ThrowGrenade",
}
EXPECTED_SKINNED_MESHES = {
    "CommandoMesh",
}
ESSENTIAL_JOINTS = {
    "ROOT",
    "base",
    "stomach",
    "chest",
    "upper_arm.l",
    "lower_arm.l",
    "hand.l",
    "upper_arm.r",
    "lower_arm.r",
    "hand.r",
    "pelvis",
    "thigh.l",
    "calf.l",
    "foot.l",
    "thigh.r",
    "calf.r",
    "foot.r",
}


def validate(path: Path) -> dict[str, int | float]:
    document, binary = read_glb(path)
    rig = document.get("asset", {}).get("extras", {}).get("rig", {})
    if rig.get("source") != "original-game-rig" or rig.get("originalGameRig") is not True:
        raise ValueError(f"{path}: missing original-game-rig provenance")
    skins = document.get("skins", [])
    if len(skins) != 1:
        raise ValueError(f"{path}: expected one skin, found {len(skins)}")
    skin = skins[0]
    if skin.get("extras", {}).get("originalGameRig") is not True:
        raise ValueError(f"{path}: skin provenance is not marked as original")
    if len(skin["joints"]) != 78:
        raise ValueError(f"{path}: expected 78 original joints, found {len(skin['joints'])}")
    joint_names = {document["nodes"][index].get("name") for index in skin["joints"]}
    if not ESSENTIAL_JOINTS.issubset(joint_names):
        raise ValueError(f"{path}: original hierarchy is missing essential joints")
    inverse_bind = read_accessor(document, binary, skin["inverseBindMatrices"])
    if len(inverse_bind) != len(skin["joints"]):
        raise ValueError(f"{path}: inverse bind matrix count does not match joints")

    skinned_meshes = set()
    vertex_count = 0
    triangle_count = 0
    weight_error = 0.0
    for node in document["nodes"]:
        if "mesh" not in node:
            continue
        if "skin" in node:
            skinned_meshes.add(node.get("name"))
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            attributes = primitive["attributes"]
            positions = read_accessor(document, binary, attributes["POSITION"])
            vertex_count += len(positions)
            indices = read_accessor(document, binary, primitive["indices"])
            triangle_count += len(indices) // 3
            if "skin" not in node:
                continue
            for semantic in ("JOINTS_0", "WEIGHTS_0"):
                if semantic not in attributes:
                    raise ValueError(f"{path}: {node.get('name')} is missing {semantic}")
            joints = read_accessor(document, binary, attributes["JOINTS_0"])
            weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
            if len(joints) != len(positions) or len(weights) != len(positions):
                raise ValueError(f"{path}: skin attribute counts do not match")
            for joint_row, weight_row in zip(joints, weights):
                if max(joint_row) >= len(skin["joints"]):
                    raise ValueError(f"{path}: joint index exceeds original rig")
                weight_error = max(weight_error, abs(sum(weight_row) - 1.0))
    if skinned_meshes != EXPECTED_SKINNED_MESHES:
        raise ValueError(f"{path}: unexpected skinned meshes: {sorted(skinned_meshes)}")
    if weight_error > 1e-4:
        raise ValueError(f"{path}: weights are not normalized: {weight_error}")

    animations = document.get("animations", [])
    animation_names = {animation.get("name") for animation in animations}
    if animation_names != EXPECTED_ANIMATIONS:
        raise ValueError(f"{path}: unexpected animation set: {sorted(animation_names)}")
    for animation in animations:
        targeted_nodes = set()
        for channel in animation["channels"]:
            sampler = animation["samplers"][channel["sampler"]]
            times = read_accessor(document, binary, sampler["input"])
            values = read_accessor(document, binary, sampler["output"])
            if len(times) != len(values) or not times:
                raise ValueError(f"{path}: {animation['name']} has invalid key counts")
            if any(times[index][0] >= times[index + 1][0] for index in range(len(times) - 1)):
                raise ValueError(f"{path}: {animation['name']} key times are not increasing")
            if channel["target"]["path"] == "rotation":
                for quaternion in values:
                    if abs(sum(value * value for value in quaternion) - 1.0) > 1e-4:
                        raise ValueError(f"{path}: {animation['name']} has a non-unit quaternion")
            targeted_nodes.add(document["nodes"][channel["target"]["node"]].get("name"))
        if len(targeted_nodes & ESSENTIAL_JOINTS) < 10:
            raise ValueError(f"{path}: {animation['name']} does not target the original body hierarchy")

    return {
        "vertices": vertex_count,
        "triangles": triangle_count,
        "joints": len(skin["joints"]),
        "animations": len(animations),
        "weightError": weight_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        result = validate(path)
        print(
            f"{path}: {result['triangles']} triangles, {result['vertices']} vertices, "
            f"{result['joints']} original joints, {result['animations']} animations, "
            f"weightError={result['weightError']:.2e}"
        )


if __name__ == "__main__":
    main()
