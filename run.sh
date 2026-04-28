#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# run.sh — host Ollama (default) or legacy vLLM stack + QAGRedo pipeline
# ============================================================================
#
# Stay in the folder that contains this script, docker-compose.yml, and .env.
# Default: Ollama on the host (port 11434). Start it yourself (`ollama serve`).
#
# Legacy GPU vLLM (two containers): set QAGREDO_USE_VLLM_STACK=1 in .env and
# use docker-compose.vllm-stack.yml (see comments in .env).
#
# Common commands:
#   bash run.sh              # run pipeline (waits for Ollama on host)
#   bash run.sh --down       # stop compose project containers
#   bash run.sh --logs       # tail qagredo / vLLM logs
#   bash run.sh --status     # compose ps + backend health
#
# Where to put settings:
#   .env                           — host paths + QAGREDO_PROFILE selector
#   config/config.<profile>.yaml   — everything else (models, temps, retries).
#                                    Each profile has its own self-contained
#                                    YAML so the file you open is exactly
#                                    what runs — no hidden env overrides.
#
# Extra options (optional, in .env or shell):
#   OLLAMA_MODEL, OLLAMA_JUDGE_MODEL, OLLAMA_HOST_PORT
#   QAGREDO_USE_OLLAMA=1 — legacy flag; profile YAML selects the provider
#   QAGREDO_OFFLINE_HOST + QAGREDO_OFFLINE_INPUT — see .env (sets DATA_DIR)
# ============================================================================

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() { echo "[run][ERROR] $*" >&2; exit 1; }

# Load .env next to this script (your presets and ports).
# Shell-supplied vars always win over .env — so
#     QAGREDO_PROFILE=vllm bash run.sh
# works even when .env says `QAGREDO_PROFILE=dev`.
if [[ -f "$HOST_DIR/.env" ]]; then
  # Snapshot existing shell env (just the names), then source .env, then
  # restore the snapshot so caller-provided values take precedence.
  _env_snapshot_file="$(mktemp)"
  export -p > "$_env_snapshot_file"
  set -a
  # shellcheck disable=SC1091
  source "$HOST_DIR/.env"
  set +a
  # shellcheck disable=SC1090
  source "$_env_snapshot_file"
  rm -f "$_env_snapshot_file"
fi

# Pick host folder for input files: .env preset, or DATA_DIR, or ./data.
if [[ -n "${QAGREDO_OFFLINE_HOST:-}" && -n "${QAGREDO_OFFLINE_INPUT:-}" ]]; then
  case "${QAGREDO_OFFLINE_INPUT}" in
    txt|json) ;;
    *)
      die "QAGREDO_OFFLINE_INPUT must be txt or json (got ${QAGREDO_OFFLINE_INPUT})"
      ;;
  esac
  case "${QAGREDO_OFFLINE_HOST}" in
    [Rr]epo|[Ll]inux)
      _repo="${QAGREDO_REPO_DATA_ROOT:-${QAGREDO_LINUX_DATA_ROOT:-}}"
      export DATA_DIR="${_repo:-$HOST_DIR/data}"
      ;;
    [Ww]indows|[Ww][Ss][Ll])
      _wroot="${QAGREDO_WINDOWS_DOWNLOADS_ROOT:-/mnt/c/Users/tyewhong/Downloads}"
      export DATA_DIR="${_wroot}/${QAGREDO_OFFLINE_INPUT}"
      ;;
    [Dd]ata)
      _droot="${QAGREDO_SHARED_DATA_ROOT:-/data/local/tyewhong/Data}"
      export DATA_DIR="${_droot}/${QAGREDO_OFFLINE_INPUT}"
      ;;
    *)
      die "QAGREDO_OFFLINE_HOST must be repo, data, or wsl (got ${QAGREDO_OFFLINE_HOST}; linux=repo; windows=wsl)"
      ;;
  esac
fi
export DATA_DIR="${DATA_DIR:-${QAGREDO_DATA_DIR:-$HOST_DIR/data}}"
export QAGREDO_DATA_DIR="${QAGREDO_DATA_DIR:-$DATA_DIR}"

# Tell Docker your user so new files on disk belong to you.
#
# Safety default: if .env has stale HOST_UID/HOST_GID from another machine,
# auto-correct to the current shell user to prevent permission regressions.
# To intentionally use a different owner mapping, set:
#   QAGREDO_ALLOW_FOREIGN_OWNERSHIP=1
_current_uid="$(id -u)"
_current_gid="$(id -g)"
export HOST_UID="${HOST_UID:-${_current_uid}}"
export HOST_GID="${HOST_GID:-${_current_gid}}"
if [[ "${HOST_UID}" != "${_current_uid}" || "${HOST_GID}" != "${_current_gid}" ]]; then
  if [[ "${QAGREDO_ALLOW_FOREIGN_OWNERSHIP:-0}" != "1" ]]; then
    echo "[run][WARN] HOST_UID/HOST_GID (${HOST_UID}:${HOST_GID}) do not match current user (${_current_uid}:${_current_gid}). Auto-correcting to current user."
    echo "[run][WARN] To keep foreign ownership mapping, set QAGREDO_ALLOW_FOREIGN_OWNERSHIP=1."
    export HOST_UID="${_current_uid}"
    export HOST_GID="${_current_gid}"
  fi
fi

export QAGREDO_USE_VLLM_STACK="${QAGREDO_USE_VLLM_STACK:-0}"
export QAGREDO_USE_OLLAMA="${QAGREDO_USE_OLLAMA:-1}"
export OLLAMA_HOST_PORT="${OLLAMA_HOST_PORT:-11434}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:${OLLAMA_HOST_PORT}/v1}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:9b}"
export OLLAMA_JUDGE_BASE_URL="${OLLAMA_JUDGE_BASE_URL:-http://localhost:${OLLAMA_HOST_PORT}/v1}"
# Must match judge.model in config/config.dev.yaml and config/config.kubeflow.yaml
export OLLAMA_JUDGE_MODEL="${OLLAMA_JUDGE_MODEL:-llama3.1:8b-instruct-fp16}"

# ---------------------------------------------------------------------------
# Profile selection: one of dev | kubeflow | vllm
#   dev      (default) host Ollama + runner container  (docker-compose.yml)
#   kubeflow single all-in-one image; Ollama runs inside the container and
#            reads models from QAGREDO_MODELS_DIR (e.g. /home/jovyan/models).
#   vllm     legacy dual-GPU vLLM stack (docker-compose.vllm-stack.yml).
#
# If QAGREDO_PROFILE is unset but QAGREDO_USE_VLLM_STACK=1, we map to vllm for
# backwards compatibility.
# ---------------------------------------------------------------------------
if [[ -z "${QAGREDO_PROFILE:-}" ]]; then
  if [[ "${QAGREDO_USE_VLLM_STACK:-0}" == "1" ]]; then
    QAGREDO_PROFILE=vllm
  else
    QAGREDO_PROFILE=dev
  fi
fi
export QAGREDO_PROFILE

case "${QAGREDO_PROFILE}" in
  dev|kubeflow|vllm) : ;;
  *)
    die "Unknown QAGREDO_PROFILE='${QAGREDO_PROFILE}' (expected dev | kubeflow | vllm)"
    ;;
esac

# Models directory on the host — mounted into the Kubeflow image.
export QAGREDO_MODELS_DIR="${QAGREDO_MODELS_DIR:-$HOST_DIR/models}"
export QAGREDO_GPU_COUNT="${QAGREDO_GPU_COUNT:-2}"

# Legacy vLLM stack env (only when QAGREDO_USE_VLLM_STACK=1)
export VLLM_MODEL="${VLLM_MODEL:-/models/Qwen2.5-7B-Instruct}"
export VLLM_SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-Qwen/Qwen2.5-7B-Instruct}"
export VLLM_API_KEY="${VLLM_API_KEY:-llama-local}"
export VLLM_TP_SIZE="${VLLM_TP_SIZE:-1}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
export VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.90}"
export VLLM_JUDGE_MODEL="${VLLM_JUDGE_MODEL:-/models/Meta-Llama-3.1-8B-Instruct}"
export VLLM_JUDGE_SERVED_NAME="${VLLM_JUDGE_SERVED_NAME:-meta-llama/Meta-Llama-3.1-8B-Instruct}"
export VLLM_JUDGE_API_KEY="${VLLM_JUDGE_API_KEY:-qwen-local}"
export VLLM_JUDGE_TP_SIZE="${VLLM_JUDGE_TP_SIZE:-1}"
export VLLM_JUDGE_MAX_MODEL_LEN="${VLLM_JUDGE_MAX_MODEL_LEN:-8192}"
export VLLM_JUDGE_GPU_UTIL="${VLLM_JUDGE_GPU_UTIL:-0.90}"
export QAGREDO_VLLM_COMPOSE_EXTRA="${QAGREDO_VLLM_COMPOSE_EXTRA:-}"

case "${QAGREDO_PROFILE}" in
  vllm)
    export QAGREDO_USE_OLLAMA=0
    export QAGREDO_USE_VLLM_STACK=1
    COMPOSE_ARGS=(-f "$HOST_DIR/docker-compose.vllm-stack.yml")
    if [[ -n "${QAGREDO_VLLM_COMPOSE_EXTRA}" ]]; then
      COMPOSE_ARGS+=(-f "$HOST_DIR/${QAGREDO_VLLM_COMPOSE_EXTRA}")
    fi
    PROFILE_CONFIG_FILE="config/config.vllm.yaml"
    ;;
  kubeflow)
    export QAGREDO_USE_OLLAMA=1
    export QAGREDO_USE_VLLM_STACK=0
    COMPOSE_ARGS=(-f "$HOST_DIR/docker-compose.kubeflow.yml")
    PROFILE_CONFIG_FILE="config/config.kubeflow.yaml"
    ;;
  dev|*)
    export QAGREDO_USE_OLLAMA=1
    export QAGREDO_USE_VLLM_STACK=0
    COMPOSE_ARGS=(-f "$HOST_DIR/docker-compose.yml")
    PROFILE_CONFIG_FILE="config/config.dev.yaml"
    ;;
esac
export QAGREDO_CONFIG_FILE="${PROFILE_CONFIG_FILE}"

PIPELINE_ARGS=("$@")

# Inject --config only if the caller didn't already specify one.
_has_config_arg=0
for _arg in "${PIPELINE_ARGS[@]}"; do
  if [[ "$_arg" == "--config" || "$_arg" == --config=* ]]; then
    _has_config_arg=1
    break
  fi
done
if [[ "$_has_config_arg" -eq 0 ]]; then
  PIPELINE_ARGS=("--config" "/workspace/${PROFILE_CONFIG_FILE}" "${PIPELINE_ARGS[@]}")
fi

_log() { echo "[run] $*"; }
_warn() { echo "[run][WARN] $*" >&2; }

# After Docker runs as root, fix cache/output folders so you can delete them.
fix_host_ownership() {
    _log "Fixing file ownership (UID=$HOST_UID GID=$HOST_GID) ..."
    docker run --rm --privileged --userns=host -u 0 \
      --entrypoint "" \
      -v "$HOST_DIR/output:/fix/output" \
      -v "$HOST_DIR/hf_cache:/fix/hf_cache" \
      -v "$HOST_DIR/hf_cache_judge:/fix/hf_cache_judge" \
      -v "$HOST_DIR/config:/fix/config" \
      -v "$DATA_DIR:/fix/data" \
      qagredo-v1:latest \
      sh -c "chown -R $HOST_UID:$HOST_GID /fix/output /fix/hf_cache /fix/hf_cache_judge /fix/config /fix/data 2>/dev/null || true" \
      2>/dev/null || _warn "Post-run permission fix skipped (non-fatal)"
}

# Command-line shortcuts (--down, --logs, …)
case "${1:-}" in
  --down)
    _log "Stopping all containers..."
    docker compose "${COMPOSE_ARGS[@]}" down
    fix_host_ownership
    _log "Done."
    exit 0
    ;;
  --logs)
    case "${QAGREDO_PROFILE}" in
      vllm)
        _log "vLLM stack logs (Ctrl+C to stop)..."
        docker compose "${COMPOSE_ARGS[@]}" logs -f vllm vllm-judge
        ;;
      kubeflow|dev|*)
        _log "qagredo runner logs (Ctrl+C to stop)..."
        docker compose "${COMPOSE_ARGS[@]}" logs -f qagredo
        ;;
    esac
    exit 0
    ;;
  --status)
    docker compose "${COMPOSE_ARGS[@]}" ps
    echo ""
    case "${QAGREDO_PROFILE}" in
      vllm)
        if curl -sf http://localhost:7100/health >/dev/null 2>&1; then
          echo "  vLLM Generator: healthy (http://localhost:7100)"
        else
          echo "  vLLM Generator: not responding"
        fi
        if curl -sf http://localhost:7101/health >/dev/null 2>&1; then
          echo "  vLLM Judge: healthy (http://localhost:7101)"
        else
          echo "  vLLM Judge: not responding"
        fi
        ;;
      kubeflow)
        echo "  Profile: kubeflow (in-container Ollama; no host port exposed by default)"
        echo "  Models dir (host): ${QAGREDO_MODELS_DIR}"
        echo "  Generator model  : ${OLLAMA_MODEL}"
        echo "  Judge model      : ${OLLAMA_JUDGE_MODEL}"
        ;;
      dev|*)
        if curl -sf "http://127.0.0.1:${OLLAMA_HOST_PORT}/api/tags" >/dev/null 2>&1; then
          echo "  Ollama (host): responding (http://localhost:${OLLAMA_HOST_PORT})"
        else
          echo "  Ollama (host): not responding — start with: ollama serve"
        fi
        ;;
    esac
    exit 0
    ;;
  --show-config)
    echo ""
    echo "=== ${PROFILE_CONFIG_FILE} (active for profile '${QAGREDO_PROFILE}') ==="
    echo ""
    cat "$HOST_DIR/${PROFILE_CONFIG_FILE}"
    echo ""
    echo "=== Host-side settings (.env) ==="
    echo "  QAGREDO_PROFILE     = ${QAGREDO_PROFILE}"
    echo "  QAGREDO_MODELS_DIR  = ${QAGREDO_MODELS_DIR}  (kubeflow profile only)"
    echo "  QAGREDO_GPU_COUNT   = ${QAGREDO_GPU_COUNT}"
    echo "  HOST_UID / HOST_GID = $HOST_UID / $HOST_GID"
    if [[ "${QAGREDO_PROFILE}" == "vllm" ]]; then
      echo "  VLLM_MODEL          = $VLLM_MODEL"
      echo "  VLLM_SERVED_MODEL   = $VLLM_SERVED_MODEL_NAME"
      echo "  VLLM_TP_SIZE        = $VLLM_TP_SIZE"
    fi
    echo ""
    echo "LLM provider / model / URL come from the profile YAML shown above."
    echo ""
    echo "=== Input data ==="
    echo "  Files in DATA_DIR ($DATA_DIR):"
    ls -lh "$DATA_DIR/" 2>/dev/null || echo "    (empty)"
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

QAGRedo — question / answer generator with strict LLM judge.
==========================================================================

FIRST-TIME USERS — DO THIS:

  1. Open  .env  and check the 5 numbered sections (profile + data path +
     models path + UID/GID). Each section is labelled "CHANGE ME" or
     "LEAVE ALONE".

  2. Open  config/config.<profile>.yaml  (dev / kubeflow / vllm) and
     check the four lines marked "CHANGE ME":
       • run.num_documents
       • question_generation.num_questions
       • llm.model
       • judge.model

  3. Run:   bash run.sh


EVERYDAY COMMANDS
-----------------
  bash run.sh                          Run the pipeline.
  bash run.sh -- --num-documents 2     Same, but only process 2 documents.
  bash run.sh --status                 Is Ollama / vLLM up? What's running?
  bash run.sh --logs                   Tail the container logs.
  bash run.sh --down                   Stop everything.
  bash run.sh --show-config            Print active profile YAML + .env.
  bash run.sh --summarize --latest     Summarise the most recent run.


PROFILES — set in .env  ( QAGREDO_PROFILE=... )
-----------------------------------------------
  dev        Ollama already running on this host (start it with: ollama serve).
             Simplest; what you usually want on a laptop or dev server.
  kubeflow   One container that bundles Ollama + QAGRedo. Models are
             mounted from QAGREDO_MODELS_DIR on the host.
             Example for Kubeflow: QAGREDO_MODELS_DIR=/home/jovyan/models
  vllm       Two vLLM GPU containers (generator + judge). Advanced.
             Optional 4-GPU override:
             QAGREDO_VLLM_COMPOSE_EXTRA=docker-compose.vllm-redserver.yml
             with VLLM_TP_SIZE=2 and VLLM_JUDGE_TP_SIZE=2.


EVERYTHING WORTH EDITING — AT A GLANCE
--------------------------------------
  .env                           → host paths, profile, UID/GID
  config/config.dev.yaml         → dev profile (Ollama on host)
  config/config.kubeflow.yaml    → kubeflow profile (Ollama in container)
  config/config.vllm.yaml        → vllm profile (dual GPU)
  data/                          → your input documents go here
  output/                        → results appear here after each run

  You should NOT need to open docker-compose.*.yml or any .py file.

Help: bash run.sh -h      (this message)
USAGE
    exit 0
    ;;
esac

# ============================================================================
# Preflight validation
# ============================================================================
[[ -f "$HOST_DIR/docker-compose.yml" ]] || die "Missing: $HOST_DIR/docker-compose.yml (are you in the qagredo_host directory?)"
if [[ "${QAGREDO_PROFILE}" == "kubeflow" ]]; then
  [[ -f "$HOST_DIR/docker-compose.kubeflow.yml" ]] || die "Missing: $HOST_DIR/docker-compose.kubeflow.yml"
  [[ -f "$HOST_DIR/Dockerfile.kubeflow" ]] || die "Missing: $HOST_DIR/Dockerfile.kubeflow"
  if [[ ! -d "${QAGREDO_MODELS_DIR}" ]]; then
    _warn "QAGREDO_MODELS_DIR does not exist: ${QAGREDO_MODELS_DIR}"
    _warn "Creating it — drop Ollama GGUF models there (or mount /home/jovyan/models on Kubeflow)."
    mkdir -p "${QAGREDO_MODELS_DIR}" 2>/dev/null || die "Cannot create ${QAGREDO_MODELS_DIR}"
  fi
fi
if [[ "${QAGREDO_USE_VLLM_STACK:-0}" == "1" ]]; then
  [[ -f "$HOST_DIR/docker-compose.vllm-stack.yml" ]] || die "Missing: $HOST_DIR/docker-compose.vllm-stack.yml"
  if [[ -n "${QAGREDO_VLLM_COMPOSE_EXTRA}" ]]; then
    [[ -f "$HOST_DIR/${QAGREDO_VLLM_COMPOSE_EXTRA}" ]] \
      || die "Missing vLLM compose override: $HOST_DIR/${QAGREDO_VLLM_COMPOSE_EXTRA}"
  fi
  _tp="${VLLM_TP_SIZE:-1}"
  if [[ "$_tp" =~ ^[0-9]+$ ]] && [[ "$_tp" -gt 1 ]] \
      && [[ -z "${QAGREDO_VLLM_COMPOSE_EXTRA}" ]] \
      && [[ "${QAGREDO_ALLOW_TP2_WITHOUT_SINGLE:-0}" != "1" ]]; then
    die "VLLM_TP_SIZE=${_tp} needs ${_tp} generator GPUs. Set QAGREDO_VLLM_COMPOSE_EXTRA to a matching override, set VLLM_TP_SIZE=1, or set QAGREDO_ALLOW_TP2_WITHOUT_SINGLE=1 after editing device_ids."
  fi
fi
docker info >/dev/null 2>&1 || die "Docker is not running or not accessible"

if [[ ! -f "$HOST_DIR/${PROFILE_CONFIG_FILE}" ]]; then
  die "Profile config file not found: $HOST_DIR/${PROFILE_CONFIG_FILE}
  Expected one of: config/config.dev.yaml, config/config.kubeflow.yaml,
  config/config.vllm.yaml. If you deleted it, re-extract from the bundle:
    tar xzf qagredo_bundle.tar.gz"
fi

if [[ ! -f "$HOST_DIR/run_qa_pipeline.py" ]]; then
  die "Missing: $HOST_DIR/run_qa_pipeline.py (are you in the qagredo_host directory?)"
fi

# Ensure writable directories exist
mkdir -p "$HOST_DIR/output" "$HOST_DIR/hf_cache" "$DATA_DIR" \
  2>/dev/null || true

# Print current settings
_log "==========================================="
_log "QAGRedo Pipeline"
_log "==========================================="
_log "  Host dir              : $HOST_DIR"
_log "  Input data (host)     : $DATA_DIR  →  /workspace/data"
_log "  Config (profile)      : $HOST_DIR/${PROFILE_CONFIG_FILE}"
case "${QAGREDO_PROFILE}" in
  vllm)
    _log "  Profile               : vllm (dual GPU stack)"
    _log "  Generator             : $VLLM_MODEL @ port 7100"
    _log "  Judge                 : $VLLM_JUDGE_MODEL @ port 7101"
    if [[ -n "${QAGREDO_VLLM_COMPOSE_EXTRA}" ]]; then
      _log "  vLLM compose override : ${QAGREDO_VLLM_COMPOSE_EXTRA}"
    fi
    ;;
  kubeflow)
    _log "  Profile               : kubeflow (single all-in-one image)"
    _log "  Models dir (host)     : ${QAGREDO_MODELS_DIR}  →  /opt/ollama/models"
    _log "  Generator model       : ${OLLAMA_MODEL}"
    _log "  Judge model           : ${OLLAMA_JUDGE_MODEL}"
    _log "  GPUs requested        : ${QAGREDO_GPU_COUNT}"
    ;;
  dev|*)
    _log "  Profile               : dev (host Ollama)"
    _log "  Ollama port           : ${OLLAMA_HOST_PORT}"
    _log "  Generator model       : ${OLLAMA_MODEL}"
    _log "  Judge model           : ${OLLAMA_JUDGE_MODEL}"
    ;;
esac
_log "  Container user        : UID=$HOST_UID GID=$HOST_GID"
_log "  Compose files         : ${COMPOSE_ARGS[*]}"
_log "==========================================="

mkdir -p "$HOST_DIR/hf_cache_judge" 2>/dev/null || true

HEALTH_TIMEOUT=300
HEALTH_INTERVAL=5

if [[ "${QAGREDO_PROFILE}" == "kubeflow" ]]; then
  _log "Kubeflow profile: starting in-container Ollama + runner ..."
  # Build-once — cached afterwards.
  docker compose "${COMPOSE_ARGS[@]}" build qagredo
elif [[ "${QAGREDO_USE_VLLM_STACK:-0}" == "1" ]]; then
  _log "Starting vLLM Generator + Judge ..."
  docker compose "${COMPOSE_ARGS[@]}" up -d vllm vllm-judge
  _log "Waiting for Generator at http://localhost:7100/health ..."
  elapsed=0
  while true; do
    if curl -sf http://localhost:7100/health >/dev/null 2>&1; then
      _log "Generator is ready! (took ~${elapsed}s)"
      break
    fi
    if [[ "$elapsed" -ge "$HEALTH_TIMEOUT" ]]; then
      die "Generator did not become healthy within ${HEALTH_TIMEOUT}s. Check: docker logs qagredo-vllm --tail 50"
    fi
    sleep "$HEALTH_INTERVAL"
    elapsed=$((elapsed + HEALTH_INTERVAL))
  done
  _log "Waiting for Judge at http://localhost:7101/health ..."
  elapsed=0
  while true; do
    if curl -sf http://localhost:7101/health >/dev/null 2>&1; then
      _log "Judge is ready! (took ~${elapsed}s)"
      break
    fi
    if [[ "$elapsed" -ge "$HEALTH_TIMEOUT" ]]; then
      die "Judge did not become healthy within ${HEALTH_TIMEOUT}s. Check: docker logs qagredo-vllm-judge --tail 50"
    fi
    sleep "$HEALTH_INTERVAL"
    elapsed=$((elapsed + HEALTH_INTERVAL))
  done
else
  _log "Waiting for Ollama at http://127.0.0.1:${OLLAMA_HOST_PORT}/api/tags ..."
  elapsed=0
  while true; do
    if curl -sf "http://127.0.0.1:${OLLAMA_HOST_PORT}/api/tags" >/dev/null 2>&1; then
      _log "Ollama is ready! (took ~${elapsed}s)"
      break
    fi
    if [[ "$elapsed" -ge "$HEALTH_TIMEOUT" ]]; then
      die "Ollama not reachable on port ${OLLAMA_HOST_PORT} within ${HEALTH_TIMEOUT}s. Start it on the host (ollama serve) and pull models: ollama pull ${OLLAMA_MODEL}"
    fi
    sleep "$HEALTH_INTERVAL"
    elapsed=$((elapsed + HEALTH_INTERVAL))
    if (( elapsed % 30 == 0 )); then
      _log "  Still waiting for Ollama... (${elapsed}s elapsed)"
    fi
  done
fi

# Run the Python pipeline inside Docker
_log "Running QAGRedo pipeline ..."
if [[ "${#PIPELINE_ARGS[@]}" -gt 0 ]]; then
  _log "Pipeline args           : ${PIPELINE_ARGS[*]}"
fi

# Ensure ownership is fixed even on Ctrl+C or unexpected exit.
trap fix_host_ownership EXIT

docker compose "${COMPOSE_ARGS[@]}" run --rm qagredo \
  python /workspace/run_qa_pipeline.py "${PIPELINE_ARGS[@]}"
PIPELINE_EXIT=$?

if [[ "$PIPELINE_EXIT" -ne 0 ]]; then
  _warn "Pipeline exited with code $PIPELINE_EXIT"
fi

# EXIT trap fires after this point, running fix_host_ownership automatically.

_log "Done! Outputs are in: $HOST_DIR/output/"
echo ""
echo "Next steps:"
echo "  bash run.sh --summarize --latest       Summarise this run"
echo "  bash run.sh --summarize --latest --json Save summary as JSON"
echo "  bash run.sh --show-config               Show current settings"
echo "  bash run.sh --down                      Stop all containers"
echo ""
echo "To edit config and re-run:"
echo "  vi ${PROFILE_CONFIG_FILE} && bash run.sh"
echo ""
echo "Full guide: docs/OFFLINE_SETUP_GUIDE.md"
