"""Transfer the Space Commando game rig onto a generated TripoSR mesh."""

from __future__ import annotations

import argparse
from copy import deepcopy
import heapq
import math
from pathlib import Path
import statistics
import struct

from add_ror2_bandit_rig import append_accessor, read_accessor, read_glb, write_glb


Vec3 = tuple[float, float, float]
WeightRow = tuple[tuple[int, int, int, int], tuple[float, float, float, float]]


class NearestWeightTree:
    """Small dependency-free 3D k-d tree for interpolating source skin weights."""

    def __init__(self, points: list[Vec3], weights: list[WeightRow]) -> None:
        if not points or len(points) != len(weights):
            raise ValueError("Skin-weight source samples are empty or mismatched")
        self.points = points
        self.weights = weights
        self.nodes: list[tuple[int, int, int | None, int | None]] = []
        self.root = self._build(list(range(len(points))), 0)

    def _build(self, indices: list[int], depth: int) -> int | None:
        if not indices:
            return None
        axis = depth % 3
        indices.sort(key=lambda index: self.points[index][axis])
        middle = len(indices) // 2
        node_index = len(self.nodes)
        self.nodes.append((indices[middle], axis, None, None))
        left = self._build(indices[:middle], depth + 1)
        right = self._build(indices[middle + 1 :], depth + 1)
        self.nodes[node_index] = (indices[middle], axis, left, right)
        return node_index

    def nearest(self, point: Vec3, count: int = 4) -> list[tuple[float, WeightRow]]:
        candidates: list[tuple[float, int]] = []

        def visit(node_index: int | None) -> None:
            if node_index is None:
                return
            sample_index, axis, left, right = self.nodes[node_index]
            sample = self.points[sample_index]
            distance_squared = sum((point[i] - sample[i]) ** 2 for i in range(3))
            if len(candidates) < count:
                heapq.heappush(candidates, (-distance_squared, sample_index))
            elif distance_squared < -candidates[0][0]:
                heapq.heapreplace(candidates, (-distance_squared, sample_index))
            delta = point[axis] - sample[axis]
            near, far = (left, right) if delta < 0.0 else (right, left)
            visit(near)
            limit = math.inf if len(candidates) < count else -candidates[0][0]
            if delta * delta < limit:
                visit(far)

        visit(self.root)
        return [(-distance, self.weights[index]) for distance, index in candidates]


def source_weight_samples(document: dict, binary: bytearray) -> tuple[list[Vec3], list[WeightRow], int]:
    body_nodes = [
        (index, node)
        for index, node in enumerate(document.get("nodes", []))
        if node.get("skin") == 0 and "mesh" in node
    ]
    if len(body_nodes) != 1:
        raise ValueError(f"Expected one skinned template mesh node, found {len(body_nodes)}")
    body_node_index, body_node = body_nodes[0]
    points: list[Vec3] = []
    rows: list[WeightRow] = []
    for primitive in document["meshes"][body_node["mesh"]]["primitives"]:
        attributes = primitive["attributes"]
        required = {"POSITION", "JOINTS_0", "WEIGHTS_0"}
        if not required.issubset(attributes):
            continue
        positions = read_accessor(document, binary, attributes["POSITION"])
        joints = read_accessor(document, binary, attributes["JOINTS_0"])
        weights = read_accessor(document, binary, attributes["WEIGHTS_0"])
        for position, joint_row, weight_row in zip(positions, joints, weights):
            points.append(tuple(float(value) for value in position))
            rows.append(
                (
                    tuple(int(value) for value in joint_row),
                    tuple(float(value) for value in weight_row),
                )
            )
    if not points:
        raise ValueError("Template skinned mesh contains no weight samples")
    return points, rows, body_node_index


def generated_mesh(document: dict, binary: bytearray) -> tuple[list[Vec3], list[int], list[tuple[int, ...]]]:
    mesh_nodes = [node for node in document.get("nodes", []) if "mesh" in node]
    if len(mesh_nodes) != 1:
        raise ValueError(f"Expected one generated mesh node, found {len(mesh_nodes)}")
    primitives = document["meshes"][mesh_nodes[0]["mesh"]].get("primitives", [])
    if len(primitives) != 1:
        raise ValueError(f"Expected one generated mesh primitive, found {len(primitives)}")
    primitive = primitives[0]
    attributes = primitive["attributes"]
    if "POSITION" not in attributes or "COLOR_0" not in attributes or "indices" not in primitive:
        raise ValueError("Generated mesh must contain POSITION, COLOR_0, and indices")
    positions = [tuple(float(value) for value in row) for row in read_accessor(document, binary, attributes["POSITION"])]
    indices = [int(row[0]) for row in read_accessor(document, binary, primitive["indices"])]
    colors = [tuple(int(value) for value in row) for row in read_accessor(document, binary, attributes["COLOR_0"])]
    return positions, indices, colors


def normalize_triposr_positions(positions: list[Vec3], target_height: float, mirror_x: bool) -> list[Vec3]:
    minimum_z = min(point[2] for point in positions)
    maximum_z = max(point[2] for point in positions)
    if maximum_z - minimum_z < 1e-5:
        raise ValueError("Generated mesh has no usable height")
    scale = target_height / (maximum_z - minimum_z)
    center_horizontal = statistics.median(point[1] for point in positions)
    center_depth = statistics.median(point[0] for point in positions)
    horizontal_sign = -1.0 if mirror_x else 1.0
    return [
        (
            horizontal_sign * (point[1] - center_horizontal) * scale,
            (point[2] - minimum_z) * scale,
            (point[0] - center_depth) * scale + 0.08,
        )
        for point in positions
    ]


def vertex_normals(positions: list[Vec3], indices: list[int], reverse_winding: bool) -> tuple[list[Vec3], list[int]]:
    output_indices = list(indices)
    if reverse_winding:
        for offset in range(0, len(output_indices) - 2, 3):
            output_indices[offset + 1], output_indices[offset + 2] = (
                output_indices[offset + 2],
                output_indices[offset + 1],
            )
    normals = [[0.0, 0.0, 0.0] for _ in positions]
    for offset in range(0, len(output_indices) - 2, 3):
        first, second, third = output_indices[offset : offset + 3]
        a, b, c = positions[first], positions[second], positions[third]
        ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        normal = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        for index in (first, second, third):
            normals[index][0] += normal[0]
            normals[index][1] += normal[1]
            normals[index][2] += normal[2]
    result = []
    for normal in normals:
        length = math.sqrt(sum(value * value for value in normal))
        result.append((0.0, 1.0, 0.0) if length < 1e-8 else tuple(value / length for value in normal))
    return result, output_indices


def interpolated_weight(neighbors: list[tuple[float, WeightRow]]) -> WeightRow:
    merged: dict[int, float] = {}
    for distance_squared, (joints, weights) in neighbors:
        influence = 1.0 / (distance_squared + 1e-5)
        for joint, weight in zip(joints, weights):
            if weight > 0.0:
                merged[joint] = merged.get(joint, 0.0) + weight * influence
    selected = sorted(merged.items(), key=lambda item: item[1], reverse=True)[:4]
    total = sum(weight for _, weight in selected)
    joints = [joint for joint, _ in selected]
    weights = [weight / total for _, weight in selected]
    while len(joints) < 4:
        joints.append(0)
        weights.append(0.0)
    return tuple(joints), tuple(weights)


def transferred_weight(
    point: Vec3,
    tree: NearestWeightTree,
    rigid_prop_joint: int | None,
    rigid_prop_side: str,
) -> WeightRow:
    # TripoSR often fuses a held prop into the body surface. POPBOT's cannon
    # extends well outside the left body silhouette and down to the ground;
    # keep that region rigid on the hand instead of blending it into the legs.
    x, y, _z = point
    side_x = -x if rigid_prop_side == "right" else x
    if rigid_prop_joint is not None and (
        (side_x < -0.42 and y < 1.15) or (side_x < -0.27 and y < 0.55)
    ):
        return (rigid_prop_joint, 0, 0, 0), (1.0, 0.0, 0.0, 0.0)
    return interpolated_weight(tree.nearest(point))


def build_rigged_model(
    source: Path,
    template: Path,
    destination: Path,
    target_height: float,
    mirror_x: bool,
    rigid_prop_hand: str,
) -> dict[str, int]:
    generated_document, generated_binary = read_glb(source)
    raw_positions, raw_indices, colors = generated_mesh(generated_document, generated_binary)
    positions = normalize_triposr_positions(raw_positions, target_height, mirror_x)
    normals, indices = vertex_normals(positions, raw_indices, reverse_winding=mirror_x)

    template_document, template_binary = read_glb(template)
    document = deepcopy(template_document)
    binary = bytearray(template_binary)
    sample_points, sample_weights, body_node_index = source_weight_samples(document, binary)
    tree = NearestWeightTree(sample_points, sample_weights)
    joint_by_name = {
        document["nodes"][node_index].get("name"): slot
        for slot, node_index in enumerate(document["skins"][0]["joints"])
    }
    rigid_prop_joint = None
    if rigid_prop_hand != "none":
        rigid_prop_joint = joint_by_name[f"hand.{rigid_prop_hand[0]}"]
    transferred = [
        transferred_weight(point, tree, rigid_prop_joint, rigid_prop_hand)
        for point in positions
    ]

    position_bytes = struct.pack(f"<{len(positions) * 3}f", *(value for row in positions for value in row))
    normal_bytes = struct.pack(f"<{len(normals) * 3}f", *(value for row in normals for value in row))
    color_width = max(len(row) for row in colors)
    if color_width not in {3, 4}:
        raise ValueError(f"Unsupported generated color width: {color_width}")
    colors = [tuple((*row, 255)[:color_width]) for row in colors]
    color_bytes = struct.pack(f"<{len(colors) * color_width}B", *(value for row in colors for value in row))
    joint_bytes = struct.pack(f"<{len(transferred) * 4}B", *(value for row, _ in transferred for value in row))
    weight_bytes = struct.pack(f"<{len(transferred) * 4}f", *(value for _, row in transferred for value in row))
    index_component = 5123 if max(indices) < 65536 else 5125
    index_format = "H" if index_component == 5123 else "I"
    index_bytes = struct.pack(f"<{len(indices)}{index_format}", *indices)

    position_accessor = append_accessor(document, binary, position_bytes, 5126, "VEC3", len(positions), target=34962)
    document["accessors"][position_accessor]["min"] = [min(row[axis] for row in positions) for axis in range(3)]
    document["accessors"][position_accessor]["max"] = [max(row[axis] for row in positions) for axis in range(3)]
    normal_accessor = append_accessor(document, binary, normal_bytes, 5126, "VEC3", len(normals), target=34962)
    color_accessor = append_accessor(document, binary, color_bytes, 5121, f"VEC{color_width}", len(colors), target=34962)
    document["accessors"][color_accessor]["normalized"] = True
    joint_accessor = append_accessor(document, binary, joint_bytes, 5121, "VEC4", len(transferred), target=34962)
    weight_accessor = append_accessor(document, binary, weight_bytes, 5126, "VEC4", len(transferred), target=34962)
    index_accessor = append_accessor(document, binary, index_bytes, index_component, "SCALAR", len(indices), target=34963)

    document["materials"] = [
        {
            "name": "GeneratedVertexColorMaterial",
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.82,
            },
            "doubleSided": True,
        }
    ]
    document["meshes"] = [
        {
            "name": "GeneratedCharacterMesh",
            "primitives": [
                {
                    "attributes": {
                        "POSITION": position_accessor,
                        "NORMAL": normal_accessor,
                        "COLOR_0": color_accessor,
                        "JOINTS_0": joint_accessor,
                        "WEIGHTS_0": weight_accessor,
                    },
                    "indices": index_accessor,
                    "material": 0,
                }
            ],
        }
    ]
    for index, node in enumerate(document["nodes"]):
        node.pop("mesh", None)
        if index == body_node_index:
            node["name"] = "GeneratedCharacter"
            node["mesh"] = 0
            node["skin"] = 0
    document.setdefault("asset", {})["generator"] = "game-model-wiki TripoSR rig transfer"
    document["asset"]["extras"] = {
        "rig": {
            "source": "space-commando-original-game-rig",
            "originalGameRig": True,
            "jointCount": len(document["skins"][0]["joints"]),
            "animations": len(document.get("animations", [])),
        },
        "imageToModel": {
            "generator": "stabilityai/TripoSR",
            "source": source.name,
            "weightTransfer": "four-nearest inverse-distance interpolation",
            "targetHeight": target_height,
            "mirrorX": mirror_x,
            "rigidPropHand": rigid_prop_hand,
        },
    }
    document["skins"][0]["name"] = "GeneratedCharacterSpaceCommandoRig"
    document["skins"][0].setdefault("extras", {})["weightTransfer"] = "nearest-template-vertices"
    write_glb(destination, document, binary)
    return {
        "vertices": len(positions),
        "triangles": len(indices) // 3,
        "joints": len(document["skins"][0]["joints"]),
        "animations": len(document.get("animations", [])),
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--template",
        type=Path,
        default=repo_root / "generated/templates/space-commando-original.glb",
    )
    parser.add_argument("--target-height", type=float, default=1.9)
    parser.add_argument("--mirror-x", action="store_true")
    parser.add_argument(
        "--rigid-prop-hand",
        choices=("none", "left", "right"),
        default="none",
        help="Rigidly bind an outer lower prop region to the selected hand",
    )
    args = parser.parse_args()
    stats = build_rigged_model(
        args.source,
        args.template,
        args.destination,
        args.target_height,
        args.mirror_x,
        args.rigid_prop_hand,
    )
    print(
        f"Built {args.destination}: {stats['triangles']} triangles, {stats['vertices']} vertices, "
        f"{stats['joints']} joints, {stats['animations']} animations"
    )


if __name__ == "__main__":
    main()
