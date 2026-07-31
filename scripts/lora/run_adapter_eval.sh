#!/usr/bin/env bash
set -euo pipefail

# Run adapter A/B evaluation on the lora_sft_eval holdout document set.
#
# Usage:
#   bash scripts/lora/run_adapter_eval.sh [SOURCE_RUN_DIR]
#
# Writes:
#   RUN_DIR/eval_holdout/manifest.json
#   RUN_DIR/eval_holdout/base_summary.json
#   RUN_DIR/eval_holdout/sft_summary.json
#   RUN_DIR/eval_holdout/dpo_summary.json
#   RUN_DIR/eval_holdout/EVAL_REPORT.md

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
die() { echo "[eval][ERROR] $*" >&2; exit 1; }
_log() { echo "[eval] $*"; }

SOURCE_RUN="${1:-}"
if [[ -z "$SOURCE_RUN" ]]; then
  SOURCE_RUN="$(ls -td "$HOST_DIR"/output/vllm/qwen-qwen3.5-9b/*/ 2>/dev/null | head -1)"
  SOURCE_RUN="${SOURCE_RUN%/}"
fi
[[ -d "$SOURCE_RUN" ]] || die "Run directory not found: $SOURCE_RUN"

HOLDOUT_DIR="$SOURCE_RUN/eval_holdout"
DOC_IDS="$HOLDOUT_DIR/doc_ids.txt"
MANIFEST="$HOLDOUT_DIR/manifest.json"

CONTAINER_DOC_IDS="/workspace/${DOC_IDS#$HOST_DIR/}"

_log "Source run: $SOURCE_RUN"
_log "Preparing holdout doc ids from lora_sft_eval.jsonl ..."
python3 "$HOST_DIR/scripts/lora/prepare_eval_holdout.py" "$SOURCE_RUN"

_log "Base summary (from original run, no pipeline rerun) ..."
python3 "$HOST_DIR/scripts/lora/summarize_doc_subset.py" \
  "$SOURCE_RUN" \
  --doc-ids "$DOC_IDS" \
  --label base \
  --out "$HOLDOUT_DIR/base_summary.json"

cd "$HOST_DIR"
# shellcheck disable=SC1091
[[ -f "$HOST_DIR/.env" ]] && source "$HOST_DIR/.env"
export QAG_PROFILE="${QAG_PROFILE:-vllm}"

validate_adapters() {
  source "$HOST_DIR/.venv-lora/bin/activate"
  python3 - <<'PY'
from pathlib import Path
from scripts.lora.lora_utils import validate_adapter_finite

validate_adapter_finite(
    Path("/data/models/Qwen3.5-9B-qag-lora"), stage="sft"
)
validate_adapter_finite(
    Path("/data/models/Qwen3.5-9B-qag-lora-dpo"), stage="dpo"
)
print("[ok] SFT and DPO adapters are finite")
PY
}

_log "Checking adapters ..."
validate_adapters

restart_generator_with_lora() {
  local modules="$1"
  export QAG_VLLM_COMPOSE_EXTRA="docker-compose.vllm-lora.yml"
  export VLLM_LORA_MODULES="$modules"
  _log "Restarting generator with LoRA: $modules"
  docker compose -f "$HOST_DIR/docker-compose.vllm-stack.yml" \
    -f "$HOST_DIR/docker-compose.vllm-lora.yml" stop vllm 2>/dev/null || true
  bash "$HOST_DIR/run.sh" --vllm-up generator
}

ensure_judge_up() {
  if curl -sf http://localhost:7101/health >/dev/null 2>&1; then
    _log "Judge already healthy"
    return 0
  fi
  _log "Starting judge ..."
  bash "$HOST_DIR/run.sh" --vllm-up judge
}

latest_run_dir() {
  python3 - "$HOST_DIR/output" <<'PY'
from pathlib import Path
import re
import sys

base = Path(sys.argv[1])
pat = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")
runs = [
    p for p in base.rglob("*")
    if p.is_dir() and pat.match(p.name)
]
if not runs:
    raise SystemExit(1)
print(max(runs, key=lambda p: p.name))
PY
}

run_eval_pipeline() {
  local config_file="$1"
  local label="$2"
  local resume_segment="eval_holdout_${label}"
  local container_resume="/workspace/output/vllm/qwen-qwen3.5-9b/${resume_segment}"
  _log "Pipeline eval ($label) on holdout docs ..."
  export QAG_VLLM_COMPOSE_EXTRA="docker-compose.vllm-lora.yml"
  bash "$HOST_DIR/run.sh" --pipeline-only --skip-preflight --quiet-skips \
    --config "/workspace/$config_file" \
    --num-documents 0 \
    --parallel-documents 4 \
    --resume-run-dir "$container_resume" \
    --only-document-ids-file "$CONTAINER_DOC_IDS"
  local run_dir
  run_dir="$(latest_run_dir)"
  _log "$label run dir: $run_dir"
  python3 "$HOST_DIR/scripts/lora/summarize_doc_subset.py" \
    "$run_dir" \
    --doc-ids "$DOC_IDS" \
    --label "$label" \
    --out "$HOLDOUT_DIR/${label}_summary.json"
  echo "$run_dir" > "$HOLDOUT_DIR/${label}_run_dir.txt"
}

_log "Stopping any old QAG vLLM stack ..."
bash "$HOST_DIR/run.sh" --down 2>/dev/null || true

ensure_judge_up
restart_generator_with_lora "qag-sft=/models/Qwen3.5-9B-qag-lora"
run_eval_pipeline "config/config.vllm.eval-sft.yaml" "sft"

restart_generator_with_lora "qag-dpo=/models/Qwen3.5-9B-qag-lora-dpo"
run_eval_pipeline "config/config.vllm.eval-dpo.yaml" "dpo"

python3 "$HOST_DIR/scripts/lora/write_eval_report.py" \
  --manifest "$MANIFEST" \
  --summary "$HOLDOUT_DIR/base_summary.json" \
  --summary "$HOLDOUT_DIR/sft_summary.json" \
  --summary "$HOLDOUT_DIR/dpo_summary.json" \
  --out-md "$HOLDOUT_DIR/EVAL_REPORT.md" \
  --out-json "$HOLDOUT_DIR/eval_comparison.json"

_log "Done."
_log "Report: $HOLDOUT_DIR/EVAL_REPORT.md"
cat "$HOLDOUT_DIR/EVAL_REPORT.md"
