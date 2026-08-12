"""Generate a multiview character mesh with Hunyuan3D-2mv on Modal L4."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import os
import sys

import modal


APP_NAME = "game-model-wiki-hunyuan3d-mv"
MODEL_ID = "tencent/Hunyuan3D-2mv"
MODEL_SUBFOLDER = "hunyuan3d-dit-v2-mv"
PAINT_MODEL_ID = "tencent/Hunyuan3D-2"
PAINT_SUBFOLDER = "hunyuan3d-paint-v2-0"
DELIGHT_SUBFOLDER = "hunyuan3d-delight-v2-0"
MODEL_ROOT = "/models/hy3dgen"
MODEL_LOCAL_DIR = f"{MODEL_ROOT}/{MODEL_ID}"
PAINT_MODEL_LOCAL_DIR = f"{MODEL_ROOT}/{PAINT_MODEL_ID}"
MODEL_VOLUME = "/models"

app = modal.App(APP_NAME)
model_cache = modal.Volume.from_name(
    "game-model-wiki-3d-model-cache",
    create_if_missing=True,
)

download_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("huggingface-hub==0.36.0")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

runtime_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04",
        add_python="3.11",
    )
    .apt_install(
        "build-essential",
        "git",
        "libgl1",
        "libglib2.0-0",
    )
    .run_commands(
        "python -m pip install --upgrade pip setuptools wheel",
        "python -m pip install --index-url https://download.pytorch.org/whl/cu124 "
        "torch==2.5.1 torchvision==0.20.1",
        "git clone --depth 1 https://github.com/Tencent-Hunyuan/Hunyuan3D-2.git /opt/Hunyuan3D-2",
        "python -m pip install 'numpy<2' Pillow==11.1.0 diffusers==0.32.2 "
        "transformers==4.48.3 accelerate==1.3.0 einops==0.8.0 "
        "omegaconf==2.3.0 trimesh==4.6.1 pymeshlab==2023.12.post2 "
        "pygltflib==1.16.3 xatlas==0.0.10 opencv-python-headless==4.10.0.84 "
        "huggingface-hub==0.36.0 safetensors==0.5.2 tqdm==4.67.1 "
        "ninja==1.11.1.3 pybind11==2.13.6 scikit-image==0.24.0",
        "python -m pip install --no-deps -e /opt/Hunyuan3D-2",
    )
    .env(
        {
            "PYTHONPATH": "/opt/Hunyuan3D-2",
            "HY3DGEN_MODELS": MODEL_ROOT,
            "HF_HOME": "/models/huggingface",
            "HF_XET_HIGH_PERFORMANCE": "1",
        }
    )
)

segmentation_image = (
    runtime_image
    .pip_install("onnxruntime==1.20.1", "rembg==2.0.61")
    .env({"U2NET_HOME": "/models/rembg"})
)

texture_image = (
    segmentation_image
    .run_commands(
        "cd /opt/Hunyuan3D-2/hy3dgen/texgen/custom_rasterizer && "
        "CC=gcc CXX=g++ CUDAHOSTCXX=g++ MAX_JOBS=4 TORCH_CUDA_ARCH_LIST=8.9 "
        "python setup.py install",
        "cd /opt/Hunyuan3D-2/hy3dgen/texgen/differentiable_renderer && "
        "CC=gcc CXX=g++ MAX_JOBS=4 python setup.py install",
    )
)


@app.function(
    image=download_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=40 * 60,
)
def prepare_model() -> str:
    """Download the multiview weights once without reserving a GPU."""
    from huggingface_hub import snapshot_download

    destination = Path(MODEL_LOCAL_DIR)
    marker = destination / MODEL_SUBFOLDER / "config.yaml"
    if not marker.exists():
        snapshot_download(
            repo_id=MODEL_ID,
            local_dir=destination,
            allow_patterns=[f"{MODEL_SUBFOLDER}/*"],
        )
        model_cache.commit()
    return str(destination)


@app.function(
    image=download_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=40 * 60,
)
def prepare_paint_model() -> str:
    """Download paint and delight weights once without reserving a GPU."""
    from huggingface_hub import snapshot_download

    destination = Path(PAINT_MODEL_LOCAL_DIR)
    paint_dir = destination / PAINT_SUBFOLDER
    delight_dir = destination / DELIGHT_SUBFOLDER
    if not paint_dir.exists() or not delight_dir.exists():
        snapshot_download(
            repo_id=PAINT_MODEL_ID,
            local_dir=destination,
            allow_patterns=[f"{PAINT_SUBFOLDER}/*", f"{DELIGHT_SUBFOLDER}/*"],
        )
        model_cache.commit()
    return str(destination)


@app.function(
    image=segmentation_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=20 * 60,
)
def prepare_background_model() -> str:
    """Cache the human segmentation model before reserving a GPU."""
    from rembg import new_session

    new_session("u2net_human_seg")
    model_cache.commit()
    return "/models/rembg"


@app.cls(
    image=segmentation_image,
    gpu="L4",
    volumes={MODEL_VOLUME: model_cache},
    timeout=30 * 60,
    scaledown_window=60,
)
class HunyuanMultiviewGenerator:
    @modal.enter()
    def load_model(self) -> None:
        sys.path.insert(0, "/opt/Hunyuan3D-2")
        import torch
        from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline

        marker = Path(MODEL_LOCAL_DIR, MODEL_SUBFOLDER, "config.yaml")
        if not marker.exists():
            raise RuntimeError("Hunyuan3D-2mv weights are missing; run prepare_model first")
        self.torch = torch
        from rembg import new_session

        self.background_session = new_session("u2net_human_seg")
        self.pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
            MODEL_ID,
            subfolder=MODEL_SUBFOLDER,
            variant="fp16",
        )

    def _generate(
        self,
        front_bytes: bytes,
        left_bytes: bytes,
        back_bytes: bytes,
        seed: int = 12345,
        steps: int = 50,
        octree_resolution: int = 380,
        num_chunks: int = 20000,
    ) -> bytes:
        from PIL import Image
        from rembg import remove

        if not 1 <= steps <= 100:
            raise ValueError("steps must be between 1 and 100")
        if not 128 <= octree_resolution <= 512:
            raise ValueError("octree_resolution must be between 128 and 512")

        sources = {"front": front_bytes, "left": left_bytes, "back": back_bytes}
        images = {}
        for name, payload in sources.items():
            source = Image.open(BytesIO(payload)).convert("RGBA")
            image = remove(
                source,
                session=self.background_session,
                post_process_mask=True,
            ).convert("RGBA")
            alpha_histogram = image.getchannel("A").histogram()
            foreground_ratio = 1.0 - sum(alpha_histogram[:8]) / (image.width * image.height)
            if not 0.03 <= foreground_ratio <= 0.55:
                raise RuntimeError(
                    f"Background removal failed for {name}: foreground ratio {foreground_ratio:.3f}"
                )
            print(f"{name} foreground ratio: {foreground_ratio:.3f}")
            images[name] = image
        with self.torch.inference_mode():
            mesh = self.pipeline(
                image=images,
                num_inference_steps=steps,
                octree_resolution=octree_resolution,
                num_chunks=num_chunks,
                generator=self.torch.manual_seed(seed),
                output_type="trimesh",
            )[0]
        exported = mesh.export(file_type="glb")
        if not isinstance(exported, (bytes, bytearray)):
            raise TypeError(f"Unexpected GLB export type: {type(exported)!r}")
        return bytes(exported)

    @modal.method()
    def generate(
        self,
        front_bytes: bytes,
        left_bytes: bytes,
        back_bytes: bytes,
        seed: int = 12345,
        steps: int = 50,
        octree_resolution: int = 380,
        num_chunks: int = 20000,
    ) -> bytes:
        return self._generate(
            front_bytes,
            left_bytes,
            back_bytes,
            seed=seed,
            steps=steps,
            octree_resolution=octree_resolution,
            num_chunks=num_chunks,
        )

    @modal.method()
    def generate_to_volume(
        self,
        front_bytes: bytes,
        left_bytes: bytes,
        back_bytes: bytes,
        output_key: str,
        seed: int = 12345,
        steps: int = 50,
        octree_resolution: int = 380,
        num_chunks: int = 20000,
    ) -> str:
        if not output_key or output_key.startswith("/") or ".." in Path(output_key).parts:
            raise ValueError("output_key must be a relative Modal Volume path")
        glb = self._generate(
            front_bytes,
            left_bytes,
            back_bytes,
            seed=seed,
            steps=steps,
            octree_resolution=octree_resolution,
            num_chunks=num_chunks,
        )
        destination = Path(MODEL_VOLUME) / output_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(glb)
        model_cache.commit()
        return output_key


@app.cls(
    image=texture_image,
    gpu="L4",
    volumes={MODEL_VOLUME: model_cache},
    timeout=40 * 60,
    scaledown_window=60,
)
class HunyuanPaintGenerator:
    @modal.enter()
    def load_model(self) -> None:
        sys.path.insert(0, "/opt/Hunyuan3D-2")
        from hy3dgen.texgen import Hunyuan3DPaintPipeline

        paint_dir = Path(PAINT_MODEL_LOCAL_DIR, PAINT_SUBFOLDER)
        delight_dir = Path(PAINT_MODEL_LOCAL_DIR, DELIGHT_SUBFOLDER)
        if not paint_dir.exists() or not delight_dir.exists():
            raise RuntimeError("Hunyuan3D Paint weights are missing; run prepare_paint_model first")
        self.pipeline = Hunyuan3DPaintPipeline.from_pretrained(
            PAINT_MODEL_ID,
            subfolder=PAINT_SUBFOLDER,
        )

    def _paint(
        self,
        mesh_bytes: bytes,
        front_bytes: bytes,
        left_bytes: bytes,
        back_bytes: bytes,
        target_faces: int = 120000,
    ) -> bytes:
        import trimesh
        from PIL import Image
        from hy3dgen.shapegen.postprocessors import FaceReducer

        if not 20000 <= target_faces <= 400000:
            raise ValueError("target_faces must be between 20000 and 400000")
        mesh = trimesh.load(
            file_obj=BytesIO(mesh_bytes),
            file_type="glb",
            force="mesh",
            process=False,
        )
        if len(mesh.faces) > target_faces:
            mesh = FaceReducer()(mesh, max_facenum=target_faces)
        images = [
            Image.open(BytesIO(front_bytes)).convert("RGBA"),
            Image.open(BytesIO(left_bytes)).convert("RGBA"),
            Image.open(BytesIO(back_bytes)).convert("RGBA"),
        ]
        textured_mesh = self.pipeline(mesh, image=images)
        exported = textured_mesh.export(file_type="glb")
        if not isinstance(exported, (bytes, bytearray)):
            raise TypeError(f"Unexpected textured GLB export type: {type(exported)!r}")
        return bytes(exported)

    @modal.method()
    def paint(
        self,
        mesh_bytes: bytes,
        front_bytes: bytes,
        left_bytes: bytes,
        back_bytes: bytes,
        target_faces: int = 120000,
    ) -> bytes:
        return self._paint(
            mesh_bytes,
            front_bytes,
            left_bytes,
            back_bytes,
            target_faces=target_faces,
        )

    @modal.method()
    def paint_to_volume(
        self,
        mesh_bytes: bytes,
        front_bytes: bytes,
        left_bytes: bytes,
        back_bytes: bytes,
        output_key: str,
        target_faces: int = 120000,
    ) -> str:
        if not output_key or output_key.startswith("/") or ".." in Path(output_key).parts:
            raise ValueError("output_key must be a relative Modal Volume path")
        glb = self._paint(
            mesh_bytes,
            front_bytes,
            left_bytes,
            back_bytes,
            target_faces=target_faces,
        )
        destination = Path(MODEL_VOLUME) / output_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(glb)
        model_cache.commit()
        return output_key


@app.local_entrypoint()
def main(
    front: str,
    left: str,
    back: str,
    output: str,
    seed: int = 12345,
    steps: int = 50,
    octree_resolution: int = 380,
    num_chunks: int = 20000,
    mesh: str = "",
    paint: bool = False,
    target_faces: int = 120000,
    volume_output_key: str = "",
) -> None:
    sources = {
        "front": Path(front).expanduser().resolve(),
        "left": Path(left).expanduser().resolve(),
        "back": Path(back).expanduser().resolve(),
    }
    for name, source in sources.items():
        if not source.is_file():
            raise FileNotFoundError(f"Missing {name} image: {source}")

    destination = Path(output).expanduser().resolve()
    if paint:
        mesh_source = Path(mesh).expanduser().resolve()
        if not mesh_source.is_file():
            raise FileNotFoundError(f"Missing source mesh: {mesh_source}")
        prepare_paint_model.remote()
        painter = HunyuanPaintGenerator()
        paint_args = (
            mesh_source.read_bytes(),
            sources["front"].read_bytes(),
            sources["left"].read_bytes(),
            sources["back"].read_bytes(),
        )
        if volume_output_key:
            remote_path = painter.paint_to_volume.remote(
                *paint_args,
                output_key=volume_output_key,
                target_faces=target_faces,
            )
            print(f"Saved textured candidate to Modal Volume: {remote_path}")
            return
        glb = painter.paint.remote(*paint_args, target_faces=target_faces)
    else:
        prepare_model.remote()
        prepare_background_model.remote()
        generator = HunyuanMultiviewGenerator()
        if volume_output_key:
            remote_path = generator.generate_to_volume.remote(
                sources["front"].read_bytes(),
                sources["left"].read_bytes(),
                sources["back"].read_bytes(),
                output_key=volume_output_key,
                seed=seed,
                steps=steps,
                octree_resolution=octree_resolution,
                num_chunks=num_chunks,
            )
            print(f"Saved candidate to Modal Volume: {remote_path}")
            return
        glb = generator.generate.remote(
            sources["front"].read_bytes(),
            sources["left"].read_bytes(),
            sources["back"].read_bytes(),
            seed=seed,
            steps=steps,
            octree_resolution=octree_resolution,
            num_chunks=num_chunks,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(glb)
    print(f"Saved {len(glb)} bytes to {destination}")
