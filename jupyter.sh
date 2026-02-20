#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# jupyter.sh  --  Start Jupyter Lab for interactive offline use
# ============================================================================
#
# Everything lives in this directory (qagredo_host/).  Jupyter mounts it
# directly so you can edit code, config, and data from the notebook UI.
#
# Usage:
#   cd qagredo_host
#   bash jupyter.sh                # start Jupyter Lab (+ vLLM)
#   bash jupyter.sh --no-vllm     # start Jupyter Lab only (no GPU needed)
#   bash jupyter.sh --down        # stop all containers
#
# Then open in your browser:
#   http://localhost:8899
#   (no token/password required)
#
# SSH tunnel (if on a remote server):
#   ssh -L 8899:localhost:8899 user@offline-server
# ============================================================================

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source .env if present
[[ -f "$HOST_DIR/.env" ]] && set -a && source "$HOST_DIR/.env" && set +a

# ---- host user identity (so container creates files owned by you, not root) ----
export HOST_UID="${HOST_UID:-$(id -u)}"
export HOST_GID="${HOST_GID:-$(id -g)}"

# ---- defaults: Generator (Llama on GPU 0) ----
export VLLM_MODEL="${VLLM_MODEL:-/models/Meta-Llama-3.1-8B-Instruct}"
export VLLM_SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-meta-llama/Meta-Llama-3.1-8B-Instruct}"
export VLLM_API_KEY="${VLLM_API_KEY:-llama-local}"
export VLLM_TP_SIZE="${VLLM_TP_SIZE:-1}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
export VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.90}"
# ---- defaults: Judge (Qwen on GPU 1) ----
export VLLM_JUDGE_MODEL="${VLLM_JUDGE_MODEL:-/models/Qwen2.5-7B-Instruct}"
export VLLM_JUDGE_SERVED_NAME="${VLLM_JUDGE_SERVED_NAME:-Qwen/Qwen2.5-7B-Instruct}"
export VLLM_JUDGE_API_KEY="${VLLM_JUDGE_API_KEY:-qwen-local}"
export VLLM_JUDGE_MAX_MODEL_LEN="${VLLM_JUDGE_MAX_MODEL_LEN:-8192}"
export VLLM_JUDGE_GPU_UTIL="${VLLM_JUDGE_GPU_UTIL:-0.90}"

JUPYTER_PORT="${JUPYTER_PORT:-8899}"

COMPOSE_FILE="$HOST_DIR/docker-compose.offline.yml"

START_VLLM=1

_log() { echo "[jupyter] $*"; }
die()  { echo "[jupyter][ERROR] $*" >&2; exit 1; }

# ---- parse args ----
for arg in "$@"; do
  case "$arg" in
    --no-vllm)
      START_VLLM=0
      ;;
    --down)
      _log "Stopping all containers..."
      docker compose -f "$COMPOSE_FILE" down
      docker rm -f qagredo-jupyter 2>/dev/null || true
      _log "Done."
      exit 0
      ;;
    --status)
      docker compose -f "$COMPOSE_FILE" ps
      exit 0
      ;;
    -h|--help)
      cat <<USAGE
Usage: bash jupyter.sh [OPTIONS]

Options:
  (no args)     Start vLLM (GPU) + Jupyter Lab
  --no-vllm     Start Jupyter Lab only (no GPU needed)
  --down        Stop all containers (vLLM + Jupyter)
  --status      Show container status
  -h, --help    Show this message

Access Jupyter at: http://localhost:${JUPYTER_PORT}  (no password)

If connecting from your laptop to a remote server:
  ssh -L ${JUPYTER_PORT}:localhost:${JUPYTER_PORT} user@offline-server

Environment:
  JUPYTER_PORT  Port for Jupyter Lab (default: 8899)
USAGE
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

# ---- preflight ----
[[ -f "$COMPOSE_FILE" ]] || die "Missing: $COMPOSE_FILE (are you in the qagredo_host directory?)"
docker info >/dev/null 2>&1 || die "Docker is not running or not accessible"

# ---- start vLLM (optional) ----
if [[ "$START_VLLM" -eq 1 ]]; then
  mkdir -p "$HOST_DIR/hf_cache_judge" 2>/dev/null || true

  _log "Starting vLLM Generator (Llama) + Judge (Qwen) ..."
  docker compose -f "$COMPOSE_FILE" up -d vllm vllm-judge

  _log "Waiting for Generator (Llama) to become healthy ..."
  HEALTH_TIMEOUT=300
  HEALTH_INTERVAL=5
  elapsed=0
  while true; do
    if curl -sf http://localhost:8100/health >/dev/null 2>&1; then
      _log "Generator (Llama) is ready! (took ~${elapsed}s)"
      break
    fi
    if [[ "$elapsed" -ge "$HEALTH_TIMEOUT" ]]; then
      die "Generator did not become healthy within ${HEALTH_TIMEOUT}s. Check: docker logs qagredo-vllm --tail 50"
    fi
    sleep "$HEALTH_INTERVAL"
    elapsed=$((elapsed + HEALTH_INTERVAL))
    if (( elapsed % 30 == 0 )); then
      _log "  Still waiting for Generator... (${elapsed}s elapsed)"
    fi
  done

  _log "Waiting for Judge (Qwen) to become healthy ..."
  elapsed=0
  while true; do
    if curl -sf http://localhost:8101/health >/dev/null 2>&1; then
      _log "Judge (Qwen) is ready! (took ~${elapsed}s)"
      break
    fi
    if [[ "$elapsed" -ge "$HEALTH_TIMEOUT" ]]; then
      die "Judge did not become healthy within ${HEALTH_TIMEOUT}s. Check: docker logs qagredo-vllm-judge --tail 50"
    fi
    sleep "$HEALTH_INTERVAL"
    elapsed=$((elapsed + HEALTH_INTERVAL))
    if (( elapsed % 30 == 0 )); then
      _log "  Still waiting for Judge... (${elapsed}s elapsed)"
    fi
  done
else
  _log "Skipping vLLM (--no-vllm). Jupyter will start without GPU backend."
fi

# ---- start Jupyter Lab ----
_log "Starting Jupyter Lab on port $JUPYTER_PORT ..."
_log ""
_log "  URL:  http://localhost:${JUPYTER_PORT}"
_log "  (no token/password required)"
_log ""
_log "  SSH tunnel (if remote):  ssh -L ${JUPYTER_PORT}:localhost:${JUPYTER_PORT} user@this-server"
_log ""
_log "  Press Ctrl+C to stop Jupyter."
_log ""

CONTAINER_NAME="qagredo-jupyter"

# Stop any existing jupyter container
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Use -it for interactive terminals, -d for non-interactive
DOCKER_TTY_FLAGS="-it"
if ! tty -s 2>/dev/null; then
  DOCKER_TTY_FLAGS="-d"
fi

docker run --rm $DOCKER_TTY_FLAGS \
  --name "$CONTAINER_NAME" \
  --network qagredo_offline_default \
  -p "${JUPYTER_PORT}:8888" \
  -e HOST_UID="${HOST_UID}" \
  -e HOST_GID="${HOST_GID}" \
  -e OFFLINE_MODE=1 \
  -e HF_HUB_OFFLINE=1 \
  -e TRANSFORMERS_OFFLINE=1 \
  -e PYDANTIC_DISABLE_PLUGIN_LOADING=1 \
  -e SENTENCE_TRANSFORMERS_MODEL_PATH="/opt/models_embed/all-MiniLM-L6-v2" \
  -e VLLM_BASE_URL="http://vllm:8100/v1" \
  -e VLLM_API_KEY="${VLLM_API_KEY}" \
  -e VLLM_JUDGE_BASE_URL="http://vllm-judge:8101/v1" \
  -e VLLM_JUDGE_MODEL="${VLLM_JUDGE_SERVED_NAME}" \
  -e VLLM_JUDGE_API_KEY="${VLLM_JUDGE_API_KEY}" \
  -e JUPYTER_DATA_DIR=/workspace/.jupyter/data \
  -e JUPYTER_RUNTIME_DIR=/workspace/.jupyter/runtime \
  -v "${HOST_DIR}/run_qa_pipeline.py:/workspace/run_qa_pipeline.py:rw" \
  -v "${HOST_DIR}/utils:/workspace/utils:rw" \
  -v "${HOST_DIR}/scripts:/workspace/scripts:rw" \
  -v "${HOST_DIR}/config:/workspace/config:rw" \
  -v "${HOST_DIR}/data:/workspace/data:rw" \
  -v "${HOST_DIR}/output:/workspace/output:rw" \
  -v "${HOST_DIR}/hf_cache:/opt/hf_cache:rw" \
  -v "${HOST_DIR}/models_embed:/opt/models_embed:rw" \
  qagredo-v1:latest \
  jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --ServerApp.token='' --ServerApp.password='' --ServerApp.allow_origin='*'

# ---- safety-net: fix ownership after Jupyter exits ----
_log "Fixing file ownership (UID=$HOST_UID GID=$HOST_GID) ..."
docker run --rm --privileged --userns=host -u 0 \
  -v "$HOST_DIR/output:/fix/output" \
  -v "$HOST_DIR/hf_cache:/fix/hf_cache" \
  -v "$HOST_DIR/hf_cache_judge:/fix/hf_cache_judge" \
  -v "$HOST_DIR/config:/fix/config" \
  -v "$HOST_DIR/data:/fix/data" \
  qagredo-v1:latest bash -c \
    "chown -R $HOST_UID:$HOST_GID /fix/output /fix/hf_cache /fix/hf_cache_judge /fix/config /fix/data 2>/dev/null || true" \
  2>/dev/null || true
