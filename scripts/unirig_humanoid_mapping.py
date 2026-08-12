"""Semantic mappings for UniRig's stable humanoid bone topologies."""

from __future__ import annotations


CORE_PROFILE_BONES = {
    34: {
        "base": "bone_0",
        "pelvis": "bone_0",
        "stomach": "bone_1",
        "chest": "bone_2",
        "head": "bone_5",
        "upper_arm.r": "bone_7",
        "lower_arm.r": "bone_8",
        "hand.r": "bone_9",
        "upper_arm.l": "bone_17",
        "lower_arm.l": "bone_18",
        "hand.l": "bone_19",
        "thigh.r": "bone_26",
        "calf.r": "bone_27",
        "foot.r": "bone_28",
        "toe.r": "bone_29",
        "thigh.l": "bone_30",
        "calf.l": "bone_31",
        "foot.l": "bone_32",
        "toe.l": "bone_33",
    },
    52: {
        "base": "bone_0",
        "pelvis": "bone_0",
        "stomach": "bone_1",
        "chest": "bone_2",
        "head": "bone_5",
        "upper_arm.r": "bone_7",
        "lower_arm.r": "bone_8",
        "hand.r": "bone_9",
        "upper_arm.l": "bone_26",
        "lower_arm.l": "bone_27",
        "hand.l": "bone_28",
        "thigh.r": "bone_44",
        "calf.r": "bone_45",
        "foot.r": "bone_46",
        "toe.r": "bone_47",
        "thigh.l": "bone_48",
        "calf.l": "bone_49",
        "foot.l": "bone_50",
        "toe.l": "bone_51",
    },
}


MIRRORED_PROFILE_PAIRS = (
    ("upper_arm.r", "upper_arm.l"),
    ("lower_arm.r", "lower_arm.l"),
    ("hand.r", "hand.l"),
    ("thigh.r", "thigh.l"),
    ("calf.r", "calf.l"),
    ("foot.r", "foot.l"),
    ("toe.r", "toe.l"),
)


def _finger_chain(prefix: int, target_prefix: str, side: str) -> dict[str, str]:
    return {
        f"bone_{prefix}": f"{target_prefix}.1.{side}",
        f"bone_{prefix + 1}": f"{target_prefix}.2.{side}",
        f"bone_{prefix + 2}": f"{target_prefix}.3.{side}",
    }


def semantic_weight_mapping(bone_count: int, *, finger_mode: str = "rigid") -> dict[str, str]:
    if bone_count not in CORE_PROFILE_BONES:
        raise ValueError(f"Unsupported UniRig humanoid topology: {bone_count} bones")
    if finger_mode not in {"rigid", "mapped"}:
        raise ValueError("finger_mode must be rigid or mapped")

    mapping = {
        "bone_0": "pelvis",
        "bone_1": "stomach",
        "bone_2": "chest",
        "bone_3": "chest",
        "bone_4": "head",
        "bone_5": "head",
    }
    if bone_count == 34:
        mapping.update(
            {
                "bone_6": "upper_arm.r",
                "bone_7": "upper_arm.r",
                "bone_8": "lower_arm.r",
                "bone_9": "hand.r",
                "bone_16": "upper_arm.l",
                "bone_17": "upper_arm.l",
                "bone_18": "lower_arm.l",
                "bone_19": "hand.l",
                "bone_26": "thigh.r",
                "bone_27": "calf.r",
                "bone_28": "foot.r",
                "bone_29": "toe.r",
                "bone_30": "thigh.l",
                "bone_31": "calf.l",
                "bone_32": "foot.l",
                "bone_33": "toe.l",
            }
        )
        for index in range(10, 16):
            mapping[f"bone_{index}"] = "hand.r"
        for index in range(20, 26):
            mapping[f"bone_{index}"] = "hand.l"
        return mapping

    mapping.update(
        {
            "bone_6": "upper_arm.r",
            "bone_7": "upper_arm.r",
            "bone_8": "lower_arm.r",
            "bone_9": "hand.r",
            "bone_25": "upper_arm.l",
            "bone_26": "upper_arm.l",
            "bone_27": "lower_arm.l",
            "bone_28": "hand.l",
            "bone_44": "thigh.r",
            "bone_45": "calf.r",
            "bone_46": "foot.r",
            "bone_47": "toe.r",
            "bone_48": "thigh.l",
            "bone_49": "calf.l",
            "bone_50": "foot.l",
            "bone_51": "toe.l",
        }
    )
    if finger_mode == "rigid":
        for index in range(10, 25):
            mapping[f"bone_{index}"] = "hand.r"
        for index in range(29, 44):
            mapping[f"bone_{index}"] = "hand.l"
        return mapping

    mapping.update(_finger_chain(10, "thumb", "r"))
    mapping["bone_12"] = "thumb.2.r"
    mapping.update(_finger_chain(13, "finger1", "r"))
    mapping.update(_finger_chain(16, "finger2", "r"))
    mapping.update(_finger_chain(19, "finger3", "r"))
    mapping.update(_finger_chain(22, "finger4", "r"))
    mapping.update(_finger_chain(29, "thumb", "l"))
    mapping["bone_31"] = "thumb.2.l"
    mapping.update(_finger_chain(32, "finger1", "l"))
    mapping.update(_finger_chain(35, "finger2", "l"))
    mapping.update(_finger_chain(38, "finger3", "l"))
    mapping.update(_finger_chain(41, "finger4", "l"))
    return mapping
