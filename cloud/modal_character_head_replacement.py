"""Replace an integrated biological head before Mixamo skinning.

The body and replacement head remain unskinned. Blender removes the old head,
trims and aligns the replacement, then bridges only the two open neck boundary
loops. Source vertices, UVs, materials, and textures remain unchanged outside
the newly created neck strip.
"""

from __future__ import annotations

from pathlib import Path
import io
import json
import math
import subprocess
import sys
import tempfile
import zipfile

import modal


APP_NAME = "game-model-wiki-character-head-replacement"
MODEL_VOLUME = "/models"
MODEL_VOLUME_NAME = "game-model-wiki-3d-model-cache"
MIN_TEXTURE_COVERAGE = 0.20

app = modal.App(APP_NAME)
model_cache = modal.Volume.from_name(MODEL_VOLUME_NAME, create_if_missing=True)

runtime_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "libegl1", "libgl1", "libglib2.0-0", "libgomp1", "libice6",
        "libsm6", "libx11-6", "libxfixes3", "libxi6", "libxkbcommon0",
        "libxrender1", "libxxf86vm1",
    )
    .run_commands(
        "python -m pip install --upgrade pip",
        "python -m pip install bpy==4.2.0 numpy==1.26.4",
    )
)


def parse_vector(value: str, *, label: str) -> tuple[float, float, float]:
    try:
        result = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise ValueError(f"{label} must contain three comma-separated numbers") from exc
    if len(result) != 3 or not all(math.isfinite(item) for item in result):
        raise ValueError(f"{label} must contain three finite comma-separated numbers")
    return result


def resolve_cut_height(minimum: float, maximum: float, ratio: float) -> float:
    if not 0.0 < ratio < 1.0:
        raise ValueError("cut ratio must be between zero and one")
    if maximum <= minimum:
        raise ValueError("bounds must have positive height")
    return minimum + (maximum - minimum) * ratio


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


def _mesh_stats(obj) -> dict[str, int]:
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    stats = {
        "vertices": len(bm.verts),
        "edges": len(bm.edges),
        "faces": len(bm.faces),
        "triangles": sum(max(1, len(face.verts) - 2) for face in bm.faces),
        "boundaryEdges": sum(1 for edge in bm.edges if edge.is_boundary),
        "nonManifoldEdges": sum(1 for edge in bm.edges if not edge.is_manifold),
        "looseVertices": sum(1 for vertex in bm.verts if not vertex.link_edges),
    }
    bm.free()
    return stats


def _non_manifold_edge_details(obj, limit: int = 20) -> list[dict[str, object]]:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    details = []
    for edge in bm.edges:
        if edge.is_manifold:
            continue
        center = (edge.verts[0].co + edge.verts[1].co) * 0.5
        details.append(
            {
                "faceCount": len(edge.link_faces),
                "center": [float(center[axis]) for axis in range(3)],
                "length": float(edge.calc_length()),
            }
        )
        if len(details) >= limit:
            break
    bm.free()
    return details


def _triangulate_mesh(obj) -> None:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _weld_coincident_vertices(obj, distance: float = 1e-7) -> int:
    """Remove zero-distance GLB export seams without changing the surface shape."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=distance)
    removed = before - len(bm.verts)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return removed


def _join_meshes(objects, name: str):
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) > 1:
        bpy.ops.object.join()
    result = bpy.context.view_layer.objects.active
    result.name = name
    return result


def _apply_world_transforms(objects) -> None:
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def _delete_vertices_by_world_z(obj, threshold: float, *, delete_above: bool) -> int:
    import bmesh
    from mathutils import Vector
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    selected = []
    for vertex in bm.verts:
        world_z = (obj.matrix_world @ Vector(vertex.co)).z
        if (world_z > threshold) if delete_above else (world_z < threshold):
            selected.append(vertex)
    if not selected:
        bm.free()
        raise RuntimeError("Head replacement cut removed no vertices; check cut ratios")
    bmesh.ops.delete(bm, geom=selected, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(selected)


def _remove_loose_cut_geometry(obj) -> dict[str, int]:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    loose_edges = [edge for edge in bm.edges if not edge.link_faces]
    loose_vertices = [vertex for vertex in bm.verts if not vertex.link_edges]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
    if loose_vertices:
        bmesh.ops.delete(bm, geom=loose_vertices, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {"edges": len(loose_edges), "vertices": len(loose_vertices)}


def _mark_primary_boundary_loop(obj, target_z: float, group_name: str) -> dict[str, object]:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
    if not boundary_edges:
        bm.free()
        raise RuntimeError(f"{obj.name} has no open boundary after the neck cut")

    edge_neighbors: dict[object, list[object]] = {}
    for edge in boundary_edges:
        for vertex in edge.verts:
            edge_neighbors.setdefault(vertex, []).append(edge)

    remaining = set(boundary_edges)
    components: list[tuple[list[object], set[object]]] = []
    while remaining:
        seed = remaining.pop()
        edges = [seed]
        vertices = set(seed.verts)
        stack = list(seed.verts)
        while stack:
            vertex = stack.pop()
            for edge in edge_neighbors.get(vertex, []):
                if edge not in remaining:
                    continue
                remaining.remove(edge)
                edges.append(edge)
                for linked in edge.verts:
                    if linked not in vertices:
                        vertices.add(linked)
                        stack.append(linked)
        components.append((edges, vertices))

    def score(component) -> tuple[int, float]:
        edges, vertices = component
        center_z = sum(float(vertex.co.z) for vertex in vertices) / len(vertices)
        return -len(edges), abs(center_z - target_z)

    edges, vertices = min(components, key=score)
    sealed_faces = []
    sealed_center_vertices = 0
    for component_edges, component_vertices in components:
        if component_edges is edges:
            continue
        center_vertex = bm.verts.new(
            tuple(
                sum(float(vertex.co[axis]) for vertex in component_vertices)
                / len(component_vertices)
                for axis in range(3)
            )
        )
        sealed_center_vertices += 1
        for edge in component_edges:
            neighboring_materials = [
                linked.material_index for linked in edge.link_faces
            ]
            face = bm.faces.new((edge.verts[1], edge.verts[0], center_vertex))
            if neighboring_materials:
                face.material_index = max(
                    set(neighboring_materials), key=neighboring_materials.count
                )
            sealed_faces.append(face)
    indices = sorted(int(vertex.index) for vertex in vertices)
    center = [
        sum(float(vertex.co[axis]) for vertex in vertices) / len(vertices)
        for axis in range(3)
    ]
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    group = obj.vertex_groups.get(group_name) or obj.vertex_groups.new(name=group_name)
    group.add(indices, 1.0, "REPLACE")
    return {
        "vertices": len(vertices),
        "edges": len(edges),
        "center": center,
        "candidateLoops": len(components),
        "sealedSecondaryLoops": max(0, len(components) - 1),
        "sealedSecondaryFaces": len(sealed_faces),
        "sealedCenterVertices": sealed_center_vertices,
    }


def _bridge_neck_boundaries(
    obj, body_group_name: str, head_group_name: str, bridge_material_indices
) -> dict[str, object]:
    import bmesh
    from mathutils import Vector

    body_group = obj.vertex_groups.get(body_group_name)
    head_group = obj.vertex_groups.get(head_group_name)
    if body_group is None or head_group is None:
        raise RuntimeError("Neck boundary vertex groups were lost while joining the meshes")

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.active
    if deform is None:
        bm.free()
        raise RuntimeError("Joined neck mesh has no vertex-group deform layer")

    def in_group(vertex, group_index: int) -> bool:
        return vertex[deform].get(group_index, 0.0) > 0.5

    body_vertices = {vertex for vertex in bm.verts if in_group(vertex, body_group.index)}
    head_vertices = {vertex for vertex in bm.verts if in_group(vertex, head_group.index)}
    body_edges = [
        edge for edge in bm.edges
        if edge.is_boundary and all(vertex in body_vertices for vertex in edge.verts)
    ]
    head_edges = [
        edge for edge in bm.edges
        if edge.is_boundary and all(vertex in head_vertices for vertex in edge.verts)
    ]
    if not body_edges or not head_edges:
        bm.free()
        raise RuntimeError(
            "Could not recover both open neck loops after joining: "
            f"body={len(body_edges)}, head={len(head_edges)}"
        )

    def ordered_loop(edges):
        edge_set = set(edges)
        if any(
            sum(1 for linked in vertex.link_edges if linked in edge_set) != 2
            for edge in edges
            for vertex in edge.verts
        ):
            raise RuntimeError("Neck boundary is not a simple closed loop")
        start = edges[0]
        ordered_edges = []
        ordered_vertices = []
        current_edge = start
        current_vertex = start.verts[0]
        while True:
            ordered_edges.append(current_edge)
            ordered_vertices.append(current_vertex)
            next_vertex = current_edge.other_vert(current_vertex)
            next_edges = [
                edge
                for edge in next_vertex.link_edges
                if edge in edge_set and edge is not current_edge
            ]
            if len(next_edges) != 1:
                raise RuntimeError("Could not order the neck boundary loop")
            current_vertex = next_vertex
            current_edge = next_edges[0]
            if current_edge is start:
                break
            if len(ordered_edges) > len(edges):
                raise RuntimeError("Neck boundary loop traversal did not close")
        if len(ordered_edges) != len(edges):
            raise RuntimeError("Neck boundary contains more than one loop")
        return ordered_edges, ordered_vertices

    def boundary_components():
        remaining = {edge for edge in bm.edges if edge.is_boundary}
        components = []
        while remaining:
            seed = remaining.pop()
            component_edges = [seed]
            component_vertices = set(seed.verts)
            stack = list(seed.verts)
            while stack:
                vertex = stack.pop()
                for edge in vertex.link_edges:
                    if edge not in remaining or not edge.is_boundary:
                        continue
                    remaining.remove(edge)
                    component_edges.append(edge)
                    for linked in edge.verts:
                        if linked not in component_vertices:
                            component_vertices.add(linked)
                            stack.append(linked)
            components.append((component_edges, component_vertices))
        return components

    body_center_before = sum((vertex.co for vertex in body_vertices), Vector()) / len(
        body_vertices
    )
    head_center_before = sum((vertex.co for vertex in head_vertices), Vector()) / len(
        head_vertices
    )
    original_body_edges = len(body_edges)
    original_head_edges = len(head_edges)
    subdivided_loop = None
    added_boundary_vertices = 0
    if len(body_edges) != len(head_edges):
        sparse_name = "body" if len(body_edges) < len(head_edges) else "head"
        sparse_edges = body_edges if sparse_name == "body" else head_edges
        target_count = max(len(body_edges), len(head_edges))
        ordered, _ordered_vertices = ordered_loop(sparse_edges)
        added_boundary_vertices = target_count - len(ordered)
        base_cuts, extra_cuts = divmod(added_boundary_vertices, len(ordered))
        edges_by_cut_count: dict[int, list[object]] = {}
        for index, edge in enumerate(ordered):
            distributed_extra = (
                (index + 1) * extra_cuts // len(ordered)
                > index * extra_cuts // len(ordered)
            )
            cuts = base_cuts + int(distributed_extra)
            if cuts:
                edges_by_cut_count.setdefault(cuts, []).append(edge)
        for cuts in sorted(edges_by_cut_count, reverse=True):
            bmesh.ops.subdivide_edges(
                bm,
                edges=edges_by_cut_count[cuts],
                cuts=cuts,
                use_grid_fill=False,
                smooth=0.0,
            )
        components = boundary_components()
        if len(components) != 2:
            bm.free()
            raise RuntimeError(
                f"Expected two neck boundaries after subdivision, found {len(components)}"
            )

        def component_center(component):
            _edges, vertices = component
            return sum((vertex.co for vertex in vertices), Vector()) / len(vertices)

        first, second = components
        first_center = component_center(first)
        second_center = component_center(second)
        if (first_center - body_center_before).length <= (
            second_center - body_center_before
        ).length:
            body_edges, body_vertices = first
            head_edges, head_vertices = second
        else:
            body_edges, body_vertices = second
            head_edges, head_vertices = first
        if len(body_edges) != len(head_edges):
            bm.free()
            raise RuntimeError(
                "Neck loop subdivision did not equalize edge counts: "
                f"body={len(body_edges)}, head={len(head_edges)}"
            )
        subdivided_loop = sparse_name

    uv_layer = bm.loops.layers.uv.active
    head_uv: dict[object, Vector] = {}
    if uv_layer is not None:
        for vertex in head_vertices:
            samples = [loop[uv_layer].uv.copy() for loop in vertex.link_loops]
            if samples:
                head_uv[vertex] = sum(samples, Vector((0.0, 0.0))) / len(samples)

    head_center = sum((vertex.co for vertex in head_vertices), Vector()) / len(head_vertices)
    head_by_angle = sorted(
        head_vertices,
        key=lambda vertex: math.atan2(
            float(vertex.co.y - head_center.y), float(vertex.co.x - head_center.x)
        ),
    )

    _ordered_body_edges, ordered_body_vertices = ordered_loop(body_edges)
    _ordered_head_edges, ordered_head_vertices = ordered_loop(head_edges)
    if len(ordered_body_vertices) != len(ordered_head_vertices):
        bm.free()
        raise RuntimeError("Neck loops must have equal vertex counts before bridging")

    # Preserve the source neck curvature. The seam is placed inside the collar,
    # where a short smooth connector follows the original boundary heights.
    body_boundary_z = sum(vertex.co.z for vertex in ordered_body_vertices) / len(
        ordered_body_vertices
    )
    head_boundary_z = sum(vertex.co.z for vertex in ordered_head_vertices) / len(
        ordered_head_vertices
    )

    def squared_distance(first, second) -> float:
        delta = first.co - second.co
        return float(delta.length_squared)

    best_score = math.inf
    best_head_vertices = None
    best_direction = 1
    best_offset = 0
    for direction, candidate in (
        (1, ordered_head_vertices),
        (-1, list(reversed(ordered_head_vertices))),
    ):
        for offset in range(len(candidate)):
            score = sum(
                squared_distance(
                    ordered_body_vertices[index],
                    candidate[(index + offset) % len(candidate)],
                )
                for index in range(len(ordered_body_vertices))
            )
            if score < best_score:
                best_score = score
                best_head_vertices = [
                    candidate[(index + offset) % len(candidate)]
                    for index in range(len(candidate))
                ]
                best_direction = direction
                best_offset = offset
    if best_head_vertices is None:
        bm.free()
        raise RuntimeError("Could not align the two neck boundary loops")

    bridge_segments = len(bridge_material_indices)
    bridge_rings = [ordered_body_vertices]
    for segment in range(1, bridge_segments):
        amount = segment / bridge_segments
        bridge_rings.append(
            [
                bm.verts.new(body.co.lerp(head.co, amount))
                for body, head in zip(ordered_body_vertices, best_head_vertices)
            ]
        )
    bridge_rings.append(best_head_vertices)

    new_faces = []
    for segment in range(bridge_segments):
        lower = bridge_rings[segment]
        upper = bridge_rings[segment + 1]
        for index, lower_vertex in enumerate(lower):
            next_index = (index + 1) % len(lower)
            try:
                face = bm.faces.new(
                    (
                        lower_vertex,
                        lower[next_index],
                        upper[next_index],
                        upper[index],
                    )
                )
            except ValueError as exc:
                bm.free()
                raise RuntimeError(
                    "Could not create deterministic neck bridge face "
                    f"segment={segment}, index={index}"
                ) from exc
            face.material_index = bridge_material_indices[segment]
            face.smooth = True
            new_faces.append(face)
    if not new_faces:
        bm.free()
        raise RuntimeError("Blender created no faces while bridging the neck boundary loops")

    bridge_face_set = set(new_faces)
    bridge_non_manifold = []
    for edge in bm.edges:
        if edge.is_manifold:
            continue
        center = (edge.verts[0].co + edge.verts[1].co) * 0.5
        bridge_non_manifold.append(
            {
                "faceCount": len(edge.link_faces),
                "bridgeFaceCount": sum(
                    1 for face in edge.link_faces if face in bridge_face_set
                ),
                "center": [float(center[axis]) for axis in range(3)],
            }
        )
    if bridge_non_manifold:
        bm.free()
        raise RuntimeError(
            "Neck bridge introduced non-manifold edges: "
            f"{json.dumps(bridge_non_manifold[:20])}"
        )

    reference_head_uv = Vector((0.5, 0.5))
    if head_uv:
        reference_vertex = min(
            head_vertices,
            key=lambda vertex: (
                float(vertex.co.y),
                abs(float(vertex.co.x - head_center.x)),
            ),
        )
        reference_head_uv = head_uv.get(reference_vertex, reference_head_uv)
    for face in new_faces:
        if uv_layer is not None:
            for loop in face.loops:
                loop[uv_layer].uv = reference_head_uv
    for vertex in set(ordered_body_vertices) | set(best_head_vertices):
        for face in vertex.link_faces:
            face.smooth = True

    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {
        "originalBodyBoundaryEdges": original_body_edges,
        "originalHeadBoundaryEdges": original_head_edges,
        "bodyBoundaryEdges": len(body_edges),
        "headBoundaryEdges": len(head_edges),
        "subdividedLoop": subdivided_loop,
        "addedBoundaryVertices": added_boundary_vertices,
        "alignmentDirection": best_direction,
        "alignmentOffset": best_offset,
        "alignmentSquaredDistance": best_score,
        "bridgeFaces": len(new_faces),
        "bridgeSegments": bridge_segments,
        "materialIndices": list(bridge_material_indices),
        "bodyBoundaryZ": float(body_boundary_z),
        "headBoundaryZ": float(head_boundary_z),
        "referenceHeadUv": [float(value) for value in reference_head_uv],
    }


def _sample_group_base_color(obj, group_name: str) -> tuple[float, float, float, float]:
    import bmesh

    group = obj.vertex_groups.get(group_name)
    if group is None:
        raise RuntimeError(f"Missing vertex group for material sampling: {group_name}")
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    deform = bm.verts.layers.deform.active
    uv_layer = bm.loops.layers.uv.active
    members = {
        vertex
        for vertex in bm.verts
        if deform is not None and vertex[deform].get(group.index, 0.0) > 0.5
    }
    if not members:
        bm.free()
        raise RuntimeError(f"Vertex group is empty: {group_name}")

    faces = {face for vertex in members for face in vertex.link_faces}
    samples = []
    for face in faces:
        material = obj.data.materials[face.material_index]
        if material is None or not material.use_nodes:
            continue
        principled = next(
            (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
            None,
        )
        if principled is None:
            continue
        base = principled.inputs.get("Base Color")
        if base is None:
            continue
        factor = tuple(float(value) for value in base.default_value)
        image = None
        if base.is_linked and base.links[0].from_node.type == "TEX_IMAGE":
            image = base.links[0].from_node.image
        for loop in face.loops:
            if loop.vert not in members:
                continue
            if image is None or uv_layer is None:
                samples.append(factor)
                continue
            u, v = loop[uv_layer].uv
            x = min(image.size[0] - 1, max(0, int((float(u) % 1.0) * image.size[0])))
            y = min(image.size[1] - 1, max(0, int((float(v) % 1.0) * image.size[1])))
            offset = (y * image.size[0] + x) * 4
            samples.append(
                tuple(image.pixels[offset + axis] * factor[axis] for axis in range(4))
            )
    bm.free()
    if not samples:
        raise RuntimeError(f"Could not sample material color for {group_name}")
    ordered = [sorted(sample[axis] for sample in samples) for axis in range(4)]
    middle = len(samples) // 2
    return tuple(values[middle] for values in ordered)


def _create_neck_bridge_materials(obj, body_color, head_color, segments: int) -> list[int]:
    import bpy

    indices = []
    for segment in range(segments):
        amount = (segment + 0.5) / segments
        color = tuple(
            body_color[axis] * (1.0 - amount) + head_color[axis] * amount
            for axis in range(4)
        )
        material = bpy.data.materials.new(f"NeckBridgeSkin{segment + 1:02d}")
        material.use_nodes = True
        principled = next(
            node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"
        )
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Roughness"].default_value = 0.5
        obj.data.materials.append(material)
        indices.append(len(obj.data.materials) - 1)
    return indices


def _copy_sources(objects):
    import bpy
    copies = []
    for obj in objects:
        duplicate = obj.copy()
        duplicate.data = obj.data.copy()
        duplicate.name = f"{obj.name}BakeSource"
        bpy.context.collection.objects.link(duplicate)
        for slot in duplicate.material_slots:
            if slot.material is not None:
                slot.material = slot.material.copy()
        copies.append(duplicate)
    return copies


def _atlas_uv(obj) -> dict[str, int]:
    import numpy as np
    import xatlas

    mesh = obj.data
    mesh.calc_loop_triangles()
    if any(len(face.vertices) != 3 for face in mesh.polygons):
        raise RuntimeError("Head replacement fusion must be triangulated before UV atlas generation")

    vertices = np.asarray([vertex.co[:] for vertex in mesh.vertices], dtype=np.float32)
    faces = np.asarray([face.vertices[:] for face in mesh.polygons], dtype=np.uint32)
    vmapping, atlas_faces, atlas_uvs = xatlas.parametrize(vertices, faces)

    original_faces: dict[tuple[int, int, int], list[int]] = {}
    for face in mesh.polygons:
        key = tuple(sorted(int(index) for index in face.vertices))
        original_faces.setdefault(key, []).append(face.index)

    uv_by_face_vertex: dict[tuple[int, int], tuple[float, float]] = {}
    for atlas_face in atlas_faces:
        original = [int(vmapping[int(index)]) for index in atlas_face]
        key = tuple(sorted(original))
        candidates = original_faces.get(key)
        if not candidates:
            raise RuntimeError("xatlas returned a triangle that does not map to the fusion mesh")
        face_index = candidates.pop()
        for atlas_index, vertex_index in zip(atlas_face, original):
            u, v = atlas_uvs[int(atlas_index)]
            uv_by_face_vertex[(face_index, vertex_index)] = (float(u), float(v))

    uv_layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    for face in mesh.polygons:
        for loop_index in face.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            try:
                uv_layer.data[loop_index].uv = uv_by_face_vertex[(face.index, vertex_index)]
            except KeyError as exc:
                raise RuntimeError("xatlas did not produce UVs for every fusion mesh loop") from exc
    mesh.update()
    return {
        "chartsVertices": int(len(atlas_uvs)),
        "triangles": int(len(atlas_faces)),
        "vertexReuseRatio": float(len(atlas_uvs) / max(1, len(atlas_faces) * 3)),
    }


def _route_base_color_to_emission(objects) -> None:
    for source in objects:
        for slot in source.material_slots:
            material = slot.material
            if material is None or not material.use_nodes:
                continue
            for node in material.node_tree.nodes:
                if node.type != "BSDF_PRINCIPLED":
                    continue
                base = node.inputs.get("Base Color")
                emission = node.inputs.get("Emission Color") or node.inputs.get("Emission")
                strength = node.inputs.get("Emission Strength")
                if base is None or emission is None:
                    continue
                if base.is_linked:
                    material.node_tree.links.new(base.links[0].from_socket, emission)
                else:
                    emission.default_value = base.default_value
                if strength is not None:
                    strength.default_value = 1.0


def _new_image_node(material, name: str, image, color_space: str):
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = name
    node.image = image
    node.image.colorspace_settings.name = color_space
    material.node_tree.nodes.active = node
    return node


def _image_coverage(image, *, threshold: float) -> float:
    import numpy as np

    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgb = pixels.reshape((-1, 4))[:, :3]
    return float(np.mean(np.max(rgb, axis=1) > threshold))


def _bake_textures(sources, target, directory: Path, texture_size: int) -> tuple[list[Path], dict[str, float]]:
    import bpy
    import numpy as np

    directory.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.image_settings.file_format = "PNG"
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.cage_extrusion = 0.015
    scene.render.bake.max_ray_distance = 0.04

    material = bpy.data.materials.new("HeadReplacedPBR")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    target.data.materials.clear()
    target.data.materials.append(material)

    def select_for_bake() -> None:
        bpy.ops.object.select_all(action="DESELECT")
        for source in sources:
            source.hide_set(False)
            source.hide_render = False
            source.select_set(True)
        target.hide_set(False)
        target.hide_render = False
        target.select_set(True)
        bpy.context.view_layer.objects.active = target

    outputs = []
    coverage: dict[str, float] = {}
    base = bpy.data.images.new("HeadReplacedBaseColor", texture_size, texture_size, alpha=True)
    base.generated_color = (0.5, 0.5, 0.5, 1.0)
    base_node = _new_image_node(material, "BaseColorBake", base, "sRGB")
    _route_base_color_to_emission(sources)
    select_for_bake()
    bpy.ops.object.bake(type="EMIT")
    pixels = np.empty(len(base.pixels), dtype=np.float32)
    base.pixels.foreach_get(pixels)
    rows = pixels.reshape((-1, 4))
    if float(rows[:, :3].max()) <= 1e-4:
        raise RuntimeError("Head replacement Base Color bake is blank")
    rows[:, 3] = 1.0
    base.pixels.foreach_set(pixels)
    base.update()
    base_path = directory / "base-color.png"
    base.filepath_raw = str(base_path)
    base.save()
    outputs.append(base_path)
    coverage["baseColor"] = _image_coverage(base, threshold=0.03)
    if coverage["baseColor"] < MIN_TEXTURE_COVERAGE:
        raise RuntimeError(
            "Head replacement Base Color UV coverage is too low: "
            f"{coverage['baseColor']:.3f}; rebuild the UV atlas"
        )
    links.new(base_node.outputs["Color"], principled.inputs["Base Color"])

    normal = bpy.data.images.new("HeadReplacedNormal", texture_size, texture_size)
    normal_node = _new_image_node(material, "NormalBake", normal, "Non-Color")
    select_for_bake()
    bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT")
    normal_path = directory / "normal.png"
    normal.filepath_raw = str(normal_path)
    normal.save()
    outputs.append(normal_path)
    coverage["normal"] = _image_coverage(normal, threshold=0.03)
    normal_map = nodes.new("ShaderNodeNormalMap")
    links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])

    ao = bpy.data.images.new("HeadReplacedAO", texture_size, texture_size)
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
    coverage["ao"] = _image_coverage(ao, threshold=0.03)
    if coverage["ao"] < MIN_TEXTURE_COVERAGE:
        raise RuntimeError(
            "Head replacement AO UV coverage is too low: "
            f"{coverage['ao']:.3f}; rebuild the UV atlas"
        )
    return outputs, coverage


def _export_glb(target, destination: Path) -> None:
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.export_scene.gltf(
        filepath=str(destination), export_format="GLB", use_selection=True,
        export_apply=True, export_materials="EXPORT", export_image_format="AUTO",
        export_texcoords=True, export_normals=True, export_yup=True,
    )


def replace_head(
    body_path: Path,
    head_path: Path,
    output_dir: Path,
    *,
    body_cut_ratio: float,
    head_cut_ratio: float,
    head_fit_height: float,
    neck_overlap: float,
    head_offset,
    head_rotation_degrees,
    voxel_size: float,
    texture_size: int,
) -> dict[str, object]:
    import bpy
    from mathutils import Euler, Matrix, Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(body_path))
    body_objects = _mesh_objects()
    if not body_objects or any(obj.find_armature() is not None for obj in body_objects):
        raise RuntimeError("Body must be a visible unskinned GLB")
    body = _join_meshes(body_objects, "BodyForHeadReplacement")
    _apply_world_transforms([body])
    body_welded = _weld_coincident_vertices(body)
    body_bounds = _bounds([body])
    body_height = body_bounds[1][2] - body_bounds[0][2]
    body_cut_z = resolve_cut_height(body_bounds[0][2], body_bounds[1][2], body_cut_ratio)
    body_removed = _delete_vertices_by_world_z(body, body_cut_z, delete_above=True)
    body_loose_cut_geometry = _remove_loose_cut_geometry(body)

    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(head_path))
    head_objects = [obj for obj in _mesh_objects() if obj not in before]
    if not head_objects or any(obj.find_armature() is not None for obj in head_objects):
        raise RuntimeError("Replacement head must be a visible unskinned GLB")
    head = _join_meshes(head_objects, "ReplacementHead")
    _apply_world_transforms([head])
    head_welded = _weld_coincident_vertices(head)
    head_bounds = _bounds([head])
    source_head_height = head_bounds[1][2] - head_bounds[0][2]
    head_cut_z = resolve_cut_height(head_bounds[0][2], head_bounds[1][2], head_cut_ratio)
    head_removed = _delete_vertices_by_world_z(head, head_cut_z, delete_above=False)
    head_loose_cut_geometry = _remove_loose_cut_geometry(head)
    trimmed_bounds = _bounds([head])
    trimmed_height = trimmed_bounds[1][2] - trimmed_bounds[0][2]
    if trimmed_height <= 1e-6:
        raise RuntimeError("Replacement head is empty after trimming")

    requested_height = head_fit_height if head_fit_height > 0 else body_height * 0.255
    scale = requested_height / trimmed_height
    source_center = Vector((
        (trimmed_bounds[0][0] + trimmed_bounds[1][0]) * 0.5,
        (trimmed_bounds[0][1] + trimmed_bounds[1][1]) * 0.5,
        trimmed_bounds[0][2],
    ))
    body_center = Vector((
        (body_bounds[0][0] + body_bounds[1][0]) * 0.5,
        (body_bounds[0][1] + body_bounds[1][1]) * 0.5,
        body_cut_z - neck_overlap,
    ))
    rotation = Euler(tuple(math.radians(value) for value in head_rotation_degrees), "XYZ")
    transform = (
        Matrix.Translation(body_center + Vector(head_offset))
        @ rotation.to_matrix().to_4x4()
        @ Matrix.Scale(scale, 4)
        @ Matrix.Translation(-source_center)
    )
    head.matrix_world = transform @ head.matrix_world
    _apply_world_transforms([head])

    body_boundary = _mark_primary_boundary_loop(body, body_cut_z, "BodyNeckBoundary")
    head_boundary = _mark_primary_boundary_loop(
        head, body_cut_z - neck_overlap, "HeadNeckBoundary"
    )
    fusion = _join_meshes([body, head], "HeadReplacedFusionSource")
    bridge_material_indices = _create_neck_bridge_materials(
        fusion,
        (0.60, 0.44, 0.39, 1.0),
        (0.60, 0.44, 0.39, 1.0),
        1,
    )
    bridge = _bridge_neck_boundaries(
        fusion, "BodyNeckBoundary", "HeadNeckBoundary", bridge_material_indices
    )
    fusion.name = "HeadReplacedCharacter"
    fusion_stats = _mesh_stats(fusion)
    if fusion_stats["boundaryEdges"] or fusion_stats["nonManifoldEdges"]:
        topology_details = _non_manifold_edge_details(fusion)
        raise RuntimeError(
            "Head replacement fusion is not watertight: "
            f"boundary={fusion_stats['boundaryEdges']}, "
            f"nonManifold={fusion_stats['nonManifoldEdges']}, "
            f"details={json.dumps(topology_details)}"
        )
    if fusion_stats["triangles"] < 10_000:
        raise RuntimeError("Head replacement mesh is unexpectedly sparse")

    output = output_dir / "head-replaced.glb"
    _export_glb(fusion, output)
    report = {
        "passed": True,
        "mode": "biological-head-replacement",
        "body": {
            "cutRatio": body_cut_ratio,
            "cutZ": body_cut_z,
            "removedVertices": body_removed,
            "weldedCoincidentVertices": body_welded,
            "removedLooseCutGeometry": body_loose_cut_geometry,
        },
        "head": {
            "cutRatio": head_cut_ratio, "sourceCutZ": head_cut_z,
            "removedVertices": head_removed, "requestedHeight": requested_height,
            "effectiveScale": scale, "offset": list(head_offset),
            "rotationDegrees": list(head_rotation_degrees),
            "weldedCoincidentVertices": head_welded,
            "removedLooseCutGeometry": head_loose_cut_geometry,
        },
        "fusion": {
            "mode": "boundary-loop-bridge-preserve-source",
            "neckOverlap": neck_overlap,
            "compatibilityVoxelSize": voxel_size,
            "bodyBoundary": body_boundary,
            "headBoundary": head_boundary,
            "bridge": bridge,
            **fusion_stats,
        },
        "artifacts": {
            "glb": output.name,
            "textures": "preserved from the source body and head GLBs",
        },
        "nextStage": "Run mixamo_character_pipeline.py prepare on head-replaced.glb before Mixamo.",
        "limitations": [
            "Facial identity remains a visual quality gate.",
            "Inspect the neck seam and silhouette before running the preparation stage.",
            "The compatibility voxel-size argument is retained but no voxel remesh is performed.",
        ],
    }
    (output_dir / "head-replacement-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


@app.function(image=runtime_image, volumes={MODEL_VOLUME: model_cache}, timeout=50 * 60, cpu=8, memory=32768)
def build_head_replacement_bundle(
    body_bytes: bytes, head_bytes: bytes, *, output_key: str,
    body_cut_ratio: float, head_cut_ratio: float, head_fit_height: float,
    neck_overlap: float, head_offset, head_rotation_degrees,
    voxel_size: float, texture_size: int,
) -> str:
    directory = Path(tempfile.mkdtemp(prefix="head-replacement-"))
    body = directory / "body.glb"
    head = directory / "head.glb"
    output_dir = directory / "output"
    output_dir.mkdir()
    body.write_bytes(body_bytes)
    head.write_bytes(head_bytes)
    replace_head(
        body, head, output_dir, body_cut_ratio=body_cut_ratio,
        head_cut_ratio=head_cut_ratio, head_fit_height=head_fit_height,
        neck_overlap=neck_overlap, head_offset=head_offset,
        head_rotation_degrees=head_rotation_degrees, voxel_size=voxel_size,
        texture_size=texture_size,
    )
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir).as_posix())
    destination = Path(MODEL_VOLUME) / output_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(archive_bytes.getvalue())
    model_cache.commit()
    return output_key


def download_volume_file(remote_path: str, destination: Path) -> None:
    subprocess.run([sys.executable, "-m", "modal", "volume", "get", "--force", MODEL_VOLUME_NAME, remote_path, str(destination)], check=True)
    subprocess.run([sys.executable, "-m", "modal", "volume", "rm", MODEL_VOLUME_NAME, remote_path], check=True)


@app.local_entrypoint()
def main(
    body: str, head: str, output_dir: str, body_cut_ratio: float = 0.865,
    head_cut_ratio: float = 0.12, head_fit_height: float = 0.0,
    neck_overlap: float = 0.018, head_offset: str = "0,0,0",
    head_rotation: str = "0,0,0", voxel_size: float = 0.0045,
    texture_size: int = 2048, force: bool = False,
) -> None:
    body_path = Path(body).expanduser().resolve()
    head_path = Path(head).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    for path in (body_path, head_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not 0.78 <= body_cut_ratio <= 0.99:
        raise ValueError("body_cut_ratio must be between 0.78 and 0.99")
    if not 0.0 < head_cut_ratio < 0.45:
        raise ValueError("head_cut_ratio must be between 0 and 0.45")
    if head_fit_height < 0 or neck_overlap < 0:
        raise ValueError("head_fit_height and neck_overlap cannot be negative")
    if not 0.0015 <= voxel_size <= 0.012:
        raise ValueError("voxel_size must be between 0.0015 and 0.012 meters")
    if texture_size not in {1024, 2048, 4096}:
        raise ValueError("texture_size must be 1024, 2048, or 4096")
    if destination.exists() and any(destination.iterdir()) and not force:
        raise FileExistsError(f"Refusing to replace non-empty output directory: {destination}")

    temporary = Path(tempfile.mkdtemp(prefix="head-replacement-download-"))
    bundle = temporary / "head-replacement.zip"
    output_key = f"character-head-replacement/{destination.name}.zip"
    remote_path = build_head_replacement_bundle.remote(
        body_path.read_bytes(), head_path.read_bytes(), output_key=output_key,
        body_cut_ratio=body_cut_ratio, head_cut_ratio=head_cut_ratio,
        head_fit_height=head_fit_height, neck_overlap=neck_overlap,
        head_offset=parse_vector(head_offset, label="head_offset"),
        head_rotation_degrees=parse_vector(head_rotation, label="head_rotation"),
        voxel_size=voxel_size, texture_size=texture_size,
    )
    download_volume_file(remote_path, bundle)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(destination)
    print(f"Replaced biological head before Mixamo: {destination / 'head-replaced.glb'}")


if __name__ == "__main__":
    main()
