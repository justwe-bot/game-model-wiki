"""Build one animated GLB from a Mixamo With Skin FBX and action FBXs."""

from __future__ import annotations

from pathlib import Path
import io
import json
import re
import subprocess
import sys
import tempfile
import zipfile

import modal


APP_NAME = "game-model-wiki-mixamo-character"
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


CORE_BONES = {
    "Hips",
    "Spine",
    "Head",
    "LeftArm",
    "LeftForeArm",
    "LeftHand",
    "RightArm",
    "RightForeArm",
    "RightHand",
    "LeftUpLeg",
    "LeftLeg",
    "LeftFoot",
    "RightUpLeg",
    "RightLeg",
    "RightFoot",
}


def canonical_bone_name(value: str) -> str:
    return value.rsplit(":", 1)[-1]


def clip_name(filename: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", " ", Path(filename).stem.lower()).strip()
    return "Mixamo_" + "".join(part.title() for part in slug.split())


def _armatures():
    import bpy

    return [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]


def _mesh_objects():
    import bpy

    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def _bone_set(armature) -> set[str]:
    return {canonical_bone_name(bone.name) for bone in armature.data.bones}


def _bounds(objects) -> tuple[list[float], list[float]]:
    from mathutils import Vector

    minimum = [float("inf"), float("inf"), float("inf")]
    maximum = [float("-inf"), float("-inf"), float("-inf")]
    for obj in objects:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                minimum[axis] = min(minimum[axis], float(point[axis]))
                maximum[axis] = max(maximum[axis], float(point[axis]))
    return minimum, maximum


def _hierarchy_roots(objects):
    roots = []
    seen = set()
    for obj in objects:
        root = obj
        while root.parent is not None:
            root = root.parent
        if root not in seen:
            roots.append(root)
            seen.add(root)
    return roots


def normalize_character(objects, target_height: float) -> dict[str, object]:
    import bpy

    if target_height <= 0:
        raise ValueError("target_height must be positive")
    minimum, maximum = _bounds(objects)
    height = maximum[2] - minimum[2]
    if height <= 1e-6:
        raise RuntimeError("With Skin FBX has no usable character height")
    roots = _hierarchy_roots(objects)
    scale = target_height / height
    for root in roots:
        root.scale = tuple(float(value) * scale for value in root.scale)
    bpy.context.view_layer.update()

    scaled_minimum, scaled_maximum = _bounds(objects)
    offset = (
        -(scaled_minimum[0] + scaled_maximum[0]) * 0.5,
        -(scaled_minimum[1] + scaled_maximum[1]) * 0.5,
        -scaled_minimum[2],
    )
    for root in roots:
        root.location = tuple(
            float(root.location[axis]) + offset[axis] for axis in range(3)
        )
    bpy.context.view_layer.update()
    final_minimum, final_maximum = _bounds(objects)
    return {
        "sourceBounds": {"minimum": minimum, "maximum": maximum},
        "sourceHeight": height,
        "scale": scale,
        "targetHeight": target_height,
        "outputBounds": {"minimum": final_minimum, "maximum": final_maximum},
    }


def stabilize_helmet_weights(
    meshes,
    *,
    rigid_threshold: float = 0.35,
    minimum_head_influence: float = 0.02,
) -> dict[str, int | float]:
    if not 0.0 <= minimum_head_influence <= rigid_threshold <= 1.0:
        raise ValueError(
            "Helmet weight thresholds must satisfy "
            "0 <= minimum_head_influence <= rigid_threshold <= 1"
        )

    vertices = 0
    changed = 0
    rigid = 0
    localized = 0
    affected_meshes = 0
    for mesh in meshes:
        groups_by_bone: dict[str, list[object]] = {}
        for group in mesh.vertex_groups:
            groups_by_bone.setdefault(canonical_bone_name(group.name), []).append(group)
        head_groups = groups_by_bone.get("Head", [])
        if not head_groups:
            vertices += len(mesh.data.vertices)
            continue
        neck_groups = groups_by_bone.get("Neck", [])
        if len(head_groups) != 1 or len(neck_groups) != 1:
            raise RuntimeError(
                f"Mesh {mesh.name} must have exactly one Head and Neck vertex group"
            )
        head_group = head_groups[0]
        neck_group = neck_groups[0]
        affected_meshes += 1

        for vertex in mesh.data.vertices:
            vertices += 1
            memberships = [
                (mesh.vertex_groups[item.group], float(item.weight))
                for item in vertex.groups
                if item.weight > 0.0
            ]
            head_weight = sum(
                weight
                for group, weight in memberships
                if canonical_bone_name(group.name) == "Head"
            )
            if head_weight < minimum_head_influence:
                continue

            for group, _weight in memberships:
                group.remove([vertex.index])
            if head_weight >= rigid_threshold:
                head_group.add([vertex.index], 1.0, "REPLACE")
                rigid += 1
            else:
                head_weight = min(1.0, max(0.0, head_weight))
                head_group.add([vertex.index], head_weight, "REPLACE")
                neck_group.add([vertex.index], 1.0 - head_weight, "REPLACE")
                localized += 1
            changed += 1

    if affected_meshes == 0:
        raise RuntimeError("No visible mesh contains a Mixamo Head vertex group")
    return {
        "meshes": affected_meshes,
        "vertices": vertices,
        "changedVertices": changed,
        "rigidVertices": rigid,
        "localizedVertices": localized,
        "rigidThreshold": rigid_threshold,
        "minimumHeadInfluence": minimum_head_influence,
    }


def force_opaque_materials(meshes) -> dict[str, int]:
    import numpy as np

    materials = []
    seen_materials = set()
    images = []
    seen_images = set()
    alpha_links_removed = 0
    for mesh in meshes:
        for slot in mesh.material_slots:
            material = slot.material
            if material is None or material in seen_materials:
                continue
            seen_materials.add(material)
            materials.append(material)
            material.diffuse_color = (
                float(material.diffuse_color[0]),
                float(material.diffuse_color[1]),
                float(material.diffuse_color[2]),
                1.0,
            )
            if hasattr(material, "blend_method"):
                material.blend_method = "OPAQUE"
            if hasattr(material, "surface_render_method"):
                material.surface_render_method = "DITHERED"
            if not material.use_nodes:
                continue
            for node in material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image is not None:
                    if node.image not in seen_images:
                        seen_images.add(node.image)
                        images.append(node.image)
                if node.type != "BSDF_PRINCIPLED":
                    continue
                alpha_input = node.inputs.get("Alpha")
                if alpha_input is None:
                    continue
                for link in list(alpha_input.links):
                    material.node_tree.links.remove(link)
                    alpha_links_removed += 1
                alpha_input.default_value = 1.0

    changed_alpha_pixels = 0
    opaque_images = 0
    for image in images:
        if image.channels < 4 or len(image.pixels) == 0:
            continue
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        alpha = pixels[3::4]
        changed_alpha_pixels += int(np.count_nonzero(alpha < 1.0 - 1e-6))
        alpha[:] = 1.0
        image.pixels.foreach_set(pixels)
        image.update()
        image.pack()
        opaque_images += 1
    return {
        "materials": len(materials),
        "images": opaque_images,
        "alphaLinksRemoved": alpha_links_removed,
        "changedAlphaPixels": changed_alpha_pixels,
    }


def remap_action_bone_paths(action, base_armature) -> int:
    base_names = {
        canonical_bone_name(bone.name): bone.name for bone in base_armature.data.bones
    }
    changed = 0
    pattern = re.compile(r'pose\.bones\["([^"]+)"\]')
    for curve in action.fcurves:
        match = pattern.search(curve.data_path)
        if match is None:
            continue
        source_name = match.group(1)
        target_name = base_names.get(canonical_bone_name(source_name))
        if target_name is None or target_name == source_name:
            continue
        curve.data_path = curve.data_path.replace(
            f'pose.bones["{source_name}"]', f'pose.bones["{target_name}"]'
        )
        changed += 1
    return changed


def _remove_objects(objects) -> None:
    import bpy

    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)


def _import_action(source: Path, base_armature, used_names: set[str]):
    import bpy

    before_objects = set(bpy.context.scene.objects)
    before_actions = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(
        filepath=str(source),
        ignore_leaf_bones=False,
        use_image_search=False,
        automatic_bone_orientation=False,
    )
    new_objects = [obj for obj in bpy.context.scene.objects if obj not in before_objects]
    new_armatures = [obj for obj in new_objects if obj.type == "ARMATURE"]
    new_actions = [action for action in bpy.data.actions if action not in before_actions]
    if len(new_armatures) != 1 or not new_actions:
        _remove_objects(new_objects)
        raise RuntimeError(
            f"Expected one action armature and at least one action in {source.name}"
        )
    source_armature = new_armatures[0]
    if not CORE_BONES.issubset(_bone_set(source_armature)):
        _remove_objects(new_objects)
        raise RuntimeError(f"Action does not contain a standard Mixamo skeleton: {source.name}")
    if _bone_set(source_armature) != _bone_set(base_armature):
        _remove_objects(new_objects)
        raise RuntimeError(f"Action skeleton differs from the With Skin base: {source.name}")

    action = max(new_actions, key=lambda item: item.frame_range[1] - item.frame_range[0])
    remap_action_bone_paths(action, base_armature)
    name = clip_name(source.name)
    suffix = 2
    while name in used_names:
        name = f"{clip_name(source.name)}_{suffix}"
        suffix += 1
    action.name = name
    action.use_fake_user = True
    used_names.add(name)
    _remove_objects(new_objects)
    return action


def verify_exported_glb(
    path: Path,
    target_height: float,
    *,
    require_opaque_materials: bool = False,
) -> dict[str, object]:
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    armatures = _armatures()
    meshes = _mesh_objects()
    actions = [action for action in bpy.data.actions if action.fcurves]
    if len(armatures) != 1 or not meshes:
        raise RuntimeError(
            "Exported GLB lost its character structure: "
            f"armatures={len(armatures)}, meshes={len(meshes)}"
        )
    if not actions:
        raise RuntimeError("Exported GLB contains no animation actions")
    transparent_materials = []
    for material in bpy.data.materials:
        if not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            alpha_input = node.inputs.get("Alpha")
            if alpha_input is not None and (
                alpha_input.is_linked or float(alpha_input.default_value) < 1.0 - 1e-6
            ):
                transparent_materials.append(material.name)
                break
    if require_opaque_materials and transparent_materials:
        raise RuntimeError(
            "Exported GLB still contains transparent character materials: "
            f"{sorted(set(transparent_materials))}"
        )
    action_count = len(actions)
    for armature in armatures:
        if armature.animation_data is not None:
            armature.animation_data_clear()
        armature.data.pose_position = "REST"
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    minimum, maximum = _bounds(meshes)
    dimensions = [maximum[axis] - minimum[axis] for axis in range(3)]
    height_axis = min(
        range(3), key=lambda axis: abs(dimensions[axis] - target_height)
    )
    height = dimensions[height_axis]
    minimum_height = target_height * 0.85
    maximum_height = target_height * 1.15
    if not minimum_height <= height <= maximum_height:
        raise RuntimeError(
            f"Exported GLB height {height:.4f} m is outside the expected "
            f"range {minimum_height:.4f}-{maximum_height:.4f} m"
        )
    return {
        "armatures": len(armatures),
        "meshes": len(meshes),
        "animations": action_count,
        "height": height,
        "heightAxis": height_axis,
        "dimensions": dimensions,
        "bounds": {"minimum": minimum, "maximum": maximum},
        "transparentMaterials": sorted(set(transparent_materials)),
    }


def build_character(
    base_fbx: Path,
    actions_zip: Path,
    glb_destination: Path,
    fbx_destination: Path,
    target_height: float = 1.9,
    stabilize_helmet: bool = False,
    helmet_rigid_threshold: float = 0.35,
    helmet_minimum_head_influence: float = 0.02,
    force_opaque: bool = False,
) -> dict[str, object]:
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(
        filepath=str(base_fbx),
        ignore_leaf_bones=False,
        use_image_search=True,
        automatic_bone_orientation=False,
    )
    armatures = _armatures()
    meshes = _mesh_objects()
    if len(armatures) != 1 or not meshes:
        raise RuntimeError(
            f"With Skin FBX must contain one armature and visible meshes; "
            f"found armatures={len(armatures)}, meshes={len(meshes)}"
        )
    base_armature = armatures[0]
    missing = sorted(CORE_BONES - _bone_set(base_armature))
    if missing:
        raise RuntimeError(f"With Skin FBX is missing Mixamo bones: {missing}")
    if any(obj.find_armature() != base_armature for obj in meshes):
        raise RuntimeError("Every visible mesh must be skinned to the Mixamo armature")
    normalization = normalize_character([base_armature, *meshes], target_height)
    helmet_stabilization = None
    if stabilize_helmet:
        helmet_stabilization = stabilize_helmet_weights(
            meshes,
            rigid_threshold=helmet_rigid_threshold,
            minimum_head_influence=helmet_minimum_head_influence,
        )
    opaque_material_stabilization = None
    if force_opaque:
        opaque_material_stabilization = force_opaque_materials(meshes)

    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    imported_actions = []
    used_names: set[str] = set()
    directory = Path(tempfile.mkdtemp(prefix="mixamo-actions-"))
    with zipfile.ZipFile(actions_zip) as archive:
        members = sorted(
            member
            for member in archive.namelist()
            if not member.endswith("/") and member.lower().endswith(".fbx")
        )
        if not members:
            raise RuntimeError("Action archive contains no FBX files")
        if len(set(members)) != len(members):
            raise RuntimeError("Action archive contains duplicate FBX filenames")
        for member in members:
            if Path(member).name != member:
                raise RuntimeError(f"Action archive must be flat: {member}")
            source = directory / Path(member).name
            source.write_bytes(archive.read(member))
            imported_actions.append(_import_action(source, base_armature, used_names))

    if base_armature.animation_data is None:
        base_armature.animation_data_create()
    base_armature.animation_data.action = imported_actions[0]
    glb_destination.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    export_objects = _hierarchy_roots([base_armature, *meshes]) + [base_armature, *meshes]
    for obj in export_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = base_armature
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
        object_types={"ARMATURE", "EMPTY", "MESH"},
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
        raise RuntimeError("Blender did not produce an animated Mixamo GLB")
    animation_names = [action.name for action in imported_actions]
    mesh_count = len(meshes)
    bone_count = len(base_armature.data.bones)
    glb_bytes = glb_destination.stat().st_size
    fbx_bytes = fbx_destination.stat().st_size
    export_verification = verify_exported_glb(
        glb_destination,
        target_height,
        require_opaque_materials=force_opaque,
    )
    if export_verification["animations"] != len(animation_names):
        raise RuntimeError(
            "Exported GLB animation count differs from the imported action set: "
            f"expected={len(animation_names)}, actual={export_verification['animations']}"
        )
    result = {
        "meshes": mesh_count,
        "bones": bone_count,
        "animations": animation_names,
        "normalization": normalization,
        "exportVerification": export_verification,
        "glbBytes": glb_bytes,
        "fbxBytes": fbx_bytes,
    }
    if helmet_stabilization is not None:
        result["helmetWeightStabilization"] = helmet_stabilization
    if opaque_material_stabilization is not None:
        result["opaqueMaterialStabilization"] = opaque_material_stabilization
    return result


@app.function(
    image=runtime_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=30 * 60,
    cpu=4,
    memory=16384,
)
def build_mixamo_bundle(
    base_bytes: bytes,
    action_bytes: bytes,
    output_key: str,
    target_height: float = 1.9,
    stabilize_helmet: bool = False,
    helmet_rigid_threshold: float = 0.35,
    helmet_minimum_head_influence: float = 0.02,
    force_opaque: bool = False,
) -> str:
    directory = Path(tempfile.mkdtemp(prefix="mixamo-character-"))
    base = directory / "character-with-skin.fbx"
    actions = directory / "actions.zip"
    glb_output = directory / "animated-character.glb"
    fbx_output = directory / "animated-character.fbx"
    report = directory / "mixamo-report.json"
    base.write_bytes(base_bytes)
    actions.write_bytes(action_bytes)
    result = build_character(
        base,
        actions,
        glb_output,
        fbx_output,
        target_height=target_height,
        stabilize_helmet=stabilize_helmet,
        helmet_rigid_threshold=helmet_rigid_threshold,
        helmet_minimum_head_influence=helmet_minimum_head_influence,
        force_opaque=force_opaque,
    )
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(glb_output, glb_output.name)
        archive.write(fbx_output, fbx_output.name)
        archive.write(report, report.name)
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
    base_fbx: str,
    actions_zip: str,
    output: str,
    output_fbx: str = "",
    report: str = "",
    target_height: float = 1.9,
    stabilize_helmet_weights: bool = False,
    helmet_rigid_threshold: float = 0.35,
    helmet_minimum_head_influence: float = 0.02,
    force_opaque_materials: bool = False,
    force: bool = False,
) -> None:
    base = Path(base_fbx).expanduser().resolve()
    actions = Path(actions_zip).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    fbx_destination = (
        Path(output_fbx).expanduser().resolve()
        if output_fbx
        else destination.with_suffix(".fbx")
    )
    report_path = (
        Path(report).expanduser().resolve()
        if report
        else destination.with_suffix(".mixamo-report.json")
    )
    if not base.is_file() or not actions.is_file():
        raise FileNotFoundError(f"Missing Mixamo input: base={base}, actions={actions}")
    existing = [
        path for path in (destination, fbx_destination, report_path) if path.exists()
    ]
    if existing and not force:
        raise FileExistsError(f"Refusing to replace outputs without --force: {existing}")

    temporary = Path(tempfile.mkdtemp(prefix="mixamo-character-download-"))
    bundle = temporary / "mixamo-character.zip"
    output_key = f"mixamo-character/{destination.stem}.zip"
    remote_path = build_mixamo_bundle.remote(
        base.read_bytes(),
        actions.read_bytes(),
        output_key=output_key,
        target_height=target_height,
        stabilize_helmet=stabilize_helmet_weights,
        helmet_rigid_threshold=helmet_rigid_threshold,
        helmet_minimum_head_influence=helmet_minimum_head_influence,
        force_opaque=force_opaque_materials,
    )
    download_volume_file(remote_path, bundle)
    with zipfile.ZipFile(bundle) as archive:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.read("animated-character.glb"))
        fbx_destination.parent.mkdir(parents=True, exist_ok=True)
        fbx_destination.write_bytes(archive.read("animated-character.fbx"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(archive.read("mixamo-report.json"))
    print(f"Animated Mixamo GLB: {destination}")
    print(f"Animated Mixamo FBX: {fbx_destination}")
    print(f"Build report: {report_path}")
