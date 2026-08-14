"""Restore the original Assets tree from one or more Unity packages."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


def pathname(path: Path) -> PurePosixPath:
    raw = path.read_bytes().decode("utf-8", errors="strict")
    value = raw.split("\n", 1)[0].rstrip("\r")
    relative = PurePosixPath(value)
    if not relative.parts or relative.parts[0] != "Assets" or ".." in relative.parts:
        raise ValueError(f"Unsafe Unity pathname: {value!r}")
    return relative


def extract(package: Path, output: Path) -> dict[str, int | str]:
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    output.mkdir(parents=True)
    assets = folders = metas = records = 0
    with tempfile.TemporaryDirectory(prefix="unitypackage-") as temp_value:
        temp = Path(temp_value)
        subprocess.run(["tar", "-xf", str(package), "-C", str(temp)], check=True)
        for record in sorted(path for path in temp.iterdir() if path.is_dir()):
            pathname_file = record / "pathname"
            if not pathname_file.is_file():
                continue
            records += 1
            relative = pathname(pathname_file)
            destination = output.joinpath(*relative.parts)
            asset = record / "asset"
            meta = record / "asset.meta"
            if asset.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset, destination)
                assets += 1
            else:
                destination.mkdir(parents=True, exist_ok=True)
                folders += 1
            if meta.is_file():
                meta_destination = destination.with_name(destination.name + ".meta")
                meta_destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(meta, meta_destination)
                metas += 1
    return {
        "package": str(package),
        "output": str(output),
        "records": records,
        "assets": assets,
        "folders": folders,
        "metas": metas,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("packages", nargs="+", type=Path)
    parser.add_argument("--suffix", default=" - extracted")
    args = parser.parse_args()
    for package in args.packages:
        output = package.parent / f"{package.stem}{args.suffix}"
        print(extract(package.resolve(), output.resolve()))


if __name__ == "__main__":
    main()
