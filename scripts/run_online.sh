#!/usr/bin/env bash
set -euo pipefail

# Layman-friendly runner for this server:
# - Creates a repo-local qagredo_host/ structure
# - Copies sample config + sample input data
# - Extracts Llama model zip and materializes it into models_llm/
# - Sets up MiniLM cache if available
# - Starts vLLM, waits for /health, then runs QAGRedo
#
# Usage:
#   cd /path/to/qagredo
#   bash scripts/run_online.sh           # safe mode (no overwrite)
#   bash scripts/run_online.sh --overwrite

OVERWRITE=0
if [[ "${1:-}" == "--overwrite" ]]; then
  OVERWRITE=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: bash scripts/run_online.sh [--overwrite]" >&2
  exit 2
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_DIR_DEFAULT="${REPO_DIR}/qagredo_host"
HOST_DIR="${QAGREDO_HOST_DIR:-$HOST_DIR_DEFAULT}"

# Default to the repo's sample input (you can override with DATA_SRC=...).
DATA_SRC_DEFAULT="${REPO_DIR}/data/dev-data.jsonl"
DATA_SRC="${DATA_SRC:-$DATA_SRC_DEFAULT}"

# Optional: place the model zip under the repo root (override with MODEL_ZIP=...).
MODEL_ZIP_DEFAULT="${REPO_DIR}/Meta-Llama-3.1-8B-Instruct_hf_cache.zip"
MODEL_ZIP="${MODEL_ZIP:-$MODEL_ZIP_DEFAULT}"
MODEL_DIR_NAME="Meta-Llama-3.1-8B-Instruct"

VLLM_IMAGE_DEFAULT="vllm/vllm-openai:v0.5.3.post1"
VLLM_IMAGE="${VLLM_IMAGE:-$VLLM_IMAGE_DEFAULT}"
VLLM_MODEL_IN_CONTAINER="/models/${MODEL_DIR_NAME}"
VLLM_API_KEY="${VLLM_API_KEY:-llama-local}"
VLLM_SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-meta-llama/Meta-Llama-3.1-8B-Instruct}"

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

info() { echo "[INFO] $*"; }
ok() { echo "[OK] $*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

copy_file() {
  local src="$1"
  local dst="$2"
  if [[ ! -f "$src" ]]; then
    die "Missing file: $src"
  fi
  if [[ -f "$dst" && "$OVERWRITE" -ne 1 ]]; then
    info "Keeping existing file: $dst"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  cp -a "$src" "$dst"
}

ensure_dir() {
  local d="$1"
  mkdir -p "$d"
}

main() {
  require_cmd docker
  require_cmd curl
  require_cmd unzip

  [[ -d "$REPO_DIR" ]] || die "Repo folder not found: $REPO_DIR"
  [[ -f "$REPO_DIR/docker-compose.offline.yml" ]] || die "Missing: $REPO_DIR/docker-compose.offline.yml"

  info "Repo folder: $REPO_DIR"
  info "Host folder: $HOST_DIR"
  info "Overwrite mode: $OVERWRITE"

  ensure_dir "$HOST_DIR"
  ensure_dir "$HOST_DIR/config"
  ensure_dir "$HOST_DIR/data"
  ensure_dir "$HOST_DIR/output"
  ensure_dir "$HOST_DIR/models_llm"
  ensure_dir "$HOST_DIR/models_embed"
  ensure_dir "$HOST_DIR/hf_cache"

  # Copy configs
  copy_file "$REPO_DIR/config/config.yaml" "$HOST_DIR/config/config.yaml"

  # Copy sample input data (skip if already present)
  local data_dst="$HOST_DIR/data/dev-data.jsonl"
  if [[ -f "$data_dst" && "$OVERWRITE" -ne 1 ]]; then
    info "Keeping existing data file: $data_dst"
  else
    [[ -f "$DATA_SRC" ]] || die "Missing sample input file: $DATA_SRC (set DATA_SRC=... or place dev-data.jsonl at $data_dst)"
    copy_file "$DATA_SRC" "$data_dst"
  fi

  # Extract model zip into HF cache
  ensure_dir "$HOST_DIR/hf_cache/hub"
  local hf_model_dir="$HOST_DIR/hf_cache/hub/models--meta-llama--Meta-Llama-3.1-8B-Instruct"
  local hf_snapshots_dir="$hf_model_dir/snapshots"
  if [[ "$OVERWRITE" -ne 1 && -d "$hf_snapshots_dir" ]] && ls -1 "$hf_snapshots_dir" >/dev/null 2>&1; then
    info "HF model cache already present at $hf_model_dir (skipping unzip)"
  else
    [[ -f "$MODEL_ZIP" ]] || die "Missing model zip: $MODEL_ZIP (set MODEL_ZIP=... or pre-populate $hf_model_dir to skip unzip)"
    info "Extracting model zip into HF cache (non-interactive)"
    unzip -o -q "$MODEL_ZIP" -d "$HOST_DIR/hf_cache/hub"
  fi

  # Materialize model folder under models_llm/
  local model_dst="$HOST_DIR/models_llm/$MODEL_DIR_NAME"
  if [[ -d "$model_dst" && "$OVERWRITE" -ne 1 ]]; then
    info "Keeping existing model folder: $model_dst"
  else
    rm -rf "$model_dst"
    ensure_dir "$model_dst"
    local snapshots_dir="$HOST_DIR/hf_cache/hub/models--meta-llama--Meta-Llama-3.1-8B-Instruct/snapshots"
    [[ -d "$snapshots_dir" ]] || die "Missing snapshots dir after unzip: $snapshots_dir"
    local snapshot
    snapshot="$(ls -1 "$snapshots_dir" | head -n 1 || true)"
    [[ -n "$snapshot" ]] || die "No snapshots found in: $snapshots_dir"
    info "Copying snapshot $snapshot into $model_dst (may take a while)"
    cp -aL "$snapshots_dir/$snapshot/." "$model_dst/"
    ok "Model folder ready: $model_dst"
  fi

  # MiniLM cache (best-effort)
  local minilm_src="$HOME/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2"
  if [[ -d "$minilm_src" ]]; then
    ensure_dir "$HOST_DIR/hf_cache/hub"
    if [[ ! -d "$HOST_DIR/hf_cache/hub/models--sentence-transformers--all-MiniLM-L6-v2" || "$OVERWRITE" -eq 1 ]]; then
      info "Copying MiniLM cache into host folder"
      rm -rf "$HOST_DIR/hf_cache/hub/models--sentence-transformers--all-MiniLM-L6-v2" || true
      cp -a "$minilm_src" "$HOST_DIR/hf_cache/hub/"
    fi
    ensure_dir "$HOST_DIR/hf_cache/sentence-transformers"
    ln -sfn ../hub/models--sentence-transformers--all-MiniLM-L6-v2 \
      "$HOST_DIR/hf_cache/sentence-transformers/models--sentence-transformers--all-MiniLM-L6-v2"
    ok "MiniLM cache linked for offline use"

    # Also materialize a direct sentence-transformers model folder under models_embed/
    # (more reliable than HF cache layout when running fully offline)
    local embed_dst="$HOST_DIR/models_embed/all-MiniLM-L6-v2"
    local hf_minilm_dir="$HOST_DIR/hf_cache/hub/models--sentence-transformers--all-MiniLM-L6-v2"
    local snapshots_dir="$hf_minilm_dir/snapshots"
    if [[ -d "$embed_dst" && "$OVERWRITE" -ne 1 ]]; then
      info "Keeping existing embedding model folder: $embed_dst"
    else
      rm -rf "$embed_dst"
      ensure_dir "$embed_dst"
      if [[ -d "$snapshots_dir" ]] && ls -1 "$snapshots_dir" >/dev/null 2>&1; then
        local snap
        snap="$(ls -1 "$snapshots_dir" | head -n 1 || true)"
        if [[ -n "$snap" && -d "$snapshots_dir/$snap" ]]; then
          info "Materializing MiniLM snapshot $snap into $embed_dst"
          cp -aL "$snapshots_dir/$snap/." "$embed_dst/"
          ok "Embedding model folder ready: $embed_dst"
        else
          info "MiniLM snapshots found but could not pick one under $snapshots_dir (skipping models_embed materialization)"
        fi
      else
        info "MiniLM cache present but snapshots folder missing under $snapshots_dir (skipping models_embed materialization)"
      fi
    fi
  else
    info "MiniLM cache not found at $minilm_src (will run with fallback warning)"
  fi

  # Pull vLLM image if missing (best-effort)
  if ! docker image inspect "$VLLM_IMAGE" >/dev/null 2>&1; then
    info "Pulling vLLM image: $VLLM_IMAGE"
    docker pull "$VLLM_IMAGE"
  fi

  # Ensure qagredo image exists
  if ! docker image inspect "qagredo-v1:latest" >/dev/null 2>&1; then
    die "Missing docker image qagredo-v1:latest. Build or load it first on this machine."
  fi

  # Start vLLM
  info "Starting vLLM (GPU) on port 8100"
  (
    cd "$REPO_DIR"
    export QAGREDO_HOST_DIR="$HOST_DIR"
    export VLLM_IMAGE="$VLLM_IMAGE"
    export VLLM_MODEL="$VLLM_MODEL_IN_CONTAINER"
    export VLLM_API_KEY="$VLLM_API_KEY"
    export VLLM_SERVED_MODEL_NAME="$VLLM_SERVED_MODEL_NAME"
    docker compose -f docker-compose.offline.yml up -d vllm
  )

  info "Waiting for vLLM health: http://localhost:8100/health"
  for i in $(seq 1 120); do
    if curl -sf http://localhost:8100/health >/dev/null; then
      ok "vLLM is ready"
      break
    fi
    sleep 2
    if [[ "$i" -eq 120 ]]; then
      die "Timed out waiting for vLLM. Check logs: docker logs qagredo-vllm --tail 100"
    fi
  done

  info "Running QAGRedo pipeline (this may take a while)"
  (
    cd "$REPO_DIR"
    export QAGREDO_HOST_DIR="$HOST_DIR"
    docker compose -f docker-compose.offline.yml run --rm qagredo
  )

  ok "Done. Output folder:"
  echo "  $HOST_DIR/output"
  echo "Latest JSON files:"
  find "$HOST_DIR/output" -name '*.json' 2>/dev/null | tail -n 5 || true
}

main

