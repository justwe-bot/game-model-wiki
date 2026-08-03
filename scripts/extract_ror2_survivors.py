"""Extract Risk of Rain 2 survivor meshes and build static Wiki GLBs.

The script reads the local Steam Addressables bundles with UnityPy, exports the
selected base-skin meshes and textures to a temporary directory, then invokes
Blender to produce original and mobile GLBs. It never writes to the game folder.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import numpy as np
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler
from PIL import Image


SURVIVORS = [
    {
        "slug": "commando",
        "name": "突击兵",
        "nameEn": "Commando",
        "bundle": "base-commando",
        "root": "mdlCommandoDualies",
        "meshes": ["CommandoMesh"],
        "texture": "texCommandoPaletteDiffuse",
        "summary": "基础远程英雄。展示从默认角色 prefab 烘焙的完整静态模型与双枪。",
    },
    {
        "slug": "huntress",
        "name": "女猎人",
        "nameEn": "Huntress",
        "bundle": "base-huntress",
        "root": "mdlHuntress",
        "meshes": ["HuntressMesh"],
        "transparentRenderers": ["HuntressScarfMesh"],
        "texture": "texHuntressDiffuse",
        "summary": "高机动追踪射手。展示从默认角色 prefab 烘焙的完整静态模型与弓。",
    },
    {
        "slug": "bandit",
        "name": "盗贼",
        "nameEn": "Bandit",
        "bundle": "base-bandit2",
        "root": "mdlBandit2@MasterAnims",
        "poseClip": r"RoR2\Base\Characters\Bandit2\Animations\Bandit_SelectPoseIdle.anim",
        "meshes": ["Bandit2BodyMesh"],
        "excludeRenderers": ["BanditShotgunMesh", "BanditPistolMesh"],
        "texture": "texBandit2Diffuse",
        "summary": "擅长背刺与连招的枪手。展示完整静态人物模型、披风与帽子，枪械已隐藏。",
    },
    {
        "slug": "mul-t",
        "name": "多功能枪兵",
        "nameEn": "MUL-T",
        "bundle": "base-toolbot",
        "root": "mdlToolbot",
        "meshes": ["ToolbotMesh"],
        "poseRoot": "mdlToolbot@MasterAnims",
        "texture": "texTrimSheetConstruction2",
        "summary": "可切换装备的重型机器人。展示默认角色 prefab 的完整静态模型。",
    },
    {
        "slug": "engineer",
        "name": "工程师",
        "nameEn": "Engineer",
        "bundle": "base-engi",
        "root": "mdlEngi",
        "meshes": ["EngiMesh"],
        "texture": "texEngiDiffuse",
        "summary": "以炮塔和区域控制作战的英雄。当前展示工程师本体，不重复附带独立炮塔单位。",
    },
    {
        "slug": "artificer",
        "name": "工匠",
        "nameEn": "Artificer",
        "bundle": "base-mage",
        "root": "mdlMage",
        "meshes": ["MageMesh"],
        "texture": "texMageDiffuse",
        "summary": "使用元素技能的爆发型英雄。展示默认角色 prefab 的完整静态模型。",
    },
    {
        "slug": "mercenary",
        "name": "佣兵",
        "nameEn": "Mercenary",
        "bundle": "base-merc",
        "root": "mdlMerc",
        "meshes": ["MercMesh"],
        "texture": "texMercDiffuse",
        "summary": "依靠近战连击和位移作战的剑士。展示完整静态模型与武器。",
    },
    {
        "slug": "rex",
        "name": "雷克斯",
        "nameEn": "REX",
        "bundle": "base-treebot",
        "root": "mdlTreebot",
        "meshes": ["TreebotBotMesh"],
        "texture": "texTreebotTreeBarkDiffuse",
        "summary": "机械与植物共生的远程英雄。展示默认形态的机械与植物分件模型。",
    },
    {
        "slug": "loader",
        "name": "装卸工",
        "nameEn": "Loader",
        "bundle": "base-loader",
        "root": "mdlLoader",
        "meshes": ["LoaderMechMesh"],
        "texture": "texLoaderPilotDiffuse",
        "summary": "使用抓钩和动力拳套的近战英雄。展示默认角色 prefab 的完整静态模型。",
    },
    {
        "slug": "acrid",
        "name": "阿克里德",
        "nameEn": "Acrid",
        "bundle": "base-croco",
        "root": "mdlCroco",
        "meshes": ["CrocoMesh"],
        "texture": "texCrocoDiffuse",
        "summary": "以毒素和近战撕咬作战的实验生物。展示默认角色 prefab 的完整静态模型。",
    },
    {
        "slug": "captain",
        "name": "船长",
        "nameEn": "Captain",
        "bundle": "base-captain",
        "root": "mdlCaptain",
        "meshes": ["Captain"],
        "texture": "texCaptainPalette",
        "summary": "通过轨道支援与信标控制战场的指挥官。展示完整静态模型与随身装备。",
    },
    {
        "slug": "railgunner",
        "name": "磁轨炮手",
        "nameEn": "Railgunner",
        "bundle": "dlc1-railgunner",
        "root": "mdlRailGunner",
        "meshes": ["mdlRailGunnerBase"],
        "texture": "texRailGunnerDiffuse",
        "summary": "《虚空幸存者》扩展中的精准射手。展示完整静态模型与磁轨步枪。",
    },
    {
        "slug": "void-fiend",
        "name": "虚空恶鬼",
        "nameEn": "Void Fiend",
        "bundle": "dlc1-voidsurvivor",
        "root": "mdlVoidSurvivor",
        "meshes": ["mdlVoidSurvivorBody"],
        "texture": "texVoidSurvivorFleshDiffuse",
        "summary": "会在受控与腐化形态间切换的虚空英雄。展示默认受控形态的完整静态模型。",
    },
    {
        "slug": "seeker",
        "name": "探寻者",
        "nameEn": "Seeker",
        "bundle": "dlc2-seeker",
        "root": "mdlSeeker",
        "meshes": ["meshSeekerBody"],
        "texture": "texSeekerDiffuse",
        "summary": "《风暴探寻者》扩展中的灵魂操控者。展示默认角色 prefab 的完整静态模型。",
    },
    {
        "slug": "chef",
        "name": "大厨",
        "nameEn": "CHEF",
        "bundle": "dlc2-chef",
        "root": "mdlChef",
        "meshes": ["meshChef"],
        "texture": "texChefDiffuse",
        "summary": "以厨具发动连锁攻击的机器人英雄。展示完整静态模型与厨具分件。",
    },
    {
        "slug": "false-son",
        "name": "伪子",
        "nameEn": "False Son",
        "bundle": "dlc2-falseson",
        "root": "mdlFalseSon",
        "meshes": ["FalseSon_Body"],
        "texture": "texFalseSonDiffuse",
        "summary": "《风暴探寻者》扩展中的可解锁近战英雄。展示完整石质静态模型与武器。",
    },
    {
        "slug": "drifter",
        "name": "漂泊者",
        "nameEn": "Drifter",
        "bundle": "dlc3-drifter",
        "root": "mdlDrifter",
        "meshes": ["meshDrifter"],
        "texture": "texDrifterDiffuse",
        "summary": "使用废料袋与临时物品作战的英雄。展示当前本机 DLC3 prefab 的完整静态模型。",
    },
]


def find_bundles(bundle_root: Path, token: str) -> list[Path]:
    pattern = str(bundle_root / f"ror2-{token}*_assets_all_*.bundle")
    return [Path(path) for path in glob.glob(pattern) if "ror2-junk-" not in Path(path).name]


def load_objects(paths: list[Path], type_name: str) -> dict[str, list[object]]:
    found: dict[str, list[object]] = {}
    for path in paths:
        environment = UnityPy.load(str(path))
        for obj in environment.objects:
            if obj.type.name != type_name:
                continue
            data = obj.read()
            found.setdefault(data.m_Name, []).append(data)
    return found


def component(game_object: object, type_name: str) -> object | None:
    for pair in game_object.m_Component:
        pointer = pair.component
        if pointer.path_id and pointer.type.name == type_name:
            return pointer.read()
    return None


def local_matrix(transform: object) -> np.ndarray:
    position = transform.m_LocalPosition
    rotation = transform.m_LocalRotation
    scale = transform.m_LocalScale
    x, y, z, w = rotation.x, rotation.y, rotation.z, rotation.w
    matrix = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), position.x],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), position.y],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), position.z],
            [0, 0, 0, 1],
        ],
        dtype=float,
    )
    return matrix @ np.diag([scale.x, scale.y, scale.z, 1])


def euler_matrix(value: tuple[float, float, float]) -> np.ndarray:
    x, y, z = np.radians(value)
    cx, sx = np.cos(x), np.sin(x)
    cy, sy = np.cos(y), np.sin(y)
    cz, sz = np.cos(z), np.sin(z)
    rotate_x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=float)
    rotate_y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=float)
    rotate_z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=float)
    matrix = np.eye(4, dtype=float)
    matrix[:3, :3] = rotate_z @ rotate_y @ rotate_x
    return matrix


def matrix_from_components(
    transform: object,
    position: tuple[float, float, float] | None,
    euler: tuple[float, float, float] | None,
    scale: tuple[float, float, float] | None,
) -> np.ndarray:
    if position is None:
        source = transform.m_LocalPosition
        position = (source.x, source.y, source.z)
    if scale is None:
        source = transform.m_LocalScale
        scale = (source.x, source.y, source.z)
    if euler is None:
        matrix = local_matrix(transform)
        matrix[:3, 3] = position
        default_scale = transform.m_LocalScale
        for axis, current in enumerate((default_scale.x, default_scale.y, default_scale.z)):
            if current:
                matrix[:3, axis] *= scale[axis] / current
        return matrix
    matrix = euler_matrix(euler)
    matrix[:3, 3] = position
    return matrix @ np.diag([*scale, 1])


def transform_key(transform: object) -> tuple[str, int]:
    reader = transform.object_reader
    return reader.assets_file.name, reader.path_id


def world_matrix(transform: object, cache: dict[tuple[str, int], np.ndarray]) -> np.ndarray:
    key = transform_key(transform)
    if key in cache:
        return cache[key]
    matrix = local_matrix(transform)
    if transform.m_Father.path_id:
        matrix = world_matrix(transform.m_Father.read(), cache) @ matrix
    cache[key] = matrix
    return matrix


def bind_matrix(value: object) -> np.ndarray:
    return np.array(
        [
            [value.e00, value.e01, value.e02, value.e03],
            [value.e10, value.e11, value.e12, value.e13],
            [value.e20, value.e21, value.e22, value.e23],
            [value.e30, value.e31, value.e32, value.e33],
        ],
        dtype=float,
    )


def root_transform(transform: object) -> object:
    while transform.m_Father.path_id:
        transform = transform.m_Father.read()
    return transform


def iter_transforms(root: object):
    stack = [(root, True)]
    while stack:
        transform, parent_active = stack.pop()
        game_object = transform.m_GameObject.read()
        active = parent_active and game_object.m_IsActive
        yield transform, game_object, active
        for child in reversed(transform.m_Children):
            try:
                stack.append((child.read(), active))
            except (FileNotFoundError, ValueError):
                continue


def renderer_mesh(game_object: object) -> tuple[str, object, object] | None:
    skinned = component(game_object, "SkinnedMeshRenderer")
    if skinned is not None and getattr(skinned, "m_Enabled", True) and skinned.m_Mesh.path_id:
        return "skinned", skinned.m_Mesh.read(), skinned
    mesh_filter = component(game_object, "MeshFilter")
    mesh_renderer = component(game_object, "MeshRenderer")
    if (
        mesh_filter is not None
        and mesh_renderer is not None
        and getattr(mesh_renderer, "m_Enabled", True)
        and mesh_filter.m_Mesh.path_id
    ):
        return "static", mesh_filter.m_Mesh.read(), mesh_renderer
    return None


def find_model_root(environment: object, mesh_names: list[str], preferred_root: str) -> object:
    candidates: dict[tuple[str, int], object] = {}
    for obj in environment.objects:
        if obj.type.name != "GameObject":
            continue
        game_object = obj.read()
        try:
            rendered = renderer_mesh(game_object)
        except (FileNotFoundError, ValueError):
            continue
        if rendered is None or rendered[1].m_Name not in mesh_names:
            continue
        transform = component(game_object, "Transform")
        if transform is None:
            continue
        root = root_transform(transform)
        candidates[transform_key(root)] = root
    if not candidates:
        raise RuntimeError(f"No prefab root referenced configured meshes: {', '.join(mesh_names)}")

    preferred = [
        root for root in candidates.values() if root.m_GameObject.read().m_Name == preferred_root
    ]
    if preferred:
        return preferred[0]

    def score(root: object) -> tuple[int, int]:
        matched = 0
        renderable = 0
        for _, game_object, active in iter_transforms(root):
            if not active:
                continue
            try:
                rendered = renderer_mesh(game_object)
            except (FileNotFoundError, ValueError):
                continue
            if rendered is not None:
                renderable += 1
                matched += rendered[1].m_Name in mesh_names
        return matched, renderable

    return max(candidates.values(), key=score)


def find_named_root(environment: object, root_name: str) -> object:
    for obj in environment.objects:
        if obj.type.name != "GameObject":
            continue
        game_object = obj.read()
        if game_object.m_Name != root_name:
            continue
        transform = component(game_object, "Transform")
        if transform is not None and not transform.m_Father.path_id:
            return transform
    raise RuntimeError(f"Prefab root {root_name} was not found")


def parse_pose_clip(path: Path) -> dict[str, dict[str, tuple[float, float, float]]]:
    text = path.read_text(encoding="utf-8")
    section_names = {
        "m_EulerCurves": "euler",
        "m_PositionCurves": "position",
        "m_ScaleCurves": "scale",
    }
    pose: dict[str, dict[str, tuple[float, float, float]]] = {}
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
    value_pattern = re.compile(
        rf"time:\s*0(?:\.0+)?\s*\r?\n\s*value:\s*\{{x:\s*({number}),\s*y:\s*({number}),\s*z:\s*({number})\}}"
    )
    for section_name, value_name in section_names.items():
        marker = f"  {section_name}:"
        start = text.find(marker)
        if start < 0:
            continue
        next_section = re.search(r"\n  m_[A-Za-z0-9_]+:", text[start + len(marker) :])
        end = start + len(marker) + next_section.start() if next_section else len(text)
        section = text[start:end]
        for block in re.split(r"\n  - curve:\s*\n", section)[1:]:
            path_match = re.search(r"\n    path:\s*(.+?)\s*\r?$", block, re.MULTILINE)
            value_match = value_pattern.search(block)
            if not path_match or not value_match:
                continue
            transform_path = path_match.group(1)
            pose.setdefault(transform_path, {})[value_name] = tuple(
                float(value_match.group(index)) for index in range(1, 4)
            )
    if not pose:
        raise RuntimeError(f"No transform curves were parsed from {path}")
    return pose


def animation_pose_matrices(root: object, clip_path: Path) -> dict[str, np.ndarray]:
    pose = parse_pose_clip(clip_path)
    matrices: dict[str, np.ndarray] = {}
    stack = [(root, "", np.eye(4, dtype=float))]
    while stack:
        transform, transform_path, parent_world = stack.pop()
        values = pose.get(transform_path, {})
        local = matrix_from_components(
            transform,
            values.get("position"),
            values.get("euler"),
            values.get("scale"),
        )
        world = parent_world @ local
        game_object = transform.m_GameObject.read()
        matrices[game_object.m_Name] = world
        for child in reversed(transform.m_Children):
            child_transform = child.read()
            child_name = child_transform.m_GameObject.read().m_Name
            child_path = f"{transform_path}/{child_name}" if transform_path else child_name
            stack.append((child_transform, child_path, world))
    return matrices


def transform_normal(matrix: np.ndarray, normal: tuple[float, ...]) -> tuple[float, float, float]:
    normal_matrix = np.linalg.inv(matrix[:3, :3]).T
    value = normal_matrix @ np.asarray(normal[:3], dtype=float)
    length = np.linalg.norm(value)
    if length:
        value /= length
    return tuple(value)


def bake_mesh(
    mesh: object,
    renderer_type: str,
    renderer: object,
    transform: object,
    matrix_cache: dict[tuple[str, int], np.ndarray],
    pose_matrices: dict[str, np.ndarray] | None = None,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[tuple[float, float]], list[int]]:
    handler = MeshHandler(mesh)
    handler.process()
    vertices = handler.m_Vertices or []
    normals = handler.m_Normals or [(0.0, 1.0, 0.0)] * len(vertices)
    uvs = handler.m_UV0 or [(0.0, 0.0)] * len(vertices)

    if renderer_type == "static":
        game_object_name = transform.m_GameObject.read().m_Name
        matrix = (
            pose_matrices.get(game_object_name, world_matrix(transform, matrix_cache))
            if pose_matrices
            else world_matrix(transform, matrix_cache)
        )
        baked_vertices = [tuple((matrix @ np.array([*vertex, 1.0]))[:3]) for vertex in vertices]
        baked_normals = [transform_normal(matrix, normal) for normal in normals]
    else:
        bone_indices = handler.m_BoneIndices
        bone_weights = handler.m_BoneWeights
        if bone_indices and not bone_weights:
            bone_weights = [
                (1.0,) + (0.0,) * (len(indices) - 1) for indices in bone_indices
            ]
        if not bone_indices or not bone_weights:
            matrix = world_matrix(transform, matrix_cache)
            baked_vertices = [tuple((matrix @ np.array([*vertex, 1.0]))[:3]) for vertex in vertices]
            baked_normals = [transform_normal(matrix, normal) for normal in normals]
            return baked_vertices, baked_normals, list(uvs), list(handler.m_IndexBuffer or [])
        bone_matrices = []
        for pointer in renderer.m_Bones:
            bone = pointer.read()
            bone_name = bone.m_GameObject.read().m_Name
            bone_matrices.append(
                pose_matrices.get(bone_name, world_matrix(bone, matrix_cache))
                if pose_matrices
                else world_matrix(bone, matrix_cache)
            )
        skin_matrices = [bone @ bind_matrix(bind) for bone, bind in zip(bone_matrices, mesh.m_BindPose)]
        baked_vertices = []
        baked_normals = []
        for vertex, normal, indices, weights in zip(
            vertices, normals, bone_indices, bone_weights
        ):
            vertex_value = np.zeros(4, dtype=float)
            normal_value = np.zeros(3, dtype=float)
            for bone_index, weight in zip(indices, weights):
                if not weight:
                    continue
                matrix = skin_matrices[bone_index]
                vertex_value += weight * (matrix @ np.array([*vertex, 1.0]))
                normal_value += weight * (np.linalg.inv(matrix[:3, :3]).T @ np.asarray(normal[:3]))
            normal_length = np.linalg.norm(normal_value)
            if normal_length:
                normal_value /= normal_length
            baked_vertices.append(tuple(vertex_value[:3]))
            baked_normals.append(tuple(normal_value))

    return baked_vertices, baked_normals, list(uvs), list(handler.m_IndexBuffer or [])


def export_prefab_objs(
    environment: object,
    mesh_names: list[str],
    preferred_root: str,
    transparent_renderers: list[str],
    excluded_renderers: list[str],
    pose_root_name: str | None,
    pose_clip_path: Path | None,
    destination_root: Path,
) -> tuple[dict[str, int | str], list[Path]]:
    root = find_model_root(environment, mesh_names, preferred_root)
    root_name = root.m_GameObject.read().m_Name
    matrix_cache: dict[tuple[str, int], np.ndarray] = {}
    pose_matrices = None
    if pose_clip_path:
        pose_matrices = animation_pose_matrices(root, pose_clip_path)
    elif pose_root_name:
        pose_root = find_named_root(environment, pose_root_name)
        pose_cache: dict[tuple[str, int], np.ndarray] = {}
        pose_matrices = {
            game_object.m_Name: world_matrix(transform, pose_cache)
            for transform, game_object, _ in iter_transforms(pose_root)
        }
    renderer_count = 0
    triangle_count = 0
    buckets = {
        "opaque": {"lines": [f"o {root_name}"], "vertex_offset": 1, "renderers": 0},
        "transparent": {"lines": [f"o {root_name}_transparent"], "vertex_offset": 1, "renderers": 0},
    }
    for transform, game_object, active in iter_transforms(root):
        if not active:
            continue
        if game_object.m_Name in excluded_renderers:
            continue
        try:
            rendered = renderer_mesh(game_object)
        except (FileNotFoundError, ValueError):
            continue
        if rendered is None:
            continue
        renderer_type, mesh, renderer = rendered
        vertices, normals, uvs, indices = bake_mesh(
            mesh,
            renderer_type,
            renderer,
            transform,
            matrix_cache,
            pose_matrices=pose_matrices,
        )
        if not vertices or not indices:
            continue
        bucket_name = "transparent" if game_object.m_Name in transparent_renderers else "opaque"
        bucket = buckets[bucket_name]
        lines = bucket["lines"]
        vertex_offset = bucket["vertex_offset"]
        lines.append(f"g {game_object.m_Name.replace(' ', '_')}")
        lines.extend(f"v {x:.8f} {y:.8f} {z:.8f}" for x, y, z in vertices)
        lines.extend(f"vt {u:.8f} {v:.8f}" for u, v in uvs)
        lines.extend(f"vn {x:.8f} {y:.8f} {z:.8f}" for x, y, z in normals)
        for submesh in mesh.m_SubMeshes:
            index_size = 2 if mesh.m_IndexFormat == 0 else 4
            first_index = submesh.firstByte // index_size
            submesh_indices = indices[first_index : first_index + submesh.indexCount]
            for index in range(0, len(submesh_indices) - 2, 3):
                face = [submesh_indices[index + corner] + submesh.baseVertex + vertex_offset for corner in range(3)]
                lines.append("f " + " ".join(f"{value}/{value}/{value}" for value in face))
                triangle_count += 1
        vertex_offset += len(vertices)
        bucket["vertex_offset"] = vertex_offset
        bucket["renderers"] += 1
        renderer_count += 1
    if not renderer_count:
        raise RuntimeError(f"Prefab {root_name} contained no active renderers")
    mesh_paths = []
    for index, (bucket_name, bucket) in enumerate(buckets.items()):
        if not bucket["renderers"]:
            continue
        destination = destination_root / f"{index:02d}-{bucket_name}.obj"
        destination.write_text("\n".join(bucket["lines"]) + "\n", encoding="utf-8")
        mesh_paths.append(destination)
    return (
        {"prefab": root_name, "rendererCount": renderer_count, "bakedTriangles": triangle_count},
        mesh_paths,
    )


def save_mobile_texture(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        image = image.convert("RGBA")
        image.thumbnail((256, 256), Image.Resampling.LANCZOS)
        alpha = image.getchannel("A")
        rgb = image.convert("RGB").quantize(colors=96, method=Image.Quantize.MEDIANCUT).convert("RGB")
        rgb.putalpha(alpha.resize(rgb.size))
        rgb.save(destination, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-root", type=Path, default=Path(r"C:\Program Files (x86)\Steam\steamapps\common\Risk of Rain 2"))
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--blender", type=Path, default=Path(os.environ.get("BLENDER_PATH", Path(os.environ["TEMP"]) / "codex-hades2-model-pipeline" / "blender-4.2.9-windows-x64" / "blender.exe")))
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument(
        "--asset-ripper-project",
        type=Path,
        default=Path(os.environ["TEMP"])
        / "codex-ror2-model-pipeline"
        / "exported-project"
        / "ExportedProject"
        / "Assets",
    )
    args = parser.parse_args()

    bundle_root = args.game_root / "Risk of Rain 2_Data" / "StreamingAssets" / "aa" / "StandaloneWindows64"
    if not bundle_root.is_dir():
        raise FileNotFoundError(bundle_root)
    if not args.blender.is_file():
        raise FileNotFoundError(args.blender)

    selected = [item for item in SURVIVORS if not args.only or item["slug"] in args.only]
    model_root = args.repo_root / "models" / "survivors"
    texture_root = args.repo_root / "textures" / "survivors"
    model_root.mkdir(parents=True, exist_ok=True)
    texture_root.mkdir(parents=True, exist_ok=True)
    blender_script = args.repo_root / "scripts" / "build_ror2_survivor_glb.py"
    results = []

    with tempfile.TemporaryDirectory(prefix="codex-ror2-survivors-") as temp_value:
        temp_root = Path(temp_value)
        for item in selected:
            print(f"Extracting {item['nameEn']}...", flush=True)
            bundle_paths = find_bundles(bundle_root, item["bundle"])
            if not bundle_paths:
                raise FileNotFoundError(f"No bundle matched {item['bundle']}")
            environment = UnityPy.load(*[str(path) for path in bundle_paths])
            textures: dict[str, list[object]] = {}
            for obj in environment.objects:
                if obj.type.name != "Texture2D":
                    continue
                texture = obj.read()
                textures.setdefault(texture.m_Name, []).append(texture)
            asset_temp = temp_root / item["slug"]
            asset_temp.mkdir()

            pose_clip_path = None
            if item.get("poseClip"):
                pose_clip_path = args.asset_ripper_project / item["poseClip"]
                if not pose_clip_path.is_file():
                    raise FileNotFoundError(pose_clip_path)
            prefab_stats, mesh_paths = export_prefab_objs(
                environment,
                item["meshes"],
                item["root"],
                item.get("transparentRenderers", []),
                item.get("excludeRenderers", []),
                item.get("poseRoot"),
                pose_clip_path,
                asset_temp,
            )
            print(
                f"  prefab {prefab_stats['prefab']}: {prefab_stats['rendererCount']} renderers, "
                f"{prefab_stats['bakedTriangles']} triangles",
                flush=True,
            )

            texture_candidates = textures.get(item["texture"], [])
            if not texture_candidates:
                raise RuntimeError(f"Texture {item['texture']} was not found for {item['nameEn']}")
            original_texture = texture_root / f"{item['slug']}-original.png"
            low_texture = texture_root / f"{item['slug']}-low.png"
            texture_candidates[0].image.save(original_texture)
            save_mobile_texture(original_texture, low_texture)

            stats_path = asset_temp / "stats.json"
            command = [
                str(args.blender), "--background", "--factory-startup", "--python", str(blender_script), "--",
                "--meshes", *[str(path) for path in mesh_paths],
                "--texture", str(original_texture),
                "--original", str(model_root / f"{item['slug']}-original.glb"),
                "--low", str(model_root / f"{item['slug']}-low.glb"),
                "--stats", str(stats_path),
            ]
            subprocess.run(command, check=True)
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            results.append({**item, **prefab_stats, **stats})

    result_path = args.repo_root / "games" / "risk-of-rain-2" / "survivors.generated.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    if args.only and result_path.is_file():
        existing_results = json.loads(result_path.read_text(encoding="utf-8"))
        rebuilt = {item["slug"]: item for item in results}
        retained = {
            item["slug"]: item
            for item in existing_results
            if item["slug"] not in rebuilt
        }
        results = [
            rebuilt[item["slug"]]
            if item["slug"] in rebuilt
            else retained[item["slug"]]
            for item in SURVIVORS
            if item["slug"] in rebuilt or item["slug"] in retained
        ]
    result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {result_path}")


if __name__ == "__main__":
    main()
