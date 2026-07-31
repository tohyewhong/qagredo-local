#!/usr/bin/env bash
set -euo pipefail

# End-to-end 2500-doc campaign (~3 days):
#   1) 2000-doc base pipeline (parallel 6)
#   2) minimise / export LoRA JSONL
#   3) SFT + DPO finetune + merge
#   4) Fair eval base/SFT/DPO on 500 holdout docs (parallel 6)
#
# Usage:
#   nohup bash scripts/lora/run_2500_campaign.sh \
#     >> /tmp/qag-2500-campaign.log 2>&1 &

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
die() { echo "[campaign][ERROR] $*" >&2; exit 1; }
_log() { echo "[campaign] $(date -Is) $*"; }

ANCHOR_RUN="${QAG_CAMPAIGN_ANCHOR:-$HOST_DIR/output/vllm/qwen-qwen3.5-9b/2026-07-17_095536}"
EVAL_DIR="$ANCHOR_RUN/eval_2500"
TRAIN_RUN="$HOST_DIR/output/vllm/qwen-qwen3.5-9b/run_2500_train"
STATE_FILE="$EVAL_DIR/campaign_state.txt"
LOG_FILE="${QAG_CAMPAIGN_LOG:-/tmp/qag-2500-campaign.log}"

PARALLEL="${QAG_CAMPAIGN_PARALLEL:-6}"
export QAG_LORA_DPO_ADAPTER="${QAG_LORA_DPO_ADAPTER:-/data/models/Qwen3.5-9B-qag-lora-dpo/checkpoint-44}"

mkdir -p "$EVAL_DIR"
touch "$LOG_FILE"

state_has() {
  [[ -f "$STATE_FILE" ]] && grep -qx "$1" "$STATE_FILE"
}

state_mark() {
  if ! state_has "$1"; then
    echo "$1" >> "$STATE_FILE"
  fi
}

count_train_outputs() {
  python3 - "$TRAIN_RUN" "$EVAL_DIR/doc_ids_2000_train.txt" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
ids_path = Path(sys.argv[2])
want = {
    ln.strip()
    for ln in ids_path.read_text(encoding="utf-8").splitlines()
    if ln.strip()
}
found = set()
for path in run_dir.glob("*_analysis.json"):
    if "_minimal_" in path.name:
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        continue
    doc = data.get("document") or {}
    doc_id = str(doc.get("id") or doc.get("title") or "").strip()
    if doc_id in want:
        found.add(doc_id)
print(len(found))
PY
}

cd "$HOST_DIR"
# shellcheck disable=SC1091
[[ -f "$HOST_DIR/.env" ]] && source "$HOST_DIR/.env"
export QAG_PROFILE="${QAG_PROFILE:-vllm}"

_log "Campaign log: $LOG_FILE"
_log "Anchor run: $ANCHOR_RUN"
_log "Eval dir: $EVAL_DIR"
_log "Train run dir: $TRAIN_RUN"
_log "Parallel documents: $PARALLEL"

if ! state_has "split_done"; then
  _log "Phase 0: preparing doc id split ..."
  python3 "$HOST_DIR/scripts/lora/prepare_eval_2500_split.py" \
    --anchor-run "$ANCHOR_RUN"
  state_mark "split_done"
fi

if ! state_has "pipeline_done"; then
  _log "Phase 1: base pipeline on 2000 train docs ..."
  mkdir -p "$TRAIN_RUN"
  bash "$HOST_DIR/run.sh" --vllm-up all || true
  bash "$HOST_DIR/run.sh" --pipeline-only --skip-preflight --quiet-skips \
    --resume \
    --num-documents 0 \
    --parallel-documents "$PARALLEL" \
    --only-document-ids-file "/workspace/${EVAL_DIR#$HOST_DIR/}/doc_ids_2000_train.txt" \
    --resume-run-dir "/workspace/output/vllm/qwen-qwen3.5-9b/run_2500_train"
  train_count="$(count_train_outputs)"
  if [[ "$train_count" -lt 2000 ]]; then
    die "Pipeline finished but only $train_count/2000 train analyses found"
  fi
  echo "$TRAIN_RUN" > "$EVAL_DIR/train_run_dir.txt"
  state_mark "pipeline_done"
fi

resolve_train_run() {
  if [[ -f "$EVAL_DIR/train_run_dir.txt" ]]; then
    TRAIN_RUN="$(cat "$EVAL_DIR/train_run_dir.txt")"
  fi
  if [[ ! -d "$TRAIN_RUN" ]]; then
    die "Train run dir missing: $TRAIN_RUN"
  fi
}

if ! state_has "minimise_done"; then
  resolve_train_run
  _log "Phase 2: minimise / export LoRA JSONL ..."
  bash "$HOST_DIR/run.sh" --minimise "$TRAIN_RUN"
  [[ -f "$TRAIN_RUN/lora_sft.jsonl" ]] \
    || die "Missing $TRAIN_RUN/lora_sft.jsonl after minimise"
  state_mark "minimise_done"
fi

if ! state_has "finetune_done"; then
  resolve_train_run
  _log "Phase 3: finetune SFT + DPO (stopping vLLM) ..."
  bash "$HOST_DIR/run.sh" --down || true
  bash "$HOST_DIR/run.sh" --finetune-lora "$TRAIN_RUN"
  bash "$HOST_DIR/run.sh" --finetune-dpo "$TRAIN_RUN"
  rm -f /data/models/Qwen3.5-9B-qag-sft-merged/qag_merge_manifest.json
  rm -f /data/models/Qwen3.5-9B-qag-dpo-merged/qag_merge_manifest.json
  state_mark "finetune_done"
fi

if ! state_has "fair_eval_done"; then
  resolve_train_run
  _log "Phase 4: fair adapter eval on 500 holdout docs ..."
  bash "$HOST_DIR/run.sh" --vllm-up all
  bash "$HOST_DIR/scripts/lora/run_fair_adapter_eval.sh" \
    "$TRAIN_RUN" \
    --full \
    --eval-dir "$EVAL_DIR" \
    --parallel "$PARALLEL"
  state_mark "fair_eval_done"
fi

_log "Campaign complete."
_log "Report: $EVAL_DIR/EVAL_REPORT.md"
if [[ -f "$EVAL_DIR/EVAL_REPORT.md" ]]; then
  cat "$EVAL_DIR/EVAL_REPORT.md"
fi
