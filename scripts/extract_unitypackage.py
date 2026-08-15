"""Extract a Unity .unitypackage into its original Assets tree."""

from __future__ import annotations

import argparse
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath


def safe_asset_path(value: str) -> Path:
    normalized = re.sub(r"\r?\n00\Z", "", value).strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe Unity asset path: {value!r}")
    allowed_roots = {"Assets", "Packages", "ProjectSettings"}
    if not path.parts or path.parts[0] not in allowed_roots:
        raise ValueError(f"Unity asset path has an unsupported root: {value!r}")
    return Path(*path.parts)


def extract_unitypackage(package: Path, output: Path) -> dict[str, int]:
    output.mkdir(parents=True, exist_ok=True)
    files = 0
    metadata = 0
    directories = 0

    with tarfile.open(package, "r:gz") as archive:
        members = {member.name.rstrip("/"): member for member in archive.getmembers()}
        roots = sorted({name.split("/", 1)[0] for name in members if "/" in name})
        for root in roots:
            pathname_member = members.get(f"{root}/pathname")
            if pathname_member is None:
                continue
            pathname_file = archive.extractfile(pathname_member)
            if pathname_file is None:
                continue
            relative = safe_asset_path(pathname_file.read().decode("utf-8-sig"))
            target = output / relative
            asset_member = members.get(f"{root}/asset")
            meta_member = members.get(f"{root}/asset.meta")

            if asset_member is None:
                target.mkdir(parents=True, exist_ok=True)
                directories += 1
            else:
                source = archive.extractfile(asset_member)
                if source is None:
                    raise ValueError(f"Unable to read asset for {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                files += 1

            if meta_member is not None:
                source = archive.extractfile(meta_member)
                if source is None:
                    raise ValueError(f"Unable to read metadata for {relative}")
                meta_target = Path(f"{target}.meta")
                meta_target.parent.mkdir(parents=True, exist_ok=True)
                with meta_target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                metadata += 1

    return {"files": files, "metadata": metadata, "directories": directories}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = extract_unitypackage(args.package.resolve(), args.output.resolve())
    print(
        f"Extracted {result['files']} files, {result['metadata']} metadata files, "
        f"and {result['directories']} directories to {args.output.resolve()}"
    )


if __name__ == "__main__":
    main()
