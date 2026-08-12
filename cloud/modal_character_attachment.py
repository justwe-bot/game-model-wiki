"""Attach a rigid GLB part to an existing animated character bone in Blender."""

from __future__ import annotations

from pathlib import Path
import io
import json
import math
import struct
import subprocess
import sys
import tempfile
import zipfile

import modal


APP_NAME = "game-model-wiki-character-attachment"
MODEL_VOLUME = "/models"
MODEL_VOLUME_NAME = "game-model-wiki-3d-model-cache"

app = modal.App(APP_NAME)
model_cache = modal.Volume.from_name(MODEL_VOLUME_NAME, create_if_missing=True)

runtime_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "libegl1",
        "libgl1",
        "libglib2.0-0",
        "libgomp1",
        "libice6",
        "libsm6",
        "libx11-6",
        "libxfixes3",
        "libxi6",
        "libxkbcommon0",
        "libxrender1",
        "libxxf86vm1",
    )
    .run_commands(
        "python -m pip install --upgrade pip",
        "python -m pip install bpy==4.2.0 numpy==1.26.4",
    )
)


def canonical_bone_name(value: str) -> str:
    return value.rsplit(":", 1)[-1]


def parse_vector(value: str, *, label: str) -> tuple[float, float, float]:
    try:
        result = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise ValueError(f"{label} must contain three comma-separated numbers") from exc
    if len(result) != 3 or not all(math.isfinite(item) for item in result):
        raise ValueError(f"{label} must contain three finite comma-separated numbers")
    return result


def _armatures():
    import bpy

    return [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]


def _mesh_objects():
    import bpy

    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def _bounds(objects) -> tuple[list[float], list[float]]:
    from mathutils import Vector

    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    for obj in objects:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                minimum[axis] = min(minimum[axis], float(point[axis]))
                maximum[axis] = max(maximum[axis], float(point[axis]))
    return minimum, maximum


def _anchor(bounds, mode: str):
    from mathutils import Vector

    minimum, maximum = bounds
    center = [(minimum[axis] + maximum[axis]) * 0.5 for axis in range(3)]
    if mode == "origin":
        return Vector((0.0, 0.0, 0.0))
    if mode == "bottom":
        center[2] = minimum[2]
    elif mode == "top":
        center[2] = maximum[2]
    elif mode != "center":
        raise ValueError(f"Unsupported part anchor: {mode}")
    return Vector(center)


def _read_glb_document(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(f"Expected a binary GLB file: {path}")
    _magic, version, total_length = struct.unpack_from("<4sII", data, 0)
    if version != 2 or total_length != len(data):
        raise ValueError(f"Invalid GLB header: {path}")
    offset = 12
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        payload = data[offset : offset + chunk_length]
        offset += chunk_length
        if chunk_type == 0x4E4F534A:
            return json.loads(payload.rstrip(b" \t\r\n\x00").decode("utf-8"))
    raise ValueError(f"GLB does not contain a JSON chunk: {path}")


def _node_local_matrix(node: dict):
    from mathutils import Matrix, Quaternion, Vector

    if "matrix" in node:
        values = [float(value) for value in node["matrix"]]
        return Matrix(
            [[values[column * 4 + row] for column in range(4)] for row in range(4)]
        )
    translation = Vector(tuple(float(value) for value in node.get("translation", (0, 0, 0))))
    rotation = tuple(float(value) for value in node.get("rotation", (0, 0, 0, 1)))
    scale = tuple(float(value) for value in node.get("scale", (1, 1, 1)))
    return (
        Matrix.Translation(translation)
        @ Quaternion((rotation[3], rotation[0], rotation[1], rotation[2])).to_matrix().to_4x4()
        @ Matrix.Diagonal((*scale, 1.0))
    )


def _source_bone_anchor(path: Path, requested_name: str, mode: str):
    from mathutils import Vector

    document = _read_glb_document(path)
    nodes = document.get("nodes", [])
    matches = [
        index
        for index, node in enumerate(nodes)
        if canonical_bone_name(node.get("name", "")) == canonical_bone_name(requested_name)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one source GLB bone {requested_name!r}, found {len(matches)}"
        )
    target_index = matches[0]
    parents = [None] * len(nodes)
    for parent_index, node in enumerate(nodes):
        for child_index in node.get("children", []):
            parents[child_index] = parent_index

    world_cache = {}

    def world_matrix(index: int):
        if index not in world_cache:
            parent_index = parents[index]
            local = _node_local_matrix(nodes[index])
            world_cache[index] = (
                world_matrix(parent_index) @ local if parent_index is not None else local
            )
        return world_cache[index]

    head = world_matrix(target_index).translation
    if mode == "head":
        anchor = head
    else:
        preferred_children = {
            "Spine2": ("Neck",),
            "LeftHand": ("LeftHandMiddle1", "LeftHandIndex1"),
            "RightHand": ("RightHandMiddle1", "RightHandIndex1"),
        }.get(canonical_bone_name(requested_name), ())
        children = list(nodes[target_index].get("children", []))
        children.sort(
            key=lambda index: (
                preferred_children.index(canonical_bone_name(nodes[index].get("name", "")))
                if canonical_bone_name(nodes[index].get("name", "")) in preferred_children
                else len(preferred_children),
                (world_matrix(index).translation - head).length,
            )
        )
        if children:
            tail = world_matrix(children[0]).translation
        elif parents[target_index] is not None:
            parent = world_matrix(parents[target_index]).translation
            tail = head + (head - parent)
        else:
            tail = head
        anchor = tail if mode == "tail" else (head + tail) * 0.5

    # glTF stores Y-up coordinates; Blender imports the same data as Z-up.
    return Vector((float(anchor.x), float(-anchor.z), float(anchor.y)))


def _find_bone(armature, requested_name: str):
    matches = [
        bone
        for bone in armature.data.bones
        if canonical_bone_name(bone.name) == canonical_bone_name(requested_name)
    ]
    if len(matches) != 1:
        available = sorted(canonical_bone_name(bone.name) for bone in armature.data.bones)
        raise RuntimeError(
            f"Expected exactly one target bone {requested_name!r}, found {len(matches)}; "
            f"available={available}"
        )
    return matches[0]


def _make_opaque(meshes) -> dict[str, int]:
    materials = set()
    alpha_links_removed = 0
    for mesh in meshes:
        for slot in mesh.material_slots:
            material = slot.material
            if material is None or material in materials:
                continue
            materials.add(material)
            material.diffuse_color = (*material.diffuse_color[:3], 1.0)
            if hasattr(material, "surface_render_method"):
                material.surface_render_method = "DITHERED"
            if not material.use_nodes:
                continue
            for node in material.node_tree.nodes:
                if node.type != "BSDF_PRINCIPLED":
                    continue
                alpha_input = node.inputs.get("Alpha")
                if alpha_input is None:
                    continue
                for link in list(alpha_input.links):
                    material.node_tree.links.remove(link)
                    alpha_links_removed += 1
                alpha_input.default_value = 1.0
    return {"materials": len(materials), "alphaLinksRemoved": alpha_links_removed}


def _remove_objects(objects) -> None:
    import bpy

    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)


def _import_part(path: Path):
    import bpy

    before_objects = set(bpy.context.scene.objects)
    before_actions = set(bpy.data.actions)
    bpy.ops.import_scene.gltf(filepath=str(path))
    imported_objects = [obj for obj in bpy.context.scene.objects if obj not in before_objects]
    imported_meshes = [obj for obj in imported_objects if obj.type == "MESH"]
    if not imported_meshes:
        _remove_objects(imported_objects)
        raise RuntimeError("Attachment GLB contains no mesh objects")

    # Generated parts should be unrigged. Converting evaluated geometry makes the
    # command deterministic even when a source GLB accidentally includes a rig.
    usable_meshes = []
    empty_meshes = []
    for obj in imported_meshes:
        world = obj.matrix_world.copy()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=bpy.context.evaluated_depsgraph_get())
        if not mesh.vertices or not mesh.polygons:
            bpy.data.meshes.remove(mesh)
            empty_meshes.append(obj)
            continue
        obj.modifiers.clear()
        obj.data = mesh
        obj.parent = None
        obj.matrix_world = world
        for group in list(obj.vertex_groups):
            obj.vertex_groups.remove(group)
        usable_meshes.append(obj)

    _remove_objects(
        [obj for obj in imported_objects if obj not in usable_meshes or obj in empty_meshes]
    )
    for action in [action for action in bpy.data.actions if action not in before_actions]:
        bpy.data.actions.remove(action)
    if not usable_meshes:
        raise RuntimeError("Attachment GLB contains no non-empty mesh geometry")
    return usable_meshes


def attach_character_part(
    character_path: Path,
    part_path: Path,
    glb_destination: Path,
    fbx_destination: Path,
    *,
    bone_name: str,
    part_name: str,
    part_anchor: str = "bottom",
    bone_anchor: str = "head",
    offset=(0.0, 0.0, 0.0),
    rotation_degrees=(0.0, 0.0, 0.0),
    scale: float = 1.0,
    fit_height: float = 0.0,
    force_opaque: bool = False,
) -> dict[str, object]:
    import bpy
    from mathutils import Euler, Matrix, Vector

    if scale <= 0 or not math.isfinite(scale):
        raise ValueError("scale must be a positive finite number")
    if fit_height < 0 or not math.isfinite(fit_height):
        raise ValueError("fit_height must be zero or a positive finite number")

    source_document = _read_glb_document(character_path)
    source_animation_names = sorted(
        animation.get("name", "") for animation in source_document.get("animations", [])
    )
    if not source_animation_names or any(not name for name in source_animation_names):
        raise RuntimeError("Character GLB must contain named animation clips")
    part_document = _read_glb_document(part_path)
    source_part_mesh_count = sum(
        1 for node in part_document.get("nodes", []) if "mesh" in node
    )
    if source_part_mesh_count <= 0:
        raise RuntimeError("Attachment GLB contains no visible mesh nodes")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(character_path))
    armatures = _armatures()
    character_meshes = _mesh_objects()
    if len(armatures) != 1 or not character_meshes:
        raise RuntimeError(
            "Character GLB must contain one armature and visible meshes; "
            f"found armatures={len(armatures)}, meshes={len(character_meshes)}"
        )
    armature = armatures[0]
    target_bone = _find_bone(armature, bone_name)
    target_bone_name = target_bone.name
    original_actions = [action for action in bpy.data.actions if action.fcurves]
    if not original_actions:
        raise RuntimeError("Character GLB contains no animation actions")

    if armature.animation_data is not None:
        armature.animation_data.action = None
    armature.data.pose_position = "REST"
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()

    part_meshes = _import_part(part_path)
    imported_part_mesh_count = len(part_meshes)
    source_bounds = _bounds(part_meshes)
    source_height = source_bounds[1][2] - source_bounds[0][2]
    if source_height <= 1e-8:
        raise RuntimeError("Attachment GLB has no usable height")
    effective_scale = scale * (fit_height / source_height if fit_height > 0 else 1.0)
    source_anchor = _anchor(source_bounds, part_anchor)
    target_anchor = _source_bone_anchor(character_path, target_bone.name, bone_anchor)
    offset_vector = Vector(offset)
    rotation = Euler(tuple(math.radians(value) for value in rotation_degrees), "XYZ")
    transform = (
        Matrix.Translation(target_anchor + offset_vector)
        @ rotation.to_matrix().to_4x4()
        @ Matrix.Scale(effective_scale, 4)
        @ Matrix.Translation(-source_anchor)
    )

    for index, obj in enumerate(part_meshes, start=1):
        obj.name = part_name if len(part_meshes) == 1 else f"{part_name}_{index:02d}"
        obj.data.name = f"{obj.name}Mesh"
        obj.matrix_world = transform @ obj.matrix_world
        world = obj.matrix_world.copy()
        obj.parent = armature
        obj.parent_type = "BONE"
        obj.parent_bone = target_bone.name
        obj.matrix_world = world

    opacity = _make_opaque(part_meshes) if force_opaque else None
    bpy.context.view_layer.update()
    output_bounds = _bounds(part_meshes)
    armature.data.pose_position = "POSE"
    if armature.animation_data is None:
        armature.animation_data_create()
    armature.animation_data.action = original_actions[0]

    export_objects = [armature, *character_meshes, *part_meshes]
    bpy.ops.object.select_all(action="DESELECT")
    for obj in export_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = armature
    glb_destination.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(glb_destination),
        export_format="GLB",
        use_selection=True,
        export_apply=False,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_texcoords=True,
        export_normals=True,
        export_skins=True,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_force_sampling=True,
        export_frame_range=True,
        export_yup=True,
    )
    bpy.ops.export_scene.fbx(
        filepath=str(fbx_destination),
        use_selection=True,
        object_types={"ARMATURE", "MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=True,
        bake_anim_use_all_bones=True,
        bake_anim_use_nla_strips=False,
        bake_anim_use_all_actions=True,
        bake_anim_force_startend_keying=True,
        apply_unit_scale=True,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="COPY",
        embed_textures=True,
    )
    if not glb_destination.is_file() or not fbx_destination.is_file():
        raise RuntimeError("Blender did not produce attachment outputs")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(glb_destination))
    exported_armatures = _armatures()
    exported_parts = [obj for obj in _mesh_objects() if obj.name.startswith(part_name)]
    if len(exported_armatures) != 1 or len(exported_parts) != source_part_mesh_count:
        raise RuntimeError(
            "Exported GLB lost its skeleton or attached part: "
            f"armatures={len(exported_armatures)}, "
            f"expectedParts={source_part_mesh_count}, actualParts={len(exported_parts)}, "
            f"partNames={[obj.name for obj in exported_parts]}"
        )
    exported_action_names = sorted(
        animation.get("name", "")
        for animation in _read_glb_document(glb_destination).get("animations", [])
    )
    if exported_action_names != source_animation_names:
        raise RuntimeError(
            "Attachment export changed the animation set: "
            f"before={source_animation_names}, after={exported_action_names}"
        )
    exported_bone = _find_bone(exported_armatures[0], bone_name)
    for obj in exported_parts:
        if (
            obj.parent != exported_armatures[0]
            or obj.parent_type != "BONE"
            or canonical_bone_name(obj.parent_bone) != canonical_bone_name(exported_bone.name)
        ):
            raise RuntimeError(f"Exported attachment {obj.name} is not parented to {bone_name}")

    result = {
        "mode": "rigid",
        "bone": target_bone_name,
        "partName": part_name,
        "partMeshes": source_part_mesh_count,
        "importedBlenderMeshes": imported_part_mesh_count,
        "animations": source_animation_names,
        "sourceBounds": {"minimum": source_bounds[0], "maximum": source_bounds[1]},
        "outputBounds": {"minimum": output_bounds[0], "maximum": output_bounds[1]},
        "partAnchor": part_anchor,
        "boneAnchor": bone_anchor,
        "targetAnchor": [float(value) for value in target_anchor],
        "offset": list(offset),
        "rotationDegrees": list(rotation_degrees),
        "requestedScale": scale,
        "fitHeight": fit_height,
        "effectiveScale": effective_scale,
        "glbBytes": glb_destination.stat().st_size,
        "fbxBytes": fbx_destination.stat().st_size,
    }
    if opacity is not None:
        result["opaqueMaterialStabilization"] = opacity
    return result


@app.function(
    image=runtime_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=30 * 60,
    cpu=4,
    memory=16384,
)
def build_attachment_bundle(
    character_bytes: bytes,
    part_bytes: bytes,
    *,
    output_key: str,
    bone_name: str,
    part_name: str,
    part_anchor: str,
    bone_anchor: str,
    offset: tuple[float, float, float],
    rotation_degrees: tuple[float, float, float],
    scale: float,
    fit_height: float,
    force_opaque: bool,
) -> str:
    directory = Path(tempfile.mkdtemp(prefix="character-attachment-"))
    character = directory / "character.glb"
    part = directory / "part.glb"
    glb_output = directory / "character-equipped.glb"
    fbx_output = directory / "character-equipped.fbx"
    report = directory / "attachment-report.json"
    character.write_bytes(character_bytes)
    part.write_bytes(part_bytes)
    result = attach_character_part(
        character,
        part,
        glb_output,
        fbx_output,
        bone_name=bone_name,
        part_name=part_name,
        part_anchor=part_anchor,
        bone_anchor=bone_anchor,
        offset=offset,
        rotation_degrees=rotation_degrees,
        scale=scale,
        fit_height=fit_height,
        force_opaque=force_opaque,
    )
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(glb_output, "character-equipped.glb")
        archive.write(fbx_output, "character-equipped.fbx")
        archive.write(report, "attachment-report.json")
    destination = Path(MODEL_VOLUME) / output_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(archive_bytes.getvalue())
    model_cache.commit()
    return output_key


def download_volume_file(remote_path: str, destination: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "modal",
            "volume",
            "get",
            "--force",
            MODEL_VOLUME_NAME,
            remote_path,
            str(destination),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "modal",
            "volume",
            "rm",
            MODEL_VOLUME_NAME,
            remote_path,
        ],
        check=True,
    )


@app.local_entrypoint()
def main(
    character: str,
    part: str,
    bone: str,
    output: str,
    output_fbx: str = "",
    report: str = "",
    part_name: str = "Attachment",
    part_anchor: str = "bottom",
    bone_anchor: str = "head",
    offset: str = "0,0,0",
    rotation: str = "0,0,0",
    scale: float = 1.0,
    fit_height: float = 0.0,
    force_opaque_materials: bool = False,
    force: bool = False,
) -> None:
    character_path = Path(character).expanduser().resolve()
    part_path = Path(part).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    fbx_destination = (
        Path(output_fbx).expanduser().resolve()
        if output_fbx
        else destination.with_suffix(".fbx")
    )
    report_path = (
        Path(report).expanduser().resolve()
        if report
        else destination.with_suffix(".attachment-report.json")
    )
    for path in (character_path, part_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if part_anchor not in {"origin", "bottom", "center", "top"}:
        raise ValueError("part_anchor must be origin, bottom, center, or top")
    if bone_anchor not in {"head", "center", "tail"}:
        raise ValueError("bone_anchor must be head, center, or tail")
    existing = [
        path for path in (destination, fbx_destination, report_path) if path.exists()
    ]
    if existing and not force:
        raise FileExistsError(f"Refusing to replace outputs without --force: {existing}")

    offset_vector = parse_vector(offset, label="offset")
    rotation_vector = parse_vector(rotation, label="rotation")
    temporary = Path(tempfile.mkdtemp(prefix="character-attachment-download-"))
    bundle = temporary / "character-attachment.zip"
    output_key = f"character-attachment/{destination.stem}.zip"
    remote_path = build_attachment_bundle.remote(
        character_path.read_bytes(),
        part_path.read_bytes(),
        output_key=output_key,
        bone_name=bone,
        part_name=part_name,
        part_anchor=part_anchor,
        bone_anchor=bone_anchor,
        offset=offset_vector,
        rotation_degrees=rotation_vector,
        scale=scale,
        fit_height=fit_height,
        force_opaque=force_opaque_materials,
    )
    download_volume_file(remote_path, bundle)
    with zipfile.ZipFile(bundle) as archive:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.read("character-equipped.glb"))
        fbx_destination.parent.mkdir(parents=True, exist_ok=True)
        fbx_destination.write_bytes(archive.read("character-equipped.fbx"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(archive.read("attachment-report.json"))
    print(f"Attached {part_name} to {bone}: {destination}")


if __name__ == "__main__":
    main()
