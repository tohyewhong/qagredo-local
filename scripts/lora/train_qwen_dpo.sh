#!/usr/bin/env bash
set -euo pipefail

# DPO stage: tune an existing SFT LoRA adapter from lora_dpo.jsonl.
#
# Usage:
#   bash scripts/lora/train_qwen_dpo.sh [RUN_DIR]
#   bash run.sh --finetune-dpo [RUN_DIR]
#
# Prerequisite: bash run.sh --finetune-lora [RUN_DIR] first.
#
# Optional .env overrides:
#   QAG_LORA_BASE_MODEL
#   QAG_LORA_OUTPUT_DIR          SFT adapter input (default)
#   QAG_LORA_DPO_OUTPUT_DIR      DPO adapter output
#   QAG_LORA_GPUS
#   QAG_LORA_QUANTIZATION_BIT

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
die() { echo "[dpo][ERROR] $*" >&2; exit 1; }
_log() { echo "[dpo] $*"; }

if [[ -f "$HOST_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$HOST_DIR/.env"
  set +a
fi

QAG_MODELS_LLM_HOST="${QAG_MODELS_LLM_HOST:-/data/models}"
QAG_LORA_BASE_MODEL="${QAG_LORA_BASE_MODEL:-$QAG_MODELS_LLM_HOST/Qwen3.5-9B}"
QAG_LORA_OUTPUT_DIR="${QAG_LORA_OUTPUT_DIR:-$QAG_MODELS_LLM_HOST/Qwen3.5-9B-qag-lora}"
QAG_LORA_SFT_ADAPTER="${QAG_LORA_SFT_ADAPTER:-$QAG_LORA_OUTPUT_DIR}"
QAG_LORA_DPO_OUTPUT_DIR="${QAG_LORA_DPO_OUTPUT_DIR:-${QAG_LORA_OUTPUT_DIR}-dpo}"
QAG_LORA_GPUS="${QAG_LORA_GPUS:-0,1}"
# DPO: default 4-bit to match SFT; single GPU avoids checkpoint errors.
QAG_LORA_DPO_GPUS="${QAG_LORA_DPO_GPUS:-0}"
QAG_LORA_DPO_QUANTIZATION_BIT="${QAG_LORA_DPO_QUANTIZATION_BIT:-4}"
QAG_LORA_VENV="${QAG_LORA_VENV:-$HOST_DIR/.venv-lora}"

_latest_run_dir() {
  python3 - "$HOST_DIR/output" <<'PYTHON_LATEST'
from pathlib import Path
import re
import sys

base = Path(sys.argv[1])
if not base.exists():
    raise SystemExit(1)
pat = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{6})?$")
run_dirs = [p for p in base.rglob("*") if p.is_dir() and pat.match(p.name)]
if not run_dirs:
    raise SystemExit(1)
print(max(run_dirs, key=lambda d: d.name))
PYTHON_LATEST
}

RUN_DIR="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi
if [[ -z "$RUN_DIR" ]]; then
  RUN_DIR="$(_latest_run_dir)" \
    || die "No run folder found. Pass RUN_DIR or run the pipeline first."
fi
[[ -d "$RUN_DIR" ]] || die "Run directory not found: $RUN_DIR"

[[ -d "$QAG_LORA_BASE_MODEL" ]] \
  || die "Base model not found: $QAG_LORA_BASE_MODEL"
[[ -f "$QAG_LORA_SFT_ADAPTER/adapter_config.json" ]] \
  || die "SFT adapter not found: $QAG_LORA_SFT_ADAPTER (run --finetune-lora first)"

if [[ ! -f "$RUN_DIR/lora_dpo.jsonl" ]]; then
  _log "Missing lora_dpo.jsonl; exporting from $RUN_DIR"
  python3 "$HOST_DIR/scripts/utils/export_lora_jsonl.py" \
    --include-dpo "$RUN_DIR"
fi
[[ -f "$RUN_DIR/lora_dpo.jsonl" ]] \
  || die "No lora_dpo.jsonl in $RUN_DIR (no captured DPO pairs in this run)"

if [[ ! -x "$QAG_LORA_VENV/bin/python" ]]; then
  _log "LoRA venv not found; running setup"
  bash "$HOST_DIR/scripts/lora/setup_lora_venv.sh"
fi

# shellcheck disable=SC1091
source "$QAG_LORA_VENV/bin/activate"

if ! python3 -c "import torch" >/dev/null 2>&1; then
  bash "$HOST_DIR/scripts/lora/setup_lora_venv.sh"
  # shellcheck disable=SC1091
  source "$QAG_LORA_VENV/bin/activate"
  python3 -c "import torch" \
    || die "torch install failed. Run: bash scripts/lora/setup_lora_venv.sh"
fi

_log "run_dir=$RUN_DIR"
_log "base_model (read-only)=$QAG_LORA_BASE_MODEL"
_log "sft_adapter=$QAG_LORA_SFT_ADAPTER"
_log "output_dir (dpo adapter)=$QAG_LORA_DPO_OUTPUT_DIR"
_log "gpus=$QAG_LORA_DPO_GPUS precision=${QAG_LORA_DPO_QUANTIZATION_BIT}-bit"

export CUDA_VISIBLE_DEVICES="$QAG_LORA_DPO_GPUS"

python3 "$HOST_DIR/scripts/lora/train_qwen_dpo.py" \
  --run-dir "$RUN_DIR" \
  --base-model "$QAG_LORA_BASE_MODEL" \
  --sft-adapter "$QAG_LORA_SFT_ADAPTER" \
  --output-dir "$QAG_LORA_DPO_OUTPUT_DIR" \
  --quantization-bit "$QAG_LORA_DPO_QUANTIZATION_BIT" \
  "$@"
