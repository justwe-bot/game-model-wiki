"""Keep rigid helmet geometry from stretching into a Mixamo character's chest."""

from __future__ import annotations

import argparse
from pathlib import Path

from add_ror2_bandit_rig import read_accessor, read_glb, write_glb
from stabilize_glb_upper_body_weights import write_accessor_rows


def canonical_name(value: str) -> str:
    return value.rsplit(":", 1)[-1]


def stabilized_helmet_row(
    joints: tuple[int, ...],
    weights: tuple[float, ...],
    *,
    head_slot: int,
    neck_slot: int,
    rigid_threshold: float,
    minimum_head_influence: float,
) -> tuple[tuple[int, int, int, int], tuple[float, float, float, float], str]:
    merged: dict[int, float] = {}
    for joint, weight in zip(joints, weights):
        if weight > 0.0:
            merged[int(joint)] = merged.get(int(joint), 0.0) + float(weight)

    head_weight = merged.get(head_slot, 0.0)
    if head_weight >= rigid_threshold:
        return (head_slot, 0, 0, 0), (1.0, 0.0, 0.0, 0.0), "rigid"

    if head_weight < minimum_head_influence:
        return (
            tuple(int(value) for value in joints),
            tuple(float(value) for value in weights),
            "unchanged",
        )

    # A helmet-to-neck transition may bend, but it must not be anchored to the
    # chest or shoulders. Keep the existing Head share and move every other
    # influence to Neck so the transition remains local to the cervical chain.
    neck_weight = max(0.0, 1.0 - head_weight)
    if neck_weight <= 1e-8:
        return (head_slot, 0, 0, 0), (1.0, 0.0, 0.0, 0.0), "rigid"
    return (
        (head_slot, neck_slot, 0, 0),
        (head_weight, neck_weight, 0.0, 0.0),
        "localized",
    )


def stabilize_mixamo_helmet(
    source: Path,
    destination: Path,
    *,
    rigid_threshold: float = 0.35,
    minimum_head_influence: float = 0.02,
) -> dict[str, int | float]:
    document, binary = read_glb(source)
    mesh_nodes = [
        node for node in document.get("nodes", []) if "mesh" in node and "skin" in node
    ]
    if len(mesh_nodes) != 1:
        raise ValueError(f"Expected one visible skinned mesh, found {len(mesh_nodes)}")

    node = mesh_nodes[0]
    skin = document["skins"][node["skin"]]
    joint_names = [
        canonical_name(document["nodes"][index].get("name", ""))
        for index in skin["joints"]
    ]
    if "Head" not in joint_names or "Neck" not in joint_names:
        raise ValueError("Target skin must contain Mixamo Head and Neck joints")
    head_slot = joint_names.index("Head")
    neck_slot = joint_names.index("Neck")

    vertices = 0
    changed = 0
    rigid = 0
    localized = 0
    for primitive in document["meshes"][node["mesh"]].get("primitives", []):
        attributes = primitive.get("attributes", {})
        required = {"JOINTS_0", "WEIGHTS_0"}
        if not required.issubset(attributes):
            continue
        joint_rows = read_accessor(document, binary, attributes["JOINTS_0"])
        weight_rows = read_accessor(document, binary, attributes["WEIGHTS_0"])
        next_joints: list[tuple[int, int, int, int]] = []
        next_weights: list[tuple[float, float, float, float]] = []
        for joints, weights in zip(joint_rows, weight_rows):
            next_row = stabilized_helmet_row(
                tuple(int(value) for value in joints),
                tuple(float(value) for value in weights),
                head_slot=head_slot,
                neck_slot=neck_slot,
                rigid_threshold=rigid_threshold,
                minimum_head_influence=minimum_head_influence,
            )
            next_joints.append(next_row[0])
            next_weights.append(next_row[1])
            if next_row[2] != "unchanged":
                changed += 1
            if next_row[2] == "rigid":
                rigid += 1
            elif next_row[2] == "localized":
                localized += 1
        write_accessor_rows(document, binary, attributes["JOINTS_0"], next_joints)
        write_accessor_rows(document, binary, attributes["WEIGHTS_0"], next_weights)
        vertices += len(joint_rows)

    document.setdefault("asset", {}).setdefault("extras", {}).setdefault(
        "meshFixes", []
    ).append(
        {
            "type": "mixamo-rigid-helmet-weight-stabilization",
            "changedVertices": changed,
            "rigidVertices": rigid,
            "localizedVertices": localized,
            "rigidThreshold": rigid_threshold,
            "minimumHeadInfluence": minimum_head_influence,
        }
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_glb(destination, document, binary)
    return {
        "vertices": vertices,
        "changedVertices": changed,
        "rigidVertices": rigid,
        "localizedVertices": localized,
        "rigidThreshold": rigid_threshold,
        "minimumHeadInfluence": minimum_head_influence,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--rigid-threshold", type=float, default=0.35)
    parser.add_argument("--minimum-head-influence", type=float, default=0.02)
    args = parser.parse_args()
    print(
        stabilize_mixamo_helmet(
            args.source.resolve(),
            args.destination.resolve(),
            rigid_threshold=args.rigid_threshold,
            minimum_head_influence=args.minimum_head_influence,
        )
    )


if __name__ == "__main__":
    main()
