"""Merge generated Risk of Rain 2 survivor records into the main catalog."""

from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    catalog_path = repo_root / "catalog.json"
    generated_path = repo_root / "games" / "risk-of-rain-2" / "survivors.generated.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    generated = json.loads(generated_path.read_text(encoding="utf-8"))
    catalog = [entry for entry in catalog if entry.get("kind") != "hero"]

    for source in generated:
        slug = source["slug"]
        catalog.append(
            {
                "slug": slug,
                "name": source["name"],
                "nameEn": source["nameEn"],
                "kind": "hero",
                "tier": "英雄",
                "status": source.get("status", "静态模型"),
                "summary": source["summary"],
                "models": {
                    "original": f"models/survivors/{slug}-original.glb",
                    "low": f"models/survivors/{slug}-low.glb",
                },
                "textures": {"original": None, "low": None},
                "emissiveTextures": {"original": None, "low": None},
                "normalTextures": {"original": None, "low": None},
                "textureSource": source["texture"],
                "emissiveTextureSource": None,
                "normalTextureSource": None,
                "modelSource": ", ".join(source["meshes"]),
                "prefabSource": source["prefab"],
                "rendererCount": source["rendererCount"],
                "bakedTriangles": source["bakedTriangles"],
                "sourceTriangles": source["sourceTriangles"],
                "lowTriangles": source["lowTriangles"],
                "sourceSizeKB": source["sourceSizeKB"],
                "lowSizeKB": source["lowSizeKB"],
                "preserveMaterials": True,
                "animations": [],
                "defaultClip": None,
                "skills": [],
                **(
                    {
                        "rigSource": source["rig"],
                        "jointCount": source["jointCount"],
                        "originalGameRig": False,
                    }
                    if source.get("rig")
                    else {}
                ),
            }
        )

    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Merged {len(generated)} survivors into {catalog_path}")


if __name__ == "__main__":
    main()
