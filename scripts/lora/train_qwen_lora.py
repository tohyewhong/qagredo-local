#!/usr/bin/env python3
"""Train a Qwen LoRA adapter from QAG-exported JSONL (base model read-only)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lora.lora_utils import (  # noqa: E402
    validate_adapter_finite,
    warn_fp16_qwen35,
)

SFT_NAME = "lora_sft.jsonl"
SFT_EVAL_NAME = "lora_sft_eval.jsonl"
DEFAULT_LORA_TARGETS = (
    "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj"
)


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


def validate_training_paths(
    base_model: Path,
    output_dir: Path,
) -> None:
    if not base_model.is_dir():
        raise FileNotFoundError(f"Base model not found: {base_model}")
    if not (base_model / "config.json").is_file():
        raise FileNotFoundError(
            f"Base model missing config.json: {base_model}"
        )
    base_resolved = base_model.resolve()
    output_resolved = output_dir.resolve()
    if base_resolved == output_resolved:
        raise ValueError(
            "output_dir must differ from base_model "
            "(Option A keeps the base checkpoint read-only)."
        )
    if output_resolved.is_dir() and any(output_resolved.iterdir()):
        if not (output_resolved / "adapter_config.json").is_file():
            raise ValueError(
                f"output_dir is not empty: {output_resolved}"
            )


def resolve_run_data(
    run_dir: Path,
) -> Tuple[Path, Optional[Path]]:
    train_path = run_dir / SFT_NAME
    eval_path = run_dir / SFT_EVAL_NAME
    if not train_path.is_file():
        raise FileNotFoundError(
            f"Missing {SFT_NAME} in {run_dir}. "
            "Run: bash run.sh --export-lora <RUN_DIR>"
        )
    eval_file = eval_path if eval_path.is_file() else None
    return train_path, eval_file


def build_text_dataset(
    rows: List[Dict[str, Any]],
    tokenizer: Any,
) -> List[Dict[str, str]]:
    dataset: List[Dict[str, str]] = []
    for row in rows:
        messages = row.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError(
                "Each row must contain a non-empty messages list."
            )
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
        )
        dataset.append({"text": text})
    return dataset


def write_training_manifest(
    output_dir: Path,
    *,
    base_model: Path,
    run_dir: Path,
    train_rows: int,
    eval_rows: int,
    gpus: str,
) -> None:
    payload = {
        "mode": "lora_adapter_only",
        "base_model": str(base_model),
        "run_dir": str(run_dir),
        "train_rows": train_rows,
        "eval_rows": eval_rows,
        "gpus": gpus,
        "notes": (
            "Adapter-only export. Base weights are not modified. "
            "Serve with base + adapter, or merge later into a new folder."
        ),
    }
    path = output_dir / "qag_lora_manifest.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train a Qwen LoRA adapter from QAG lora_sft.jsonl. "
            "The base model directory is read-only."
        )
    )
    parser.add_argument(
        "--run-dir",
        type=_as_path,
        required=True,
        help="QAG run folder containing lora_sft.jsonl.",
    )
    parser.add_argument(
        "--base-model",
        type=_as_path,
        required=True,
        help="Read-only Hugging Face model directory (e.g. Qwen3.5-9B).",
    )
    parser.add_argument(
        "--output-dir",
        type=_as_path,
        required=True,
        help="Directory for the LoRA adapter (must not be the base model).",
    )
    parser.add_argument(
        "--epochs",
        type=float,
        default=2.0,
        help="Training epochs (default: 2).",
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=5e-5,
        help="Learning rate (default: 5e-5).",
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
        default=2048,
        help="Max sequence length (default: 2048).",
    )
    parser.add_argument(
        "--lora-rank",
        type=int,
        default=32,
        help="LoRA rank (default: 32).",
    )
    parser.add_argument(
        "--lora-alpha",
        type=int,
        default=64,
        help="LoRA alpha (default: 64).",
    )
    parser.add_argument(
        "--lora-dropout",
        type=float,
        default=0.05,
        help="LoRA dropout (default: 0.05).",
    )
    parser.add_argument(
        "--lora-target",
        default=DEFAULT_LORA_TARGETS,
        help="Comma-separated LoRA target modules.",
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
        default=0,
        help=(
            "QLoRA quantization: 0 = fp16 (default, best quality), "
            "8, or 4 (~5 GB/GPU, lower fidelity)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the resolved plan without training.",
    )
    return parser.parse_args()


def _train(args: argparse.Namespace) -> int:
    train_path, eval_path = resolve_run_data(args.run_dir)
    validate_training_paths(args.base_model, args.output_dir)

    train_rows = _load_jsonl(train_path)
    eval_rows = _load_jsonl(eval_path) if eval_path else []
    if not train_rows:
        raise SystemExit(f"No training rows in {train_path}")

    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    is_main = local_rank == 0

    if is_main:
        warn_fp16_qwen35(args.quantization_bit, stage="lora")
        qmode = (
            "fp16"
            if args.quantization_bit == 0
            else f"{args.quantization_bit}-bit"
        )
        print(f"[lora] base_model (read-only): {args.base_model}")
        print(f"[lora] output_dir (adapter):  {args.output_dir}")
        print(
            f"[lora] rows: train={len(train_rows)} eval={len(eval_rows)} "
            f"gpus={world_size} precision={qmode} lora_r={args.lora_rank}"
        )

    if args.dry_run:
        if is_main:
            print("[lora] dry-run ok")
        return 0

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, TaskType
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
        )
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:
        raise SystemExit(
            "Missing LoRA training packages. Run:\n"
            "  bash scripts/lora/setup_lora_venv.sh"
        ) from exc

    args.output_dir.mkdir(parents=True, exist_ok=True)

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

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb_config,
        dtype=torch.float16 if bnb_config is None else None,
        device_map="auto",
        trust_remote_code=True,
    )
    model.config.use_cache = False

    target_modules = [
        part.strip()
        for part in args.lora_target.split(",")
        if part.strip()
    ]
    peft_config = LoraConfig(
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
        target_modules=target_modules,
    )

    train_dataset = Dataset.from_list(
        build_text_dataset(train_rows, tokenizer)
    )
    eval_dataset = (
        Dataset.from_list(build_text_dataset(eval_rows, tokenizer))
        if eval_rows
        else None
    )

    use_fp16 = False
    training_args = SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.learning_rate,
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch" if eval_dataset is not None else "no",
        fp16=use_fp16,
        bf16=False,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        loss_type="nll",
        report_to=[],
        seed=args.seed,
        max_length=args.cutoff_len,
        dataset_text_field="text",
        packing=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        peft_config=peft_config,
        processing_class=tokenizer,
    )

    trainer.train()
    if is_main:
        trainer.model.save_pretrained(args.output_dir)
        tokenizer.save_pretrained(args.output_dir)
        validate_adapter_finite(args.output_dir, stage="lora")
        write_training_manifest(
            args.output_dir,
            base_model=args.base_model,
            run_dir=args.run_dir,
            train_rows=len(train_rows),
            eval_rows=len(eval_rows),
            gpus=os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        )
        print(f"[lora] adapter saved to {args.output_dir}")
    return 0


def main() -> int:
    args = _parse_args()
    return _train(args)


if __name__ == "__main__":
    raise SystemExit(main())
