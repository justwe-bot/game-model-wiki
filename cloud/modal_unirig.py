"""Run UniRig skeleton prediction and skinning on Modal L4 GPUs."""

from __future__ import annotations

from pathlib import Path
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

import modal


APP_NAME = "game-model-wiki-unirig"
UNIRIG_REPO = "/opt/UniRig"
HF_HOME = "/models/huggingface"
MODEL_VOLUME = "/models"
MODEL_VOLUME_NAME = "game-model-wiki-3d-model-cache"

app = modal.App(APP_NAME)
model_cache = modal.Volume.from_name(
    MODEL_VOLUME_NAME,
    create_if_missing=True,
)

download_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("huggingface-hub==0.36.0")
    .env({"HF_HOME": HF_HOME, "HF_XET_HIGH_PERFORMANCE": "1"})
)

runtime_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04",
        add_python="3.11",
    )
    .apt_install(
        "build-essential",
        "git",
        "libegl1",
        "libgl1",
        "libglib2.0-0",
        "libgomp1",
        "libice6",
        "libsm6",
        "libx11-6",
        "libxfixes3",
        "libxi6",
        "libxkbcommon0",
        "libxrender1",
        "libxxf86vm1",
        "ninja-build",
    )
    .run_commands(
        "GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/VAST-AI-Research/UniRig.git /opt/UniRig",
        "python -m pip install --upgrade pip setuptools wheel packaging ninja",
        "python -m pip install --index-url https://download.pytorch.org/whl/cu124 "
        "torch==2.5.1 torchvision==0.20.1",
        "python -m pip install numpy==1.26.4 transformers==4.51.3 python-box einops "
        "omegaconf pytorch-lightning==2.5.2 lightning==2.5.2 addict timm "
        "fast-simplification bpy==4.2.0 trimesh open3d==0.18.0 pyrender "
        "huggingface-hub==0.36.0 wandb scipy tqdm pyyaml psutil",
        "python -m pip install spconv-cu120==2.3.6",
        "python -m pip install torch_scatter torch_cluster "
        "-f https://data.pyg.org/whl/torch-2.5.1+cu124.html --no-cache-dir",
        "MAX_JOBS=4 python -m pip install flash-attn==2.7.4.post1 --no-build-isolation",
    )
    .env(
        {
            "PYTHONPATH": UNIRIG_REPO,
            "HF_HOME": HF_HOME,
            "HF_XET_HIGH_PERFORMANCE": "1",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        }
    )
)


@app.function(
    image=download_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=40 * 60,
)
def prepare_models() -> list[str]:
    """Download UniRig checkpoints without reserving a GPU."""
    from huggingface_hub import hf_hub_download

    filenames = [
        "skeleton/articulation-xl_quantization_256/model.ckpt",
        "skin/articulation-xl/model.ckpt",
    ]
    results = [
        hf_hub_download(repo_id="VAST-AI/UniRig", filename=filename)
        for filename in filenames
    ]
    model_cache.commit()
    return results


def inspect_fbx_skeleton(path: Path) -> dict[str, object]:
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(path), ignore_leaf_bones=False, use_image_search=False)
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(f"Expected one armature, found {len(armatures)}")
    armature = armatures[0]
    world = armature.matrix_world
    bones = []
    for bone in armature.data.bones:
        head = world @ bone.head_local
        tail = world @ bone.tail_local
        bones.append(
            {
                "name": bone.name,
                "parent": bone.parent.name if bone.parent else None,
                "head": [float(value) for value in head],
                "tail": [float(value) for value in tail],
            }
        )
    return {"boneCount": len(bones), "bones": bones}


@app.function(image=runtime_image, timeout=10 * 60)
def inspect_skeleton(mesh_bytes: bytes) -> dict[str, object]:
    directory = Path(tempfile.mkdtemp(prefix="unirig-inspect-"))
    source = directory / "skeleton.fbx"
    source.write_bytes(mesh_bytes)
    return inspect_fbx_skeleton(source)


@app.function(
    image=runtime_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=40 * 60,
)
def simplify_mesh(mesh_bytes: bytes, output_key: str, target_faces: int) -> str:
    """Decimate a textured GLB while retaining its material and UV data."""
    import bpy

    if not 20_000 <= target_faces <= 1_000_000:
        raise ValueError("target_faces must be between 20000 and 1000000")
    directory = Path(tempfile.mkdtemp(prefix="unirig-simplify-"))
    source = directory / "input.glb"
    destination = directory / "simplified.glb"
    source.write_bytes(mesh_bytes)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    source_faces = sum(len(obj.data.polygons) for obj in mesh_objects)
    if source_faces <= 0:
        raise RuntimeError("Input GLB has no mesh faces")
    ratio = min(1.0, target_faces / source_faces)
    for obj in mesh_objects:
        if len(obj.data.polygons) < 100 or ratio >= 0.999:
            continue
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        modifier = obj.modifiers.new(name="WikiDecimate", type="DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = ratio
        modifier.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        obj.select_set(False)

    result_faces = sum(len(obj.data.polygons) for obj in mesh_objects)
    bpy.ops.export_scene.gltf(
        filepath=str(destination),
        export_format="GLB",
        export_apply=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_texcoords=True,
        export_normals=True,
        export_yup=True,
    )
    if not destination.is_file():
        raise RuntimeError("Blender did not produce a simplified GLB")
    volume_destination = Path(MODEL_VOLUME) / output_key
    volume_destination.parent.mkdir(parents=True, exist_ok=True)
    volume_destination.write_bytes(destination.read_bytes())
    model_cache.commit()
    print(f"Simplified mesh: {source_faces:,} -> {result_faces:,} faces at {output_key}")
    return output_key


@app.function(
    image=runtime_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=20 * 60,
)
def export_mixamo_fbx(mesh_bytes: bytes, output_key: str) -> str:
    """Convert a mesh-only GLB to a centered, texture-embedded Mixamo FBX."""
    import bpy

    directory = Path(tempfile.mkdtemp(prefix="mixamo-upload-"))
    source = directory / "input.glb"
    destination = directory / "character.fbx"
    source.write_bytes(mesh_bytes)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("Input GLB has no mesh objects")
    if any(obj.find_armature() is not None for obj in mesh_objects):
        raise RuntimeError("Mixamo upload source must not contain an existing armature")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]
    if len(mesh_objects) > 1:
        bpy.ops.object.join()
    character = bpy.context.view_layer.objects.active
    character.name = "POPBOT_Mixamo"

    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    world_vertices = [character.matrix_world @ vertex.co for vertex in character.data.vertices]
    minimum = [min(vertex[axis] for vertex in world_vertices) for axis in range(3)]
    maximum = [max(vertex[axis] for vertex in world_vertices) for axis in range(3)]
    character.location.x -= (minimum[0] + maximum[0]) * 0.5
    character.location.y -= (minimum[1] + maximum[1]) * 0.5
    character.location.z -= minimum[2]
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    bpy.ops.export_scene.fbx(
        filepath=str(destination),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        apply_unit_scale=True,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="COPY",
        embed_textures=True,
    )
    if not destination.is_file():
        raise RuntimeError("Blender did not produce the Mixamo FBX")
    volume_destination = Path(MODEL_VOLUME) / output_key
    volume_destination.parent.mkdir(parents=True, exist_ok=True)
    volume_destination.write_bytes(destination.read_bytes())
    model_cache.commit()
    print(f"Generated Mixamo upload FBX: {destination.stat().st_size:,} bytes at {output_key}")
    return output_key


def convert_mixamo_path(source: Path, destination: Path, clip_name: str) -> None:
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(source), ignore_leaf_bones=False, use_image_search=False)
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(f"Expected one Mixamo armature, found {len(armatures)}")

    armature = armatures[0]
    actions = list(bpy.data.actions)
    if not actions:
        raise RuntimeError("Mixamo FBX has no animation action")
    for index, action in enumerate(actions):
        action.name = clip_name if index == 0 else f"{clip_name}_{index + 1}"
    if armature.animation_data is None:
        armature.animation_data_create()
    armature.animation_data.action = actions[0]

    bpy.ops.export_scene.gltf(
        filepath=str(destination),
        export_format="GLB",
        export_apply=False,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_texcoords=True,
        export_normals=True,
        export_yup=True,
        export_skins=True,
        export_animations=True,
        export_animation_mode="ACTIVE_ACTIONS",
    )
    if not destination.is_file():
        raise RuntimeError("Blender did not produce the Mixamo GLB")


@app.function(
    image=runtime_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=20 * 60,
)
def convert_mixamo_fbx(fbx_bytes: bytes, output_key: str, clip_name: str = "Mixamo_Run") -> str:
    """Convert one Mixamo animation FBX to a self-contained animated GLB."""
    directory = Path(tempfile.mkdtemp(prefix="mixamo-result-"))
    source = directory / "mixamo.fbx"
    destination = directory / "mixamo.glb"
    source.write_bytes(fbx_bytes)
    convert_mixamo_path(source, destination, clip_name)

    volume_destination = Path(MODEL_VOLUME) / output_key
    volume_destination.parent.mkdir(parents=True, exist_ok=True)
    volume_destination.write_bytes(destination.read_bytes())
    model_cache.commit()
    print(f"Generated Mixamo GLB: {destination.stat().st_size:,} bytes at {output_key}")
    return output_key


@app.function(
    image=runtime_image,
    volumes={MODEL_VOLUME: model_cache},
    timeout=20 * 60,
)
def convert_mixamo_archive(archive_bytes: bytes, output_key: str) -> str:
    """Convert every FBX in a Mixamo archive into an animation-only GLB archive."""
    directory = Path(tempfile.mkdtemp(prefix="mixamo-pack-"))
    outputs: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as source_archive:
        members = sorted(
            member for member in source_archive.namelist()
            if not member.endswith("/") and member.lower().endswith(".fbx")
        )
        if not members:
            raise RuntimeError("Mixamo archive contains no FBX files")
        for index, member in enumerate(members):
            if Path(member).name != member:
                raise RuntimeError(f"Mixamo archive member must be a flat filename: {member}")
            slug = re.sub(r"[^a-z0-9]+", "-", Path(member).stem.lower()).strip("-")
            source = directory / f"{index:02d}-{slug}.fbx"
            destination = directory / f"{slug}.glb"
            source.write_bytes(source_archive.read(member))
            clip_name = "Mixamo_" + "_".join(part.title() for part in slug.split("-"))
            convert_mixamo_path(source, destination, clip_name)
            outputs.append((destination.name, destination.read_bytes()))

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as output_archive:
        for name, payload in outputs:
            output_archive.writestr(name, payload)
    volume_destination = Path(MODEL_VOLUME) / output_key
    volume_destination.parent.mkdir(parents=True, exist_ok=True)
    volume_destination.write_bytes(archive_buffer.getvalue())
    model_cache.commit()
    print(f"Generated {len(outputs)} Mixamo GLBs at {output_key}")
    return output_key


@app.cls(
    image=runtime_image,
    gpu="L4",
    volumes={MODEL_VOLUME: model_cache},
    timeout=40 * 60,
    scaledown_window=60,
)
class UniRigRunner:
    @modal.method()
    def predict_skeleton(
        self,
        mesh_bytes: bytes,
        output_key: str,
        seed: int = 12345,
        faces_target_count: int = 50_000,
    ) -> str:
        if not 10_000 <= faces_target_count <= 100_000:
            raise ValueError("faces_target_count must be between 10000 and 100000")
        directory = Path(tempfile.mkdtemp(prefix="unirig-skeleton-"))
        source = directory / "input.glb"
        destination = directory / "skeleton.fbx"
        source.write_bytes(mesh_bytes)
        env = os.environ.copy()
        subprocess.run(
            [
                "bash",
                "launch/inference/generate_skeleton.sh",
                "--input",
                str(source),
                "--output",
                str(destination),
                "--seed",
                str(seed),
                "--faces_target_count",
                str(faces_target_count),
            ],
            cwd=UNIRIG_REPO,
            env=env,
            check=True,
        )
        if not destination.is_file():
            raise RuntimeError("UniRig did not produce the skeleton FBX")
        fbx_bytes = destination.read_bytes()
        volume_destination = Path(MODEL_VOLUME) / output_key
        volume_destination.parent.mkdir(parents=True, exist_ok=True)
        volume_destination.write_bytes(fbx_bytes)
        model_cache.commit()
        print(f"Generated skeleton FBX: {len(fbx_bytes):,} bytes at {output_key}")
        return output_key

    @modal.method()
    def predict_rigged_mesh(
        self,
        mesh_bytes: bytes,
        output_key: str,
        seed: int = 12345,
        faces_target_count: int = 50_000,
    ) -> str:
        directory = Path(tempfile.mkdtemp(prefix="unirig-full-"))
        source = directory / "input.glb"
        skeleton = directory / "skeleton.fbx"
        skinned = directory / "skinned.fbx"
        destination = directory / "rigged.glb"
        source.write_bytes(mesh_bytes)
        common = ["--seed", str(seed), "--faces_target_count", str(faces_target_count)]
        subprocess.run(
            [
                "bash",
                "launch/inference/generate_skeleton.sh",
                "--input",
                str(source),
                "--output",
                str(skeleton),
                *common,
            ],
            cwd=UNIRIG_REPO,
            check=True,
        )
        subprocess.run(
            [
                "bash",
                "launch/inference/generate_skin.sh",
                "--input",
                str(skeleton),
                "--output",
                str(skinned),
                *common,
            ],
            cwd=UNIRIG_REPO,
            check=True,
        )
        subprocess.run(
            [
                "bash",
                "launch/inference/merge.sh",
                "--source",
                str(skinned),
                "--target",
                str(source),
                "--output",
                str(destination),
            ],
            cwd=UNIRIG_REPO,
            check=True,
        )
        if not destination.is_file():
            raise RuntimeError("UniRig did not produce the rigged GLB")
        volume_destination = Path(MODEL_VOLUME) / output_key
        volume_destination.parent.mkdir(parents=True, exist_ok=True)
        volume_destination.write_bytes(destination.read_bytes())
        model_cache.commit()
        print(f"Generated rigged GLB at {output_key}")
        return output_key


def download_volume_file(remote_path: str, destination: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "modal",
            "volume",
            "get",
            "--force",
            MODEL_VOLUME_NAME,
            remote_path,
            str(destination),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "modal",
            "volume",
            "rm",
            MODEL_VOLUME_NAME,
            remote_path,
        ],
        check=True,
    )


@app.local_entrypoint()
def main(
    input: str,
    output: str,
    stage: str = "skeleton",
    clip_name: str = "Mixamo_Run",
    seed: int = 12345,
    faces_target_count: int = 50_000,
    target_faces: int = 120_000,
) -> None:
    source = Path(input).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if stage not in {
        "skeleton",
        "inspect",
        "full",
        "simplify",
        "mixamo-upload",
        "mixamo-result",
        "mixamo-pack",
    }:
        raise ValueError(
            "stage must be skeleton, inspect, full, simplify, mixamo-upload, "
            "mixamo-result, or mixamo-pack"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    if stage == "inspect":
        result = inspect_skeleton.remote(source.read_bytes())
        destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved skeleton metadata to {destination}")
        return

    if stage == "simplify":
        output_key = f"unirig-outputs/{destination.name}"
        remote_path = simplify_mesh.remote(
            source.read_bytes(),
            output_key=output_key,
            target_faces=target_faces,
        )
        download_volume_file(remote_path, destination)
        print(f"Saved simplified mesh to {destination}")
        return

    if stage == "mixamo-upload":
        output_key = f"unirig-outputs/{destination.name}"
        remote_path = export_mixamo_fbx.remote(source.read_bytes(), output_key=output_key)
        download_volume_file(remote_path, destination)
        print(f"Saved Mixamo upload FBX to {destination}")
        return

    if stage == "mixamo-result":
        output_key = f"unirig-outputs/{destination.name}"
        remote_path = convert_mixamo_fbx.remote(
            source.read_bytes(),
            output_key=output_key,
            clip_name=clip_name,
        )
        download_volume_file(remote_path, destination)
        print(f"Saved Mixamo animated GLB to {destination}")
        return

    if stage == "mixamo-pack":
        output_key = f"unirig-outputs/{destination.name}"
        remote_path = convert_mixamo_archive.remote(
            source.read_bytes(),
            output_key=output_key,
        )
        download_volume_file(remote_path, destination)
        print(f"Saved Mixamo GLB archive to {destination}")
        return

    prepare_models.remote()
    output_key = f"unirig-outputs/{destination.name}"
    print(f"Remote output path: {output_key}")
    if stage == "skeleton":
        remote_path = UniRigRunner().predict_skeleton.remote(
            source.read_bytes(),
            output_key=output_key,
            seed=seed,
            faces_target_count=faces_target_count,
        )
        download_volume_file(remote_path, destination)
        print(f"Saved skeleton to {destination}")
    else:
        remote_path = UniRigRunner().predict_rigged_mesh.remote(
            source.read_bytes(),
            output_key=output_key,
            seed=seed,
            faces_target_count=faces_target_count,
        )
        download_volume_file(remote_path, destination)
        print(f"Saved rigged mesh to {destination}")
