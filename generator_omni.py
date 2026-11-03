# SPDX-License-Identifier: Apache-2.0
"""FastH3 Trim / OmniRef backends for the local DashScope server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from local_api.mapping import (
    DEFAULT_MODEL_PATH,
    OMNI_GRID_POINTS,
    OMNI_VSA_SPARSITY,
    TRIM_GRID_POINTS,
    TRIM_VSA_SPARSITY,
    TRIM_VSA_TILE_SIZE,
    GenerationError,
    PreparedGeneration,
    infer_quant,
)


def build_generator_config(
    *,
    model_path: str,
    quant: str,
    num_gpus: int,
    vsa_sparsity: float,
    offload: bool,
    mode: str = "trim",
) -> Any:
    from fastvideo.api.schema import (
        CompileConfig,
        EngineConfig,
        GeneratorConfig,
        OffloadConfig,
        ParallelismConfig,
        PipelineSelection,
        QuantizationConfig,
    )

    resolved = infer_quant(model_path, quant)
    if num_gpus < 1:
        raise ValueError("num_gpus must be at least 1")
    if not 0.0 <= vsa_sparsity < 1.0:
        raise ValueError("vsa_sparsity must be in [0, 1)")

    quantization = None
    if resolved == "nvfp4":
        quantization = QuantizationConfig(transformer_quant="NVFP4", layer_profile="h3_dit_vsa")
    elif resolved == "fp8":
        quantization = QuantizationConfig(transformer_quant="FP8")

    if mode == "ref2va":
        # L20 (Ada sm_89, ~46GB): closest to RTX 4090 OmniRef FP8 profile.
        offload_config = OffloadConfig(
            dit=False,
            dit_layerwise=True,
            text_encoder=True,
            image_encoder=False,
            vae=False,
            pin_cpu_memory=True,
            lazy_module_load=None,
        )
        pipeline = PipelineSelection(
            workload_type="i2v",
            components={"override_pipeline_cls_name": "MiniMaxH3Ref2VAModularPipeline"},
            vae_tiling=True,
            experimental={
                "attention_backend": "VIDEO_SPARSE_ATTN_H3",
                "video_decode_backend": "h3-vae",
                "h3_sequential_load": True,
                "inference_torch_compile": False,
                "vae_parallel_decode": False,
            },
        )
    else:
        if offload:
            offload_config = OffloadConfig(
                dit=False,
                dit_layerwise=False,
                text_encoder=True,
                image_encoder=False,
                vae=True,
                pin_cpu_memory=True,
                lazy_module_load=None,
            )
        else:
            offload_config = OffloadConfig(
                dit=False,
                dit_layerwise=False,
                text_encoder=False,
                image_encoder=False,
                vae=False,
                pin_cpu_memory=False,
                lazy_module_load=False,
            )
        pipeline = PipelineSelection(
            workload_type="t2v",
            vae_tiling=True,
            experimental={
                "attention_backend": "VIDEO_SPARSE_ATTN_H3",
                "VSA_sparsity": vsa_sparsity,
                "VSA_tile_size": TRIM_VSA_TILE_SIZE,
                "h3_sequential_load": False,
                "inference_torch_compile": False,
                "vae_parallel_decode": False,
                "video_decode_backend": "h3-vae",
            },
        )

    return GeneratorConfig(
        model_path=model_path,
        engine=EngineConfig(
            num_gpus=num_gpus,
            execution_backend="mp",
            use_fsdp_inference=False,
            quantization=quantization,
            parallelism=ParallelismConfig(tp_size=1, sp_size=num_gpus),
            offload=offload_config,
            compile=CompileConfig(enabled=False, vae_enabled=False),
        ),
        pipeline=pipeline,
    )


def generation_request_raw(prepared: PreparedGeneration, mode: str = "trim") -> dict[str, Any]:
    if mode == "ref2va":
        if prepared.steps != OMNI_GRID_POINTS:
            raise GenerationError(
                f"FastH3 OmniRef requires {OMNI_GRID_POINTS} steps, got {prepared.steps}",
                "InvalidParameter",
            )
        if not prepared.references:
            raise GenerationError("OmniRef requires at least one reference", "InvalidParameter")
        references = [
            {"source": str(path), "media_type": media_type}
            for media_type, path in prepared.references
        ]
        return {
            "prompt": prepared.prompt,
            "negative_prompt": "",
            "inputs": {"references": references},
            "sampling": {
                "seed": prepared.seed,
                "height": prepared.height,
                "width": prepared.width,
                "num_frames": prepared.num_frames,
                "fps": 24,
                "num_inference_steps": prepared.steps,
                "num_videos_per_prompt": 1,
                "guidance_scale": 1.0,
                "batch_cfg": False,
            },
            "output": {
                "output_path": str(prepared.output_path),
                "save_video": True,
                "return_frames": False,
            },
        }

    if prepared.steps != TRIM_GRID_POINTS:
        raise GenerationError(
            f"FastH3 Trim requires {TRIM_GRID_POINTS} sigma-grid points, got {prepared.steps}",
            "InvalidParameter",
        )
    raw: dict[str, Any] = {
        "prompt": prepared.prompt,
        "negative_prompt": "",
        "sampling": {
            "seed": prepared.seed,
            "height": prepared.height,
            "width": prepared.width,
            "num_frames": prepared.num_frames,
            "fps": 24,
            "num_inference_steps": prepared.steps,
            "num_videos_per_prompt": 1,
            "guidance_scale": 1.0,
            "batch_cfg": False,
        },
        "output": {
            "output_path": str(prepared.output_path),
            "save_video": True,
            "return_frames": False,
        },
    }
    inputs: dict[str, Any] = {}
    if prepared.first_frame is not None:
        inputs["image_path"] = str(prepared.first_frame)
    if prepared.last_frame is not None:
        inputs["last_image"] = _load_pil(prepared.last_frame)
    if inputs:
        raw["inputs"] = inputs
    return raw


def build_generation_request(prepared: PreparedGeneration, mode: str = "trim") -> Any:
    from fastvideo.api.parser import parse_config
    from fastvideo.api.schema import GenerationRequest

    return parse_config(GenerationRequest, generation_request_raw(prepared, mode=mode))


def _load_pil(path: Path) -> Any:
    from PIL import Image

    with Image.open(path) as image:
        return image.copy()


class FastVideoBackend:
    """One long-lived VideoGenerator for Trim or OmniRef."""

    def __init__(
        self,
        *,
        model_path: str = DEFAULT_MODEL_PATH,
        quant: str = "auto",
        num_gpus: int = 1,
        vsa_sparsity: float | None = None,
        offload: bool = False,
        mode: str = "trim",
    ) -> None:
        self.mode = mode if mode in ("trim", "ref2va") else "trim"
        self.model_path = model_path
        self.quant = infer_quant(model_path, quant)
        self.num_gpus = num_gpus
        if vsa_sparsity is None:
            self.vsa_sparsity = OMNI_VSA_SPARSITY if self.mode == "ref2va" else TRIM_VSA_SPARSITY
        else:
            self.vsa_sparsity = vsa_sparsity
        self.offload = offload
        self._generator: Any = None

    def load(self) -> None:
        if self._generator is not None:
            return
        _require_model_path(self.model_path)
        if self.mode == "ref2va":
            _apply_omniref_env()
        from fastvideo.entrypoints.video_generator import VideoGenerator

        config = build_generator_config(
            model_path=self.model_path,
            quant=self.quant,
            num_gpus=self.num_gpus,
            vsa_sparsity=self.vsa_sparsity,
            offload=self.offload,
            mode=self.mode,
        )
        self._generator = VideoGenerator.from_config(config)

    def generate(self, prepared: PreparedGeneration) -> Path:
        if self._generator is None:
            self.load()
        request = build_generation_request(prepared, mode=self.mode)
        label = "OmniRef" if self.mode == "ref2va" else "Trim"
        try:
            result = self._generator.generate(request)
        except GenerationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(f"FastH3 {label} generation failed: {exc}", "InternalError") from exc
        return _video_path(result, prepared.output_path, label)


FastVideoTrimBackend = FastVideoBackend


def _apply_omniref_env() -> None:
    import os

    defaults = {
        "FASTVIDEO_ATTENTION_BACKEND": "VIDEO_SPARSE_ATTN_H3",
        "FASTVIDEO_H3_VSA_FP4": "0",
        "FASTVIDEO_H3_VSA_FINE_ATTENTION": "emulator-bf16",
        "FASTVIDEO_MINIMAX_H3_FUSIONS": "all",
        "FASTVIDEO_VSA_TRITON": "1",
        "FASTVIDEO_VSA_PTX_BLACKWELL": "0",
        "FASTVIDEO_FA4": "0",
        "FASTVIDEO_LAYERWISE_OFFLOAD_BUFFERS": "1",
        "FASTVIDEO_LAYERWISE_RESIDENT_BLOCKS": "16",
        "FASTVIDEO_H3_FFN_CHUNK_TOKENS": "16384",
        "FASTVIDEO_H3_PINNED_SWAP": "0",
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        "FASTVIDEO_H3_VAE_TILE_BATCH": "4",
    }
    model = os.environ.get("FASTH3_MODEL", "")
    adaln = os.environ.get("FASTVIDEO_H3_ADALN_TABLE", "")
    if not adaln:
        for candidate in (
            Path(model) / "adaln_table.pt",
            Path("/opt/fastvideo/models/FastH3-Omni-FP8/adaln_table.pt"),
        ):
            if candidate.is_file():
                defaults["FASTVIDEO_H3_ADALN_TABLE"] = str(candidate)
                break
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _require_model_path(model: str) -> None:
    path = Path(model).expanduser()
    if path.exists():
        if not path.is_dir():
            raise SystemExit(f"model path is not a directory: {path}")
        return
    looks_local = model.startswith(("/", ".", "~")) or path.is_absolute()
    if looks_local:
        raise SystemExit(f"model directory not found: {path}")


def _video_path(result: Any, fallback: Path, label: str) -> Path:
    if isinstance(result, list):
        if not result:
            raise GenerationError(f"FastH3 {label} returned no video", "InternalError")
        result = result[0]
    video_path = getattr(result, "video_path", None)
    path = Path(video_path) if video_path else fallback
    if not path.is_file() or path.stat().st_size <= 0:
        raise GenerationError(f"FastH3 {label} exited without writing a video", "InternalError")
    return path
