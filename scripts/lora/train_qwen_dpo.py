#!/usr/bin/env python3
"""Train a DPO LoRA adapter from QAG-exported lora_dpo.jsonl."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lora.lora_utils import (  # noqa: E402
    multi_gpu_visible,
    validate_adapter_finite,
    warn_fp16_qwen35,
)

DPO_NAME = "lora_dpo.jsonl"


def _as_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_no} of {path}: {exc}"
                ) from exc
            rows.append(row)
    return rows


def validate_base_model(base_model: Path) -> None:
    if not base_model.is_dir():
        raise FileNotFoundError(f"Base model not found: {base_model}")
    if not (base_model / "config.json").is_file():
        raise FileNotFoundError(
            f"Base model missing config.json: {base_model}"
        )


def validate_sft_adapter(adapter_dir: Path) -> None:
    if not adapter_dir.is_dir():
        raise FileNotFoundError(f"SFT adapter not found: {adapter_dir}")
    if not (adapter_dir / "adapter_config.json").is_file():
        raise FileNotFoundError(
            f"SFT adapter missing adapter_config.json: {adapter_dir}. "
            "Run: bash run.sh --finetune-lora [RUN_DIR]"
        )


def validate_dpo_output(base_model: Path, output_dir: Path) -> None:
    if base_model.resolve() == output_dir.resolve():
        raise ValueError(
            "output_dir must differ from base_model "
            "(Option A keeps the base checkpoint read-only)."
        )


def resolve_dpo_data(run_dir: Path) -> Path:
    dpo_path = run_dir / DPO_NAME
    if not dpo_path.is_file():
        raise FileNotFoundError(
            f"Missing {DPO_NAME} in {run_dir}. "
            "Re-export with: bash run.sh --export-lora <RUN_DIR> "
            "after a run that captured gate-passing DPO pairs."
        )
    return dpo_path


def build_dpo_dataset(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    dataset: List[Dict[str, str]] = []
    for row in rows:
        prompt = str(row.get("prompt", "")).strip()
        chosen = str(row.get("chosen", "")).strip()
        rejected = str(row.get("rejected", "")).strip()
        system = str(row.get("system", "")).strip()
        if not prompt or not chosen or not rejected:
            raise ValueError(
                "Each DPO row needs non-empty prompt, chosen, rejected."
            )
        if system:
            prompt = f"{system}\n\n{prompt}"
        dataset.append(
            {
                "prompt": prompt,
                "chosen": chosen,
                "rejected": rejected,
            }
        )
    return dataset


def write_dpo_manifest(
    output_dir: Path,
    *,
    base_model: Path,
    sft_adapter: Path,
    run_dir: Path,
    train_rows: int,
    gpus: str,
) -> None:
    payload = {
        "mode": "lora_dpo_adapter_only",
        "base_model": str(base_model),
        "sft_adapter": str(sft_adapter),
        "run_dir": str(run_dir),
        "train_rows": train_rows,
        "gpus": gpus,
        "notes": (
            "DPO adapter export. Base weights are not modified. "
            "Serve with base + this adapter, or merge later."
        ),
    }
    path = output_dir / "qag_lora_dpo_manifest.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train a DPO LoRA adapter from QAG lora_dpo.jsonl, "
            "starting from an existing SFT adapter."
        )
    )
    parser.add_argument(
        "--run-dir",
        type=_as_path,
        required=True,
        help="QAG run folder containing lora_dpo.jsonl.",
    )
    parser.add_argument(
        "--base-model",
        type=_as_path,
        required=True,
        help="Read-only Hugging Face model directory.",
    )
    parser.add_argument(
        "--sft-adapter",
        type=_as_path,
        required=True,
        help="Existing SFT LoRA adapter directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=_as_path,
        required=True,
        help="Directory for the DPO-tuned adapter.",
    )
    parser.add_argument(
        "--epochs",
        type=float,
        default=3.0,
        help="Training epochs (default: 3).",
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=1e-6,
        help="Learning rate (default: 1e-6).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Per-device train batch size (default: 1).",
    )
    parser.add_argument(
        "--grad-accum",
        type=int,
        default=4,
        help="Gradient accumulation steps (default: 4).",
    )
    parser.add_argument(
        "--cutoff-len",
        type=int,
        default=768,
        help="Max sequence length (default: 768).",
    )
    parser.add_argument(
        "--beta",
        type=float,
        default=0.1,
        help="DPO beta (default: 0.1).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed (default: 42).",
    )
    parser.add_argument(
        "--quantization-bit",
        type=int,
        choices=(4, 8, 0),
        default=4,
        help="QLoRA: 4 (default for DPO), 8, or 0 = fp16.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the resolved plan without training.",
    )
    return parser.parse_args()


def _train(args: argparse.Namespace) -> int:
    dpo_path = resolve_dpo_data(args.run_dir)
    validate_base_model(args.base_model)
    validate_sft_adapter(args.sft_adapter)
    validate_dpo_output(args.base_model, args.output_dir)

    train_rows = _load_jsonl(dpo_path)
    if not train_rows:
        raise SystemExit(f"No DPO rows in {dpo_path}")

    is_main = int(os.environ.get("LOCAL_RANK", "0")) == 0
    qmode = (
        "fp16"
        if args.quantization_bit == 0
        else f"{args.quantization_bit}-bit"
    )

    if is_main:
        warn_fp16_qwen35(args.quantization_bit, stage="dpo")
        print(f"[dpo] base_model (read-only): {args.base_model}")
        print(f"[dpo] sft_adapter:          {args.sft_adapter}")
        print(f"[dpo] output_dir (adapter): {args.output_dir}")
        print(
            f"[dpo] rows={len(train_rows)} precision={qmode} beta={args.beta}"
        )
        if len(train_rows) < 10:
            print(
                "[dpo][WARN] Very small DPO set; results are experimental."
            )

    if args.dry_run:
        if is_main:
            print("[dpo] dry-run ok")
        return 0

    try:
        import torch
        from datasets import Dataset
        from peft import PeftModel
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
        )
        from trl import DPOConfig, DPOTrainer
    except ImportError as exc:
        raise SystemExit(
            "Missing LoRA training packages. Run:\n"
            "  bash scripts/lora/setup_lora_venv.sh"
        ) from exc

    args.output_dir.mkdir(parents=True, exist_ok=True)
    hf_cache = args.output_dir / ".hf_datasets_cache"
    hf_cache.mkdir(parents=True, exist_ok=True)
    os.environ["HF_DATASETS_CACHE"] = str(hf_cache)
    os.environ["TMPDIR"] = str(hf_cache)

    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model,
        trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb_config: Optional[BitsAndBytesConfig] = None
    if args.quantization_bit == 4:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    elif args.quantization_bit == 8:
        bnb_config = BitsAndBytesConfig(load_in_8bit=True)

    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    multi_gpu = multi_gpu_visible()
    device_map: Any = {"": 0}
    if args.quantization_bit == 0 and multi_gpu:
        device_map = "auto"

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb_config,
        dtype=torch.float16 if bnb_config is None else None,
        device_map=device_map,
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(
        model,
        str(args.sft_adapter),
        is_trainable=True,
        autocast_adapter_dtype=False,
    )
    model.config.use_cache = False
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()
    use_gc = not multi_gpu
    if use_gc and hasattr(model, "gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable()

    train_dataset = Dataset.from_list(build_dpo_dataset(train_rows))

    training_args = DPOConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.learning_rate,
        logging_steps=5,
        save_strategy="epoch",
        fp16=False,
        bf16=False,
        beta=args.beta,
        max_length=args.cutoff_len,
        gradient_checkpointing=use_gc,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to=[],
        seed=args.seed,
        remove_unused_columns=False,
    )

    trainer = DPOTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        processing_class=tokenizer,
    )

    trainer.train()
    if is_main:
        trainer.model.save_pretrained(args.output_dir)
        tokenizer.save_pretrained(args.output_dir)
        validate_adapter_finite(args.output_dir, stage="dpo")
        write_dpo_manifest(
            args.output_dir,
            base_model=args.base_model,
            sft_adapter=args.sft_adapter,
            run_dir=args.run_dir,
            train_rows=len(train_rows),
            gpus=os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        )
        print(f"[dpo] adapter saved to {args.output_dir}")
    return 0


def main() -> int:
    args = _parse_args()
    return _train(args)


if __name__ == "__main__":
    raise SystemExit(main())
