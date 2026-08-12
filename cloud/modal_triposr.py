"""Run TripoSR image-to-GLB generation on a Modal L4 GPU.

The container image is cached by Modal. Model weights and rembg assets are kept
in a persistent Volume so scale-to-zero cold starts do not download them again.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import os
import sys

import modal


APP_NAME = "game-model-wiki-triposr"
MODEL_ID = "stabilityai/TripoSR"
MODEL_DIR = "/models/triposr"
MODEL_VOLUME = "/models"

app = modal.App(APP_NAME)
model_cache = modal.Volume.from_name(
    "game-model-wiki-3d-model-cache",
    create_if_missing=True,
)

download_image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install("huggingface-hub==0.36.0")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

runtime_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04",
        add_python="3.10",
    )
    .apt_install(
        "build-essential",
        "git",
        "libgl1",
        "libglib2.0-0",
    )
    .run_commands(
        "python -m pip install --upgrade pip setuptools wheel",
        "python -m pip install --index-url https://download.pytorch.org/whl/cu121 "
        "torch==2.1.2 torchvision==0.16.2",
        "git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git /opt/TripoSR",
        "python -m pip install 'numpy<2' omegaconf==2.3.0 Pillow==10.1.0 "
        "einops==0.7.0 transformers==4.35.0 trimesh==4.0.5 "
        "rembg==2.0.57 onnxruntime==1.17.3 huggingface-hub==0.17.3 "
        "imageio[ffmpeg] xatlas==0.0.9 moderngl==5.10.0",
        "CC=gcc CXX=g++ CUDAHOSTCXX=g++ TORCH_CUDA_ARCH_LIST=8.9 "
        "python -m pip install "
        "git+https://github.com/tatsy/torchmcubes.git",
    )
    .env(
        {
            "PYTHONPATH": "/opt/TripoSR",
            "HF_HOME": "/models/huggingface",
            "U2NET_HOME": "/models/rembg",
        }
    )
)


@app.function(
    image=download_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=30 * 60,
)
def prepare_model() -> str:
    """Download immutable model weights once without reserving a GPU."""
    from huggingface_hub import snapshot_download

    destination = Path(MODEL_DIR)
    if not (destination / "model.ckpt").exists():
        snapshot_download(
            repo_id=MODEL_ID,
            local_dir=destination,
            allow_patterns=["config.yaml", "model.ckpt"],
        )
        model_cache.commit()
    return str(destination)


@app.cls(
    image=runtime_image,
    gpu="L4",
    volumes={MODEL_VOLUME: model_cache},
    timeout=20 * 60,
    scaledown_window=60,
)
class TripoSRGenerator:
    @modal.enter()
    def load_model(self) -> None:
        sys.path.insert(0, "/opt/TripoSR")
        import torch
        from tsr.system import TSR

        if not Path(MODEL_DIR, "model.ckpt").exists():
            raise RuntimeError("TripoSR weights are missing; run prepare_model first")
        self.torch = torch
        self.model = TSR.from_pretrained(
            MODEL_DIR,
            config_name="config.yaml",
            weight_name="model.ckpt",
        )
        self.model.renderer.set_chunk_size(8192)
        self.model.to("cuda:0")

    @modal.method()
    def generate(
        self,
        image_bytes: bytes,
        foreground_ratio: float = 0.85,
        resolution: int = 256,
        remove_background: bool = True,
    ) -> bytes:
        import numpy as np
        import rembg
        from PIL import Image
        from tsr.utils import remove_background as cutout_background
        from tsr.utils import resize_foreground

        if resolution not in {128, 192, 256, 320, 384}:
            raise ValueError("resolution must be one of 128, 192, 256, 320, or 384")
        image = Image.open(BytesIO(image_bytes)).convert("RGBA")
        if remove_background:
            session = rembg.new_session()
            image = cutout_background(image, session)
            image = resize_foreground(image, foreground_ratio)
            pixels = np.asarray(image).astype(np.float32) / 255.0
            pixels = pixels[:, :, :3] * pixels[:, :, 3:4] + (1.0 - pixels[:, :, 3:4]) * 0.5
            image = Image.fromarray((pixels * 255.0).astype(np.uint8))
            model_cache.commit()
        else:
            image = image.convert("RGB")

        with self.torch.inference_mode():
            scene_codes = self.model([image], device="cuda:0")
            mesh = self.model.extract_mesh(
                scene_codes,
                True,
                resolution=resolution,
            )[0]
        exported = mesh.export(file_type="glb")
        if not isinstance(exported, (bytes, bytearray)):
            raise TypeError(f"Unexpected GLB export type: {type(exported)!r}")
        return bytes(exported)


@app.local_entrypoint()
def main(
    input: str,
    output: str,
    resolution: int = 256,
    foreground_ratio: float = 0.85,
    keep_background: bool = False,
) -> None:
    source = Path(input).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    prepare_model.remote()
    glb = TripoSRGenerator().generate.remote(
        source.read_bytes(),
        foreground_ratio=foreground_ratio,
        resolution=resolution,
        remove_background=not keep_background,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(glb)
    print(f"Saved {len(glb)} bytes to {destination}")
