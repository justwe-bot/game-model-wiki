"""Extract Yet Another Zombie Survivors environment props (buildings, decor, props) as textured GLB.

Geometry comes from the individual meshes that the scene bundles keep un-batched. Unity only
leaves static-batched "Combined Mesh (root: scene)" objects for most scenery, but a few thousand
renderers still point at standalone meshes with real MeshFilter + MeshRenderer links, and those
give a trustworthy mesh -> material -> texture chain. Everything else would have to be guessed
from name conventions, which is why this script only exports what it can verify.

Textures follow the Unity Standard layout: `_MainTex` (albedo), `_BumpMap`/`_NormalMap`,
`_MetallicGlossMap` (metallic in R, smoothness in A) and `_OcclusionMap`. glTF wants a packed
occlusion/roughness/metallic map, so the script converts instead of passing textures through.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import trimesh
import UnityPy
from PIL import Image
from UnityPy.helpers.MeshHelper import MeshHandler

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Yet Another Zombie Survivors"
    r"\Yet Another Zombie Survivors_Data"
)
GAME_SLUG = "yet-another-zombie-survivors"
MODELS_DIR = ROOT / "models" / GAME_SLUG
CATALOG_PATH = ROOT / "games" / GAME_SLUG / "props.json"

SCENES = [
    "gameplayscene_lab",
    "gameplayscene_greenhell",
    "gameplayscene_trainstation",
    "gameplayscene_desert_02",
    "gameplayscene_abandonedcity",
    "prototypescene",
]

# Ordered classification rules: first match wins. `(?<![a-z])` keeps a keyword from
# matching inside a longer word (Bush must not match "bus", Carrot must not match "car"),
# while still allowing suffixed names like TrainObject or Wood_Debris_2.
CATEGORY_RULES: list[tuple[str, str]] = [
    (r"(?<![a-z])(skull|skeleton|bone|fallen|debris|garbage|trash|rubbish|rubble|wreck|scrap|corpse|remains)",
     "废墟与残骸"),
    (r"(?<![a-z])(spruce|birch|oak|pine|bush|fern|grass|lawn|flower|branch|needle|ivy|moss|heather|reed|sapling|leaf|tree|log|shrub|trunk)",
     "植被"),
    (r"(?<![a-z])(forklift|tractor|wheel|tire|train|wagon|car[a-z_0-9]*_|retriever|hood|van|tank[a-z_0-9]|wz_)",
     "载具与部件"),
    (r"(?<![a-z])(hangar|warehouse|parking|barrack|bunker|toilet|container|house|foundation|base)",
     "建筑与结构"),
    (r"(?<![a-z])(roof|wall|floor|door|window|gate|entrance|frame|support|corner|pillar|column|stair|terrace|platform|bridge|trasa|rail)",
     "建筑与结构"),
    (r"(?<![a-z])(decor|poster|flag|sign|streetlight|hydrant|candle|cork|cocoon|bio|web|shrine)",
     "装饰与陈设"),
    (r"(?<![a-z])(hp_|sm_|02_sm_|03_sm_|04_sm_|ax|hammer|shield|weapon|survival|solar|ammo|barrel|crate|box|plate|glass|food|bottle|can|pot|pan|tool|vice|bench|lamp|pipe|valve|pump|battery|generator|transformator|radio|computer|screen|camera|printer|file|register|document|floppy|cable|tube|drain|broccoli|carrot|cucumber|eyeball|water)",
     "生活道具"),
    (r"(?<![a-z])(rock|cliff|stone|ter_|terrain|ground|road|sewer|manhole|puddle|sand|dirt|gravel)",
     "地形与道路"),
]

# 原始命名在烘焙流程里丢失的网格，只有 Maya 默认名，无法归入任何语义分类。
UNNAMED_PATTERN = re.compile(
    r"^(poly\s*surface\s*\d*|mesh|p?cube\d*|plane|cylinder\d*|sphere.*|object\d*|default.*|"
    r"shen|zhuan|floppy|support|\d+_floor)$"
)

CATEGORY_ORDER = ["建筑与结构", "载具与部件", "装饰与陈设", "生活道具", "植被",
                  "地形与道路", "废墟与残骸", "未命名模型", "其它"]


def load_bundle(path: Path):
    env = UnityPy.load(str(path))
    go_names, mesh_objs, mat_objs, tex_objs = {}, {}, {}, {}
    for o in env.objects:
        t = o.type.name
        try:
            n = o.peek_name()
        except Exception:
            n = None
        if t == "GameObject":
            go_names[o.path_id] = n
        elif t == "Mesh":
            mesh_objs[o.path_id] = o
        elif t == "Material":
            mat_objs[o.path_id] = o
        elif t == "Texture2D":
            tex_objs[o.path_id] = o

    filters, renderers = {}, {}
    for o in env.objects:
        if o.type.name == "MeshFilter":
            try:
                r = o.read()
            except Exception:
                continue
            go, m = getattr(r, "m_GameObject", None), getattr(r, "m_Mesh", None)
            if go is not None and m is not None:
                filters[go.m_PathID] = m.m_PathID
        elif o.type.name == "MeshRenderer":
            try:
                r = o.read()
            except Exception:
                continue
            go = getattr(r, "m_GameObject", None)
            if go is not None:
                renderers[go.m_PathID] = [m.m_PathID for m in (getattr(r, "m_Materials", None) or []) if m]
    return go_names, mesh_objs, mat_objs, tex_objs, filters, renderers


def as_float_array(seq, width):
    """Unity 顶点通道宽度不一致（Vector3/Vector4 混排），统一截断到 width 再用 numpy 打包。"""
    if not seq:
        return None
    return np.array([tuple(v[:width]) for v in seq], dtype=np.float64)


def mesh_payload(mesh_obj):
    """Return (vertices, uv, [faces per submesh]).

    get_triangles() yields one list per submesh and its indices are absolute vertex
    indices, so the per-submesh lists can be kept apart and paired with the renderer's
    material list instead of being flattened into a single material.
    """
    h = MeshHandler(mesh_obj.read())
    h.process()
    verts = as_float_array(h.m_Vertices, 3)
    if verts is None:
        return None
    try:
        per_submesh = h.get_triangles()
    except ValueError:
        return None                     # 线/点拓扑，不是可导出的面片道具
    uvs = as_float_array(h.m_UV0, 2)

    parts = []
    for rows in per_submesh:
        if not rows:
            continue
        faces = np.array([tuple(r[:3]) for r in rows if len(r) >= 3], dtype=np.int64)
        if faces.size == 0:
            continue
        faces = faces[(faces >= 0).all(axis=1) & (faces < len(verts)).all(axis=1)]
        if faces.size:
            parts.append(faces)
    if not parts:
        return None
    return verts, uvs, parts


def material_textures(material, tex_objs):
    """Return the Unity Standard texture set plus scalar factors."""
    slots = {}
    try:
        for slot, pair in material.m_SavedProperties.m_TexEnvs:
            t = getattr(pair, "m_Texture", None)
            if t is not None and t.m_PathID and t.m_PathID in tex_objs:
                slots[slot] = tex_objs[t.m_PathID]
    except Exception:
        pass
    floats = {}
    try:
        for k, v in material.m_SavedProperties.m_Floats or []:
            floats[k] = v
    except Exception:
        pass
    colors = {}
    try:
        for k, v in material.m_SavedProperties.m_Colors or []:
            colors[k] = v
    except Exception:
        pass
    return slots, floats, colors


def decode_texture(obj, size_limit: int):
    try:
        img = obj.read().image
    except Exception:
        return None
    if img is None:
        return None
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    if max(img.size) > size_limit:
        scale = size_limit / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), 1)
    return img


def as_opaque_jpeg(img):
    """基础色走 JPEG 能省下大量体积；有实际透明度的贴图必须留在 PNG。"""
    if img is None:
        return None
    if img.mode == "RGBA":
        alpha = img.getchannel("A")
        if alpha.getextrema() != (255, 255):
            return img.convert("RGBA")
    out = img.convert("RGB")
    out.format = "JPEG"
    return out


def build_orm(occlusion, metallic_gloss, metallic_factor: float, smoothness_factor: float):
    """Pack Unity Standard maps into a glTF ORM texture (R=occlusion, G=roughness, B=metallic)."""
    size = None
    for img in (occlusion, metallic_gloss):
        if img is not None:
            size = img.size
            break
    if size is None:
        size = (4, 4)

    if occlusion is not None:
        # Unity Standard 的 _OcclusionMap 取的是 G 通道，不是亮度
        ao = np.asarray(occlusion.convert("RGB").resize(size).getchannel("G"), dtype=np.float32)
    else:
        ao = np.full((size[1], size[0]), 255.0, dtype=np.float32)

    if metallic_gloss is not None:
        arr = np.asarray(metallic_gloss.convert("RGBA").resize(size), dtype=np.float32)
        metallic = arr[..., 0]                       # Unity: R = metallic
        roughness = 255.0 - arr[..., 3]              # Unity: A = smoothness
    else:
        metallic = np.full((size[1], size[0]), max(0.0, min(1.0, metallic_factor)) * 255.0, dtype=np.float32)
        roughness = np.full((size[1], size[0]), (1.0 - max(0.0, min(1.0, smoothness_factor))) * 255.0,
                            dtype=np.float32)

    orm = np.stack([ao, roughness, metallic], axis=-1).clip(0, 255).astype(np.uint8)
    return Image.fromarray(orm, "RGB")


def categorize(name: str) -> str:
    # 烘焙产物后缀（-POLY_REDUCED1、_combined_static）会干扰前缀匹配，先剥掉
    low = re.sub(r"(-poly_reduced\d*|_combined_static)", "", name.lower()).strip()
    if UNNAMED_PATTERN.match(low):
        return "未命名模型"
    for pattern, category in CATEGORY_RULES:
        if re.search(pattern, low):
            return category
    return "其它"


def slugify(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return text or "prop"


def collect(data_dir: Path):
    """Union of scene-linked props across every scene bundle."""
    import collections

    props: dict[str, dict] = {}
    go_usage: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for scene in SCENES:
        path = data_dir / "StreamingAssets" / "AssetBundles" / scene
        if not path.is_file():
            print(f"  ! 缺少 {scene}", file=sys.stderr)
            continue
        go_names, mesh_objs, mat_objs, tex_objs, filters, renderers = load_bundle(path)
        linked = 0
        for go_pid, mesh_pid in filters.items():
            mesh_obj = mesh_objs.get(mesh_pid)
            if mesh_obj is None:
                continue
            name = mesh_obj.peek_name()
            if not name or name.startswith("Combined Mesh"):
                continue
            go_name = go_names.get(go_pid)
            linked += 1
            if go_name:
                go_usage[name][go_name] += 1
            # 同名网格可能在多个场景出现；保留材质贴图更全的那份
            score = sum(1 for m in renderers.get(go_pid, []) if m in mat_objs)
            existing = props.get(name)
            if existing and existing["_score"] >= score:
                continue
            props[name] = {
                "mesh": name,
                "meshPathId": mesh_pid,
                "scene": scene,
                "gameObject": go_name or name,
                "materials": [m for m in renderers.get(go_pid, []) if m in mat_objs],
                "_score": score,
            }
        print(f"  {scene:30s} 单体渲染器 {linked:5d}（累计唯一网格 {len(props)}）")

    # 一个网格常被多个同名实例引用，取出现次数最多的实例名更有代表性
    for name, info in props.items():
        counter = go_usage.get(name)
        if counter:
            info["gameObject"] = counter.most_common(1)[0][0]
    return props


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--limit", type=int, default=0, help="only export the first N props")
    parser.add_argument("--only", default="", help="comma separated substrings to filter mesh names")
    parser.add_argument("--texture-size", type=int, default=512, help="base color texture cap")
    parser.add_argument("--normal-size", type=int, default=256, help="normal / ORM texture cap")
    parser.add_argument("--recategorize", action="store_true",
                        help="only re-apply CATEGORY_RULES to the existing catalog (no re-export)")
    args = parser.parse_args()

    if args.recategorize:
        if not CATALOG_PATH.exists():
            print(f"找不到 {CATALOG_PATH}", file=sys.stderr)
            return 2
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        for item in catalog:
            item["category"] = categorize(item["name"])
        catalog.sort(key=lambda item: (CATEGORY_ORDER.index(item["category"])
                                       if item["category"] in CATEGORY_ORDER else 99,
                                       item["name"].lower()))
        CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        import collections
        print(f"重分类 {len(catalog)} 个道具：")
        for cat, n in collections.Counter(item["category"] for item in catalog).most_common():
            print(f"   {cat:12s} {n}")
        update_games_json(catalog)
        return 0

    if not args.game.is_dir():
        print(f"游戏数据目录不存在: {args.game}", file=sys.stderr)
        return 2

    print("扫描场景包中的单体渲染器…")
    props = collect(args.game)
    names = sorted(props)
    if args.only:
        wanted = [w.strip().lower() for w in args.only.split(",") if w.strip()]
        names = [n for n in names if any(w in n.lower() for w in wanted)]
    if args.limit:
        names = names[: args.limit]
    print(f"\n待导出 {len(names)} 个道具")

    # 按场景缓存 bundle，避免每个道具都重新加载
    cache: dict[str, tuple] = {}
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    catalog = []
    failures = []

    for index, name in enumerate(names, 1):
        info = props[name]
        scene = info["scene"]
        if scene not in cache:
            cache.clear()
            cache[scene] = load_bundle(args.game / "StreamingAssets" / "AssetBundles" / scene)
        go_names, mesh_objs, mat_objs, tex_objs, filters, renderers = cache[scene]

        mesh_obj = mesh_objs.get(info["meshPathId"])
        if mesh_obj is None:
            failures.append((name, "mesh 缺失"))
            continue
        try:
            payload = mesh_payload(mesh_obj)
        except Exception as exc:
            failures.append((name, f"网格解析 {type(exc).__name__}: {exc}"))
            continue
        if payload is None:
            failures.append((name, "网格为空"))
            continue
        verts, uvs, parts = payload

        def build_material(mat_obj):
            """Unity Standard 材质 -> glTF PBR 材质，并返回用到的贴图名。"""
            if mat_obj is None:
                fallback = trimesh.visual.material.PBRMaterial(name=name, baseColorFactor=[1, 1, 1, 1])
                return fallback, {}, None
            material_src = mat_obj.read()
            slots, floats, colors = material_textures(material_src, tex_objs)

            def tex(*keys):
                for k in keys:
                    if k in slots:
                        return slots[k]
                return None

            albedo_src = tex("_MainTex", "_BaseMap", "_Albedo")
            normal_src = tex("_BumpMap", "_NormalMap")
            mg_src = tex("_MetallicGlossMap", "_SpecGlossMap", "_MetallicSmoothness")
            occ_src = tex("_OcclusionMap")

            albedo = decode_texture(albedo_src, args.texture_size) if albedo_src else None
            # 法线与 ORM 是通道数据，走 PNG 且体积占比最大，所以单独用更低的上限
            normal = decode_texture(normal_src, args.normal_size) if normal_src else None
            metallic_gloss = decode_texture(mg_src, args.normal_size) if mg_src else None
            occlusion = decode_texture(occ_src, args.normal_size) if occ_src else None

            metallic_factor = float(floats.get("_Metallic", 0.0))
            smoothness_factor = float(floats.get("_Glossiness", floats.get("_Smoothness", 0.5)))
            base_color = colors.get("_Color")
            base_factor = [1.0, 1.0, 1.0, 1.0]
            if base_color is not None:
                base_factor = [base_color.r, base_color.g, base_color.b, base_color.a]

            pbr = trimesh.visual.material.PBRMaterial(
                name=material_src.m_Name,
                baseColorTexture=as_opaque_jpeg(albedo),
                baseColorFactor=base_factor,
                metallicFactor=0.0 if metallic_gloss is not None else metallic_factor,
                roughnessFactor=1.0 - smoothness_factor if metallic_gloss is None else 1.0,
                normalTexture=normal,
            )
            # Unity 的 MetallicGlossMap 把金属度放 R、光滑度放 A；glTF 要的是
            # R=遮蔽 G=粗糙 B=金属 的打包贴图，所以重排而不是直接透传。
            if metallic_gloss is not None or occlusion is not None:
                pbr.metallicRoughnessTexture = build_orm(occlusion, metallic_gloss,
                                                         metallic_factor, smoothness_factor)
            info = {
                "baseColor": albedo_src.peek_name() if albedo_src else None,
                "normal": normal_src.peek_name() if normal_src else None,
                "metallicGloss": mg_src.peek_name() if mg_src else None,
                "occlusion": occ_src.peek_name() if occ_src else None,
            }
            return pbr, info, material_src.m_Name

        # 子网格 i 用渲染器材质列表里的第 i 个；材质不够时退到最后一个
        mesh_mats = info["materials"]
        by_material: dict[int, list] = {}
        for i, faces in enumerate(parts):
            mat_pid = mesh_mats[i] if i < len(mesh_mats) else (mesh_mats[-1] if mesh_mats else -1)
            by_material.setdefault(mat_pid, []).append(faces)

        geometries = []
        texture_info = {}
        material_names = []
        for mat_pid, face_groups in by_material.items():
            merged = np.concatenate(face_groups, axis=0)
            pbr, tex_info, mname = build_material(mat_objs.get(mat_pid) if mat_pid in mat_objs else None)
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
            failures.append((name, "没有可用子网格"))
            continue

        try:
            exportable = geometries[0] if len(geometries) == 1 else trimesh.Scene(geometries)
            glb = MODELS_DIR / f"{slugify(name)}.glb"
            exportable.export(glb)
        except Exception as exc:
            failures.append((name, f"导出 {type(exc).__name__}: {exc}"))
            continue

        triangles = int(sum(len(g.faces) for g in geometries))
        catalog.append({
            "slug": slugify(name),
            "name": name,
            "category": categorize(name),
            "sourceScene": scene,
            "inGameObject": info["gameObject"],
            "materials": material_names,
            "triangles": triangles,
            "subMeshes": len(parts),
            "vertices": int(len(verts)),
            "hasUV": uvs is not None,
            "textures": texture_info,
            "model": f"models/{GAME_SLUG}/{glb.name}",
            "sizeKB": round(glb.stat().st_size / 1024, 1),
        })
        if index % 25 == 0 or index == len(names):
            print(f"  已导出 {index}/{len(names)} …")

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    catalog.sort(key=lambda item: (CATEGORY_ORDER.index(item["category"]) if item["category"] in CATEGORY_ORDER else 99,
                                   item["name"].lower()))
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n写出 {CATALOG_PATH.relative_to(ROOT)}：{len(catalog)} 个道具")

    import collections
    for cat, n in collections.Counter(item["category"] for item in catalog).most_common():
        print(f"   {cat:10s} {n}")

    update_games_json(catalog)

    if failures:
        print(f"\n失败 {len(failures)} 项（前 10）:", file=sys.stderr)
        for name, why in failures[:10]:
            print(f"   {name}: {why}", file=sys.stderr)
    return 0


def update_games_json(catalog: list[dict]) -> None:
    """Record the prop catalog on the game entry so props.html can find it."""
    import collections

    games_path = ROOT / "games.json"
    games = json.loads(games_path.read_text(encoding="utf-8"))
    counts = collections.Counter(item["category"] for item in catalog)
    order = [c for c in CATEGORY_ORDER if counts.get(c)]
    summary = " / ".join(f"{counts[c]} 个{c}" for c in order)
    for index, game in enumerate(games):
        if game.get("slug") != GAME_SLUG:
            continue
        game["propsCatalog"] = f"games/{GAME_SLUG}/props.json"
        game["propsSubtitle"] = f"从关卡场景提取的 {len(catalog)} 个场景资产：{summary}"
        games[index] = game
        break
    games_path.write_text(json.dumps(games, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"更新 {games_path.relative_to(ROOT)}：propsCatalog / propsSubtitle")


if __name__ == "__main__":
    raise SystemExit(main())
