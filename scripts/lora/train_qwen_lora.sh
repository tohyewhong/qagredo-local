#!/usr/bin/env bash
set -euo pipefail

# Train a Qwen LoRA adapter (Option A) on 2 GPUs without modifying the base model.
#
# Usage:
#   bash scripts/lora/train_qwen_lora.sh [RUN_DIR]
#   bash run.sh --finetune-lora [RUN_DIR]
#
# Optional .env overrides:
#   QAG_LORA_BASE_MODEL        read-only HF folder
#   QAG_LORA_OUTPUT_DIR        adapter output folder
#   QAG_LORA_GPUS              comma-separated GPU ids (default: 0,1)
#   QAG_LORA_QUANTIZATION_BIT  0=fp16 (default), 8, or 4
#   QAG_LORA_VENV              venv path

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
die() { echo "[lora][ERROR] $*" >&2; exit 1; }
_log() { echo "[lora] $*"; }

if [[ -f "$HOST_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$HOST_DIR/.env"
  set +a
fi

QAG_MODELS_LLM_HOST="${QAG_MODELS_LLM_HOST:-/data/models}"
QAG_LORA_BASE_MODEL="${QAG_LORA_BASE_MODEL:-$QAG_MODELS_LLM_HOST/Qwen3.5-9B}"
QAG_LORA_OUTPUT_DIR="${QAG_LORA_OUTPUT_DIR:-$QAG_MODELS_LLM_HOST/Qwen3.5-9B-qag-lora}"
# fp16 (default) shards layers across GPUs via device_map="auto" — not DDP.
# Qwen3.5 fp16 LoRA can produce NaN weights without flash-linear-attention.
QAG_LORA_GPUS="${QAG_LORA_GPUS:-0,1}"
QAG_LORA_QUANTIZATION_BIT="${QAG_LORA_QUANTIZATION_BIT:-4}"
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
[[ "$QAG_LORA_BASE_MODEL" != "$QAG_LORA_OUTPUT_DIR" ]] \
  || die "QAG_LORA_OUTPUT_DIR must differ from QAG_LORA_BASE_MODEL"

if [[ ! -x "$QAG_LORA_VENV/bin/python" ]]; then
  _log "LoRA venv not found; running setup"
  bash "$HOST_DIR/scripts/lora/setup_lora_venv.sh"
fi
LORA_PY="$QAG_LORA_VENV/bin/python"
[[ -x "$LORA_PY" ]] || die "LoRA venv python not found: $LORA_PY"

if ! "$LORA_PY" -c "import torch" >/dev/null 2>&1; then
  _log "LoRA venv missing torch; running setup"
  bash "$HOST_DIR/scripts/lora/setup_lora_venv.sh"
  [[ -x "$LORA_PY" ]] \
    || die "LoRA venv python not found after setup: $LORA_PY"
  "$LORA_PY" -c "import torch" \
    || die "torch install failed. Run: bash scripts/lora/setup_lora_venv.sh"
fi

"$LORA_PY" - <<'PY' || die "LoRA venv preflight failed (see above)."
import jinja2
import torch

print(f"[lora] venv python ok: torch={torch.__version__} "
      f"cuda={torch.version.cuda} jinja2={jinja2.__version__}")
major, minor = (int(x) for x in jinja2.__version__.split(".")[:2])
if (major, minor) < (3, 1):
    raise SystemExit(
        f"jinja2>=3.1.0 required; got {jinja2.__version__!r}. "
        "Replace .venv-lora from lora_venv.tar.gz and set QAG_LORA_VENV."
    )
PY

if [[ ! -f "$RUN_DIR/lora_sft.jsonl" ]]; then
  _log "Missing lora_sft.jsonl; exporting from $RUN_DIR"
  "$LORA_PY" "$HOST_DIR/scripts/utils/export_lora_jsonl.py" \
    --include-dpo "$RUN_DIR"
fi

IFS=',' read -r -a _gpu_ids <<< "$QAG_LORA_GPUS"
NPROC="${#_gpu_ids[@]}"
[[ "$NPROC" -ge 1 ]] || die "QAG_LORA_GPUS must list at least one GPU"

_log "run_dir=$RUN_DIR"
_log "base_model (read-only)=$QAG_LORA_BASE_MODEL"
_log "output_dir (adapter)=$QAG_LORA_OUTPUT_DIR"
_log "gpus=$QAG_LORA_GPUS precision=${QAG_LORA_QUANTIZATION_BIT:-0}-bit"

export CUDA_VISIBLE_DEVICES="$QAG_LORA_GPUS"

_train_py=(
  "$HOST_DIR/scripts/lora/train_qwen_lora.py"
  --run-dir "$RUN_DIR"
  --base-model "$QAG_LORA_BASE_MODEL"
  --output-dir "$QAG_LORA_OUTPUT_DIR"
  --quantization-bit "$QAG_LORA_QUANTIZATION_BIT"
)

# device_map="auto" shards layers across QAG_LORA_GPUS (not DDP).
"$LORA_PY" "${_train_py[@]}" "$@"
