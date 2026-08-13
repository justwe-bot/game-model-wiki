"""Remove invalid Unity texture references from published Ultimate Pack GLBs."""

from __future__ import annotations

import sys
from pathlib import Path

from merge_glb_animations import read_glb, strip_texture_references, write_glb


def main() -> None:
    for value in sys.argv[1:]:
        path = Path(value)
        document, binary = read_glb(path)
        strip_texture_references(document)
        write_glb(path, document, binary)
        print(path)


if __name__ == "__main__":
    main()
