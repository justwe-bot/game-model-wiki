"""Extract the 45 weapon models of Zombie Survivors from the survivor bundles.

Each survivor bundle carries its five weapons as real geometry: most are static
`<Weapon>_Decimated` objects with a MeshFilter/MeshRenderer, the archer's bow and the
ninja's katana are SkinnedMeshRenderer at bind pose, and the minigun is split into a
`Gun` + `Barrell` pair under one root. This script assembles one GLB per weapon, bakes
the object's local transform (Unity scales differ wildly: the medic handgun is 0.025,
the engineer blaster 1.85) and reuses the Unity Standard -> glTF material conversion from
extract_yazs_props.py.

Weapons that differ only by ammunition or slash effect share their geometry, so they
point at the owner's GLB instead of shipping an identical copy.

Usage:
    python scripts/extract_yazs_weapon_models.py                 # all 45 weapons
    python scripts/extract_yazs_weapon_models.py --only tank-shotgun,medic-handgun
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import trimesh
import UnityPy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_yazs_props import (  # noqa: E402  （复用道具脚本里的贴图/材质转换）
    as_opaque_jpeg,
    build_orm,
    decode_texture,
    material_textures,
    mesh_payload,
)

GAME_SLUG = "zombie-survivors"
DEFAULT_GAME = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Yet Another Zombie Survivors"
    r"\Yet Another Zombie Survivors_Data"
)
MODELS_DIR = ROOT / "models" / GAME_SLUG / "weapons"
CATALOG_PATH = ROOT / "games" / GAME_SLUG / "weapon-models.json"
WEAPONS_PATH = ROOT / "games" / GAME_SLUG / "weapons.json"

# slug -> 提取来源。objects 里可以写 "名字" 或 ("名字", "材质名过滤")：
# 少数对象只叫 Model，得靠材质把它们区分开（工程师的激光枪就是其中之一）。
WEAPON_SOURCES: dict[str, dict] = {
    # --- SWAT ---
    "swat-rifle": {"bundle": "gameplayerswat", "objects": ["Rifle_Decimated"]},
    "swat-sniper": {"bundle": "gameplayerswat", "objects": ["Sniper_Rifle_Decimated"]},
    "swat-pistol": {"bundle": "gameplayerswat", "objects": ["Deagle_Decimated"]},
    "swat-uzi": {"bundle": "gameplayerswat", "objects": ["Uzi_Decimated"]},
    "swat-grenadelauncher": {"bundle": "gameplayerswat", "objects": ["GL_Decimated"]},
    # --- 坦克 ---
    "tank-shotgun": {"bundle": "gameplayertank", "objects": ["Shotgun_Decimated"]},
    "tank-doubleshotgun": {"bundle": "gameplayertank", "objects": ["Double_Shotgun_Decimated"]},
    "tank-supershotgun": {"bundle": "gameplayertank", "objects": ["Super_Shotgun_Decimated"]},
    "tank-rocket": {"bundle": "gameplayertank", "objects": ["Rocket_Launcher_Decimated"]},
    # 转轮机枪在资源包里拆成枪体与枪管两个网格，同一父节点下组装
    "tank-minigun": {"bundle": "gameplayertank", "objects": ["Gun", "Barrell"]},
    # --- 女猎手：五种弓共享同一张弓身网格 ---
    "huntress-bow": {"bundle": "gameplayerhuntress", "objects": ["BowMain"]},
    "huntress-bowmulti": {"bundle": "gameplayerhuntress", "sharedWith": "huntress-bow",
                          "note": "多重发射与弓共用同一张弓身网格，差别在箭矢数量与弹道。"},
    "huntress-bowexplosive": {"bundle": "gameplayerhuntress", "sharedWith": "huntress-bow",
                              "note": "爆炸箭与弓共用弓身网格，差别在箭矢材质与命中特效。"},
    "huntress-bowfreezing": {"bundle": "gameplayerhuntress", "sharedWith": "huntress-bow",
                             "note": "冰冻箭与弓共用弓身网格，差别在箭矢材质与命中特效。"},
    "huntress-bowpoison": {"bundle": "gameplayerhuntress", "sharedWith": "huntress-bow",
                           "note": "毒箭与弓共用弓身网格，差别在箭矢材质与命中特效。"},
    # --- 忍者：五种刀技共享同一把武士刀 ---
    "ninja-katana": {"bundle": "gameplayerninja", "objects": ["Katana_Decimated"]},
    "ninja-katanasplash": {"bundle": "gameplayerninja", "sharedWith": "ninja-katana",
                           "note": "横扫与武士刀共用刀身网格，差别在挥砍特效。"},
    "ninja-katanasuperslash": {"bundle": "gameplayerninja", "sharedWith": "ninja-katana",
                               "note": "夺魂与武士刀共用刀身网格，差别在挥砍特效。"},
    "ninja-katanamultislash": {"bundle": "gameplayerninja", "sharedWith": "ninja-katana",
                               "note": "千刃与武士刀共用刀身网格，差别在挥砍特效。"},
    "ninja-katanahurricanewave": {"bundle": "gameplayerninja", "sharedWith": "ninja-katana",
                                  "note": "裂风与武士刀共用刀身网格，差别在挥砍特效。"},
    # --- 医生 ---
    "medic-syringethrow": {"bundle": "gameplayermedic", "objects": ["Syringe_Gun"]},
    "medic-syringerifle": {"bundle": "gameplayermedic", "objects": ["SyringeRifle"]},
    "medic-handgun": {"bundle": "gameplayermedic", "objects": ["Handgun_Decimated"]},
    "medic-dotabottlethrow": {"bundle": "gameplayermedic", "objects": ["Potion"]},
    "medic-freezebottlethrow": {"bundle": "gameplayermedic", "sharedWith": "medic-dotabottlethrow",
                                "note": "冰冻瓶与解毒瓶共用同一个瓶子网格，差别在瓶内液体材质与命中特效。"},
    # --- 游侠 ---
    "ranger-crossbow": {"bundle": "gameplayerranger", "objects": ["Crossbow"]},
    "ranger-crossbowbarber": {"bundle": "gameplayerranger", "objects": ["Crossbow_Barber"]},
    "ranger-crossbowfirebrand": {"bundle": "gameplayerranger", "objects": ["Crossbow_Firebrand"]},
    "ranger-crossbowrepeater": {"bundle": "gameplayerranger", "objects": ["Crossbow_Repeater"]},
    "ranger-crossbowshockspike": {"bundle": "gameplayerranger", "objects": ["Crossbow_Shockspike"]},
    # --- 纵火者 ---
    "pyro-axethrow": {"bundle": "gameplayerpyro", "objects": ["AxeThrow_Decimated"]},
    "pyro-blowtorch": {"bundle": "gameplayerpyro", "objects": ["BlowTorch_Decimated"]},
    "pyro-fireaxe": {"bundle": "gameplayerpyro", "objects": ["FireAxe_Splash"]},
    "pyro-infernax": {"bundle": "gameplayerpyro", "objects": ["FireAxe_Infernax_Skull"]},
    "pyro-flamethrower": {"bundle": "gameplayerpyro", "objects": ["FlameThrower_Decimated"]},
    # --- 机械师 ---
    "mechanic-chainsaw": {"bundle": "gameplayermechanic", "objects": ["Weapon_Mechanic_Chainsaw"]},
    "mechanic-lancer": {"bundle": "gameplayermechanic", "objects": ["Lancer"]},
    "mechanic-nitrogun": {"bundle": "gameplayermechanic", "objects": ["NitroGun"]},
    "mechanic-chaosengine": {"bundle": "gameplayermechanic", "objects": ["ChaosEngine"]},
    "mechanic-keyblade": {"bundle": "gameplayermechanic", "objects": ["Wrench"]},
    # --- 工程师 ---
    "eng-blaster": {"bundle": "gameplayerengineer", "objects": ["Blaster_Decimated"]},
    "eng-plasma": {"bundle": "gameplayerengineer", "objects": ["PlasmaGun_Decimated"]},
    "eng-taser": {"bundle": "gameplayerengineer", "objects": ["Taser_Decimated"]},
    "eng-tesla": {"bundle": "gameplayerengineer", "objects": ["Tesla_Decimated"]},
    # 激光枪的对象名只有 Model，靠 Sci-Fi Rifle 材质与 Laser 父节点认出
    "eng-laser": {"bundle": "gameplayerengineer", "objects": [("Model", "Sci-Fi Rifle")],
                  "parentIs": "Laser"},
}


def load_bundle(path: Path):
    """Index a bundle: names, meshes, materials, textures, renderers and transforms."""
    env = UnityPy.load(str(path))
    go_names, mesh_objs, mat_objs, tex_objs = {}, {}, {}, {}
    for obj in env.objects:
        kind = obj.type.name
        try:
            peek = obj.peek_name()
        except Exception:
            peek = None
        if kind == "GameObject":
            go_names[obj.path_id] = peek or ""
        elif kind == "Mesh":
            mesh_objs[obj.path_id] = obj
        elif kind == "Material":
            mat_objs[obj.path_id] = obj
        elif kind == "Texture2D":
            tex_objs[obj.path_id] = obj

    transforms, transform_of_go, parents = {}, {}, {}
    entries: dict[int, dict] = {}
    for obj in env.objects:
        kind = obj.type.name
        try:
            data = obj.read()
        except Exception:
            continue
        if kind == "Transform":
            transforms[obj.path_id] = data
            go = getattr(data, "m_GameObject", None)
            if go is not None:
                transform_of_go[go.m_PathID] = obj.path_id
                father = getattr(data, "m_Father", None)
                parents[go.m_PathID] = father.m_PathID if father is not None else 0
        elif kind in ("MeshFilter", "SkinnedMeshRenderer"):
            go, mesh = getattr(data, "m_GameObject", None), getattr(data, "m_Mesh", None)
            if go is None or mesh is None or not mesh.m_PathID:
                continue
            # MeshRenderer 可能比同物体上的 MeshFilter 先被遍历到，所以网格必须显式赋值，
            # 不能只靠 setdefault 的默认值（否则会留下 mesh=None）。
            entry = entries.setdefault(go.m_PathID, {"mesh": 0, "materials": [], "skinned": False})
            entry["mesh"] = mesh.m_PathID
            entry["skinned"] = kind == "SkinnedMeshRenderer"
            materials = [m.m_PathID for m in (getattr(data, "m_Materials", None) or []) if m]
            if materials:
                entry["materials"] = materials
        elif kind == "MeshRenderer":
            go = getattr(data, "m_GameObject", None)
            if go is None:
                continue
            entry = entries.setdefault(go.m_PathID, {"mesh": 0, "materials": [], "skinned": False})
            materials = [m.m_PathID for m in (getattr(data, "m_Materials", None) or []) if m]
            if materials:
                entry["materials"] = materials

    return {"go_names": go_names, "meshes": mesh_objs, "materials": mat_objs, "textures": tex_objs,
            "transforms": transforms, "transform_of_go": transform_of_go, "parents": parents,
            "entries": entries}


def local_matrix(transform) -> np.ndarray:
    """Unity local transform -> 4x4 matrix (quaternion is x, y, z, w)."""
    matrix = np.eye(4, dtype=np.float64)
    if transform is None:
        return matrix
    rot = getattr(transform, "m_LocalRotation", None)
    scale = getattr(transform, "m_LocalScale", None)
    pos = getattr(transform, "m_LocalPosition", None)
    if rot is not None:
        x, y, z, w = float(rot.x), float(rot.y), float(rot.z), float(rot.w)
        matrix[:3, :3] = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ], dtype=np.float64)
    if scale is not None:
        matrix[:3, 0] *= float(scale.x)
        matrix[:3, 1] *= float(scale.y)
        matrix[:3, 2] *= float(scale.z)
    if pos is not None:
        matrix[:3, 3] = [float(pos.x), float(pos.y), float(pos.z)]
    return matrix


def parent_name(bundle: dict, go_pid: int) -> str:
    father = bundle["parents"].get(go_pid, 0)
    if not father:
        return ""
    father_transform = bundle["transforms"].get(father)
    go = getattr(father_transform, "m_GameObject", None) if father_transform else None
    return bundle["go_names"].get(go.m_PathID, "") if go else ""


def resolve_objects(bundle: dict, spec: dict) -> tuple[list[tuple[int, list[int]]], list[str]]:
    """Return [(go_pid, material pids)] for the weapon's source objects."""
    found, missing = [], []
    entries = bundle["entries"]
    for item in spec["objects"]:
        name, mat_filter = (item, None) if isinstance(item, str) else (item[0], item[1])
        matches = [pid for pid, entry in entries.items()
                   if bundle["go_names"].get(pid) == name and entry.get("mesh")]
        if mat_filter:
            def has_material(pid: int) -> bool:
                for mat_pid in entries[pid].get("materials", []):
                    obj = bundle["materials"].get(mat_pid)
                    if obj is not None and (obj.peek_name() or "") == mat_filter:
                        return True
                return False
            matches = [pid for pid in matches if has_material(pid)]
        if spec.get("parentIs"):
            matches = [pid for pid in matches if parent_name(bundle, pid) == spec["parentIs"]]
        if not matches:
            missing.append(name if not mat_filter else f"{name}({mat_filter})")
            continue
        # 同名对象可能有多个实例（弓/箭矢各一份），取材质最全的那个
        best = max(matches, key=lambda pid: len(entries[pid].get("materials", [])))
        found.append((best, entries[best].get("materials", [])))
    return found, missing


def build_material(mat_obj, tex_objs, texture_size: int, normal_size: int, fallback_name: str):
    if mat_obj is None:
        return trimesh.visual.material.PBRMaterial(name=fallback_name, baseColorFactor=[1, 1, 1, 1]), {}, None
    material_src = mat_obj.read()
    slots, floats, colors = material_textures(material_src, tex_objs)

    def tex(*keys):
        for key in keys:
            if key in slots:
                return slots[key]
        return None

    albedo_src = tex("_MainTex", "_BaseMap", "_Albedo")
    normal_src = tex("_BumpMap", "_NormalMap")
    mg_src = tex("_MetallicGlossMap", "_SpecGlossMap", "_MetallicSmoothness")
    occ_src = tex("_OcclusionMap")

    albedo = decode_texture(albedo_src, texture_size) if albedo_src else None
    normal = decode_texture(normal_src, normal_size) if normal_src else None
    metallic_gloss = decode_texture(mg_src, normal_size) if mg_src else None
    occlusion = decode_texture(occ_src, normal_size) if occ_src else None

    metallic_factor = float(floats.get("_Metallic", 0.0))
    smoothness_factor = float(floats.get("_Glossiness", floats.get("_Smoothness", 0.5)))
    base_color = colors.get("_Color")
    base_factor = [1.0, 1.0, 1.0, 1.0]
    if base_color is not None:
        base_factor = [base_color.r, base_color.g, base_color.b, base_color.a]
    if albedo is None and base_color is None:
        # 没有基础色贴图的武器（例如瓶子内液体）只能靠材质颜色，这里保留配色以便辨认
        base_factor = [1.0, 1.0, 1.0, 1.0]

    pbr = trimesh.visual.material.PBRMaterial(
        name=getattr(material_src, "m_Name", fallback_name) or fallback_name,
        baseColorTexture=as_opaque_jpeg(albedo),
        baseColorFactor=base_factor,
        metallicFactor=0.0 if metallic_gloss is not None else metallic_factor,
        roughnessFactor=1.0 - smoothness_factor if metallic_gloss is None else 1.0,
        normalTexture=normal,
    )
    if metallic_gloss is not None or occlusion is not None:
        pbr.metallicRoughnessTexture = build_orm(occlusion, metallic_gloss, metallic_factor, smoothness_factor)
    info = {
        "baseColor": albedo_src.peek_name() if albedo_src else None,
        "normal": normal_src.peek_name() if normal_src else None,
        "metallicGloss": mg_src.peek_name() if mg_src else None,
        "occlusion": occ_src.peek_name() if occ_src else None,
    }
    return pbr, info, getattr(material_src, "m_Name", None)


def export_weapon(slug: str, spec: dict, bundle: dict, args) -> tuple[dict | None, str | None]:
    sources, missing = resolve_objects(bundle, spec)
    if missing:
        return None, f"找不到对象 {missing}"
    if not sources:
        return None, "没有可用对象"

    geometries = []
    material_names: list[str] = []
    texture_info: dict[str, str] = {}
    total_parts = 0
    vertices = 0

    for go_pid, mat_pids in sources:
        mesh_obj = bundle["meshes"].get(bundle["entries"][go_pid]["mesh"])
        if mesh_obj is None:
            return None, "mesh 缺失"
        try:
            payload = mesh_payload(mesh_obj)
        except Exception as exc:  # noqa: BLE001
            return None, f"网格解析 {type(exc).__name__}: {exc}"
        if payload is None:
            return None, "网格为空"
        verts, uvs, parts = payload
        transform = bundle["transforms"].get(bundle["transform_of_go"].get(go_pid, 0))
        matrix = local_matrix(transform)
        verts = verts @ matrix[:3, :3].T + matrix[:3, 3]
        vertices += len(verts)
        total_parts += len(parts)

        by_material: dict[int, list] = {}
        for index, faces in enumerate(parts):
            mat_pid = mat_pids[index] if index < len(mat_pids) else (mat_pids[-1] if mat_pids else -1)
            by_material.setdefault(mat_pid, []).append(faces)

        for mat_pid, face_groups in by_material.items():
            merged = np.concatenate(face_groups, axis=0)
            pbr, tex_info, mname = build_material(bundle["materials"].get(mat_pid), bundle["textures"],
                                                  args.texture_size, args.normal_size, slug)
            for key, value in tex_info.items():
                if value and not texture_info.get(key):
                    texture_info[key] = value
            if mname:
                material_names.append(mname)
            geometries.append(trimesh.Trimesh(
                vertices=verts, faces=merged,
                visual=trimesh.visual.TextureVisuals(uv=uvs if uvs is not None else None, material=pbr),
                process=False,
            ))

    if not geometries:
        return None, "没有可用子网格"

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    glb = MODELS_DIR / f"{slug}.glb"
    try:
        exportable = geometries[0] if len(geometries) == 1 else trimesh.Scene(geometries)
        exportable.export(glb)
    except Exception as exc:  # noqa: BLE001
        return None, f"导出 {type(exc).__name__}: {exc}"

    return {
        "slug": slug,
        "model": f"models/{GAME_SLUG}/weapons/{glb.name}",
        "bundle": spec["bundle"],
        "sourceObjects": [bundle["go_names"].get(pid, "") for pid, _ in sources],
        "materials": material_names,
        "triangles": int(sum(len(g.faces) for g in geometries)),
        "subMeshes": total_parts,
        "vertices": int(vertices),
        "textures": texture_info,
        "sizeKB": round(glb.stat().st_size / 1024, 1),
    }, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--only", default="", help="逗号分隔的武器 slug")
    parser.add_argument("--texture-size", type=int, default=512)
    parser.add_argument("--normal-size", type=int, default=256)
    args = parser.parse_args()

    if not args.game.is_dir():
        print(f"游戏数据目录不存在: {args.game}", file=sys.stderr)
        return 2
    if not WEAPONS_PATH.exists():
        print(f"先运行 extract_yazs_weapons.py 生成 {WEAPONS_PATH}", file=sys.stderr)
        return 2

    weapons_doc = json.loads(WEAPONS_PATH.read_text(encoding="utf-8"))
    weapon_names = {w["slug"]: w for w in weapons_doc["weapons"]}
    # 共用几何的条目没有 objects，它们在本轮之后指向拥有者的 GLB
    exportable = {slug: spec for slug, spec in WEAPON_SOURCES.items() if "objects" in spec}
    wanted = {x.strip() for x in args.only.split(",") if x.strip()}
    slugs = [slug for slug in exportable if not wanted or slug in wanted]
    print(f"待处理 {len(slugs)} 把武器（另有 {len(WEAPON_SOURCES) - len(exportable)} 把共用几何）")

    bundles = args.game / "StreamingAssets" / "AssetBundles"
    cache: dict[str, dict] = {}
    catalog: list[dict] = []
    failures: list[tuple[str, str]] = []
    by_slug: dict[str, dict] = {}

    for index, slug in enumerate(slugs, 1):
        spec = WEAPON_SOURCES[slug]
        bundle_name = spec["bundle"]
        if bundle_name not in cache:
            cache.clear()                      # 包很大，一次只留一个
            cache[bundle_name] = load_bundle(bundles / bundle_name)
        entry, error = export_weapon(slug, spec, cache[bundle_name], args)
        if error:
            failures.append((slug, error))
            print(f"  [失败] {slug}: {error}", file=sys.stderr)
            continue
        weapon = weapon_names.get(slug) or {}
        entry.update({
            "name": weapon.get("name", slug),
            "nameEn": weapon.get("nameEn", ""),
            "character": weapon.get("character", ""),
            "category": weapon.get("category", ""),
            "from": "extracted",
            "note": "",
        })
        by_slug[slug] = entry
        print(f"  [{index}/{len(slugs)}] {slug:26s} {entry['triangles']:>6} 面 "
              f"{entry['sizeKB']:>7.1f} KB  来自 {'+'.join(entry['sourceObjects'])}")

    # 共用几何的武器指向拥有者的 GLB，不再复制一份同样的模型
    for slug, spec in WEAPON_SOURCES.items():
        if "sharedWith" not in spec or slug in by_slug:
            continue
        owner = by_slug.get(spec["sharedWith"])
        if owner is None:
            continue
        weapon = weapon_names.get(slug) or {}
        by_slug[slug] = {
            "slug": slug,
            "model": owner["model"],
            "bundle": spec["bundle"],
            "sourceObjects": owner["sourceObjects"],
            "materials": owner["materials"],
            "triangles": owner["triangles"],
            "subMeshes": owner["subMeshes"],
            "vertices": owner["vertices"],
            "textures": owner["textures"],
            "sizeKB": owner["sizeKB"],
            "name": weapon.get("name", slug),
            "nameEn": weapon.get("nameEn", ""),
            "character": weapon.get("character", ""),
            "category": weapon.get("category", ""),
            "from": f"shared:{spec['sharedWith']}",
            "note": spec.get("note", ""),
        }

    catalog = [by_slug[slug] for slug in WEAPON_SOURCES if slug in by_slug]
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    extracted = [e for e in catalog if e["from"] == "extracted"]
    shared = [e for e in catalog if e["from"].startswith("shared:")]
    print(f"\n写出 {CATALOG_PATH.relative_to(ROOT)}：{len(catalog)} 把武器"
          f"（独立模型 {len(extracted)}，共用模型 {len(shared)}）")
    print(f"GLB 目录 {MODELS_DIR.relative_to(ROOT)}，合计 "
          f"{sum(e['sizeKB'] for e in extracted) / 1024:.1f} MB")
    for slug, why in failures:
        print(f"  未导出 {slug}: {why}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
