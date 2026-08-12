from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts.export_mixamo_upload import export_archive


class ExportMixamoUploadTests(unittest.TestCase):
    def test_cli_preserves_source_heading_by_default(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["export_mixamo_upload.py", "source.glb", "upload.zip"],
            ),
            patch("scripts.export_mixamo_upload.export_archive") as export_archive_mock,
        ):
            from scripts import export_mixamo_upload

            export_mixamo_upload.main()

        self.assertEqual(export_archive_mock.call_args.args[3], 0.0)

    def test_exports_position_only_y_up_proxy(self) -> None:
        document = {
            "nodes": [{"mesh": 0}],
            "meshes": [
                {
                    "primitives": [
                        {
                            "attributes": {"POSITION": 0},
                            "indices": 1,
                        }
                    ]
                }
            ],
        }
        positions = [
            [-1.0, -1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ]
        indices = [[0], [1], [2]]

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "proxy.zip"
            with (
                patch("scripts.export_mixamo_upload.read_glb", return_value=(document, bytearray())),
                patch(
                    "scripts.export_mixamo_upload.read_accessor",
                    side_effect=lambda _document, _binary, accessor: positions
                    if accessor == 0
                    else indices,
                ),
            ):
                export_archive(
                    Path("proxy.glb"),
                    output,
                    target_height=2.0,
                    yaw_degrees=0.0,
                    asset_name="clean_proxy",
                    source_up="y",
                )

            with zipfile.ZipFile(output) as archive:
                obj = archive.read("clean_proxy.obj").decode("ascii")
                metadata = json.loads(archive.read("clean_proxy-export.json"))

        vertices = [
            [float(value) for value in line.split()[1:]]
            for line in obj.splitlines()
            if line.startswith("v ")
        ]
        self.assertEqual([vertex[1] for vertex in vertices], [0.0, 1.0, 2.0])
        self.assertIn("f 1 2 3", obj)
        self.assertEqual(metadata["sourceUp"], "y")
        self.assertEqual(metadata["targetHeightMeters"], 2.0)


if __name__ == "__main__":
    unittest.main()
