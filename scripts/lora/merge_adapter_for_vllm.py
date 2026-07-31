#!/usr/bin/env python3
"""Merge a LoRA adapter into the base model for vLLM serving without LoRA."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge LoRA adapter weights into a standalone HF model folder."
        ),
    )
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate paths only; do not merge.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    base = args.base_model.expanduser().resolve()
    adapter = args.adapter.expanduser().resolve()
    out = args.output_dir.expanduser().resolve()

    if not base.is_dir():
        raise SystemExit(f"Base model not found: {base}")
    if not (adapter / "adapter_config.json").is_file():
        raise SystemExit(f"Adapter not found: {adapter}")

    if args.dry_run:
        print(f"[dry-run] base={base} adapter={adapter} -> {out}")
        return

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from scripts.lora.lora_utils import warn_fp16_qwen35

    warn_fp16_qwen35(0, stage="merge")

    print(f"[merge] base={base}")
    print(f"[merge] adapter={adapter}")
    print(f"[merge] output={out}")

    tokenizer = AutoTokenizer.from_pretrained(
        str(base),
        trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        str(base),
        dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(model, str(adapter))
    model = model.merge_and_unload()
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out), safe_serialization=True)
    tokenizer.save_pretrained(str(out))

    # Keep the base VL config so vLLM (--language-model-only) can load it.
    for name in (
        "config.json",
        "generation_config.json",
        "preprocessor_config.json",
        "video_preprocessor_config.json",
    ):
        src = base / name
        if src.is_file():
            (out / name).write_bytes(src.read_bytes())

    manifest = {
        "mode": "merged_lora_for_vllm",
        "base_model": str(base),
        "adapter": str(adapter),
        "output_dir": str(out),
        "dtype": "float16",
    }
    (out / "qag_merge_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[ok] merged model saved to {out}")


if __name__ == "__main__":
    main()
