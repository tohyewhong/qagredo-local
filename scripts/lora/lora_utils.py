"""Shared helpers for QAG LoRA / DPO training scripts."""

from __future__ import annotations

from pathlib import Path

import torch
from safetensors import safe_open


def warn_fp16_qwen35(quantization_bit: int, *, stage: str) -> None:
    """Warn when fp16 LoRA may corrupt on Qwen3.5 without FLA."""
    if quantization_bit != 0:
        return
    print(
        f"[{stage}][WARN] fp16 LoRA on Qwen3.5 needs flash-linear-attention; "
        "without it, adapter weights can become NaN. "
        "Prefer QAG_LORA_QUANTIZATION_BIT=4 on TITAN RTX."
    )


def validate_adapter_finite(adapter_dir: Path, *, stage: str) -> None:
    """Fail fast if saved LoRA weights contain NaN/Inf."""
    path = adapter_dir / "adapter_model.safetensors"
    if not path.is_file():
        raise FileNotFoundError(f"Missing adapter weights: {path}")
    bad: list[str] = []
    with safe_open(str(path), framework="pt") as handle:
        for key in handle.keys():
            tensor = handle.get_tensor(key)
            if not torch.isfinite(tensor).all():
                bad.append(key)
    if bad:
        raise SystemExit(
            f"[{stage}][ERROR] Adapter has {len(bad)} non-finite tensors "
            f"(e.g. {bad[0]}). Training output is unusable. "
            "Re-run with QAG_LORA_QUANTIZATION_BIT=4."
        )


def multi_gpu_visible() -> bool:
    visible = __import__("os").environ.get("CUDA_VISIBLE_DEVICES", "")
    return "," in visible
