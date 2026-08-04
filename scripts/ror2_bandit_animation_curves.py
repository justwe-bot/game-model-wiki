"""Parse selected original Risk of Rain 2 Bandit animation curves."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re


@dataclass(frozen=True)
class ClipSpec:
    file_name: str
    name: str
    label: str
    kind: str


CLIPS = (
    ClipSpec("Bandit_SelectPoseIdle.anim", "Bandit_SelectPoseIdle", "待机", "idle"),
    ClipSpec("BanditArmature_RunForward_ MainWeapon.anim", "Bandit_RunForward", "向前移动", "movement"),
    ClipSpec("BanditArmature_RunBackward_ MainWeapon.anim", "Bandit_RunBackward", "向后移动", "movement"),
    ClipSpec("BanditArmature_RunLeft_ MainWeapon.anim", "Bandit_RunLeft", "向左移动", "movement"),
    ClipSpec("BanditArmature_RunRight_ MainWeapon.anim", "Bandit_RunRight", "向右移动", "movement"),
    ClipSpec("BanditArmature_FireMainWeapon.anim", "Bandit_FireMainWeapon", "主武器攻击", "attack"),
    ClipSpec("BanditArmature_Reload.anim", "Bandit_Reload", "装填", "other"),
    ClipSpec("BanditArmature_SlashBlade.anim", "Bandit_SlashBlade", "近战挥砍", "attack"),
)


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
KEY_PATTERN = re.compile(
    rf"time:\s*({NUMBER})\s*\r?\n\s*value:\s*\{{x:\s*({NUMBER}),\s*y:\s*({NUMBER}),\s*z:\s*({NUMBER})\}}"
)
QUATERNION_KEY_PATTERN = re.compile(
    rf"time:\s*({NUMBER})\s*\r?\n\s*value:\s*\{{x:\s*({NUMBER}),\s*y:\s*({NUMBER}),\s*z:\s*({NUMBER}),\s*w:\s*({NUMBER})\}}"
)


def quat_mul(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def quat_normalize(value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    length = math.sqrt(sum(component * component for component in value))
    return tuple(component / length for component in value)


def axis_angle(axis: tuple[float, float, float], angle: float) -> tuple[float, float, float, float]:
    half = math.radians(angle) * 0.5
    sine = math.sin(half)
    return axis[0] * sine, axis[1] * sine, axis[2] * sine, math.cos(half)


def euler_quaternion(value: tuple[float, float, float]) -> tuple[float, float, float, float]:
    rotate_x = axis_angle((1.0, 0.0, 0.0), value[0])
    rotate_y = axis_angle((0.0, 1.0, 0.0), value[1])
    rotate_z = axis_angle((0.0, 0.0, 1.0), value[2])
    return quat_normalize(quat_mul(quat_mul(rotate_z, rotate_y), rotate_x))


def parse_rotation_curves(
    path: Path,
) -> dict[str, list[tuple[float, tuple[float, float, float, float]]]]:
    text = path.read_text(encoding="utf-8")
    quaternion_curves = "  m_RotationCurves: []" not in text
    marker = "  m_RotationCurves:" if quaternion_curves else "  m_EulerCurves:"
    next_marker = "\n  m_CompressedRotationCurves:" if quaternion_curves else "\n  m_PositionCurves:"
    start = text.find(marker)
    end = text.find(next_marker, start)
    if start < 0 or end < 0:
        raise ValueError(f"{path}: rotation curve section was not found")
    curves = {}
    for block in re.split(r"\n  - curve:\s*\n", text[start:end])[1:]:
        path_match = re.search(r"\n    path:\s*(.+?)\s*\r?$", block, re.MULTILINE)
        if not path_match:
            continue
        if quaternion_curves:
            keys = [
                (
                    float(match.group(1)),
                    quat_normalize(tuple(float(match.group(index)) for index in range(2, 6))),
                )
                for match in QUATERNION_KEY_PATTERN.finditer(block)
            ]
        else:
            keys = [
                (
                    float(match.group(1)),
                    euler_quaternion(tuple(float(match.group(index)) for index in range(2, 5))),
                )
                for match in KEY_PATTERN.finditer(block)
            ]
        if keys:
            curves[path_match.group(1)] = keys
    if not curves:
        raise ValueError(f"{path}: no rotation keys were parsed")
    return curves
