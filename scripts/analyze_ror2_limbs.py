"""Analyze body-element modeling (hands/feet/head/limbs) in Risk of Rain 2 GLBs.

Pure-stdlib glTF/GLB reader. For every skin joint, computes the bind-pose
average position and bounding-box size of the vertices most influenced by
that joint, so we can identify how hands, feet and other elements are
modeled, and compare original vs low-poly retention.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COMPONENT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def parse_glb(path: Path):
    data = path.read_bytes()
    magic, _ver, _length = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67, f"not a glb: {path}"
    off = 12
    chunks = {}
    while off < len(data):
        clen, ctype = struct.unpack("<II", data[off : off + 8])
        chunks.setdefault(ctype, []).append(data[off + 8 : off + 8 + clen])
        off += 8 + clen
    return json.loads(chunks[0x4E4F534A][0]), chunks.get(0x004E4942, [b""])[0]


def accessor_data(gltf, bin_data, index):
    acc = gltf["accessors"][index]
    fmt = "<" + COMPONENT[acc["componentType"]] * (COUNT[acc["type"]] * acc["count"])
    if "bufferView" not in acc:
        return []
    bv = gltf["bufferViews"][acc["bufferView"]]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    raw = bin_data[start : start + struct.calcsize(fmt)]
    vals = struct.unpack(fmt, raw)
    n = COUNT[acc["type"]]
    return [vals[i : i + n] for i in range(0, len(vals), n)]


def node_world(gltf, node_id, parent_world=None):
    node = gltf["nodes"][node_id]
    m = node.get("matrix")
    if m:
        m = [m[0], m[1], m[2], m[3], 0, m[4], m[5], m[6], m[7], 0, m[8], m[9], m[10], m[11], 0, m[12], m[13], m[14], m[15], 1]
    else:
        t = node.get("translation", [0, 0, 0])
        r = node.get("rotation", [0, 0, 0, 1])
        s = node.get("scale", [1, 1, 1])
        x, y, z, w = r
        m = [
            1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0,
            2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0,
            2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0,
            t[0], t[1], t[2], 1,
        ]
        m = [m[0] * s[0], m[1] * s[0], m[2] * s[0], m[3], m[4] * s[1], m[5] * s[1], m[6] * s[1], m[7],
             m[8] * s[2], m[9] * s[2], m[10] * s[2], m[11], m[12], m[13], m[14], m[15]]
    if parent_world is None:
        return m
    return mat_mul(parent_world, m)


def mat_mul(a, b):
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
    return out


def mat_mul_vec(m, v):
    return [
        m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12],
        m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13],
        m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14],
    ]


def bind_worlds(gltf):
    worlds = [None] * len(gltf["nodes"])
    children_of = [set() for _ in gltf["nodes"]]
    for i, node in enumerate(gltf["nodes"]):
        for c in node.get("children", []):
            children_of[c].add(i)
    roots = [i for i, p in enumerate(children_of) if not p]
    stack = [(r, None) for r in roots]
    while stack:
        nid, pw = stack.pop()
        w = node_world(gltf, nid, pw)
        worlds[nid] = w
        for c in gltf["nodes"][nid].get("children", []):
            stack.append((c, w))
    return worlds


def analyze(path: Path):
    gltf, bin_data = parse_glb(path)
    result = {"file": path.name, "meshes": [], "bones": []}
    for mesh in gltf.get("meshes", []):
        tris = 0
        verts = 0
        for prim in mesh["primitives"]:
            verts += gltf["accessors"][prim["attributes"]["POSITION"]]["count"]
            if "indices" in prim:
                tris += gltf["accessors"][prim["indices"]]["count"] // 3
            else:
                tris += verts // 3
        result["meshes"].append({"name": mesh.get("name"), "tris": tris, "verts": verts})

    skins = gltf.get("skins", [])
    # pick the skin actually used by a mesh node (or the first with IBM)
    used_skin = None
    for node in gltf["nodes"]:
        if "skin" in node:
            used_skin = skins[node["skin"]]
            break
    if used_skin is None and skins:
        used_skin = next((s for s in skins if "inverseBindMatrices" in s), skins[0])
    if used_skin is None:
        return result  # static mesh, no skeleton
    for skin in [used_skin]:
        joints = skin["joints"]
        names = [gltf["nodes"][j].get("name", f"joint{j}") for j in joints]
        if "inverseBindMatrices" in skin:
            ibm = accessor_data(gltf, bin_data, skin["inverseBindMatrices"])
        else:
            ibm = [[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]] * len(joints)
        worlds = bind_worlds(gltf)
        result["bones"] = names
        # accumulate vertex influence across every skinned primitive
        joint_verts = {j: [[], []] for j in range(len(joints))}
        for mesh in gltf.get("meshes", []):
            for prim in mesh["primitives"]:
                attrs = prim["attributes"]
                if "JOINTS_0" not in attrs:
                    continue
                pos = accessor_data(gltf, bin_data, attrs["POSITION"])
                ji = accessor_data(gltf, bin_data, attrs["JOINTS_0"])
                wv = accessor_data(gltf, bin_data, attrs["WEIGHTS_0"])
                for p, j, w in zip(pos, ji, wv):
                    best = max(range(4), key=lambda k: w[k])
                    joint_verts[j[best]][0].append(p)
                    joint_verts[j[best]][1].append(w[best])
        for j, (vp, vw) in joint_verts.items():
            if not vp:
                continue
            # world-space min/max of vertices influenced by this joint
            bmin = [1e9] * 3
            bmax = [-1e9] * 3
            for v in vp:
                wv = mat_mul_vec(worlds[joints[j]], mat_mul_vec(ibm[j], v))
                for k in range(3):
                    bmin[k] = min(bmin[k], wv[k])
                    bmax[k] = max(bmax[k], wv[k])
            size = [bmax[k] - bmin[k] for k in range(3)]
            diag = sum(s * s for s in size) ** 0.5
            result["bones"].append(
                {
                    "bone": names[j],
                    "verts": len(vp),
                    "center": [round(c, 4) for c in [(bmin[k] + bmax[k]) / 2 for k in range(3)]],
                    "size": [round(s, 4) for s in size],
                    "diag": round(diag, 4),
                }
            )
    return result


def classify(name: str) -> str:
    low = name.lower()
    for key, cls in [
        ("hand", "hand"), ("finger", "hand"), ("claw", "hand"),
        ("foot", "foot"), ("toe", "foot"), ("hoof", "foot"),
        ("leg", "leg"), ("thigh", "leg"), ("calf", "leg"),
        ("arm", "arm"), ("shoulder", "arm"), ("elbow", "arm"), ("forearm", "arm"),
        ("head", "head"), ("neck", "head"),
        ("tail", "tail"), ("wing", "wing"), ("horn", "horn"), ("spike", "horn"),
        ("eye", "eye"), ("mouth", "mouth"), ("jaw", "mouth"),
    ]:
        if key in low:
            return cls
    return "other"


if __name__ == "__main__":
    targets = sys.argv[1:] or sorted((ROOT / "models").glob("*-original.glb"))
    for t in targets:
        res = analyze(Path(t))
        mesh_tris = sum(m["tris"] for m in res["meshes"])
        bones = [b for b in res["bones"] if isinstance(b, dict)]
        print(f"\n== {res['file']} | meshes={[m['name'] for m in res['meshes']]} tris={mesh_tris}")
        for b in bones:
            if b["verts"] >= 8 or classify(b["bone"]) != "other":
                print(f"  {b['bone']:<24} verts={b['verts']:<6} size={b['size']} center={b['center']}")
