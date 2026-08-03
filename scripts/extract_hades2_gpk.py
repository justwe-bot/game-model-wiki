#!/usr/bin/env python3
"""Inspect and optionally extract blocks from a Hades II Granny packfile.

The emitted blocks are not standalone GR2 files. They still depend on the
matching SDB string database and are intended only for format research.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_.-" else "_" for character in value).strip("._")


def find_entries(data: bytes) -> list[tuple[str, bytes]]:
    version, declared_count = struct.unpack_from("<II", data)
    if version != 1:
        raise ValueError(f"Unsupported GPK version {version}")

    entries: list[tuple[str, bytes]] = []
    cursor = 8
    for _ in range(declared_count):
        name_length = data[cursor]
        cursor += 1
        name = data[cursor : cursor + name_length].decode("ascii")
        cursor += name_length
        payload_length = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        payload = data[cursor : cursor + payload_length]
        if len(payload) != payload_length:
            raise ValueError(f"Truncated payload for {name}")
        cursor += payload_length
        entries.append((name, payload))

    if cursor != len(data):
        raise ValueError(f"Unexpected trailing bytes: {len(data) - cursor}")
    return entries


def extract_gpk(source: Path, output_dir: Path | None) -> list[Path]:
    data = source.read_bytes()
    if len(data) < 8:
        raise ValueError(f"GPK is too small: {source}")

    version, declared_count = struct.unpack_from("<II", data)
    if version != 1:
        raise ValueError(f"Unsupported GPK version {version}: {source}")

    entries = find_entries(data)

    if len(entries) != declared_count:
        raise ValueError(
            f"Expected {declared_count} entries in {source.name}, found {len(entries)}"
        )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, payload in entries:
        print(f"{name}: {len(payload):,} bytes")
        if output_dir is not None:
            target = output_dir / f"{safe_name(name)}.gr2pack"
            target.write_bytes(payload)
            written.append(target)

    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path, nargs="?", help="Optional research output directory")
    args = parser.parse_args()
    extract_gpk(args.source, args.output_dir)


if __name__ == "__main__":
    main()
