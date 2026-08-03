"""Build animated original and mobile GLBs from the Hades II H2GX bridge file."""

from __future__ import annotations

import argparse
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import bpy  # type: ignore
from mathutils import Matrix, Quaternion, Vector  # type: ignore


FPS = 30
MATERIAL_COLORS = (
    (0.19, 0.16, 0.13, 1.0),
    (0.43, 0.35, 0.25, 1.0),
    (0.60, 0.50, 0.34, 1.0),
    (0.34, 0.14, 0.10, 1.0),
    (0.10, 0.08, 0.07, 1.0),
)


@dataclass
class Transform:
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    scale_shear: tuple[float, ...]


class Reader:
    def __init__(self, path: Path):
        self.data = memoryview(path.read_bytes())
        self.offset = 0

    def unpack(self, fmt: str):
        values = struct.unpack_from("<" + fmt, self.data, self.offset)
        self.offset += struct.calcsize("<" + fmt)
        return values

    def integer(self) -> int:
        return self.unpack("i")[0]

    def floating(self) -> float:
        return self.unpack("f")[0]

    def string(self) -> str:
        length = self.integer()
        value = bytes(self.data[self.offset : self.offset + length]).decode("utf-8")
        self.offset += length
        return value

    def transform(self) -> Transform:
        return Transform(self.unpack("3f"), self.unpack("4f"), self.unpack("9f"))


def read_bundle(path: Path):
    reader = Reader(path)
    if bytes(reader.data[:4]) != b"H2GX":
        raise ValueError("Not an H2GX bundle")
    reader.offset = 4
    if reader.integer() != 1:
        raise ValueError("Unsupported H2GX version")

    bones = []
    for _ in range(reader.integer()):
        bones.append({"name": reader.string(), "parent": reader.integer(), "rest": reader.transform()})

    meshes = []
    for _ in range(reader.integer()):
        name = reader.string()
        vertices = []
        for _ in range(reader.integer()):
            position = reader.unpack("3f")
            weights = reader.unpack("4B")
            indices = reader.unpack("4B")
            normal = reader.unpack("3f")
            uv = reader.unpack("2f")
            vertices.append((position, weights, indices, normal, uv))
        indices = list(reader.unpack(f"{reader.integer()}i"))
        groups = [reader.unpack("3i") for _ in range(reader.integer())]
        bindings = [reader.string() for _ in range(reader.integer())]
        materials = [reader.string() for _ in range(reader.integer())]
        meshes.append({
            "name": name,
            "vertices": vertices,
            "indices": indices,
            "groups": groups,
            "bindings": bindings,
            "materials": materials,
        })

    animations = []
    for _ in range(reader.integer()):
        name = reader.string()
        duration = reader.floating()
        frames = []
        for _ in range(reader.integer()):
            time = reader.floating()
            frames.append((time, [reader.transform() for _ in bones]))
        animations.append({"name": name, "duration": duration, "frames": frames})

    if reader.offset != len(reader.data):
        raise ValueError(f"Trailing H2GX bytes: {len(reader.data) - reader.offset}")
    return bones, meshes, animations


def transform_matrix(value: Transform) -> Matrix:
    x, y, z, w = value.orientation
    rotation = Quaternion((w, x, y, z)).normalized().to_matrix().to_4x4()
    scale = value.scale_shear
    scale_matrix = Matrix((
        (scale[0], scale[1], scale[2], 0.0),
        (scale[3], scale[4], scale[5], 0.0),
        (scale[6], scale[7], scale[8], 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))
    return Matrix.Translation(Vector(value.position)) @ rotation @ scale_matrix


def clear_scene() -> None:
    bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.object and bpy.context.object.mode != "OBJECT" else None
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.armatures, bpy.data.actions, bpy.data.materials):
        for item in list(collection):
            collection.remove(item)


def create_armature(bones):
    armature_data = bpy.data.armatures.new("CrawlerSkeleton")
    armature = bpy.data.objects.new("CrawlerArmature", armature_data)
    bpy.context.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    local_matrices = [transform_matrix(bone["rest"]) for bone in bones]
    world_matrices = []
    for index, bone in enumerate(bones):
        parent = bone["parent"]
        world_matrices.append((world_matrices[parent] @ local_matrices[index]) if parent >= 0 else local_matrices[index])

    children = [[] for _ in bones]
    for index, bone in enumerate(bones):
        if bone["parent"] >= 0:
            children[bone["parent"]].append(index)

    edit_bones = []
    for index, bone in enumerate(bones):
        edit_bone = armature_data.edit_bones.new(bone["name"] or f"Bone_{index}")
        edit_bone.matrix = world_matrices[index]
        if children[index]:
            distances = [
                (world_matrices[child].translation - world_matrices[index].translation).length
                for child in children[index]
            ]
            edit_bone.length = max(0.01, min(0.25, sum(distances) / len(distances) * 0.45))
        else:
            edit_bone.length = 0.035
        edit_bones.append(edit_bone)

    for index, bone in enumerate(bones):
        if bone["parent"] >= 0:
            edit_bones[index].parent = edit_bones[bone["parent"]]
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.show_in_front = True
    return armature, local_matrices


def material_for(name: str, index: int):
    key = name or f"CrawlerMaterial_{index}"
    material = bpy.data.materials.get(key)
    if material:
        return material
    material = bpy.data.materials.new(key)
    material.diffuse_color = MATERIAL_COLORS[index % len(MATERIAL_COLORS)]
    material.roughness = 0.82
    material.metallic = 0.0
    return material


def create_meshes(meshes, armature):
    objects = []
    for mesh_index, source in enumerate(meshes):
        if "Outline" in source["name"] or "ShadowMesh" in source["name"]:
            print(f"H2GX_SKIP_HELPER name={source['name']} triangles={len(source['indices']) // 3}")
            continue
        vertices = [item[0] for item in source["vertices"]]
        faces = [source["indices"][index : index + 3] for index in range(0, len(source["indices"]), 3)]
        mesh = bpy.data.meshes.new(source["name"] or f"CrawlerMesh_{mesh_index}")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()

        uv_layer = mesh.uv_layers.new(name="UVMap")
        for loop in mesh.loops:
            u, v = source["vertices"][loop.vertex_index][4]
            uv_layer.data[loop.index].uv = (u, 1.0 - v)

        try:
            mesh.normals_split_custom_set_from_vertices([item[3] for item in source["vertices"]])
        except (AttributeError, RuntimeError):
            pass

        material_names = source["materials"] or ["CrawlerMaterial"]
        for index, name in enumerate(material_names):
            mesh.materials.append(material_for(name, index))
        for material_index, first_triangle, triangle_count in source["groups"]:
            for polygon_index in range(first_triangle, min(first_triangle + triangle_count, len(mesh.polygons))):
                mesh.polygons[polygon_index].material_index = min(material_index, len(mesh.materials) - 1)

        obj = bpy.data.objects.new(mesh.name, mesh)
        bpy.context.collection.objects.link(obj)
        obj.parent = armature
        modifier = obj.modifiers.new("Armature", "ARMATURE")
        modifier.object = armature

        groups = [obj.vertex_groups.new(name=name or f"Binding_{index}") for index, name in enumerate(source["bindings"])]
        for vertex_index, (_, weights, bone_indices, _, _) in enumerate(source["vertices"]):
            total = sum(weights)
            if total <= 0:
                continue
            for weight, binding_index in zip(weights, bone_indices):
                if weight and binding_index < len(groups):
                    groups[binding_index].add([vertex_index], weight / total, "REPLACE")
        objects.append(obj)
    return objects


def create_actions(armature, bones, rest_local_matrices, animations):
    armature.animation_data_create()
    for animation in animations:
        action = bpy.data.actions.new(animation["name"])
        action.use_fake_user = True
        armature.animation_data.action = action
        for time, transforms in animation["frames"]:
            frame = time * FPS + 1.0
            for index, transform in enumerate(transforms):
                pose_bone = armature.pose.bones.get(bones[index]["name"])
                if pose_bone is None:
                    continue
                basis = rest_local_matrices[index].inverted_safe() @ transform_matrix(transform)
                location, rotation, scale = basis.decompose()
                pose_bone.rotation_mode = "QUATERNION"
                pose_bone.location = location
                pose_bone.rotation_quaternion = rotation
                pose_bone.scale = scale
                pose_bone.keyframe_insert("location", frame=frame, group=pose_bone.name)
                pose_bone.keyframe_insert("rotation_quaternion", frame=frame, group=pose_bone.name)
                pose_bone.keyframe_insert("scale", frame=frame, group=pose_bone.name)
        for curve in action.fcurves:
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"
        action["hades2_duration"] = animation["duration"]
    armature.animation_data.action = bpy.data.actions.get("Crawler_Base_Idle_C_00")


def triangle_count(objects) -> int:
    return sum(len(obj.data.polygons) for obj in objects)


def export_glb(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=False,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_force_sampling=False,
        export_frame_range=False,
        export_yup=True,
    )


def build_low(objects, target_ratio: float):
    before = triangle_count(objects)
    for obj in objects:
        triangles = len(obj.data.polygons)
        if triangles <= 350:
            continue
        local_ratio = max(target_ratio, min(1.0, 350.0 / triangles))
        if local_ratio >= 0.999:
            continue
        modifier = obj.modifiers.new("MobilePreserve", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = local_ratio
        modifier.use_collapse_triangulate = True
        modifier.delimit = {"UV", "MATERIAL", "SEAM"}
        obj.modifiers.move(len(obj.modifiers) - 1, 0)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        limit_vertex_influences(obj, 4)
        obj.select_set(False)
    return before, triangle_count(objects)


def limit_vertex_influences(obj, maximum: int):
    for vertex in obj.data.vertices:
        weighted = sorted(vertex.groups, key=lambda item: item.weight, reverse=True)
        keep = weighted[:maximum]
        remove = weighted[maximum:]
        for item in remove:
            obj.vertex_groups[item.group].remove([vertex.index])
        total = sum(item.weight for item in keep)
        if total <= 0:
            continue
        for item in keep:
            obj.vertex_groups[item.group].add([vertex.index], item.weight / total, "REPLACE")


def reduce_action_sampling(step: int = 2):
    for action in bpy.data.actions:
        for curve in action.fcurves:
            points = list(curve.keyframe_points)
            for index in range(len(points) - 2, 0, -1):
                frame = int(round(points[index].co.x - 1.0))
                if frame % step:
                    curve.keyframe_points.remove(points[index], fast=True)
            curve.update()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("original", type=Path)
    parser.add_argument("low", type=Path)
    parser.add_argument("--low-ratio", type=float, default=0.58)
    args = parser.parse_args(argv)

    bones, meshes, animations = read_bundle(args.bundle)
    clear_scene()
    bpy.context.scene.render.fps = FPS
    armature, rest_local_matrices = create_armature(bones)
    objects = create_meshes(meshes, armature)
    create_actions(armature, bones, rest_local_matrices, animations)
    source_triangles = triangle_count(objects)
    export_glb(args.original)
    _, low_triangles = build_low(objects, args.low_ratio)
    reduce_action_sampling(2)
    export_glb(args.low)
    print(f"H2GX_RESULT bones={len(bones)} meshes={len(objects)} animations={len(animations)} source_triangles={source_triangles} low_triangles={low_triangles}")
    return 0


if __name__ == "__main__":
    script_args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    raise SystemExit(main(script_args))
