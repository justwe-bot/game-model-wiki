"""Build a game-ready POPBOT heavy gunner on the Commando animation rig.

Only the 78-joint skeleton and 18 animation clips are inherited. All visible
geometry is authored for the supplied POPBOT_PRO_01 character sheet so the
mobile triangle budget is spent on the character rather than hidden source art.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import io
import json
import math
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFilter

from add_ror2_bandit_rig import append_accessor, read_glb, write_glb
from build_space_commando import (
    MeshBuilder,
    add,
    animation_duration,
    append_animation_channel,
    append_buffer_view,
    append_mesh,
    cross,
    length,
    lerp,
    matrix_inverse,
    matrix_position,
    mesh_stats,
    normalize,
    sample_animation_value,
    scale,
    solve_arm_ik,
    sub,
    world_matrices,
)


MAX_TRIANGLES = 10_000


def material(name: str, color: tuple[float, float, float, float], *, metallic: float = 0.0) -> dict:
    return {
        "name": name,
        "pbrMetallicRoughness": {
            "baseColorFactor": list(color),
            "metallicFactor": metallic,
            "roughnessFactor": 0.76 if metallic == 0.0 else 0.43,
        },
    }


MATERIALS = [
    material("PopbotInnerSuit", (0.018, 0.022, 0.03, 1.0)),
    material("PopbotBlue", (0.0, 0.25, 1.0, 1.0)),
    material("PopbotDarkBlue", (0.0, 0.075, 0.30, 1.0)),
    material("PopbotMagenta", (1.0, 0.0, 0.48, 1.0)),
    material("PopbotYellow", (1.0, 0.74, 0.0, 1.0)),
    material("PopbotWhite", (0.97, 0.98, 1.0, 1.0)),
    material("PopbotGray", (0.32, 0.35, 0.39, 1.0), metallic=0.34),
    material("PopbotSkin", (1.0, 0.76, 0.70, 1.0)),
    material("PopbotHair", (0.92, 0.96, 1.0, 1.0)),
    material("PopbotHairShadow", (0.58, 0.73, 0.96, 1.0)),
    {
        "name": "PopbotFaceDecal",
        "pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0},
            "metallicFactor": 0.0,
            "roughnessFactor": 1.0,
        },
        "alphaMode": "BLEND",
        "doubleSided": True,
    },
]

SUIT = 0
BLUE = 1
DARK_BLUE = 2
MAGENTA = 3
YELLOW = 4
WHITE = 5
GRAY = 6
SKIN = 7
HAIR = 8
HAIR_SHADOW = 9
FACE = 10

Vec3 = tuple[float, float, float]
Influences = tuple[tuple[int, float], ...]


class PopbotMeshBuilder(MeshBuilder):
    def weighted_vertex(
        self,
        material_index: int,
        position: Vec3,
        normal: Vec3,
        influences: Influences,
    ) -> int:
        if not self.skinned:
            return self.vertex(material_index, position, normal, None)
        primitive = self.primitives[material_index]
        index = len(primitive.positions)
        primitive.positions.append(position)
        primitive.normals.append(normalize(normal))
        ordered = sorted(influences, key=lambda item: item[1], reverse=True)[:4]
        total = sum(weight for _, weight in ordered)
        if total <= 1e-8:
            raise ValueError("Weighted geometry requires a positive influence")
        joints = [joint for joint, _ in ordered]
        weights = [weight / total for _, weight in ordered]
        while len(joints) < 4:
            joints.append(0)
            weights.append(0.0)
        primitive.joints.append(tuple(joints))
        primitive.weights.append(tuple(weights))
        return index

    def loft_y(
        self,
        rings: list[tuple[float, float, float, float, float, Influences]],
        material_index: int,
        segments: int,
        *,
        capped: bool = True,
    ) -> None:
        rows: list[list[int]] = []
        for y, center_x, center_z, radius_x, radius_z, influences in rings:
            row = []
            for segment in range(segments):
                angle = math.tau * segment / segments
                cos_angle = math.cos(angle)
                sin_angle = math.sin(angle)
                position = (
                    center_x + radius_x * cos_angle,
                    y,
                    center_z + radius_z * sin_angle,
                )
                normal = (cos_angle / max(radius_x, 1e-6), 0.0, sin_angle / max(radius_z, 1e-6))
                row.append(self.weighted_vertex(material_index, position, normal, influences))
            rows.append(row)
        for upper, lower in zip(rows, rows[1:]):
            for segment in range(segments):
                next_segment = (segment + 1) % segments
                self.triangle(material_index, upper[segment], upper[next_segment], lower[next_segment])
                self.triangle(material_index, upper[segment], lower[next_segment], lower[segment])
        if not capped:
            return
        for ring_index, normal, reverse in ((0, (0.0, -1.0, 0.0), True), (-1, (0.0, 1.0, 0.0), False)):
            y, center_x, center_z, _, _, influences = rings[ring_index]
            center = self.weighted_vertex(material_index, (center_x, y, center_z), normal, influences)
            row = rows[ring_index]
            for segment in range(segments):
                next_segment = (segment + 1) % segments
                if reverse:
                    self.triangle(material_index, center, row[next_segment], row[segment])
                else:
                    self.triangle(material_index, center, row[segment], row[next_segment])

    def loft_y_open(
        self,
        rings: list[tuple[float, float, float, float, float, Influences]],
        material_index: int,
        segments: int,
        *,
        opening_radians: float,
    ) -> None:
        """Build an open-front vertical shell for the bomber jacket."""
        start_angle = -math.pi * 0.5 + opening_radians * 0.5
        arc = math.tau - opening_radians
        rows: list[list[int]] = []
        for y, center_x, center_z, radius_x, radius_z, influences in rings:
            row = []
            for segment in range(segments + 1):
                angle = start_angle + arc * segment / segments
                cos_angle = math.cos(angle)
                sin_angle = math.sin(angle)
                row.append(
                    self.weighted_vertex(
                        material_index,
                        (
                            center_x + radius_x * cos_angle,
                            y,
                            center_z + radius_z * sin_angle,
                        ),
                        (
                            cos_angle / max(radius_x, 1e-6),
                            0.0,
                            sin_angle / max(radius_z, 1e-6),
                        ),
                        influences,
                    )
                )
            rows.append(row)
        for upper, lower in zip(rows, rows[1:]):
            for segment in range(segments):
                self.triangle(material_index, upper[segment], upper[segment + 1], lower[segment + 1])
                self.triangle(material_index, upper[segment], lower[segment + 1], lower[segment])

    def tube_between(
        self,
        start: Vec3,
        end: Vec3,
        rings: list[tuple[float, float, float, Influences]],
        material_index: int,
        segments: int,
        *,
        capped: bool = True,
        outward_hint: Vec3 = (0.0, 0.0, -1.0),
    ) -> None:
        axis = normalize(sub(end, start))
        outward = sub(outward_hint, scale(axis, sum(outward_hint[i] * axis[i] for i in range(3))))
        if length(outward) < 1e-5:
            outward = (0.0, 1.0, 0.0)
        outward = normalize(outward)
        right = normalize(cross(axis, outward))
        outward = normalize(cross(right, axis))
        rows: list[list[int]] = []
        for amount, radius_right, radius_outward, influences in rings:
            center = lerp(start, end, amount)
            row = []
            for segment in range(segments):
                angle = math.tau * segment / segments
                cos_angle = math.cos(angle)
                sin_angle = math.sin(angle)
                radial = add(scale(right, radius_right * cos_angle), scale(outward, radius_outward * sin_angle))
                normal = add(
                    scale(right, cos_angle / max(radius_right, 1e-6)),
                    scale(outward, sin_angle / max(radius_outward, 1e-6)),
                )
                row.append(self.weighted_vertex(material_index, add(center, radial), normal, influences))
            rows.append(row)
        for upper, lower in zip(rows, rows[1:]):
            for segment in range(segments):
                next_segment = (segment + 1) % segments
                self.triangle(material_index, upper[segment], upper[next_segment], lower[next_segment])
                self.triangle(material_index, upper[segment], lower[next_segment], lower[segment])
        if not capped:
            return
        for ring_index, normal, reverse in ((0, scale(axis, -1.0), True), (-1, axis, False)):
            amount, _, _, influences = rings[ring_index]
            center = self.weighted_vertex(material_index, lerp(start, end, amount), normal, influences)
            row = rows[ring_index]
            for segment in range(segments):
                next_segment = (segment + 1) % segments
                if reverse:
                    self.triangle(material_index, center, row[next_segment], row[segment])
                else:
                    self.triangle(material_index, center, row[segment], row[next_segment])

    def prism_xy(
        self,
        center: Vec3,
        outline: list[tuple[float, float]],
        depth: float,
        material_index: int,
        influences: Influences | None,
        *,
        rotation: float = 0.0,
    ) -> None:
        cos_rotation = math.cos(rotation)
        sin_rotation = math.sin(rotation)

        def point(x: float, y: float, z: float) -> Vec3:
            return (
                center[0] + x * cos_rotation - y * sin_rotation,
                center[1] + x * sin_rotation + y * cos_rotation,
                center[2] + z,
            )

        def add_vertex(position: Vec3, normal: Vec3) -> int:
            if self.skinned:
                if influences is None:
                    raise ValueError("Skinned prism requires influences")
                return self.weighted_vertex(material_index, position, normal, influences)
            return self.vertex(material_index, position, normal, None)

        front_z = -depth * 0.5
        back_z = depth * 0.5
        front = [add_vertex(point(x, y, front_z), (0.0, 0.0, -1.0)) for x, y in outline]
        back = [add_vertex(point(x, y, back_z), (0.0, 0.0, 1.0)) for x, y in outline]
        for index in range(1, len(outline) - 1):
            self.triangle(material_index, front[0], front[index + 1], front[index])
            self.triangle(material_index, back[0], back[index], back[index + 1])
        for index, next_index in zip(range(len(outline)), list(range(1, len(outline))) + [0]):
            x1, y1 = outline[index]
            x2, y2 = outline[next_index]
            edge = (x2 - x1, y2 - y1)
            normal = normalize((edge[1], -edge[0], 0.0))
            vertices = [
                add_vertex(point(x1, y1, front_z), normal),
                add_vertex(point(x2, y2, front_z), normal),
                add_vertex(point(x2, y2, back_z), normal),
                add_vertex(point(x1, y1, back_z), normal),
            ]
            self.triangle(material_index, vertices[0], vertices[1], vertices[2])
            self.triangle(material_index, vertices[0], vertices[2], vertices[3])

    def prism_xz(
        self,
        center: Vec3,
        outline: list[tuple[float, float]],
        height: float,
        material_index: int,
        influences: Influences | None,
        *,
        rotation: float = 0.0,
    ) -> None:
        """Extrude an XZ outline along Y for sneaker soles and toe blocks."""
        cos_rotation = math.cos(rotation)
        sin_rotation = math.sin(rotation)

        def point(x: float, z: float, y: float) -> Vec3:
            rotated_x = x * cos_rotation + z * sin_rotation
            rotated_z = -x * sin_rotation + z * cos_rotation
            return (center[0] + rotated_x, center[1] + y, center[2] + rotated_z)

        def add_vertex(position: Vec3, normal: Vec3) -> int:
            if self.skinned:
                if influences is None:
                    raise ValueError("Skinned prism requires influences")
                return self.weighted_vertex(material_index, position, normal, influences)
            return self.vertex(material_index, position, normal, None)

        bottom_y = -height * 0.5
        top_y = height * 0.5
        bottom = [add_vertex(point(x, z, bottom_y), (0.0, -1.0, 0.0)) for x, z in outline]
        top = [add_vertex(point(x, z, top_y), (0.0, 1.0, 0.0)) for x, z in outline]
        for index in range(1, len(outline) - 1):
            self.triangle(material_index, bottom[0], bottom[index + 1], bottom[index])
            self.triangle(material_index, top[0], top[index], top[index + 1])
        for index, next_index in zip(range(len(outline)), list(range(1, len(outline))) + [0]):
            x1, z1 = outline[index]
            x2, z2 = outline[next_index]
            edge = (x2 - x1, z2 - z1)
            normal = normalize((edge[1], 0.0, -edge[0]))
            vertices = [
                add_vertex(point(x1, z1, bottom_y), normal),
                add_vertex(point(x2, z2, bottom_y), normal),
                add_vertex(point(x2, z2, top_y), normal),
                add_vertex(point(x1, z1, top_y), normal),
            ]
            self.triangle(material_index, vertices[0], vertices[1], vertices[2])
            self.triangle(material_index, vertices[0], vertices[2], vertices[3])


def face_texture_png() -> bytes:
    concept = Image.open(Path(__file__).resolve().parents[1] / "references/popbot-pro-concept.png").convert("RGBA")
    crop = concept.crop((231, 86, 294, 141))
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).polygon(
        [
            (6, 6),
            (25, 0),
            (46, 0),
            (60, 8),
            (58, 17),
            (54, 29),
            (47, 36),
            (37, 39),
            (23, 38),
            (10, 33),
            (3, 28),
            (1, 17),
        ],
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(1.1))
    crop.putalpha(mask)
    crop = crop.resize((232, 202), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    image.alpha_composite(crop, (12, 27))
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def prepare_template(source_document: dict, source_binary: bytearray, *, variant: str) -> tuple[dict, bytearray]:
    document = deepcopy(source_document)
    binary = bytearray(source_binary)
    document["materials"] = deepcopy(MATERIALS)
    face_view = append_buffer_view(document, binary, face_texture_png())
    document["images"] = [{"name": "texPopbotFace", "bufferView": face_view, "mimeType": "image/png"}]
    document["textures"] = [{"sampler": 0, "source": 0}]
    document["samplers"] = [{"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071}]
    document.setdefault("asset", {}).update(
        {
            "generator": "game-model-wiki POPBOT custom character builder",
            "extras": {
                "rig": {
                    "source": "original-game-rig",
                    "originalGameRig": True,
                    "jointCount": 78,
                    "animations": 25,
                },
                "popbotPro": {
                    "commandoBaseMesh": False,
                    "customVisibleMesh": True,
                    "sourceCharacter": "commando",
                    "concept": "POPBOT_PRO_01 character sheet",
                    "mobileTriangleLimit": MAX_TRIANGLES,
                    "variant": variant,
                },
            },
        }
    )
    document["skins"][0]["name"] = "PopbotCommandoGameRig"
    document["skins"][0].setdefault("extras", {})["originalGameRig"] = True
    return document, binary


def rig_context(document: dict) -> tuple[list[list[list[float]]], dict[str, int], dict[str, Vec3], dict[str, int]]:
    nodes = document["nodes"]
    matrices = world_matrices(document)
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes) if node.get("name")}
    positions = {name: matrix_position(matrices[index]) for name, index in node_by_name.items()}
    skin = document["skins"][0]
    joint_by_name = {nodes[node_index].get("name"): slot for slot, node_index in enumerate(skin["joints"])}
    return matrices, node_by_name, positions, joint_by_name


def influence(joint: int, amount: float = 1.0) -> Influences:
    return ((joint, amount),)


def blend(first: int, second: int, amount: float) -> Influences:
    return ((first, 1.0 - amount), (second, amount))


def build_character_mesh(document: dict, *, detail: str) -> PopbotMeshBuilder:
    _, _, positions, joint = rig_context(document)
    segments = 26 if detail == "original" else 16
    limb_segments = 18 if detail == "original" else 12
    builder = PopbotMeshBuilder(skinned=True)

    pelvis = joint["pelvis"]
    stomach = joint["stomach"]
    chest = joint["chest"]
    # Slim inner torso and neck remain visible through the jacket opening.
    builder.loft_y(
        [
            (0.91, 0.0, 0.015, 0.165, 0.10, influence(pelvis)),
            (1.03, 0.0, 0.01, 0.18, 0.11, blend(pelvis, stomach, 0.35)),
            (1.18, 0.0, 0.005, 0.135, 0.085, influence(stomach)),
            (1.36, 0.0, 0.01, 0.145, 0.09, blend(stomach, chest, 0.72)),
            (1.50, 0.0, 0.025, 0.15, 0.095, influence(chest)),
        ],
        SUIT,
        segments,
    )
    builder.cylinder((0.0, 1.47, 0.025), (0.0, 1.59, 0.035), 0.068, 0.064, SKIN, chest, limb_segments)

    # High-waisted fitted shorts remain visible beneath the oversized hem.
    builder.loft_y(
        [
            (0.90, 0.0, 0.0, 0.175, 0.115, influence(pelvis)),
            (0.985, 0.0, 0.0, 0.19, 0.125, influence(pelvis)),
            (1.06, 0.0, 0.005, 0.18, 0.115, blend(pelvis, stomach, 0.2)),
        ],
        SUIT,
        segments,
    )
    builder.prism_xy((0.0, 1.045, -0.128), [(-0.14, -0.026), (0.14, -0.026), (0.12, 0.026), (-0.12, 0.026)], 0.018, GRAY, blend(pelvis, stomach, 0.2))

    # Fuller bare thighs and asymmetric socks match the character sheet.
    for suffix in ("l", "r"):
        thigh_joint = joint[f"thigh.{suffix}"]
        calf_joint = joint[f"calf.{suffix}"]
        builder.tube_between(
            positions[f"thigh.{suffix}"],
            positions[f"calf.{suffix}"],
            [
                (0.05, 0.083, 0.086, influence(thigh_joint)),
                (0.32, 0.087, 0.089, influence(thigh_joint)),
                (0.67, 0.075, 0.078, blend(thigh_joint, calf_joint, 0.15)),
                (0.89, 0.064, 0.067, blend(thigh_joint, calf_joint, 0.45)),
            ],
            SKIN,
            limb_segments,
        )

        sock_material = SUIT if suffix == "l" else WHITE
        foot_joint = joint[f"foot.{suffix}"]
        builder.tube_between(
            positions[f"calf.{suffix}"],
            positions[f"foot.{suffix}"],
            [
                (0.07, 0.066, 0.069, influence(calf_joint)),
                (0.31, 0.068, 0.071, influence(calf_joint)),
                (0.68, 0.073, 0.076, blend(calf_joint, foot_joint, 0.16)),
                (0.86, 0.082, 0.082, blend(calf_joint, foot_joint, 0.44)),
            ],
            sock_material,
            limb_segments,
        )
        builder.tube_between(
            lerp(positions[f"calf.{suffix}"], positions[f"foot.{suffix}"], 0.03),
            lerp(positions[f"calf.{suffix}"], positions[f"foot.{suffix}"], 0.12),
            [
                (0.0, 0.071, 0.073, influence(calf_joint)),
                (1.0, 0.071, 0.073, influence(calf_joint)),
            ],
            WHITE if suffix == "l" else YELLOW,
            limb_segments,
        )
        marker = lerp(positions[f"calf.{suffix}"], positions[f"foot.{suffix}"], 0.48)
        builder.prism_xy((marker[0], marker[1], marker[2] - 0.071), [(-0.035, -0.009), (0.035, -0.009), (0.035, 0.009), (-0.035, 0.009)], 0.012, YELLOW, influence(calf_joint), rotation=0.72)
        builder.prism_xy((marker[0], marker[1], marker[2] - 0.072), [(-0.035, -0.009), (0.035, -0.009), (0.035, 0.009), (-0.035, 0.009)], 0.012, YELLOW, influence(calf_joint), rotation=-0.72)

    # True open-front bomber shell: narrow ribbed hem, broad dropped shoulders,
    # deep back volume, and enough negative space to read the crop top beneath.
    builder.loft_y_open(
        [
            (0.99, 0.0, 0.045, 0.29, 0.17, blend(stomach, chest, 0.22)),
            (1.07, 0.0, 0.045, 0.335, 0.19, blend(stomach, chest, 0.45)),
            (1.22, 0.0, 0.05, 0.37, 0.215, influence(chest)),
            (1.39, 0.0, 0.05, 0.35, 0.20, influence(chest)),
            (1.52, 0.0, 0.045, 0.285, 0.165, influence(chest)),
        ],
        BLUE,
        22 if detail == "original" else 14,
        opening_radians=1.35,
    )
    # White crop top, black rabbit graphic, exposed midriff, and layered lapels.
    builder.prism_xy((0.0, 1.34, -0.142), [(-0.12, -0.17), (0.12, -0.17), (0.13, 0.10), (0.07, 0.19), (-0.07, 0.19), (-0.13, 0.10)], 0.022, WHITE, influence(chest))
    builder.prism_xy((0.0, 1.36, -0.159), [(-0.055, -0.08), (0.055, -0.08), (0.075, 0.045), (0.0, 0.11), (-0.075, 0.045)], 0.014, SUIT, influence(chest))
    builder.prism_xy((0.0, 1.115, -0.125), [(-0.105, -0.035), (0.105, -0.035), (0.10, 0.035), (-0.10, 0.035)], 0.016, SKIN, influence(stomach))
    builder.prism_xy((-0.205, 1.29, -0.19), [(-0.09, -0.25), (0.035, -0.23), (0.075, 0.16), (-0.015, 0.23), (-0.10, 0.11)], 0.035, BLUE, influence(chest), rotation=-0.02)
    builder.prism_xy((0.205, 1.29, -0.19), [(-0.035, -0.23), (0.09, -0.25), (0.10, 0.11), (0.015, 0.23), (-0.075, 0.16)], 0.035, BLUE, influence(chest), rotation=0.02)
    builder.prism_xy((-0.145, 1.035, -0.17), [(-0.125, -0.045), (0.125, -0.045), (0.105, 0.045), (-0.105, 0.045)], 0.028, DARK_BLUE, blend(stomach, chest, 0.25))
    builder.prism_xy((0.145, 1.035, -0.17), [(-0.125, -0.045), (0.125, -0.045), (0.105, 0.045), (-0.105, 0.045)], 0.028, DARK_BLUE, blend(stomach, chest, 0.25))
    builder.prism_xy((-0.135, 1.47, -0.17), [(-0.05, -0.10), (0.035, -0.12), (0.08, 0.08), (0.015, 0.13)], 0.025, MAGENTA, influence(chest), rotation=-0.08)
    builder.prism_xy((0.135, 1.47, -0.17), [(-0.035, -0.12), (0.05, -0.10), (-0.015, 0.13), (-0.08, 0.08)], 0.025, MAGENTA, influence(chest), rotation=0.08)
    for x in (-0.225, 0.225):
        builder.prism_xy((x, 1.24, -0.185), [(-0.012, -0.17), (0.012, -0.17), (0.012, 0.17), (-0.012, 0.17)], 0.018, GRAY, influence(chest))

    # Oversized dropped sleeves carry most of the bomber silhouette.
    for suffix in ("l", "r"):
        upper_joint = joint[f"upper_arm.{suffix}"]
        lower_joint = joint[f"lower_arm.{suffix}"]
        hand_joint = joint[f"hand.{suffix}"]
        builder.tube_between(
            positions[f"upper_arm.{suffix}"],
            positions[f"lower_arm.{suffix}"],
            [
                (0.02, 0.15, 0.155, influence(upper_joint)),
                (0.22, 0.175, 0.17, influence(upper_joint)),
                (0.58, 0.162, 0.158, blend(upper_joint, lower_joint, 0.22)),
                (0.88, 0.125, 0.12, blend(upper_joint, lower_joint, 0.58)),
            ],
            BLUE,
            limb_segments,
        )
        builder.tube_between(
            positions[f"lower_arm.{suffix}"],
            positions[f"hand.{suffix}"],
            [
                (0.02, 0.128, 0.125, influence(lower_joint)),
                (0.27, 0.148, 0.143, influence(lower_joint)),
                (0.66, 0.12, 0.116, blend(lower_joint, hand_joint, 0.18)),
                (0.86, 0.079, 0.075, blend(lower_joint, hand_joint, 0.46)),
            ],
            BLUE,
            limb_segments,
        )
        cuff = lerp(positions[f"lower_arm.{suffix}"], positions[f"hand.{suffix}"], 0.82)
        builder.tube_between(
            lerp(positions[f"lower_arm.{suffix}"], positions[f"hand.{suffix}"], 0.76),
            lerp(positions[f"lower_arm.{suffix}"], positions[f"hand.{suffix}"], 0.91),
            [(0.0, 0.085, 0.08, influence(lower_joint)), (1.0, 0.078, 0.073, blend(lower_joint, hand_joint, 0.65))],
            SUIT,
            limb_segments,
        )
        hand = positions[f"hand.{suffix}"]
        builder.ellipsoid(hand, (0.055, 0.068, 0.046), SKIN, hand_joint, limb_segments, 7 if detail == "original" else 5)
        builder.cylinder((hand[0], hand[1] - 0.005, hand[2] - 0.012), (hand[0], hand[1] - 0.075, hand[2] - 0.018), 0.026, 0.018, SKIN, hand_joint, 8 if detail == "original" else 6)
        thumb_direction = -1.0 if suffix == "l" else 1.0
        builder.cylinder(hand, (hand[0] + thumb_direction * 0.045, hand[1] - 0.025, hand[2] - 0.005), 0.021, 0.015, SKIN, hand_joint, 8 if detail == "original" else 6)

    # Jacket patches and hardware use geometry so they survive small textures.
    chest_inf = influence(chest)
    builder.prism_xy((-0.175, 1.36, -0.218), [(-0.038, -0.05), (0.038, -0.05), (0.043, 0.038), (0.0, 0.06), (-0.043, 0.038)], 0.014, WHITE, chest_inf)
    builder.prism_xy((-0.195, 1.445, -0.219), [(-0.016, -0.06), (0.016, -0.06), (0.013, 0.06), (-0.013, 0.06)], 0.014, WHITE, chest_inf, rotation=-0.12)
    builder.prism_xy((-0.145, 1.445, -0.219), [(-0.016, -0.06), (0.016, -0.06), (0.013, 0.06), (-0.013, 0.06)], 0.014, WHITE, chest_inf, rotation=0.12)
    builder.prism_xy((0.185, 1.235, -0.218), [(-0.055, -0.013), (0.055, -0.013), (0.055, 0.013), (-0.055, 0.013)], 0.014, YELLOW, chest_inf, rotation=0.74)
    builder.prism_xy((0.185, 1.235, -0.219), [(-0.055, -0.013), (0.055, -0.013), (0.055, 0.013), (-0.055, 0.013)], 0.014, YELLOW, chest_inf, rotation=-0.74)
    # Back bunny patch and star.
    builder.prism_xy((0.0, 1.34, 0.264), [(-0.085, -0.065), (0.085, -0.065), (0.095, 0.045), (0.0, 0.10), (-0.095, 0.045)], 0.016, WHITE, chest_inf)
    builder.prism_xy((-0.04, 1.47, 0.264), [(-0.02, -0.085), (0.02, -0.085), (0.015, 0.085), (-0.015, 0.085)], 0.016, WHITE, chest_inf, rotation=-0.1)
    builder.prism_xy((0.04, 1.47, 0.264), [(-0.02, -0.085), (0.02, -0.085), (0.015, 0.085), (-0.015, 0.085)], 0.016, WHITE, chest_inf, rotation=0.1)
    builder.prism_xy((0.0, 1.205, 0.266), [(-0.067, -0.015), (0.067, -0.015), (0.067, 0.015), (-0.067, 0.015)], 0.016, MAGENTA, chest_inf)
    builder.prism_xy((0.0, 1.205, 0.267), [(-0.067, -0.015), (0.067, -0.015), (0.067, 0.015), (-0.067, 0.015)], 0.016, MAGENTA, chest_inf, rotation=math.pi / 2)

    # Waist belt, buckle, hanging straps, and knee bandage.
    builder.prism_xy((0.0, 1.005, -0.132), [(-0.185, -0.018), (0.185, -0.018), (0.185, 0.018), (-0.185, 0.018)], 0.016, GRAY, blend(pelvis, stomach, 0.2))
    builder.prism_xy((0.0, 1.005, -0.144), [(-0.04, -0.03), (0.04, -0.03), (0.04, 0.03), (-0.04, 0.03)], 0.016, YELLOW, blend(pelvis, stomach, 0.2))
    builder.prism_xy((-0.155, 0.93, -0.108), [(-0.022, -0.09), (0.022, -0.09), (0.022, 0.09), (-0.022, 0.09)], 0.014, GRAY, influence(pelvis), rotation=-0.03)
    right_knee = lerp(positions["thigh.r"], positions["calf.r"], 0.66)
    builder.prism_xy((right_knee[0], right_knee[1], right_knee[2] - 0.072), [(-0.048, -0.035), (0.048, -0.035), (0.055, 0.02), (0.0, 0.055), (-0.055, 0.02)], 0.014, MAGENTA, influence(joint["thigh.r"]), rotation=0.16)

    return builder


def local_attachment(
    document: dict,
    parent_name: str,
    name: str,
    builder: MeshBuilder,
) -> tuple[int, str, MeshBuilder]:
    matrices, node_by_name, _, _ = rig_context(document)
    parent_index = node_by_name[parent_name]
    builder.transform(matrix_inverse(matrices[parent_index]))
    return parent_index, name, builder


def build_head(document: dict, *, detail: str) -> tuple[int, str, MeshBuilder]:
    _, _, _, _ = rig_context(document)
    segments = 30 if detail == "original" else 18
    builder = PopbotMeshBuilder(skinned=False)
    center_z = -0.025
    # Larger anime head with full cheeks and a tapered chin.
    builder.loft_y(
        [
            (1.535, 0.0, center_z, 0.052, 0.058, ()),
            (1.575, 0.0, center_z - 0.004, 0.105, 0.105, ()),
            (1.64, 0.0, center_z - 0.008, 0.152, 0.137, ()),
            (1.72, 0.0, center_z, 0.172, 0.148, ()),
            (1.80, 0.0, center_z + 0.008, 0.165, 0.145, ()),
            (1.87, 0.0, center_z + 0.02, 0.135, 0.12, ()),
            (1.905, 0.0, center_z + 0.03, 0.085, 0.075, ()),
        ],
        SKIN,
        segments,
    )
    # Hair cap sits behind the face while layered panels create the sharp bob.
    builder.loft_y(
        [
            (1.53, 0.0, 0.065, 0.105, 0.11, ()),
            (1.60, 0.0, 0.075, 0.17, 0.16, ()),
            (1.71, 0.0, 0.08, 0.19, 0.175, ()),
            (1.82, 0.0, 0.08, 0.18, 0.165, ()),
            (1.91, 0.0, 0.075, 0.12, 0.11, ()),
        ],
        HAIR,
        segments,
    )

    # Pointed white bangs sit in front of the face decal.
    bang_centers = (-0.13, -0.09, -0.047, -0.005, 0.04, 0.085, 0.127)
    for index, x in enumerate(bang_centers):
        length_y = 0.115 + (0.03 if index in (2, 3, 4) else 0.0)
        builder.prism_xy(
            (x, 1.84 - length_y * 0.10, -0.194),
            [
                (-0.028, length_y * 0.5),
                (0.028, length_y * 0.5),
                (0.022, length_y * 0.08),
                (0.0, -length_y * 0.5),
                (-0.022, length_y * 0.08),
            ],
            0.024,
            HAIR,
            None,
            rotation=(index - 3.0) * 0.05,
        )
    # Geometry facial features remain legible after mobile texture filtering.
    eye_outline = [(-0.047, -0.012), (0.047, -0.008), (0.034, 0.018), (-0.03, 0.021)]
    iris_outline = [(-0.026, -0.009), (0.026, -0.006), (0.019, 0.013), (-0.018, 0.014)]
    for x, accent, rotation in ((-0.07, DARK_BLUE, -0.10), (0.07, MAGENTA, 0.10)):
        builder.prism_xy((x, 1.725, -0.213), eye_outline, 0.008, SUIT, None, rotation=rotation)
        builder.prism_xy((x, 1.724, -0.218), iris_outline, 0.006, accent, None, rotation=rotation)
        builder.box((x + 0.01, 1.732, -0.223), (0.011, 0.012, 0.005), WHITE, None)
    builder.prism_xy((-0.07, 1.782, -0.213), [(-0.043, -0.006), (0.043, -0.006), (0.043, 0.006), (-0.043, 0.006)], 0.006, SUIT, None, rotation=-0.12)
    builder.prism_xy((0.07, 1.782, -0.213), [(-0.043, -0.006), (0.043, -0.006), (0.043, 0.006), (-0.043, 0.006)], 0.006, SUIT, None, rotation=0.12)
    builder.prism_xy((0.012, 1.646, -0.206), [(-0.018, -0.004), (0.018, -0.004), (0.012, 0.006), (-0.01, 0.006)], 0.006, MAGENTA, None, rotation=-0.04)
    for side in (-1.0, 1.0):
        x = side * 0.158
        builder.prism_xy(
            (x, 1.625, -0.14),
            [(-0.038, -0.15), (0.038, -0.15), (0.045, 0.11), (0.0, 0.16), (-0.045, 0.11)],
            0.038,
            HAIR,
            None,
            rotation=side * 0.05,
        )
        builder.prism_xy(
            (x * 1.04, 1.49, -0.142),
            [(-0.026, -0.08), (0.026, -0.08), (0.032, 0.05), (0.0, 0.09), (-0.032, 0.05)],
            0.04,
            HAIR_SHADOW,
            None,
            rotation=side * 0.07,
        )

    # Fitted blue cap, short bill, and tall asymmetric rabbit ears.
    builder.ellipsoid((0.0, 1.925, 0.06), (0.185, 0.082, 0.155), BLUE, None, segments, 8 if detail == "original" else 5)
    builder.prism_xy((0.0, 1.89, -0.132), [(-0.11, -0.018), (0.11, -0.018), (0.085, 0.035), (-0.085, 0.035)], 0.055, DARK_BLUE, None)
    builder.box((-0.13, 1.94, 0.015), (0.04, 0.04, 0.022), SUIT, None, rotation=(0.0, 0.0, 0.78))
    builder.box((0.13, 1.94, 0.015), (0.04, 0.04, 0.022), YELLOW, None, rotation=(0.0, 0.0, -0.78))
    ear_outline = [(-0.055, -0.19), (0.055, -0.19), (0.065, -0.08), (0.054, 0.11), (0.032, 0.19), (0.0, 0.235), (-0.032, 0.19), (-0.054, 0.11), (-0.065, -0.08)]
    inner_outline = [(-0.025, -0.145), (0.025, -0.145), (0.032, -0.055), (0.022, 0.105), (0.0, 0.17), (-0.022, 0.105), (-0.032, -0.055)]
    builder.prism_xy((-0.087, 2.105, 0.05), ear_outline, 0.055, MAGENTA, None, rotation=-0.13)
    builder.prism_xy((-0.087, 2.11, 0.017), inner_outline, 0.014, DARK_BLUE, None, rotation=-0.13)
    builder.prism_xy((0.095, 2.11, 0.05), ear_outline, 0.055, BLUE, None, rotation=0.16)
    builder.prism_xy((0.095, 2.115, 0.017), inner_outline, 0.014, MAGENTA, None, rotation=0.16)

    # Headphones include black cups, pink X badges, and yellow fasteners.
    sides = 14 if detail == "original" else 9
    for side in (-1.0, 1.0):
        cup_x = side * 0.195
        builder.cylinder((side * 0.165, 1.73, 0.045), (cup_x, 1.73, 0.045), 0.072, 0.068, SUIT, None, sides)
        builder.cylinder((side * 0.196, 1.73, 0.045), (side * 0.218, 1.73, 0.045), 0.052, 0.052, MAGENTA, None, sides)
        builder.box((side * 0.224, 1.73, 0.045), (0.018, 0.085, 0.018), SUIT, None, rotation=(0.0, 0.0, 0.72))
        builder.box((side * 0.225, 1.73, 0.045), (0.018, 0.085, 0.018), SUIT, None, rotation=(0.0, 0.0, -0.72))
        builder.box((side * 0.235, 1.78, 0.045), (0.02, 0.05, 0.05), YELLOW, None)

    # Hair tie and split ponytail create the long side/back silhouette.
    builder.cylinder((0.075, 1.74, 0.17), (0.125, 1.69, 0.20), 0.055, 0.055, MAGENTA, None, sides)
    builder.tube_between((0.12, 1.69, 0.19), (0.19, 1.43, 0.25), [(0.0, 0.07, 0.056, ()), (0.48, 0.082, 0.062, ()), (1.0, 0.048, 0.036, ())], HAIR, sides)
    builder.tube_between((0.19, 1.43, 0.25), (0.135, 1.18, 0.27), [(0.0, 0.05, 0.038, ()), (0.55, 0.057, 0.042, ()), (1.0, 0.018, 0.013, ())], HAIR_SHADOW, sides)
    builder.tube_between((0.105, 1.67, 0.20), (0.04, 1.40, 0.27), [(0.0, 0.05, 0.04, ()), (0.55, 0.058, 0.044, ()), (1.0, 0.022, 0.016, ())], HAIR, sides)
    return local_attachment(document, "head", "PopbotHead", builder)


def append_face_decal(document: dict, binary: bytearray) -> tuple[int, str]:
    matrices, node_by_name, _, _ = rig_context(document)
    parent_index = node_by_name["head"]
    inverse = matrix_inverse(matrices[parent_index])

    def transform_point(position: Vec3) -> Vec3:
        return (
            sum(inverse[0][axis] * position[axis] for axis in range(3)) + inverse[0][3],
            sum(inverse[1][axis] * position[axis] for axis in range(3)) + inverse[1][3],
            sum(inverse[2][axis] * position[axis] for axis in range(3)) + inverse[2][3],
        )

    positions = [
        transform_point((-0.135, 1.585, -0.184)),
        transform_point((0.135, 1.585, -0.184)),
        transform_point((0.135, 1.815, -0.184)),
        transform_point((-0.135, 1.815, -0.184)),
    ]
    normals = [(0.0, 0.0, -1.0)] * 4
    texcoords = [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    indices = (0, 1, 2, 0, 2, 3)
    position_bytes = struct.pack("<12f", *(value for row in positions for value in row))
    normal_bytes = struct.pack("<12f", *(value for row in normals for value in row))
    uv_bytes = struct.pack("<8f", *(value for row in texcoords for value in row))
    index_bytes = struct.pack("<6H", *indices)
    position_accessor = append_accessor(document, binary, position_bytes, 5126, "VEC3", 4, target=34962)
    document["accessors"][position_accessor]["min"] = [min(row[axis] for row in positions) for axis in range(3)]
    document["accessors"][position_accessor]["max"] = [max(row[axis] for row in positions) for axis in range(3)]
    normal_accessor = append_accessor(document, binary, normal_bytes, 5126, "VEC3", 4, target=34962)
    uv_accessor = append_accessor(document, binary, uv_bytes, 5126, "VEC2", 4, target=34962)
    index_accessor = append_accessor(document, binary, index_bytes, 5123, "SCALAR", 6, target=34963)
    mesh_index = len(document["meshes"])
    document["meshes"].append(
        {
            "name": "PopbotFaceDecalMesh",
            "primitives": [
                {
                    "attributes": {
                        "POSITION": position_accessor,
                        "NORMAL": normal_accessor,
                        "TEXCOORD_0": uv_accessor,
                    },
                    "indices": index_accessor,
                    "material": FACE,
                }
            ],
        }
    )
    return parent_index, "PopbotFaceDecal", mesh_index


def build_shoe(document: dict, *, suffix: str, detail: str) -> tuple[int, str, MeshBuilder]:
    matrices, node_by_name, positions, _ = rig_context(document)
    foot = positions[f"foot.{suffix}"]
    label = suffix.upper()
    segments = 20 if detail == "original" else 12
    builder = PopbotMeshBuilder(skinned=False)
    yaw = -0.035 if suffix == "l" else 0.035
    sole_outline = [
        (-0.13, 0.15),
        (0.13, 0.15),
        (0.14, -0.10),
        (0.11, -0.205),
        (-0.11, -0.205),
        (-0.14, -0.10),
    ]
    midsole_outline = [
        (-0.12, 0.14),
        (0.12, 0.14),
        (0.13, -0.095),
        (0.10, -0.19),
        (-0.10, -0.19),
        (-0.13, -0.095),
    ]
    builder.prism_xz((foot[0], foot[1] - 0.018, foot[2] - 0.055), sole_outline, 0.065, MAGENTA, None, rotation=yaw)
    builder.prism_xz((foot[0], foot[1] + 0.025, foot[2] - 0.055), midsole_outline, 0.035, WHITE, None, rotation=yaw)
    builder.ellipsoid((foot[0], foot[1] + 0.085, foot[2] - 0.07), (0.118, 0.10, 0.18), BLUE, None, segments, 8 if detail == "original" else 5)
    builder.ellipsoid((foot[0], foot[1] + 0.072, foot[2] - 0.17), (0.105, 0.065, 0.085), WHITE, None, segments, 7 if detail == "original" else 5)
    builder.box((foot[0], foot[1] + 0.16, foot[2] + 0.005), (0.145, 0.215, 0.13), SUIT, None, rotation=(0.0, yaw, 0.0))
    builder.box((foot[0], foot[1] + 0.17, foot[2] - 0.072), (0.078, 0.17, 0.045), WHITE, None, rotation=(0.0, yaw, 0.0))
    outer_x = foot[0] + (0.085 if suffix == "r" else -0.085)
    builder.box((outer_x, foot[1] + 0.105, foot[2] - 0.025), (0.055, 0.12, 0.14), YELLOW, None, rotation=(0.0, yaw, 0.0))
    builder.box((outer_x, foot[1] + 0.08, foot[2] - 0.14), (0.04, 0.07, 0.07), MAGENTA, None, rotation=(0.0, yaw, 0.0))
    for index, y in enumerate((foot[1] + 0.115, foot[1] + 0.15, foot[1] + 0.185)):
        builder.box((foot[0], y, foot[2] - 0.125 + index * 0.012), (0.14, 0.013, 0.016), GRAY, None, rotation=(0.0, yaw, 0.0))
    builder.box((foot[0], foot[1] + 0.25, foot[2] + 0.04), (0.075, 0.055, 0.035), BLUE, None, rotation=(0.0, yaw, 0.0))
    parent_index = node_by_name[f"foot.{suffix}"]
    builder.transform(matrix_inverse(matrices[parent_index]))
    return parent_index, f"PopbotShoe{label}", builder


def build_backpack(document: dict, *, detail: str) -> tuple[int, str, MeshBuilder]:
    sides = 16 if detail == "original" else 10
    builder = PopbotMeshBuilder(skinned=False)
    # Yellow energy tank, black carrier frame, and pink hose from the sheet.
    tank_top = (0.22, 1.57, 0.34)
    tank_bottom = (0.28, 1.12, 0.37)
    builder.cylinder(tank_top, tank_bottom, 0.10, 0.10, YELLOW, None, sides)
    builder.cylinder((0.215, 1.60, 0.338), tank_top, 0.115, 0.105, SUIT, None, sides)
    builder.cylinder(tank_bottom, (0.285, 1.08, 0.372), 0.11, 0.115, SUIT, None, sides)
    builder.box((0.17, 1.34, 0.31), (0.25, 0.58, 0.10), SUIT, None, rotation=(0.05, 0.0, -0.13))
    builder.box((0.24, 1.35, 0.445), (0.10, 0.31, 0.035), BLUE, None, rotation=(0.05, 0.0, -0.13))
    builder.box((0.08, 1.34, 0.39), (0.045, 0.48, 0.055), GRAY, None, rotation=(0.05, 0.0, -0.13))
    builder.prism_xy((0.25, 1.23, 0.467), [(-0.05, -0.014), (0.05, -0.014), (0.05, 0.014), (-0.05, 0.014)], 0.014, SUIT, None, rotation=0.72)
    builder.prism_xy((0.25, 1.23, 0.468), [(-0.05, -0.014), (0.05, -0.014), (0.05, 0.014), (-0.05, 0.014)], 0.014, SUIT, None, rotation=-0.72)
    builder.cylinder((0.18, 1.61, 0.38), (-0.04, 1.57, 0.34), 0.02, 0.02, MAGENTA, None, sides)
    builder.cylinder((-0.04, 1.57, 0.34), (-0.22, 1.41, 0.28), 0.02, 0.02, MAGENTA, None, sides)
    return local_attachment(document, "chest", "PopbotBackpack", builder)


def build_weapon(document: dict, *, detail: str) -> tuple[int, str, MeshBuilder]:
    _, _, positions, _ = rig_context(document)
    gun = positions["gun.r"]
    sides = 14 if detail == "original" else 9
    builder = PopbotMeshBuilder(skinned=False)
    center = (gun[0] + 0.02, gun[1] - 0.36, gun[2] - 0.045)
    rotation = 0.24
    cos_rotation = math.cos(rotation)
    sin_rotation = math.sin(rotation)

    def weapon_point(x: float, y: float, z: float = 0.0) -> Vec3:
        return (
            center[0] + x * cos_rotation - y * sin_rotation,
            center[1] + x * sin_rotation + y * cos_rotation,
            center[2] + z,
        )
    body_outline = [
        (-0.155, -0.58),
        (0.10, -0.58),
        (0.15, -0.46),
        (0.15, 0.46),
        (0.09, 0.60),
        (-0.11, 0.60),
        (-0.16, 0.48),
        (-0.16, -0.48),
    ]
    builder.prism_xy(center, body_outline, 0.20, SUIT, None, rotation=rotation)
    builder.prism_xy(
        weapon_point(-0.005, -0.19, -0.115),
        [(-0.10, -0.31), (0.09, -0.27), (0.09, 0.25), (0.015, 0.33), (-0.10, 0.25)],
        0.026,
        YELLOW,
        None,
        rotation=rotation,
    )
    builder.prism_xy(
        weapon_point(0.005, 0.24, -0.118),
        [(-0.095, -0.20), (0.095, -0.18), (0.09, 0.18), (0.035, 0.24), (-0.09, 0.17)],
        0.026,
        BLUE,
        None,
        rotation=rotation,
    )
    builder.box(weapon_point(0.0, 0.55), (0.30, 0.13, 0.24), GRAY, None, rotation=(0.0, 0.0, rotation))
    builder.box(weapon_point(0.0, -0.58), (0.29, 0.10, 0.23), DARK_BLUE, None, rotation=(0.0, 0.0, rotation))
    builder.box(weapon_point(0.115, -0.01, 0.115), (0.052, 0.88, 0.055), GRAY, None, rotation=(0.0, 0.0, rotation))
    builder.box((gun[0] - 0.005, gun[1] + 0.075, gun[2] - 0.04), (0.09, 0.22, 0.11), SUIT, None, rotation=(0.0, 0.0, rotation + 0.08))
    builder.box((gun[0] - 0.025, gun[1] + 0.035, gun[2] - 0.105), (0.035, 0.08, 0.025), YELLOW, None, rotation=(0.0, 0.0, rotation))
    for y in (center[1] - 0.29, center[1] + 0.03):
        offset_y = y - center[1]
        builder.prism_xy(weapon_point(0.0, offset_y, -0.136), [(-0.055, -0.014), (0.055, -0.014), (0.055, 0.014), (-0.055, 0.014)], 0.016, SUIT, None, rotation=0.72 + rotation)
        builder.prism_xy(weapon_point(0.0, offset_y, -0.137), [(-0.055, -0.014), (0.055, -0.014), (0.055, 0.014), (-0.055, 0.014)], 0.016, SUIT, None, rotation=-0.72 + rotation)
    builder.cylinder(weapon_point(-0.16, 0.43), weapon_point(-0.245, 0.31), 0.02, 0.02, MAGENTA, None, sides)
    builder.cylinder(weapon_point(-0.245, 0.31), weapon_point(-0.245, -0.27), 0.02, 0.02, MAGENTA, None, sides)
    builder.cylinder(weapon_point(-0.245, -0.27), weapon_point(-0.15, -0.39), 0.02, 0.02, MAGENTA, None, sides)
    builder.cylinder(weapon_point(0.0, -0.62), weapon_point(0.0, -0.73), 0.075, 0.06, GRAY, None, sides)
    return local_attachment(document, "hand.r", "PopbotHeavyWeapon", builder)


def add_popbot_heavy_animation(document: dict, binary: bytearray) -> None:
    nodes = document["nodes"]
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    animation_by_name = {
        animation.get("name"): animation
        for animation in document.get("animations", [])
    }
    source = animation_by_name["Commando_FireBarrage"]
    source_duration = animation_duration(document, binary, source)
    action = deepcopy(source)
    action["name"] = "Popbot_HeavyFire"
    action["extras"] = {
        "source": "Commando_FireBarrage",
        "originalGameRig": True,
        "proceduralTwoHandedWeapon": True,
    }

    ready_pose = solve_arm_ik(document, "l", (0.28, 1.31, -0.44), (-1.0, -0.4, 0.1))
    recoil_pose = solve_arm_ik(document, "l", (0.28, 1.31, -0.36), (-1.0, -0.4, 0.1))
    times = [0.0, 0.18, 0.42, 0.50, 0.66, 0.74, 1.18, 1.45]
    poses = [None, ready_pose, ready_pose, recoil_pose, ready_pose, recoil_pose, ready_pose, None]
    for arm_name in ("upper_arm.l", "lower_arm.l"):
        node_index = node_by_name[arm_name]
        start = sample_animation_value(document, binary, source, node_index, "rotation", 0.0)
        end = sample_animation_value(document, binary, source, node_index, "rotation", source_duration)
        values = [
            start if pose is None and index == 0 else end if pose is None else pose[arm_name]
            for index, pose in enumerate(poses)
        ]
        append_animation_channel(document, binary, action, node_index, "rotation", times, values)
    document["animations"].append(action)


def add_popbot_carry_animations(document: dict, binary: bytearray) -> None:
    nodes = document["nodes"]
    node_by_name = {node.get("name"): index for index, node in enumerate(nodes)}
    animation_by_name = {
        animation.get("name"): animation
        for animation in document.get("animations", [])
    }
    carry_sources = (
        ("Commando_Idle", "Popbot_Idle"),
        ("Commando_RunForward", "Popbot_RunForward"),
        ("Commando_RunBackward", "Popbot_RunBackward"),
        ("Commando_RunLeft", "Popbot_RunLeft"),
        ("Commando_RunRight", "Popbot_RunRight"),
        ("Commando_SprintForward", "Popbot_SprintForward"),
    )
    carry_nodes = (
        "upper_arm.l",
        "lower_arm.l",
        "hand.l",
        "upper_arm.r",
        "lower_arm.r",
        "hand.r",
    )
    for source_name, action_name in carry_sources:
        source = animation_by_name[source_name]
        duration = animation_duration(document, binary, source)
        action = deepcopy(source)
        action["name"] = action_name
        action["extras"] = {
            "source": source_name,
            "originalGameRig": True,
            "proceduralHeavyCarry": True,
        }
        for node_name in carry_nodes:
            node_index = node_by_name[node_name]
            rotation = tuple(nodes[node_index].get("rotation", [0.0, 0.0, 0.0, 1.0]))
            append_animation_channel(
                document,
                binary,
                action,
                node_index,
                "rotation",
                [0.0, duration],
                [rotation, rotation],
            )
        document["animations"].append(action)


def referenced_stats(document: dict) -> tuple[int, int]:
    vertices = 0
    triangles = 0
    for node in document["nodes"]:
        if "mesh" not in node:
            continue
        mesh_vertices, mesh_triangles = mesh_stats(document, node["mesh"])
        vertices += mesh_vertices
        triangles += mesh_triangles
    return vertices, triangles


def build_variant(template: Path, destination: Path, *, detail: str) -> dict[str, int]:
    source_document, source_binary = read_glb(template)
    body_node_index = next(index for index, node in enumerate(source_document["nodes"]) if node.get("name") == "CommandoMesh")
    document, binary = prepare_template(source_document, source_binary, variant=detail)

    body_builder = build_character_mesh(document, detail=detail)
    body_mesh_index, _, _ = append_mesh(document, binary, "PopbotBaseMesh", body_builder)
    body_node = document["nodes"][body_node_index]
    body_node["name"] = "PopbotBaseMesh"
    body_node["mesh"] = body_mesh_index
    body_node["skin"] = 0

    for node in document["nodes"]:
        if node.get("name") in {"GunMesh", "GunMesh.001"}:
            node.pop("mesh", None)
            node["name"] = f"Hidden{node['name']}"

    attachments = [
        build_head(document, detail=detail),
        build_shoe(document, suffix="l", detail=detail),
        build_shoe(document, suffix="r", detail=detail),
        build_backpack(document, detail=detail),
        build_weapon(document, detail=detail),
    ]
    for parent_index, name, builder in attachments:
        mesh_index, _, _ = append_mesh(document, binary, f"{name}Mesh", builder)
        node_index = len(document["nodes"])
        document["nodes"].append({"name": name, "mesh": mesh_index})
        document["nodes"][parent_index].setdefault("children", []).append(node_index)

    face_parent, face_name, face_mesh = append_face_decal(document, binary)
    face_node = len(document["nodes"])
    document["nodes"].append({"name": face_name, "mesh": face_mesh})
    document["nodes"][face_parent].setdefault("children", []).append(face_node)

    add_popbot_heavy_animation(document, binary)
    add_popbot_carry_animations(document, binary)

    vertices, triangles = referenced_stats(document)
    if triangles > MAX_TRIANGLES:
        raise ValueError(f"{detail} variant exceeds {MAX_TRIANGLES} triangles: {triangles}")
    write_glb(destination, document, binary)
    return {"vertices": vertices, "triangles": triangles, "animations": len(document["animations"])}


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, default=repo_root / "models/survivors/commando-original.glb")
    parser.add_argument("--original", type=Path, default=repo_root / "generated/legacy/popbot-pro-original.glb")
    parser.add_argument("--low", type=Path, default=repo_root / "generated/legacy/popbot-pro-low.glb")
    parser.add_argument("--stats", type=Path, default=repo_root / "generated/legacy/popbot-pro.stats.json")
    args = parser.parse_args()

    original = build_variant(args.template, args.original, detail="original")
    low = build_variant(args.template, args.low, detail="low")
    stats = {
        "sourceTriangles": original["triangles"],
        "lowTriangles": low["triangles"],
        "sourceVertices": original["vertices"],
        "lowVertices": low["vertices"],
        "sourceSizeKB": round(args.original.stat().st_size / 1024),
        "lowSizeKB": round(args.low.stat().st_size / 1024),
        "jointCount": 78,
        "animationCount": original["animations"],
        "triangleLimit": MAX_TRIANGLES,
    }
    args.stats.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(
        f"Built POPBOT Pro: {stats['sourceTriangles']} original / {stats['lowTriangles']} low triangles, "
        f"limit={MAX_TRIANGLES}, {stats['jointCount']} joints, {stats['animationCount']} animations"
    )


if __name__ == "__main__":
    main()
