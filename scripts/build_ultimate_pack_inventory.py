"""Create a redistributable Ultimate Pack inventory from a local full catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ENTRY_FIELDS = (
    "slug",
    "name",
    "nameEn",
    "package",
    "packageSlug",
    "kind",
    "tier",
    "category",
    "status",
    "summary",
    "modelSource",
    "prefabSource",
    "sourceTriangles",
    "lowTriangles",
    "animationCount",
)


def action_key(actions: list[dict]) -> str:
    return json.dumps(actions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_inventory(source: Path) -> dict:
    catalog = json.loads(source.read_text(encoding="utf-8"))
    action_ids: dict[str, str] = {}
    action_sets: dict[str, list[dict]] = {}
    entries = []

    for item in catalog:
        actions = [
            {
                key: action[key]
                for key in ("name", "clip", "kind", "sourceHasKeyframes", "variants")
                if key in action
            }
            for action in item.get("animations", [])
        ]
        key = action_key(actions)
        action_set = action_ids.get(key)
        if action_set is None:
            action_set = f"actions-{len(action_sets) + 1}"
            action_ids[key] = action_set
            action_sets[action_set] = actions

        entry = {field: item[field] for field in ENTRY_FIELDS if field in item}
        entry.update({
            "animationSet": action_set,
            "available": False,
            "unavailableMessage": "公开仓库未包含购买的模型文件；请在本机运行 Ultimate Pack 导入工具生成预览资源。",
        })
        entries.append(entry)

    return {
        "formatVersion": 1,
        "metadataOnly": True,
        "actionSets": action_sets,
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Path to the local full catalog JSON")
    parser.add_argument("output", type=Path, help="Path to the public inventory JSON")
    args = parser.parse_args()

    inventory = build_inventory(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(inventory, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
