"""Prepare a Hunyuan character mesh for Mixamo on Modal.

The pipeline is deliberately conservative: it repairs small structural issues,
rejects obviously unsuitable poses, remeshes with QRemeshify, bakes the source
appearance, and exports both GLB and FBX deliverables. It does not claim to
recover semantic boundaries that were already fused by image-to-3D generation.
"""

from __future__ import annotations

from pathlib import Path
import json
import math
import shutil
import subprocess
import sys
import tempfile
import zipfile

import modal


APP_NAME = "game-model-wiki-character-mesh"
MODEL_VOLUME = "/models"
MODEL_VOLUME_NAME = "game-model-wiki-3d-model-cache"
QREMESHIFY_RELEASE = (
    "https://github.com/ksami/QRemeshify/releases/download/1.1.0/"
    "QRemeshify-1.1.0-linux.zip"
)

app = modal.App(APP_NAME)
model_cache = modal.Volume.from_name(MODEL_VOLUME_NAME, create_if_missing=True)

runtime_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "curl",
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
        "unzip",
    )
    .run_commands(
        "python -m pip install --upgrade pip",
        "python -m pip install bpy==4.2.0 numpy==1.26.4",
        "mkdir -p /opt/qremeshify && "
        f"curl -L --fail --silent --show-error '{QREMESHIFY_RELEASE}' "
        "-o /tmp/qremeshify.zip && "
        "unzip -q /tmp/qremeshify.zip -d /opt/qremeshify",
    )
    .env({"PYTHONPATH": "/opt/qremeshify"})
)


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


def _mesh_stats(obj) -> dict[str, int]:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary = sum(1 for edge in bm.edges if edge.is_boundary)
    non_manifold = sum(1 for edge in bm.edges if not edge.is_manifold)
    loose_vertices = sum(1 for vertex in bm.verts if not vertex.link_edges)
    stats = {
        "vertices": len(bm.verts),
        "edges": len(bm.edges),
        "faces": len(bm.faces),
        "triangles": sum(max(1, len(face.verts) - 2) for face in bm.faces),
        "boundaryEdges": boundary,
        "nonManifoldEdges": non_manifold,
        "looseVertices": loose_vertices,
    }
    bm.free()
    return stats


def _cleanup_mesh(obj, merge_distance: float) -> dict[str, int]:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    before_vertices = len(bm.verts)
    before_faces = len(bm.faces)
    if bm.verts:
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_distance)
    if bm.edges:
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=merge_distance)
    loose = [vertex for vertex in bm.verts if not vertex.link_edges]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {
        "removedVertices": before_vertices - len(obj.data.vertices),
        "removedFaces": before_faces - len(obj.data.polygons),
    }


def _seal_boundary_loops(obj) -> dict[str, int]:
    """Close remesher-created open boundary loops before Mixamo auto-rigging."""
    import bmesh

    before = _mesh_stats(obj)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    removed_faces = 0
    for _ in range(3):
        wire_edges = [edge for edge in bm.edges if not edge.link_faces]
        if wire_edges:
            bmesh.ops.delete(bm, geom=wire_edges, context="EDGES")

        extra_faces = set()
        for edge in bm.edges:
            if len(edge.link_faces) <= 2:
                continue
            ordered = sorted(edge.link_faces, key=lambda face: face.calc_area(), reverse=True)
            extra_faces.update(ordered[2:])
        if extra_faces:
            removed_faces += len(extra_faces)
            bmesh.ops.delete(bm, geom=list(extra_faces), context="FACES_ONLY")

        boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
        if boundary_edges:
            bmesh.ops.holes_fill(bm, edges=boundary_edges, sides=0)
        if not any(not edge.is_manifold for edge in bm.edges):
            break

    for _ in range(4):
        bad_edges = [edge for edge in bm.edges if not edge.is_manifold]
        if not bad_edges:
            break
        adjacent_faces = {face for edge in bad_edges for face in edge.link_faces}
        if adjacent_faces:
            removed_faces += len(adjacent_faces)
            bmesh.ops.delete(bm, geom=list(adjacent_faces), context="FACES")
        else:
            bmesh.ops.delete(bm, geom=bad_edges, context="EDGES")
        boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
        if boundary_edges:
            bmesh.ops.holes_fill(bm, edges=boundary_edges, sides=0)
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    after = _mesh_stats(obj)
    return {
        "boundaryEdgesBefore": before["boundaryEdges"],
        "boundaryEdgesAfter": after["boundaryEdges"],
        "nonManifoldEdgesBefore": before["nonManifoldEdges"],
        "nonManifoldEdgesAfter": after["nonManifoldEdges"],
        "facesAdded": after["faces"] - before["faces"],
        "conflictingFacesRemoved": removed_faces,
    }


def _normalize_character(objects, target_height: float) -> dict[str, object]:
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    minimum, maximum = _bounds(objects)
    height = maximum[2] - minimum[2]
    if height <= 1e-6:
        raise RuntimeError("Character mesh has no usable height")
    scale = target_height / height
    for obj in objects:
        obj.scale = (scale, scale, scale)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    scaled_minimum, scaled_maximum = _bounds(objects)
    center_x = (scaled_minimum[0] + scaled_maximum[0]) * 0.5
    center_y = (scaled_minimum[1] + scaled_maximum[1]) * 0.5
    for obj in objects:
        obj.location.x -= center_x
        obj.location.y -= center_y
        obj.location.z -= scaled_minimum[2]
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    normalized_minimum, normalized_maximum = _bounds(objects)
    dimensions = [
        normalized_maximum[axis] - normalized_minimum[axis] for axis in range(3)
    ]
    return {
        "sourceHeight": height,
        "targetHeight": target_height,
        "scale": scale,
        "bounds": {"minimum": normalized_minimum, "maximum": normalized_maximum},
        "dimensions": dimensions,
    }


def _join_meshes(objects, name: str):
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) > 1:
        bpy.ops.object.join()
    result = bpy.context.view_layer.objects.active
    result.name = name
    return result


def _decimate(obj, target_faces: int) -> None:
    import bpy

    triangle_count = sum(max(1, len(polygon.vertices) - 2) for polygon in obj.data.polygons)
    if triangle_count <= target_faces:
        return
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    modifier = obj.modifiers.new(name="AnimationMeshPreDecimate", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(0.001, target_faces / triangle_count)
    modifier.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def _qremesh(obj, target_faces: int, symmetry_x: bool):
    import bpy
    import QRemeshify

    try:
        QRemeshify.register()
    except ValueError:
        pass

    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    source_faces = max(1, len(obj.data.polygons))
    scene = bpy.context.scene
    scene.quadwild_props.enableRemesh = True
    scene.quadwild_props.enableSmoothing = True
    scene.quadwild_props.enableSharp = True
    scene.quadwild_props.sharpAngle = 40.0
    scene.quadwild_props.symmetryX = symmetry_x
    scene.quadpatches_props.scaleFact = max(
        0.15, min(8.0, math.sqrt(source_faces / max(target_faces, 1)))
    )
    scene.quadpatches_props.timeLimit = 240
    before = set(bpy.context.scene.objects)
    result = bpy.ops.qremeshify.remesh()
    created = [
        item for item in bpy.context.scene.objects if item not in before and item.type == "MESH"
    ]
    if "FINISHED" not in result or not created:
        raise RuntimeError(f"QRemeshify did not produce a mesh: {result}")
    remeshed = max(created, key=lambda item: len(item.data.polygons))
    remeshed.name = "AnimationReadyCharacter"
    remeshed.hide_set(False)
    bpy.context.view_layer.objects.active = remeshed
    bpy.ops.object.select_all(action="DESELECT")
    remeshed.select_set(True)
    return remeshed


def _refine_to_target(obj, source, target_triangles: int) -> dict[str, int]:
    import bpy

    before = sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)
    projected = before
    levels = 0
    while levels < 2 and projected * 4 <= int(target_triangles * 1.15):
        projected *= 4
        levels += 1
    if levels == 0:
        return {"subdivisionLevels": 0, "trianglesBefore": before, "trianglesAfter": before}

    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    subdivision = obj.modifiers.new(name="AnimationMeshTargetRefine", type="SUBSURF")
    subdivision.subdivision_type = "SIMPLE"
    subdivision.levels = levels
    subdivision.render_levels = levels
    bpy.ops.object.modifier_apply(modifier=subdivision.name)

    shrinkwrap = obj.modifiers.new(name="AnimationMeshHighSourceProjection", type="SHRINKWRAP")
    shrinkwrap.target = source
    shrinkwrap.wrap_method = "NEAREST_SURFACEPOINT"
    shrinkwrap.wrap_mode = "ON_SURFACE"
    bpy.ops.object.modifier_apply(modifier=shrinkwrap.name)

    after = sum(max(1, len(face.vertices) - 2) for face in obj.data.polygons)
    return {"subdivisionLevels": levels, "trianglesBefore": before, "trianglesAfter": after}


def _smart_uv(obj) -> None:
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(89.0), island_margin=0.008)
    bpy.ops.object.mode_set(mode="OBJECT")


def _new_image_node(material, name: str, image, color_space: str):
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = name
    node.label = name
    node.image = image
    node.image.colorspace_settings.name = color_space
    material.node_tree.nodes.active = node
    return node


def _route_base_color_to_emission(source) -> None:
    for slot in source.material_slots:
        material = slot.material
        if material is None or not material.use_nodes:
            continue
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        for node in nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            base_color = node.inputs.get("Base Color")
            emission_color = node.inputs.get("Emission Color")
            if emission_color is None:
                emission_color = node.inputs.get("Emission")
            emission_strength = node.inputs.get("Emission Strength")
            if base_color is None or emission_color is None:
                continue
            if base_color.is_linked:
                links.new(base_color.links[0].from_socket, emission_color)
            else:
                emission_color.default_value = base_color.default_value
            if emission_strength is not None:
                emission_strength.default_value = 1.0


def _bake_textures(
    source,
    target,
    directory: Path,
    texture_size: int,
    *,
    cage_extrusion: float = 0.006,
    max_ray_distance: float = 0.015,
) -> list[Path]:
    import bpy
    import numpy as np

    directory.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.image_settings.file_format = "PNG"
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.cage_extrusion = cage_extrusion
    scene.render.bake.max_ray_distance = max_ray_distance

    material = bpy.data.materials.new("AnimationReadyPBR")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    target.data.materials.clear()
    target.data.materials.append(material)

    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and obj not in {source, target}:
            obj.hide_render = True
            obj.hide_set(True)

    def select_for_bake() -> None:
        bpy.ops.object.select_all(action="DESELECT")
        source.hide_set(False)
        source.hide_render = False
        target.hide_set(False)
        target.hide_render = False
        source.select_set(True)
        target.select_set(True)
        bpy.context.view_layer.objects.active = target

    outputs: list[Path] = []
    base = bpy.data.images.new("AnimationReadyBaseColor", texture_size, texture_size, alpha=True)
    base.generated_color = (0.5, 0.5, 0.5, 1.0)
    base_node = _new_image_node(material, "BaseColorBake", base, "sRGB")
    _route_base_color_to_emission(source)
    select_for_bake()
    bpy.ops.object.bake(type="EMIT")
    base_pixels = np.empty(len(base.pixels), dtype=np.float32)
    base.pixels.foreach_get(base_pixels)
    base_rows = base_pixels.reshape((-1, 4))
    if float(base_rows[:, :3].max()) <= 1e-4:
        raise RuntimeError("Base Color bake is blank; verify source material visibility")
    # The character material is opaque. A few unbaked transparent texels can make
    # Mixamo's FBX round-trip mark the entire material as blended, which causes
    # depth-sorting holes across armor and clothing.
    base_rows[:, 3] = 1.0
    base.pixels.foreach_set(base_pixels)
    base.update()
    base_path = directory / "base-color.png"
    base.filepath_raw = str(base_path)
    base.save()
    outputs.append(base_path)
    links.new(base_node.outputs["Color"], principled.inputs["Base Color"])

    normal = bpy.data.images.new("AnimationReadyNormal", texture_size, texture_size)
    normal_node = _new_image_node(material, "NormalBake", normal, "Non-Color")
    select_for_bake()
    bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT")
    normal_path = directory / "normal.png"
    normal.filepath_raw = str(normal_path)
    normal.save()
    outputs.append(normal_path)
    normal_map = nodes.new("ShaderNodeNormalMap")
    links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])

    ao = bpy.data.images.new("AnimationReadyAO", texture_size, texture_size)
    _new_image_node(material, "AOBake", ao, "Non-Color")
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    scene.render.bake.use_selected_to_active = False
    bpy.ops.object.bake(type="AO", margin=16)
    ao_path = directory / "ao.png"
    ao.filepath_raw = str(ao_path)
    ao.save()
    outputs.append(ao_path)
    return outputs


def _export_outputs(target, output_dir: Path) -> tuple[Path, Path]:
    import bpy

    glb = output_dir / "animation-ready.glb"
    fbx = output_dir / "mixamo-upload.fbx"
    bpy.ops.object.select_all(action="DESELECT")
    target.hide_set(False)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.export_scene.gltf(
        filepath=str(glb),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_texcoords=True,
        export_normals=True,
        export_yup=True,
    )
    bpy.ops.export_scene.fbx(
        filepath=str(fbx),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        apply_unit_scale=True,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="COPY",
        embed_textures=True,
    )
    return glb, fbx


def prepare_character(
    source_path: Path,
    output_dir: Path,
    *,
    target_faces: int,
    target_height: float,
    texture_size: int,
    symmetry_x: bool,
) -> dict[str, object]:
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source_path))
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("Input GLB has no mesh objects")
    if any(obj.find_armature() is not None for obj in mesh_objects):
        raise RuntimeError("Mesh preparation expects an unskinned Hunyuan GLB")

    normalized = _normalize_character(mesh_objects, target_height)
    character = _join_meshes(mesh_objects, "HunyuanHighSource")
    cleanup = _cleanup_mesh(character, target_height * 1e-6)
    source_stats = _mesh_stats(character)
    dimensions = normalized["dimensions"]
    width_height = float(dimensions[0]) / max(float(dimensions[2]), 1e-8)
    depth_height = float(dimensions[1]) / max(float(dimensions[2]), 1e-8)
    errors: list[str] = []
    warnings: list[str] = []
    if width_height < 0.65:
        errors.append("character is not in a usable T-pose: arm span is too narrow")
    if depth_height > 0.65:
        warnings.append("character depth is unusually large; verify front axis and pose")
    non_manifold_ratio = source_stats["nonManifoldEdges"] / max(source_stats["edges"], 1)
    if non_manifold_ratio > 0.08:
        errors.append("source mesh has excessive non-manifold geometry")
    elif non_manifold_ratio > 0.02:
        warnings.append("source mesh has elevated non-manifold geometry")
    if errors:
        raise RuntimeError("; ".join(errors))

    high_source = character.copy()
    high_source.data = character.data.copy()
    high_source.name = "BakeSource"
    bpy.context.collection.objects.link(high_source)
    for slot in high_source.material_slots:
        if slot.material is not None:
            slot.material = slot.material.copy()
    high_source.hide_render = False

    _decimate(character, min(100_000, max(target_faces * 5, target_faces)))
    low = _qremesh(character, target_faces, symmetry_x)
    refinement = _refine_to_target(low, high_source, target_faces)
    low_triangles = sum(max(1, len(face.vertices) - 2) for face in low.data.polygons)
    if low_triangles > int(target_faces * 1.15):
        _decimate(low, target_faces)
    _cleanup_mesh(low, target_height * 1e-7)
    topology_repair = _seal_boundary_loops(low)
    repaired_stats = _mesh_stats(low)
    if repaired_stats["nonManifoldEdges"]:
        raise RuntimeError(
            "animation mesh remains non-manifold after boundary repair: "
            f"{repaired_stats['nonManifoldEdges']} edges"
        )
    _smart_uv(low)
    texture_paths = _bake_textures(high_source, low, output_dir / "textures", texture_size)
    high_source.hide_set(True)
    character.hide_set(True)
    glb, fbx = _export_outputs(low, output_dir)
    output_stats = _mesh_stats(low)
    report = {
        "passed": True,
        "source": source_stats,
        "output": output_stats,
        "normalization": normalized,
        "cleanup": cleanup,
        "topologyRepair": topology_repair,
        "refinement": refinement,
        "pose": {"widthHeightRatio": width_height, "depthHeightRatio": depth_height},
        "warnings": warnings,
        "limitations": [
            "semantic clothing/body fusion cannot be reconstructed automatically",
            "visual T-pose and deformation review remains mandatory",
        ],
        "artifacts": {
            "glb": glb.name,
            "fbx": fbx.name,
            "textures": [path.relative_to(output_dir).as_posix() for path in texture_paths],
        },
    }
    (output_dir / "mesh-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


@app.function(
    image=runtime_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=50 * 60,
    cpu=8,
    memory=32768,
)
def prepare_mesh_bundle(
    mesh_bytes: bytes,
    output_key: str,
    target_faces: int = 15_000,
    target_height: float = 1.9,
    texture_size: int = 2048,
    symmetry_x: bool = False,
) -> str:
    directory = Path(tempfile.mkdtemp(prefix="character-mesh-"))
    source = directory / "source.glb"
    output_dir = directory / "output"
    output_dir.mkdir()
    source.write_bytes(mesh_bytes)
    prepare_character(
        source,
        output_dir,
        target_faces=target_faces,
        target_height=target_height,
        texture_size=texture_size,
        symmetry_x=symmetry_x,
    )
    bundle = directory / "animation-ready.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir).as_posix())
    destination = Path(MODEL_VOLUME) / output_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(bundle.read_bytes())
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
    input: str,
    output_dir: str,
    target_faces: int = 15_000,
    target_height: float = 1.9,
    texture_size: int = 2048,
    symmetry_x: bool = False,
    force: bool = False,
) -> None:
    source = Path(input).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not 4_000 <= target_faces <= 100_000:
        raise ValueError("target_faces must be between 4000 and 100000")
    if texture_size not in {512, 1024, 2048, 4096}:
        raise ValueError("texture_size must be 512, 1024, 2048, or 4096")
    if destination.exists() and any(destination.iterdir()) and not force:
        raise FileExistsError(f"Refusing to replace non-empty output directory: {destination}")

    destination.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="character-mesh-download-"))
    archive = temporary / "animation-ready.zip"
    output_key = f"character-mesh/{source.stem}-animation-ready.zip"
    remote_path = prepare_mesh_bundle.remote(
        source.read_bytes(),
        output_key=output_key,
        target_faces=target_faces,
        target_height=target_height,
        texture_size=texture_size,
        symmetry_x=symmetry_x,
    )
    download_volume_file(remote_path, archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)
    shutil.rmtree(temporary)
    print(f"Prepared Mixamo mesh assets in {destination}")
