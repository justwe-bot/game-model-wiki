"""Run high-fidelity TRELLIS.2 image-to-GLB generation on Modal H100.

The container image and model weights are cached independently. Weight downloads
run without a GPU; the H100 is reserved only while loading and running TRELLIS.2.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
import tempfile

import modal


APP_NAME = "game-model-wiki-trellis2"
MODEL_ID = "microsoft/TRELLIS.2-4B"
MODEL_DIR = "/models/trellis2-4b"
MODEL_VOLUME = "/models"
HF_HOME = "/models/huggingface"

app = modal.App(APP_NAME)
model_cache = modal.Volume.from_name(
    "game-model-wiki-3d-model-cache",
    create_if_missing=True,
)
hf_secret = modal.Secret.from_name("game-model-wiki-huggingface")

download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface-hub==0.36.0")
    .env(
        {
            "HF_HOME": HF_HOME,
            "HF_XET_HIGH_PERFORMANCE": "1",
        }
    )
)

# These prebuilt CUDA 13 / Python 3.12 wheels are the versions used by the
# official Hugging Face Space. They avoid compiling several large CUDA
# extensions every time this app definition changes.
runtime_image = (
    modal.Image.from_registry(
        "nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04",
        add_python="3.12",
    )
    .apt_install(
        "build-essential",
        "git",
        "libgl1",
        "libglib2.0-0",
        "libjpeg-dev",
    )
    .run_commands(
        "git clone --depth 1 https://huggingface.co/spaces/microsoft/TRELLIS.2 /opt/TRELLIS.2",
        "python -m pip install --upgrade pip setuptools wheel",
        "python -m pip install --extra-index-url https://download.pytorch.org/whl/cu130 "
        "torch==2.11.0 torchvision==0.26.0 triton==3.6.0",
        "python -m pip install Pillow==12.0.0 imageio==2.37.2 imageio-ffmpeg==0.6.0 "
        "tqdm==4.67.1 easydict==1.13 opencv-python-headless==4.12.0.88 "
        "trimesh==4.10.1 transformers==4.57.3 zstandard==0.25.0 kornia==0.8.2 "
        "timm==1.0.22 huggingface-hub==0.36.0 plyfile",
        "python -m pip install "
        "git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8",
        "python -m pip install "
        "https://github.com/adithyaxx/flash-attention/releases/download/v2.8.3/"
        "flash_attn-2.8.3+cu13torch2.11cxx11abiTRUE-cp312-cp312-linux_x86_64.whl",
        "python -m pip install "
        "https://github.com/LDYang694/Storages/releases/download/rtxpro6000/"
        "flex_gemm-1.0.0%2Btorch2.11.0.cu130-cp312-cp312-linux_x86_64.whl",
        "python -m pip install "
        "https://github.com/LDYang694/Storages/releases/download/rtxpro6000/"
        "nvdiffrast-0.4.0%2Btorch2.11.0.cu130-cp312-cp312-linux_x86_64.whl",
        "python -m pip install "
        "https://github.com/LDYang694/Storages/releases/download/rtxpro6000/"
        "nvdiffrec_render-0.0.0%2Btorch2.11.0.cu130-cp312-cp312-linux_x86_64.whl",
        "python -m pip install "
        "https://github.com/LDYang694/Storages/releases/download/rtxpro6000/"
        "cumesh-0.0.1%2Btorch2.11.0.cu130-cp312-cp312-linux_x86_64.whl",
        "python -m pip install "
        "https://github.com/LDYang694/Storages/releases/download/rtxpro6000/"
        "o_voxel-0.0.1%2Btorch2.11.0.cu130-cp312-cp312-linux_x86_64.whl",
    )
    .env(
        {
            "PYTHONPATH": "/opt/TRELLIS.2",
            "HF_HOME": HF_HOME,
            "HF_XET_HIGH_PERFORMANCE": "1",
            "OPENCV_IO_ENABLE_OPENEXR": "1",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
            "ATTN_BACKEND": "flash_attn",
            "SPCONV_ALGO": "native",
        }
    )
)


@app.function(
    image=download_image,
    secrets=[hf_secret],
    volumes={MODEL_VOLUME: model_cache},
    timeout=60 * 60,
)
def prepare_models() -> str:
    """Download all model assets once without reserving a GPU."""
    from huggingface_hub import snapshot_download

    destination = Path(MODEL_DIR)
    marker = destination / "pipeline.json"
    if not marker.exists():
        snapshot_download(repo_id=MODEL_ID, local_dir=destination)
        model_cache.commit()

    # TRELLIS.2's pipeline references these external repositories at runtime.
    dependencies = (
        "microsoft/TRELLIS-image-large",
        "facebook/dinov3-vitl16-pretrain-lvd1689m",
        "briaai/RMBG-2.0",
    )
    for repo_id in dependencies:
        snapshot_download(repo_id=repo_id)
        model_cache.commit()

    return str(destination)


@app.function(
    image=download_image,
    secrets=[hf_secret],
    timeout=2 * 60,
)
def check_hf_access() -> dict[str, object]:
    """Report the token identity and gated-model access without exposing it."""
    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    api = HfApi()
    identity = api.whoami()
    auth = identity.get("auth") or {}
    access_token = auth.get("accessToken") or {}
    result: dict[str, object] = {
        "name": identity.get("name"),
        "token_role": access_token.get("role"),
        "token_display_name": access_token.get("displayName"),
        "models": {},
    }

    models = result["models"]
    assert isinstance(models, dict)
    for repo_id in (
        "facebook/dinov3-vitl16-pretrain-lvd1689m",
        "briaai/RMBG-2.0",
    ):
        try:
            api.model_info(repo_id)
            models[repo_id] = {"accessible": True}
        except HfHubHTTPError as error:
            models[repo_id] = {
                "accessible": False,
                "status": error.response.status_code if error.response else None,
            }

    print(result)
    return result


@app.cls(
    image=runtime_image,
    gpu="H100",
    secrets=[hf_secret],
    volumes={MODEL_VOLUME: model_cache},
    timeout=40 * 60,
    scaledown_window=60,
)
class Trellis2Generator:
    @modal.enter()
    def load_model(self) -> None:
        sys.path.insert(0, "/opt/TRELLIS.2")
        import torch
        from trellis2.pipelines import Trellis2ImageTo3DPipeline

        if not Path(MODEL_DIR, "pipeline.json").exists():
            raise RuntimeError("TRELLIS.2 weights are missing; run prepare_models first")
        self.torch = torch
        self.pipeline = Trellis2ImageTo3DPipeline.from_pretrained(MODEL_DIR)
        self.pipeline.cuda()

    @modal.method()
    def generate(
        self,
        image_bytes: bytes,
        seed: int = 12345,
        resolution: int = 1024,
        decimation_target: int = 500_000,
        texture_size: int = 2048,
    ) -> bytes:
        from PIL import Image
        import o_voxel

        pipeline_types = {
            512: "512",
            1024: "1024_cascade",
            1536: "1536_cascade",
        }
        if resolution not in pipeline_types:
            raise ValueError("resolution must be 512, 1024, or 1536")
        if not 100_000 <= decimation_target <= 1_000_000:
            raise ValueError("decimation_target must be between 100000 and 1000000")
        if texture_size not in {1024, 2048, 4096}:
            raise ValueError("texture_size must be 1024, 2048, or 4096")

        image = Image.open(BytesIO(image_bytes)).convert("RGBA")
        with self.torch.inference_mode():
            mesh = self.pipeline.run(
                image,
                seed=seed,
                pipeline_type=pipeline_types[resolution],
            )[0]
            mesh.simplify(16_777_216)
            glb = o_voxel.postprocess.to_glb(
                vertices=mesh.vertices,
                faces=mesh.faces,
                attr_volume=mesh.attrs,
                coords=mesh.coords,
                attr_layout=self.pipeline.pbr_attr_layout,
                grid_size=resolution,
                aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
                decimation_target=decimation_target,
                texture_size=texture_size,
                remesh=True,
                remesh_band=1,
                remesh_project=0,
                use_tqdm=True,
            )

        with tempfile.TemporaryDirectory() as temporary_dir:
            destination = Path(temporary_dir, "output.glb")
            glb.export(str(destination), extension_webp=True)
            output = destination.read_bytes()
        self.torch.cuda.empty_cache()
        return output


@app.local_entrypoint()
def main(
    input: str,
    output: str,
    seed: int = 12345,
    resolution: int = 1024,
    decimation_target: int = 500_000,
    texture_size: int = 2048,
) -> None:
    source = Path(input).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    prepare_models.remote()
    glb = Trellis2Generator().generate.remote(
        source.read_bytes(),
        seed=seed,
        resolution=resolution,
        decimation_target=decimation_target,
        texture_size=texture_size,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(glb)
    print(f"Saved {len(glb)} bytes to {destination}")
