#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# run.sh  --  Run the QAGRedo pipeline (vLLM + QAGRedo containers)
# ============================================================================
#
# Everything lives in one directory (qagredo_host/).  This script, the compose
# file, code, config, data, and output all sit side-by-side.  Docker mounts
# them directly, so any edit you make here persists across container restarts.
#
# Output files are owned by YOUR user (not root) because the container's
# entrypoint maps its internal user to your UID/GID at startup.
#
# Usage:
#   cd qagredo_host
#   bash run.sh                    # start vLLM + run pipeline
#   bash run.sh --down             # stop all containers
#   bash run.sh --logs             # tail vLLM logs
#   bash run.sh --status           # show container status
#
# Editable files (changes persist and are picked up on next run):
#   config/config.yaml             # pipeline configuration
#   data/                          # input JSONL files
#   utils/                         # Python source code
#   run_qa_pipeline.py             # main entry point
#   scripts/                       # helper scripts
#
# Environment overrides:
#   VLLM_MODEL=/models/<folder>
#   VLLM_SERVED_MODEL_NAME=<org/model>
#   VLLM_API_KEY=my-key
#   VLLM_TP_SIZE=2
#   VLLM_MAX_MODEL_LEN=8192
#   VLLM_GPU_UTIL=0.85
# ============================================================================

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source .env if present (written by setup_offline.sh)
[[ -f "$HOST_DIR/.env" ]] && set -a && source "$HOST_DIR/.env" && set +a

# ---- host user identity ----
# Passed to docker-compose → container entrypoint adjusts its runtime user
# to match, so all output files are owned by you.
export HOST_UID="${HOST_UID:-$(id -u)}"
export HOST_GID="${HOST_GID:-$(id -g)}"

# ---- defaults: Generator LLM (Llama on GPU 0, port 8100) ----
export VLLM_MODEL="${VLLM_MODEL:-/models/Meta-Llama-3.1-8B-Instruct}"
export VLLM_SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-meta-llama/Meta-Llama-3.1-8B-Instruct}"
export VLLM_API_KEY="${VLLM_API_KEY:-llama-local}"
export VLLM_TP_SIZE="${VLLM_TP_SIZE:-1}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
export VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.90}"

# ---- defaults: Judge LLM (Qwen on GPU 1, port 8101) ----
export VLLM_JUDGE_MODEL="${VLLM_JUDGE_MODEL:-/models/Qwen2.5-7B-Instruct}"
export VLLM_JUDGE_SERVED_NAME="${VLLM_JUDGE_SERVED_NAME:-Qwen/Qwen2.5-7B-Instruct}"
export VLLM_JUDGE_API_KEY="${VLLM_JUDGE_API_KEY:-qwen-local}"
export VLLM_JUDGE_MAX_MODEL_LEN="${VLLM_JUDGE_MAX_MODEL_LEN:-8192}"
export VLLM_JUDGE_GPU_UTIL="${VLLM_JUDGE_GPU_UTIL:-0.90}"

COMPOSE_FILE="$HOST_DIR/docker-compose.offline.yml"

_log() { echo "[run] $*"; }
_warn() { echo "[run][WARN] $*" >&2; }
die()  { echo "[run][ERROR] $*" >&2; exit 1; }

# ---- handle flags ----
case "${1:-}" in
  --down)
    _log "Stopping all containers..."
    docker compose -f "$COMPOSE_FILE" down
    _log "Done."
    exit 0
    ;;
  --logs)
    _log "Showing vLLM container logs (Ctrl+C to stop)..."
    docker compose -f "$COMPOSE_FILE" logs -f vllm vllm-judge
    exit 0
    ;;
  --status)
    docker compose -f "$COMPOSE_FILE" ps
    echo ""
    if curl -sf http://localhost:8100/health >/dev/null 2>&1; then
      echo "  vLLM Generator (Llama): healthy (http://localhost:8100)"
    else
      echo "  vLLM Generator (Llama): not responding"
    fi
    if curl -sf http://localhost:8101/health >/dev/null 2>&1; then
      echo "  vLLM Judge    (Qwen) : healthy (http://localhost:8101)"
    else
      echo "  vLLM Judge    (Qwen) : not responding"
    fi
    exit 0
    ;;
  --show-config)
    echo ""
    echo "=== config/config.yaml ==="
    echo ""
    cat "$HOST_DIR/config/config.yaml"
    echo ""
    echo "=== Environment overrides ==="
    echo "  VLLM_MODEL           = $VLLM_MODEL"
    echo "  VLLM_SERVED_MODEL_NAME = $VLLM_SERVED_MODEL_NAME"
    echo "  VLLM_MAX_MODEL_LEN   = $VLLM_MAX_MODEL_LEN"
    echo "  VLLM_TP_SIZE         = $VLLM_TP_SIZE"
    echo "  VLLM_GPU_UTIL        = $VLLM_GPU_UTIL"
    echo "  VLLM_API_KEY         = $VLLM_API_KEY"
    echo "  VLLM_JUDGE_MODEL     = $VLLM_JUDGE_MODEL"
    echo "  VLLM_JUDGE_SERVED_NAME = $VLLM_JUDGE_SERVED_NAME"
    echo "  VLLM_JUDGE_API_KEY   = $VLLM_JUDGE_API_KEY"
    echo "  HOST_UID             = $HOST_UID"
    echo "  HOST_GID             = $HOST_GID"
    echo ""
    echo "=== Input data ==="
    echo "  Files in data/:"
    ls -lh "$HOST_DIR/data/" 2>/dev/null || echo "    (empty)"
    echo ""
    exit 0
    ;;
  --summarize)
    shift
    bash "$HOST_DIR/scripts/utils/summarize_run.sh" "$@"
    exit 0
    ;;
  --convert)
    shift
    if [[ $# -lt 2 ]]; then
      echo "Usage: bash run.sh --convert <input.json> <output.jsonl>"
      echo ""
      echo "Converts JSON documents to QAGRedo JSONL format."
      echo "  input:  path to JSON file (relative to qagredo_host/ or absolute)"
      echo "  output: path to output JSONL file"
      exit 1
    fi
    python3 "$HOST_DIR/scripts/conversion/convert_to_qagredo_jsonl.py" "$@"
    exit 0
    ;;
  -h|--help)
    cat <<USAGE
Usage: bash run.sh [COMMAND]

Pipeline:
  (no args)           Start vLLM + run QAGRedo pipeline
  --down              Stop all containers
  --status            Show container status + vLLM health
  --logs              Tail vLLM container logs (Ctrl+C to stop)

Configuration:
  --show-config       Display current config.yaml + env overrides + data files

Results:
  --summarize         Summarise results (pass --latest, --all, or --json)
                      e.g. bash run.sh --summarize --latest --json

Data:
  --convert IN OUT    Convert JSON to JSONL format
                      e.g. bash run.sh --convert data/input.json data/output.jsonl

Help:
  -h, --help          Show this message

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Everything lives in this directory (qagredo_host/):

  ★ config/config.yaml    Pipeline settings (edit this)
  ★ data/                 Your input files (put JSONL here)
  ★ output/               Results appear here
    utils/                Python modules
    run_qa_pipeline.py    Main entry point
    OFFLINE_GUIDE.md      Full offline server guide
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE
    exit 0
    ;;
esac

# ============================================================================
# Preflight validation
# ============================================================================
[[ -f "$COMPOSE_FILE" ]] || die "Missing: $COMPOSE_FILE (are you in the qagredo_host directory?)"
docker info >/dev/null 2>&1 || die "Docker is not running or not accessible"

if [[ ! -f "$HOST_DIR/config/config.yaml" ]]; then
  die "Config file not found: $HOST_DIR/config/config.yaml
  This file is required. If you deleted it, re-extract from the bundle:
    tar xzf qagredo_bundle.tar.gz"
fi

if [[ ! -f "$HOST_DIR/run_qa_pipeline.py" ]]; then
  die "Missing: $HOST_DIR/run_qa_pipeline.py (are you in the qagredo_host directory?)"
fi

# Ensure writable directories exist
mkdir -p "$HOST_DIR/output" "$HOST_DIR/hf_cache" 2>/dev/null || true

# ---- show configuration ----
_log "==========================================="
_log "QAGRedo Pipeline"
_log "==========================================="
_log "  Host dir              : $HOST_DIR"
_log "  Config                : $HOST_DIR/config/config.yaml"
_log "  Generator (Llama)     : $VLLM_MODEL (GPU 0, port 8100)"
_log "  Judge     (Qwen)      : $VLLM_JUDGE_MODEL (GPU 1, port 8101)"
_log "  Container user        : UID=$HOST_UID GID=$HOST_GID"
_log "  Compose file          : $COMPOSE_FILE"
_log "==========================================="

# Ensure judge hf_cache directory exists
mkdir -p "$HOST_DIR/hf_cache_judge" 2>/dev/null || true

# ---- start both vLLM services ----
_log "Starting vLLM Generator (Llama on GPU 0, port 8100) ..."
_log "Starting vLLM Judge     (Qwen  on GPU 1, port 8101) ..."
docker compose -f "$COMPOSE_FILE" up -d vllm vllm-judge

# ---- wait for Generator (Llama) ----
_log "Waiting for Generator (Llama) at http://localhost:8100/health ..."
HEALTH_TIMEOUT=300
HEALTH_INTERVAL=5
elapsed=0
while true; do
  if curl -sf http://localhost:8100/health >/dev/null 2>&1; then
    _log "Generator (Llama) is ready! (took ~${elapsed}s)"
    break
  fi
  if [[ "$elapsed" -ge "$HEALTH_TIMEOUT" ]]; then
    die "Generator (Llama) did not become healthy within ${HEALTH_TIMEOUT}s. Check: docker logs qagredo-vllm --tail 50"
  fi
  sleep "$HEALTH_INTERVAL"
  elapsed=$((elapsed + HEALTH_INTERVAL))
  if (( elapsed % 30 == 0 )); then
    _log "  Still waiting for Generator... (${elapsed}s elapsed)"
  fi
done

# ---- wait for Judge (Qwen) ----
_log "Waiting for Judge (Qwen) at http://localhost:8101/health ..."
elapsed=0
while true; do
  if curl -sf http://localhost:8101/health >/dev/null 2>&1; then
    _log "Judge (Qwen) is ready! (took ~${elapsed}s)"
    break
  fi
  if [[ "$elapsed" -ge "$HEALTH_TIMEOUT" ]]; then
    die "Judge (Qwen) did not become healthy within ${HEALTH_TIMEOUT}s. Check: docker logs qagredo-vllm-judge --tail 50"
  fi
  sleep "$HEALTH_INTERVAL"
  elapsed=$((elapsed + HEALTH_INTERVAL))
  if (( elapsed % 30 == 0 )); then
    _log "  Still waiting for Judge... (${elapsed}s elapsed)"
  fi
done

# ---- run QAGRedo pipeline ----
_log "Running QAGRedo pipeline ..."

docker compose -f "$COMPOSE_FILE" run --rm qagredo
PIPELINE_EXIT=$?

# ---- safety-net: fix ownership of ALL writable dirs ----
# The container entrypoint has its own EXIT trap, but as a belt-and-suspenders
# measure we also fix from the host side.  This covers:
#   - vLLM writing root-owned files to hf_cache/
#   - any edge case where the entrypoint trap didn't fire
_log "Fixing file ownership (UID=$HOST_UID GID=$HOST_GID) ..."
# --privileged --userns=host ensures chown works even when Docker uses
# user namespace remapping (which maps container root to an unprivileged
# host UID, making normal chown/chmod fail with "Operation not permitted").
docker run --rm --privileged --userns=host -u 0 \
  -v "$HOST_DIR/output:/fix/output" \
  -v "$HOST_DIR/hf_cache:/fix/hf_cache" \
  -v "$HOST_DIR/hf_cache_judge:/fix/hf_cache_judge" \
  -v "$HOST_DIR/config:/fix/config" \
  -v "$HOST_DIR/data:/fix/data" \
  qagredo-v1:latest bash -c \
    "chown -R $HOST_UID:$HOST_GID /fix/output /fix/hf_cache /fix/hf_cache_judge /fix/config /fix/data 2>/dev/null || true" \
  2>/dev/null || _warn "Post-run permission fix skipped (non-fatal)"

if [[ "$PIPELINE_EXIT" -ne 0 ]]; then
  _warn "Pipeline exited with code $PIPELINE_EXIT"
fi

_log "Done! Outputs are in: $HOST_DIR/output/"
echo ""
echo "Next steps:"
echo "  bash run.sh --summarize --latest       Summarise this run"
echo "  bash run.sh --summarize --latest --json Save summary as JSON"
echo "  bash run.sh --show-config               Show current settings"
echo "  bash run.sh --down                      Stop all containers"
echo ""
echo "To edit config and re-run:"
echo "  vi config/config.yaml && bash run.sh"
echo ""
echo "Full guide: OFFLINE_GUIDE.md"
