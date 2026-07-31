#!/usr/bin/env bash
set -euo pipefail

# Fair base vs SFT vs DPO evaluation: fresh pipeline run for each model.
#
# Usage:
#   bash scripts/lora/run_fair_adapter_eval.sh [SOURCE_RUN_DIR] [--smoke N]
#
# Example:
#   bash scripts/lora/run_fair_adapter_eval.sh \
#     output/vllm/qwen-qwen3.5-9b/2026-07-17_095536 --smoke 20

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
die() { echo "[fair-eval][ERROR] $*" >&2; exit 1; }
_log() { echo "[fair-eval] $*"; }

SOURCE_RUN=""
SMOKE_N=20
SKIP_BASE=0
SKIP_SFT=0
PARALLEL_N=6
EVAL_DIR_OVERRIDE=""
DOC_IDS_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke)
      SMOKE_N="${2:-20}"
      shift 2
      ;;
    --full)
      SMOKE_N=0
      shift
      ;;
    --skip-base)
      SKIP_BASE=1
      shift
      ;;
    --skip-sft)
      SKIP_SFT=1
      shift
      ;;
    --parallel)
      PARALLEL_N="${2:-6}"
      shift 2
      ;;
    --eval-dir)
      EVAL_DIR_OVERRIDE="$2"
      shift 2
      ;;
    --doc-ids-file)
      DOC_IDS_OVERRIDE="$2"
      shift 2
      ;;
    *)
      if [[ -z "$SOURCE_RUN" ]]; then
        SOURCE_RUN="$1"
        shift
      else
        die "Unknown argument: $1"
      fi
      ;;
  esac
done

if [[ -z "$SOURCE_RUN" ]]; then
  SOURCE_RUN="$(ls -td "$HOST_DIR"/output/vllm/qwen-qwen3.5-9b/*/ 2>/dev/null | head -1)"
  SOURCE_RUN="${SOURCE_RUN%/}"
fi
[[ -d "$SOURCE_RUN" ]] || die "Run directory not found: $SOURCE_RUN"

HOLDOUT_DIR="$SOURCE_RUN/eval_holdout"
FULL_DOC_IDS="$HOLDOUT_DIR/doc_ids.txt"
USE_CUSTOM_EVAL=0
if [[ -n "$EVAL_DIR_OVERRIDE" ]]; then
  if [[ "$EVAL_DIR_OVERRIDE" = /* ]]; then
    EVAL_DIR="$EVAL_DIR_OVERRIDE"
  else
    EVAL_DIR="$SOURCE_RUN/$EVAL_DIR_OVERRIDE"
  fi
  USE_CUSTOM_EVAL=1
elif [[ "$SMOKE_N" -gt 0 ]]; then
  EVAL_DIR="$SOURCE_RUN/eval_fair_smoke"
else
  EVAL_DIR="$SOURCE_RUN/eval_fair_full"
fi
DOC_IDS="$EVAL_DIR/doc_ids.txt"
MANIFEST="$EVAL_DIR/manifest.json"
LOG="$EVAL_DIR/run.log"

mkdir -p "$EVAL_DIR"
exec > >(tee -a "$LOG") 2>&1

_log "Source run: $SOURCE_RUN"
_log "Fair eval output: $EVAL_DIR"
_log "Parallel documents: $PARALLEL_N"

if [[ "$USE_CUSTOM_EVAL" -eq 1 ]]; then
  if [[ -n "$DOC_IDS_OVERRIDE" ]]; then
    if [[ "$DOC_IDS_OVERRIDE" = /* ]]; then
      cp "$DOC_IDS_OVERRIDE" "$DOC_IDS"
    else
      cp "$HOST_DIR/$DOC_IDS_OVERRIDE" "$DOC_IDS"
    fi
  elif [[ -f "$EVAL_DIR/doc_ids_500_holdout.txt" ]]; then
    cp "$EVAL_DIR/doc_ids_500_holdout.txt" "$DOC_IDS"
  fi
  [[ -f "$DOC_IDS" ]] || die "Custom eval dir missing doc_ids.txt: $EVAL_DIR"
  _log "Custom holdout: $(wc -l < "$DOC_IDS") documents"
elif [[ ! -f "$FULL_DOC_IDS" ]]; then
  python3 "$HOST_DIR/scripts/lora/prepare_eval_holdout.py" "$SOURCE_RUN"
  if [[ "$SMOKE_N" -gt 0 ]]; then
    EVAL_DIR="$SOURCE_RUN/eval_fair_smoke"
  else
    EVAL_DIR="$SOURCE_RUN/eval_fair_full"
  fi
  DOC_IDS="$EVAL_DIR/doc_ids.txt"
  MANIFEST="$EVAL_DIR/manifest.json"
  head -n "$SMOKE_N" "$FULL_DOC_IDS" > "$DOC_IDS"
  if [[ "$SMOKE_N" -gt 0 ]]; then
    _log "Smoke subset: $SMOKE_N documents"
  else
    cp "$FULL_DOC_IDS" "$DOC_IDS"
    _log "Full holdout: $(wc -l < "$DOC_IDS") documents"
  fi
else
  if [[ "$SMOKE_N" -gt 0 ]]; then
    head -n "$SMOKE_N" "$FULL_DOC_IDS" > "$DOC_IDS"
    _log "Smoke subset: $SMOKE_N documents"
  else
    cp "$FULL_DOC_IDS" "$DOC_IDS"
    _log "Full holdout: $(wc -l < "$DOC_IDS") documents"
  fi
fi

CONTAINER_DOC_IDS="/workspace/${DOC_IDS#$HOST_DIR/}"

cd "$HOST_DIR"
# shellcheck disable=SC1091
[[ -f "$HOST_DIR/.env" ]] && source "$HOST_DIR/.env"
export QAG_PROFILE="${QAG_PROFILE:-vllm}"

python3 - <<PY
import json
from pathlib import Path

eval_dir = Path("$EVAL_DIR")
full = Path("$FULL_DOC_IDS")
ids = [ln.strip() for ln in eval_dir.joinpath("doc_ids.txt").read_text().splitlines() if ln.strip()]
use_custom = int("$USE_CUSTOM_EVAL")
if use_custom:
    split_manifest = eval_dir / "manifest.json"
    if split_manifest.is_file():
        selection = json.loads(
            split_manifest.read_text(encoding="utf-8")
        ).get("selection_method", "Custom eval_2500 holdout")
    else:
        selection = "Custom holdout doc id list"
    full_count = len(ids)
else:
    full_count = sum(1 for _ in full.open() if _.strip()) if full.is_file() else len(ids)
    selection = (
        f"First {len(ids)} sorted doc ids from lora_sft_eval holdout "
        "(smoke test)" if len(ids) < 134 else "Full lora_sft_eval holdout"
    )
manifest = {
    "protocol": "fair_fresh_pipeline_all_models",
    "source_run_dir": "$SOURCE_RUN",
    "eval_dir": str(eval_dir),
    "holdout_documents": len(ids),
    "full_holdout_documents": full_count,
    "parallel_documents": int("$PARALLEL_N"),
    "selection_method": selection,
    "doc_ids_file": str(eval_dir / "doc_ids.txt"),
    "notes": [
        "Base, SFT, and DPO each get a fresh pipeline run on the same doc ids.",
        "Same judge and config; only generator weights change.",
    ],
}
(eval_dir / "manifest.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print(f"[ok] manifest -> {eval_dir / 'manifest.json'}")
PY

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
print("[ok] adapters finite")
PY
}

latest_under() {
  local rel="$1"
  python3 - "$HOST_DIR/output/$rel" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
if not root.is_dir():
    raise SystemExit(1)
pat = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")
runs = [p for p in root.iterdir() if p.is_dir() and pat.match(p.name)]
if not runs:
    raise SystemExit(1)
print(max(runs, key=lambda p: p.name))
PY
}

restart_generator_base() {
  unset QAG_VLLM_COMPOSE_EXTRA
  unset VLLM_LORA_MODULES
  export VLLM_MODEL="/models/Qwen3.5-9B"
  export VLLM_SERVED_MODEL_NAME="Qwen/Qwen3.5-9B"
  _log "Restarting generator (base model, no LoRA) ..."
  docker compose -f "$HOST_DIR/docker-compose.vllm-stack.yml" \
    stop vllm 2>/dev/null || true
  bash "$HOST_DIR/run.sh" --vllm-up generator
}

restart_generator_merged() {
  local model_path="$1"
  local served_name="$2"
  unset QAG_VLLM_COMPOSE_EXTRA
  unset VLLM_LORA_MODULES
  export VLLM_MODEL="$model_path"
  export VLLM_SERVED_MODEL_NAME="$served_name"
  _log "Restarting generator (merged weights): $model_path as $served_name"
  docker compose -f "$HOST_DIR/docker-compose.vllm-stack.yml" \
    stop vllm 2>/dev/null || true
  bash "$HOST_DIR/run.sh" --vllm-up generator
}

generator_smoke() {
  local model="$1"
  _log "Generator smoke test model=$model (non-fatal) ..."
  local api_key="${VLLM_API_KEY:-llama-local}"
  local doc_text
  doc_text="$(python3 -c 'print("word " * 120)')"
  local payload
  payload="$(python3 - "$model" "$doc_text" <<'PY'
import json
import sys

model, doc_text = sys.argv[1], sys.argv[2]
print(json.dumps({
    "model": model,
    "messages": [{
        "role": "user",
        "content": (
            f"Document:\n{doc_text}\n\n"
            "Question: What is this about?\nAnswer briefly."
        ),
    }],
    "max_tokens": 32,
    "temperature": 0,
}))
PY
)"
  local resp
  if ! resp="$(curl -sf --max-time 60 http://localhost:7100/v1/chat/completions \
    -H "Authorization: Bearer ${api_key}" \
    -H "Content-Type: application/json" \
    -d "$payload")"; then
    _log "[warn] smoke test timed out; continuing (pipeline uses longer prompts)"
    return 0
  fi
  echo "$resp" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
text = (data["choices"][0]["message"].get("content") or "").strip()
if not text:
    raise SystemExit("empty completion")
print(f"[ok] smoke completion -> {text[:80]!r}")
' || _log "[warn] smoke test parse failed; continuing anyway"
}

merge_adapter_if_needed() {
  local adapter="$1"
  local out="$2"
  if [[ -f "$out/qag_merge_manifest.json" ]]; then
    _log "Merged model already exists: $out"
    return 0
  fi
  _log "Merging adapter -> $out (needs GPU; stopping vLLM first) ..."
  docker compose -f "$HOST_DIR/docker-compose.vllm-stack.yml" \
    -f "$HOST_DIR/docker-compose.vllm-lora.yml" stop vllm 2>/dev/null || true
  source "$HOST_DIR/.venv-lora/bin/activate"
  CUDA_VISIBLE_DEVICES="${QAG_LORA_GPUS:-0}" python3 \
    "$HOST_DIR/scripts/lora/merge_adapter_for_vllm.py" \
    --base-model /data/models/Qwen3.5-9B \
    --adapter "$adapter" \
    --output-dir "$out"
}

ensure_judge() {
  if curl -sf http://localhost:7101/health >/dev/null 2>&1; then
    return 0
  fi
  bash "$HOST_DIR/run.sh" --vllm-up judge
}

run_pipeline() {
  local config_file="$1"
  local label="$2"
  local resume_segment="eval_fair_${label}_$(date +%Y%m%d_%H%M%S)"
  local container_resume="/workspace/output/vllm/qwen-qwen3.5-9b/${resume_segment}"
  _log "Pipeline ($label) config=$config_file ..."
  bash "$HOST_DIR/run.sh" --pipeline-only --skip-preflight --quiet-skips \
    --config "/workspace/$config_file" \
    --num-documents 0 \
    --parallel-documents "$PARALLEL_N" \
    --resume-run-dir "$container_resume" \
    --only-document-ids-file "$CONTAINER_DOC_IDS"
}

summarize_label() {
  local label="$1"
  local run_dir="$2"
  python3 "$HOST_DIR/scripts/lora/summarize_doc_subset.py" \
    "$run_dir" \
    --doc-ids "$DOC_IDS" \
    --label "$label" \
    --out "$EVAL_DIR/${label}_summary.json"
  echo "$run_dir" > "$EVAL_DIR/${label}_run_dir.txt"
}

_log "Validating adapters ..."
validate_adapters

_log "Ensuring judge is up ..."
ensure_judge

# --- BASE (fresh pipeline, no LoRA) ---
if [[ "$SKIP_BASE" -eq 0 ]]; then
  restart_generator_base
  run_pipeline "config/config.vllm.yaml" "base"
  BASE_RUN="$(latest_under "vllm/qwen-qwen3.5-9b")"
  _log "Base run dir: $BASE_RUN"
  summarize_label "base" "$BASE_RUN"
else
  _log "Skipping base (--skip-base); using existing base_summary.json"
fi

SFT_MERGED="/data/models/Qwen3.5-9B-qag-sft-merged"
DPO_MERGED="/data/models/Qwen3.5-9B-qag-dpo-merged"
# Final DPO epoch can collapse generation; checkpoint-44 is stable on smoke tests.
DPO_ADAPTER="${QAG_LORA_DPO_ADAPTER:-/data/models/Qwen3.5-9B-qag-lora-dpo/checkpoint-44}"

_log "Preparing merged weights for vLLM (avoids runtime LoRA hang on Qwen3.5) ..."
merge_adapter_if_needed /data/models/Qwen3.5-9B-qag-lora "$SFT_MERGED"
merge_adapter_if_needed "$DPO_ADAPTER" "$DPO_MERGED"

# --- SFT ---
if [[ "$SKIP_SFT" -eq 0 ]]; then
restart_generator_merged "/models/Qwen3.5-9B-qag-sft-merged" "qag-sft-merged"
generator_smoke "qag-sft-merged"
run_pipeline "config/config.vllm.eval-sft-merged.yaml" "sft"
SFT_RUN="$(latest_under "vllm/qag-sft-merged")"
_log "SFT run dir: $SFT_RUN"
summarize_label "sft" "$SFT_RUN"
else
  _log "Skipping SFT (--skip-sft); using existing sft_summary.json"
fi

# --- DPO ---
restart_generator_merged "/models/Qwen3.5-9B-qag-dpo-merged" "qag-dpo-merged"
generator_smoke "qag-dpo-merged"
run_pipeline "config/config.vllm.eval-dpo-merged.yaml" "dpo"
DPO_RUN="$(latest_under "vllm/qag-dpo-merged")"
_log "DPO run dir: $DPO_RUN"
summarize_label "dpo" "$DPO_RUN"

python3 "$HOST_DIR/scripts/lora/write_eval_report.py" \
  --manifest "$MANIFEST" \
  --summary "$EVAL_DIR/base_summary.json" \
  --summary "$EVAL_DIR/sft_summary.json" \
  --summary "$EVAL_DIR/dpo_summary.json" \
  --out-md "$EVAL_DIR/EVAL_REPORT.md" \
  --out-json "$EVAL_DIR/eval_comparison.json"

_log "Done."
_log "Report: $EVAL_DIR/EVAL_REPORT.md"
cat "$EVAL_DIR/EVAL_REPORT.md"
