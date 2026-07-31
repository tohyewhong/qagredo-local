#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# run.sh — profile-driven QAG runner (ollama | kubeflow | vllm)
# ============================================================================
#
# Stay in the folder that contains this script, docker-compose.yml, and .env.
# `ollama`: host Ollama on port 11434 (`ollama serve` on host).
# `kubeflow`: in-container Ollama, warm persistent container; released by --down.
# `vllm`: dual vLLM GPU services (generator + judge).
#
# Common commands:
#   bash run.sh              # run pipeline for active profile
#   bash run.sh --down       # stop compose project containers
#   bash run.sh --logs       # tail qag / vLLM logs
#   bash run.sh --status     # compose ps + backend health
#   bash run.sh --vllm-up generator|judge|all   # vllm profile: start vLLM step-by-step
#   bash run.sh --pipeline-only [--num-documents N]     # vllm: run pipeline only
#   bash run.sh -- --resume                             # skip docs with output; reuse latest run folder
#   bash run.sh -- --skip-existing-outputs              # skip only; new run folder unless --resume
#   bash run.sh --summarize --latest
#   bash run.sh --minimise   # post-run: minimal + good/bad pair exports
#   bash run.sh --finetune-lora [RUN_DIR]  # 2-GPU LoRA SFT (adapter only)
#   bash run.sh --finetune-dpo [RUN_DIR]   # DPO stage after SFT adapter
#   bash run.sh -- --minimal-qa-output   # during run: slim *_analysis.json
#
# Maintainer docs: docs/HANDOVER.md · docs/OFFLINE_SETUP_GUIDE.md
#
# Where to put settings:
#   .env                           — host paths + QAG_PROFILE selector
#   config/config.<profile>.yaml   — everything else (models, temps, retries).
#                                    Each profile has its own self-contained
#                                    YAML. External-vLLM env values select the
#                                    redserver variant; --show-config confirms.
#
# Extra options (optional, in .env or shell):
#   OLLAMA_MODEL, OLLAMA_JUDGE_MODEL, OLLAMA_HOST_PORT
#   QAG_OFFLINE_HOST + QAG_OFFLINE_INPUT — see .env (sets DATA_DIR)
# ============================================================================

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() { echo "[run][ERROR] $*" >&2; exit 1; }
_log() { echo "[run] $*"; }
_warn() { echo "[run][WARN] $*" >&2; }

# Load .env next to this script (your presets and ports).
# Shell-supplied vars always win over .env — so
#     QAG_PROFILE=vllm bash run.sh
# works even when .env sets a different profile.
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
if [[ -n "${QAG_OFFLINE_HOST:-}" && -n "${QAG_OFFLINE_INPUT:-}" ]]; then
  case "${QAG_OFFLINE_INPUT}" in
    txt|json) ;;
    *)
      die "QAG_OFFLINE_INPUT must be txt or json (got ${QAG_OFFLINE_INPUT})"
      ;;
  esac
  case "${QAG_OFFLINE_HOST}" in
    [Rr]epo|[Ll]inux)
      _repo="${QAG_REPO_DATA_ROOT:-${QAG_LINUX_DATA_ROOT:-}}"
      export DATA_DIR="${_repo:-$HOST_DIR/data}"
      ;;
    [Ww]indows|[Ww][Ss][Ll])
      _wroot="${QAG_WINDOWS_DOWNLOADS_ROOT:-/mnt/c/Users/tyewhong/Downloads}"
      export DATA_DIR="${_wroot}/${QAG_OFFLINE_INPUT}"
      ;;
    [Dd]ata)
      _droot="${QAG_SHARED_DATA_ROOT:-/data/local/tyewhong/Data}"
      export DATA_DIR="${_droot}/${QAG_OFFLINE_INPUT}"
      ;;
    *)
      die "QAG_OFFLINE_HOST must be repo, data, or wsl (got ${QAG_OFFLINE_HOST}; linux=repo; windows=wsl)"
      ;;
  esac
fi
export DATA_DIR="${DATA_DIR:-${QAG_DATA_DIR:-$HOST_DIR/data}}"
export QAG_DATA_DIR="${QAG_DATA_DIR:-$DATA_DIR}"

# Tell Docker your user so new files on disk belong to you.
#
# Safety default: if .env has stale HOST_UID/HOST_GID from another machine,
# auto-correct to the current shell user to prevent permission regressions.
# To intentionally use a different owner mapping, set:
#   QAG_ALLOW_FOREIGN_OWNERSHIP=1
_current_uid="$(id -u)"
_current_gid="$(id -g)"
export HOST_UID="${HOST_UID:-${_current_uid}}"
export HOST_GID="${HOST_GID:-${_current_gid}}"
if [[ "${HOST_UID}" != "${_current_uid}" || "${HOST_GID}" != "${_current_gid}" ]]; then
  if [[ "${QAG_ALLOW_FOREIGN_OWNERSHIP:-0}" != "1" ]]; then
    echo "[run][WARN] HOST_UID/HOST_GID (${HOST_UID}:${HOST_GID}) do not match current user (${_current_uid}:${_current_gid}). Auto-correcting to current user."
    echo "[run][WARN] To keep foreign ownership mapping, set QAG_ALLOW_FOREIGN_OWNERSHIP=1."
    export HOST_UID="${_current_uid}"
    export HOST_GID="${_current_gid}"
  fi
fi

export OLLAMA_HOST_PORT="${OLLAMA_HOST_PORT:-11434}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:${OLLAMA_HOST_PORT}/v1}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:9b}"
export OLLAMA_JUDGE_BASE_URL="${OLLAMA_JUDGE_BASE_URL:-http://localhost:${OLLAMA_HOST_PORT}/v1}"
# Must match judge.model in config/config.ollama.yaml and config/config.kubeflow.yaml
export OLLAMA_JUDGE_MODEL="${OLLAMA_JUDGE_MODEL:-llama3.1:8b-instruct-fp16}"

# ---------------------------------------------------------------------------
# Profile selection: ollama | kubeflow | vllm
#   ollama   host Ollama + runner container  (docker-compose.yml)
#   kubeflow single all-in-one image; Ollama runs inside a persistent container
#            and reads models from QAG_MODELS_DIR (e.g. /home/jovyan/models).
#            `bash run.sh --down` releases warm container + GPU memory.
#   vllm     dual-GPU vLLM stack (docker-compose.vllm-stack.yml).
#
#   Unset QAG_PROFILE defaults to ollama (with a warning).
#   QAG_PROFILE=dev is an old name for ollama (warn once, then run).
# ---------------------------------------------------------------------------
if [[ -z "${QAG_PROFILE:-}" ]]; then
  _warn "QAG_PROFILE not set in .env — defaulting to ollama."
  _warn "  For vLLM or Kubeflow, set QAG_PROFILE=vllm or kubeflow in .env."
  QAG_PROFILE=ollama
fi
export QAG_PROFILE

case "${QAG_PROFILE}" in
  ollama|kubeflow|vllm) ;;
  dev)
    _warn "QAG_PROFILE=dev is the old name for ollama."
    _warn "  Update .env: QAG_PROFILE=ollama"
    QAG_PROFILE=ollama
    export QAG_PROFILE
    ;;
  *)
    die "Unknown QAG_PROFILE='${QAG_PROFILE}' (expected ollama | kubeflow | vllm)"
    ;;
esac

# Models directory on the host — mounted into the Kubeflow image.
export QAG_MODELS_DIR="${QAG_MODELS_DIR:-$HOST_DIR/models}"
export QAG_GPU_COUNT="${QAG_GPU_COUNT:-2}"

# vLLM stack env (vllm profile)
export VLLM_MODEL="${VLLM_MODEL:-/models/Qwen3.5-9B}"
export VLLM_SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-Qwen/Qwen3.5-9B}"
export VLLM_API_KEY="${VLLM_API_KEY:-llama-local}"
export VLLM_TP_SIZE="${VLLM_TP_SIZE:-1}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
export VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.90}"
export VLLM_JUDGE_MODEL="${VLLM_JUDGE_MODEL:-/models/Selene-1-Mini-Llama-3.1-8B}"
export VLLM_JUDGE_SERVED_NAME="${VLLM_JUDGE_SERVED_NAME:-AtlaAI/Selene-1-Mini-Llama-3.1-8B}"
export VLLM_JUDGE_API_KEY="${VLLM_JUDGE_API_KEY:-qwen-local}"
export VLLM_JUDGE_TP_SIZE="${VLLM_JUDGE_TP_SIZE:-1}"
export VLLM_JUDGE_MAX_MODEL_LEN="${VLLM_JUDGE_MAX_MODEL_LEN:-8192}"
export VLLM_JUDGE_GPU_UTIL="${VLLM_JUDGE_GPU_UTIL:-0.90}"
export VLLM_BASE_URL="${VLLM_BASE_URL:-}"
export VLLM_JUDGE_BASE_URL="${VLLM_JUDGE_BASE_URL:-}"
export QAG_VLLM_COMPOSE_EXTRA="${QAG_VLLM_COMPOSE_EXTRA:-}"

case "${QAG_PROFILE}" in
  vllm)
    COMPOSE_ARGS=(-f "$HOST_DIR/docker-compose.vllm-stack.yml")
    if [[ -n "${QAG_VLLM_COMPOSE_EXTRA}" ]]; then
      COMPOSE_ARGS+=(-f "$HOST_DIR/${QAG_VLLM_COMPOSE_EXTRA}")
    fi
    PROFILE_CONFIG_FILE="${QAG_VLLM_CONFIG_FILE:-config/config.vllm.yaml}"
    ;;
  kubeflow)
    COMPOSE_ARGS=(-f "$HOST_DIR/docker-compose.kubeflow.yml")
    PROFILE_CONFIG_FILE="config/config.kubeflow.yaml"
    ;;
  ollama|*)
    COMPOSE_ARGS=(-f "$HOST_DIR/docker-compose.yml")
    PROFILE_CONFIG_FILE="config/config.ollama.yaml"
    ;;
esac
export QAG_CONFIG_FILE="${PROFILE_CONFIG_FILE}"

_ensure_pipeline_config_arg() {
  local _has_config_arg=0
  local _arg
  for _arg in "${PIPELINE_ARGS[@]}"; do
    if [[ "$_arg" == "--config" || "$_arg" == --config=* ]]; then
      _has_config_arg=1
      break
    fi
  done
  if [[ "$_has_config_arg" -eq 0 ]]; then
    PIPELINE_ARGS=("--config" "/workspace/${PROFILE_CONFIG_FILE}" "${PIPELINE_ARGS[@]}")
  fi
}

PIPELINE_ARGS=("$@")
_ensure_pipeline_config_arg

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

_lora_export_py() {
  python3 "$HOST_DIR/scripts/utils/export_lora_jsonl.py" \
    --include-dpo "$@"
}

HEALTH_TIMEOUT=300
HEALTH_INTERVAL=5
VLLM_HEALTH_TIMEOUT="${QAG_VLLM_HEALTH_TIMEOUT:-900}"

_vllm_preflight() {
  [[ "${QAG_PROFILE}" == "vllm" ]] \
    || die "--vllm-up and --pipeline-only require QAG_PROFILE=vllm in .env"
  [[ -f "$HOST_DIR/docker-compose.vllm-stack.yml" ]] \
    || die "Missing: $HOST_DIR/docker-compose.vllm-stack.yml"
  docker info >/dev/null 2>&1 || die "Docker is not running or not accessible"
  mkdir -p "$HOST_DIR/output" "$HOST_DIR/hf_cache" "$HOST_DIR/hf_cache_judge" "$DATA_DIR" \
    2>/dev/null || true
}

_vllm_wait_generator() {
  _log "Waiting for Generator at http://localhost:7100/health (timeout ${VLLM_HEALTH_TIMEOUT}s; Qwen3.5 may take several minutes) ..."
  local elapsed=0
  while true; do
    if curl -sf http://localhost:7100/health >/dev/null 2>&1; then
      _log "Generator is ready! (took ~${elapsed}s)"
      return 0
    fi
    if [[ "$elapsed" -ge "$VLLM_HEALTH_TIMEOUT" ]]; then
      die "Generator did not become healthy within ${VLLM_HEALTH_TIMEOUT}s. Check: docker logs qag-vllm --tail 50"
    fi
    sleep "$HEALTH_INTERVAL"
    elapsed=$((elapsed + HEALTH_INTERVAL))
  done
}

_vllm_wait_judge() {
  _log "Waiting for Judge at http://localhost:7101/health (timeout ${VLLM_HEALTH_TIMEOUT}s) ..."
  local elapsed=0
  while true; do
    if curl -sf http://localhost:7101/health >/dev/null 2>&1; then
      _log "Judge is ready! (took ~${elapsed}s)"
      return 0
    fi
    if [[ "$elapsed" -ge "$VLLM_HEALTH_TIMEOUT" ]]; then
      die "Judge did not become healthy within ${VLLM_HEALTH_TIMEOUT}s. Check: docker logs qag-vllm-judge --tail 50"
    fi
    sleep "$HEALTH_INTERVAL"
    elapsed=$((elapsed + HEALTH_INTERVAL))
  done
}

_vllm_openai_base_to_health() {
  local base="${1%/}"
  base="${base%/v1}"
  echo "${base}/health"
}

_vllm_require_both_healthy() {
  local gen_health judge_health
  if [[ -n "${VLLM_BASE_URL}" ]]; then
    gen_health="$(_vllm_openai_base_to_health "$VLLM_BASE_URL")"
  else
    gen_health="http://localhost:7100/health"
  fi
  if [[ -n "${VLLM_JUDGE_BASE_URL}" ]]; then
    judge_health="$(_vllm_openai_base_to_health "$VLLM_JUDGE_BASE_URL")"
  else
    judge_health="http://localhost:7101/health"
  fi
  curl -sf "$gen_health" >/dev/null 2>&1 \
    || die "Generator not healthy at ${gen_health}. Check vLLM or VLLM_BASE_URL in .env"
  curl -sf "$judge_health" >/dev/null 2>&1 \
    || die "Judge not healthy at ${judge_health}. Check vLLM or VLLM_JUDGE_BASE_URL in .env"
}

_vllm_up() {
  local target="${1:-}"
  case "${target}" in
    generator)
      _log "Starting vLLM Generator (GPU 0, port 7100) ..."
      docker compose "${COMPOSE_ARGS[@]}" up -d vllm
      _vllm_wait_generator
      ;;
    judge)
      _log "Starting vLLM Judge (GPU 1, port 7101) ..."
      docker compose "${COMPOSE_ARGS[@]}" up -d vllm-judge
      _vllm_wait_judge
      ;;
    all)
      _log "Starting vLLM Generator + Judge ..."
      docker compose "${COMPOSE_ARGS[@]}" up -d vllm vllm-judge
      _vllm_wait_generator
      _vllm_wait_judge
      ;;
    *)
      die "Usage: bash run.sh --vllm-up generator|judge|all"
      ;;
  esac
}

_run_vllm_pipeline() {
  local use_no_deps="${1:-0}"
  _log "Running QAG pipeline ..."
  if [[ "${#PIPELINE_ARGS[@]}" -gt 0 ]]; then
    _log "Pipeline args           : ${PIPELINE_ARGS[*]}"
  fi
  trap fix_host_ownership EXIT
  if [[ "$use_no_deps" == "1" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" run --rm --no-deps qag \
      python /workspace/run_qa_pipeline.py "${PIPELINE_ARGS[@]}"
  else
    docker compose "${COMPOSE_ARGS[@]}" run --rm qag \
      python /workspace/run_qa_pipeline.py "${PIPELINE_ARGS[@]}"
  fi
}

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
      qag-v1:latest \
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
    case "${QAG_PROFILE}" in
      vllm)
        _log "vLLM stack logs (Ctrl+C to stop)..."
        docker compose "${COMPOSE_ARGS[@]}" logs -f vllm vllm-judge
        ;;
      kubeflow|ollama|*)
        _log "qag runner logs (Ctrl+C to stop)..."
        docker compose "${COMPOSE_ARGS[@]}" logs -f qag
        ;;
    esac
    exit 0
    ;;
  --status)
    docker compose "${COMPOSE_ARGS[@]}" ps
    echo ""
    case "${QAG_PROFILE}" in
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
        echo "  Models dir (host): ${QAG_MODELS_DIR}"
        echo "  Generator model  : ${OLLAMA_MODEL}"
        echo "  Judge model      : ${OLLAMA_JUDGE_MODEL}"
        ;;
      ollama|*)
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
    echo "=== ${PROFILE_CONFIG_FILE} (active for profile '${QAG_PROFILE}') ==="
    echo ""
    cat "$HOST_DIR/${PROFILE_CONFIG_FILE}"
    echo ""
    echo "=== Host-side settings (.env) ==="
    echo "  QAG_PROFILE     = ${QAG_PROFILE}"
    echo "  QAG_MODELS_DIR  = ${QAG_MODELS_DIR}  (kubeflow profile only)"
    echo "  QAG_GPU_COUNT   = ${QAG_GPU_COUNT}"
    echo "  HOST_UID / HOST_GID = $HOST_UID / $HOST_GID"
    if [[ "${QAG_PROFILE}" == "vllm" ]]; then
      echo "  VLLM_MODEL          = $VLLM_MODEL"
      echo "  VLLM_SERVED_MODEL   = $VLLM_SERVED_MODEL_NAME"
      echo "  VLLM_TP_SIZE        = $VLLM_TP_SIZE"
    fi
    echo ""
    echo "LLM provider / model / URL come from the profile YAML shown above."
    echo "See config/README.md for which profile YAML to edit."
    echo ""
    echo "=== Input data ==="
    echo "  Files in DATA_DIR ($DATA_DIR):"
    ls -lh "$DATA_DIR/" 2>/dev/null || echo "    (empty)"
    echo ""
    exit 0
    ;;
  --edit-config)
    _editor="${EDITOR:-vi}"
    _cfg="$HOST_DIR/${PROFILE_CONFIG_FILE}"
    [[ -f "$_cfg" ]] || die "Profile config not found: $_cfg"
    echo "[run] Opening $_cfg (profile=${QAG_PROFILE}) with ${_editor}"
    exec "$_editor" "$_cfg"
    ;;
  --summarize)
    shift
    bash "$HOST_DIR/scripts/utils/summarize_run.sh" "$@"
    exit 0
    ;;
  --minimise|--minimize)
    shift
    # Export minimal artifacts from existing *_analysis.json outputs.
    # Also export minimal good/bad pair artifacts + LoRA JSONL.
    # Default target is the latest run folder in ./output.
    if [[ $# -eq 0 ]]; then
      _latest_run_dir="$(_latest_run_dir)" \
        || die "No run folders found under $HOST_DIR/output. Run the pipeline first or pass a path."
      python3 "$HOST_DIR/scripts/utils/export_analysis_minimal.py" "$_latest_run_dir"
      python3 "$HOST_DIR/scripts/utils/export_analysis_training_jsonl.py" \
        --mode good "$_latest_run_dir"
      python3 "$HOST_DIR/scripts/utils/export_analysis_training_jsonl.py" \
        --mode bad "$_latest_run_dir"
      _lora_export_py "$_latest_run_dir"
    else
      python3 "$HOST_DIR/scripts/utils/export_analysis_minimal.py" "$@"
      python3 "$HOST_DIR/scripts/utils/export_analysis_training_jsonl.py" \
        --mode good "$@"
      python3 "$HOST_DIR/scripts/utils/export_analysis_training_jsonl.py" \
        --mode bad "$@"
      _lora_export_py "$@"
    fi
    exit 0
    ;;
  --export-lora)
    shift
    if [[ $# -eq 0 ]]; then
      _latest_run_dir="$(_latest_run_dir)" \
        || die "No run folders found under $HOST_DIR/output. Run the pipeline first or pass a path."
      _lora_export_py "$_latest_run_dir"
    else
      _lora_export_py "$@"
    fi
    exit 0
    ;;
  --finetune-lora)
    shift
    bash "$HOST_DIR/scripts/lora/train_qwen_lora.sh" "$@"
    exit 0
    ;;
  --finetune-dpo)
    shift
    bash "$HOST_DIR/scripts/lora/train_qwen_dpo.sh" "$@"
    exit 0
    ;;
  --minimise-good)
    shift
    if [[ $# -eq 0 ]]; then
      _latest_run_dir="$(_latest_run_dir)" \
        || die "No run folders found under $HOST_DIR/output. Run the pipeline first or pass a path."
      python3 "$HOST_DIR/scripts/utils/export_analysis_training_jsonl.py" \
        --mode good "$_latest_run_dir"
    else
      python3 "$HOST_DIR/scripts/utils/export_analysis_training_jsonl.py" \
        --mode good "$@"
    fi
    exit 0
    ;;
  --minimise-bad)
    shift
    if [[ $# -eq 0 ]]; then
      _latest_run_dir="$(_latest_run_dir)" \
        || die "No run folders found under $HOST_DIR/output. Run the pipeline first or pass a path."
      python3 "$HOST_DIR/scripts/utils/export_analysis_training_jsonl.py" \
        --mode bad "$_latest_run_dir"
    else
      python3 "$HOST_DIR/scripts/utils/export_analysis_training_jsonl.py" \
        --mode bad "$@"
    fi
    exit 0
    ;;
  --vllm-up)
    shift
    _vllm_target="${1:-}"
    shift || true
    _vllm_preflight
    _vllm_up "${_vllm_target}"
    _log "vLLM ready. Next: bash run.sh --pipeline-only  (or bash run.sh --vllm-up judge if you only started generator)"
    exit 0
    ;;
  --fast)
    shift
    exec bash "$HOST_DIR/run.sh" --pipeline-only --skip-preflight --quiet-skips "$@"
    ;;
  --pipeline-only)
    shift
    PIPELINE_ARGS=("$@")
    _ensure_pipeline_config_arg
    _vllm_preflight
    [[ -f "$HOST_DIR/${PROFILE_CONFIG_FILE}" ]] \
      || die "Profile config not found: $HOST_DIR/${PROFILE_CONFIG_FILE}"
    [[ -f "$HOST_DIR/run_qa_pipeline.py" ]] \
      || die "Missing: $HOST_DIR/run_qa_pipeline.py"
    _vllm_require_both_healthy
    _log "==========================================="
    _log "QAG Pipeline (vLLM already running)"
    _log "  Generator             : ${VLLM_BASE_URL:-http://vllm:7100/v1}"
    _log "  Judge                 : ${VLLM_JUDGE_BASE_URL:-http://vllm-judge:7101/v1}"
    _log "  Config                : ${PROFILE_CONFIG_FILE}"
    _log "==========================================="
    _run_vllm_pipeline 1
    PIPELINE_EXIT=$?
    if [[ "$PIPELINE_EXIT" -ne 0 ]]; then
      _warn "Pipeline exited with code $PIPELINE_EXIT"
    fi
    _log "Done! Outputs are in: $HOST_DIR/output/"
    exit "$PIPELINE_EXIT"
    ;;
  --convert)
    shift
    if [[ $# -lt 2 ]]; then
      echo "Usage: bash run.sh --convert <input.json> <output.jsonl>"
      echo ""
      echo "Converts JSON documents to QAG JSONL format."
      echo "  input:  path to JSON file (relative to qag_host/ or absolute)"
      echo "  output: path to output JSONL file"
      exit 1
    fi
    python3 "$HOST_DIR/scripts/conversion/convert_to_qag_jsonl.py" "$@"
    exit 0
    ;;
  -h|--help)
    cat <<USAGE

QAG — question / answer generator with strict LLM judge.
==========================================================================

FIRST-TIME USERS — DO THIS:

  1. Open  .env  and check the 5 numbered sections (profile + data path +
     models path + UID/GID). Each section is labelled "CHANGE ME" or
     "LEAVE ALONE".

  2. Open  config/config.<profile>.yaml  (ollama / kubeflow / vllm) and
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
  bash run.sh --minimise               Write minimal + good/bad pairs + LoRA JSONL.
  bash run.sh --export-lora [RUN_DIR]  LoRA JSONL only (lora_sft.jsonl, optional DPO).
  bash run.sh --finetune-lora [RUN_DIR]
                                     2-GPU LoRA SFT; base model read-only, adapter only.
  bash run.sh --finetune-dpo [RUN_DIR]
                                     DPO preference tune from lora_dpo.jsonl (needs SFT first).
  bash run.sh --minimise-good          Write *_analysis_minimal_good_pairs.json.
  bash run.sh --minimise-bad           Write *_analysis_minimal_bad_pairs.json.

  vllm profile — split startup (dual GPU, optional):
  bash run.sh --vllm-up generator      Start Qwen vLLM on GPU 0 (:7100), wait for health.
  bash run.sh --vllm-up judge          Start Llama judge vLLM on GPU 1 (:7101), wait for health.
  bash run.sh --vllm-up all            Start both (same as default bash run.sh vLLM phase).
  bash run.sh --pipeline-only          Run pipeline only (both vLLM must already be healthy).
  bash run.sh --pipeline-only --num-documents 2
  bash run.sh --pipeline-only --parallel-documents 2
  bash run.sh --fast --pipeline-only   # skip preflight + quiet prefilter skips
  bash run.sh -- --resume              Skip docs with *_analysis.json; reuse latest run folder.
  bash run.sh -- --skip-existing-outputs
                                     Skip docs already in latest run; write to new folder.

  Example 3-step workflow:
    bash run.sh --vllm-up generator
    bash run.sh --vllm-up judge
    bash run.sh --pipeline-only --num-documents 1


PROFILES — set in .env  ( QAG_PROFILE=... )
-----------------------------------------------
  ollama     Ollama already running on this host (start it with: ollama serve).
             Simplest; what you usually want on a laptop or dev server.
  kubeflow   One container that bundles Ollama + QAG. Models are
             mounted from QAG_MODELS_DIR on the host.
             Example for Kubeflow: QAG_MODELS_DIR=/home/jovyan/models
  vllm       Two vLLM GPU containers (generator + judge). Advanced.
             Local: leave redserver config/URL overrides unset; --show-config
             must report config/config.vllm.yaml.
             Redserver external: set config override, both base URLs, and
             QAG_VLLM_COMPOSE_EXTRA; use --pipeline-only (no --vllm-up).
             Shell-exported values override the saved .env.
             Optional 4-GPU override:
             QAG_VLLM_COMPOSE_EXTRA=docker-compose.vllm-siteserver.yml
             with VLLM_TP_SIZE=2 and VLLM_JUDGE_TP_SIZE=2.


EVERYTHING WORTH EDITING — AT A GLANCE
--------------------------------------
  .env                           → host paths, profile, UID/GID
  config/README.md               → which YAML to edit (read this first)
  config/config.<profile>.yaml   → active tuning (ollama | kubeflow | vllm)
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
[[ -f "$HOST_DIR/docker-compose.yml" ]] || die "Missing: $HOST_DIR/docker-compose.yml (are you in the qag_host directory?)"
if [[ "${QAG_PROFILE}" == "kubeflow" ]]; then
  [[ -f "$HOST_DIR/docker-compose.kubeflow.yml" ]] || die "Missing: $HOST_DIR/docker-compose.kubeflow.yml"
  [[ -f "$HOST_DIR/Dockerfile.kubeflow" ]] || die "Missing: $HOST_DIR/Dockerfile.kubeflow"
  if [[ ! -d "${QAG_MODELS_DIR}" ]]; then
    _warn "QAG_MODELS_DIR does not exist: ${QAG_MODELS_DIR}"
    _warn "Creating it — drop Ollama GGUF models there (or mount /home/jovyan/models on Kubeflow)."
    mkdir -p "${QAG_MODELS_DIR}" 2>/dev/null || die "Cannot create ${QAG_MODELS_DIR}"
  fi
fi
if [[ "${QAG_PROFILE}" == "vllm" ]]; then
  [[ -f "$HOST_DIR/docker-compose.vllm-stack.yml" ]] || die "Missing: $HOST_DIR/docker-compose.vllm-stack.yml"
  if [[ -n "${QAG_VLLM_COMPOSE_EXTRA}" ]]; then
    [[ -f "$HOST_DIR/${QAG_VLLM_COMPOSE_EXTRA}" ]] \
      || die "Missing vLLM compose override: $HOST_DIR/${QAG_VLLM_COMPOSE_EXTRA}"
  fi
  _tp="${VLLM_TP_SIZE:-1}"
  if [[ "$_tp" =~ ^[0-9]+$ ]] && [[ "$_tp" -gt 1 ]] \
      && [[ -z "${QAG_VLLM_COMPOSE_EXTRA}" ]] \
      && [[ "${QAG_ALLOW_TP2_WITHOUT_SINGLE:-0}" != "1" ]]; then
    die "VLLM_TP_SIZE=${_tp} needs ${_tp} generator GPUs. Set QAG_VLLM_COMPOSE_EXTRA to a matching override, set VLLM_TP_SIZE=1, or set QAG_ALLOW_TP2_WITHOUT_SINGLE=1 after editing device_ids."
  fi
fi
docker info >/dev/null 2>&1 || die "Docker is not running or not accessible"

if [[ ! -f "$HOST_DIR/${PROFILE_CONFIG_FILE}" ]]; then
  die "Profile config file not found: $HOST_DIR/${PROFILE_CONFIG_FILE}
  Expected one of: config/config.ollama.yaml, config/config.kubeflow.yaml,
  config/config.vllm.yaml. If you deleted it, re-extract from the bundle:
    tar xzf qag_bundle.tar.gz"
fi

if [[ ! -f "$HOST_DIR/run_qa_pipeline.py" ]]; then
  die "Missing: $HOST_DIR/run_qa_pipeline.py (are you in the qag_host directory?)"
fi

# Ensure writable directories exist
mkdir -p "$HOST_DIR/output" "$HOST_DIR/hf_cache" "$DATA_DIR" \
  2>/dev/null || true

# Print current settings
_log "==========================================="
_log "QAG Pipeline"
_log "==========================================="
_log "  Host dir              : $HOST_DIR"
_log "  Input data (host)     : $DATA_DIR  →  /workspace/data"
_log "  Config (profile)      : $HOST_DIR/${PROFILE_CONFIG_FILE}"
case "${QAG_PROFILE}" in
  vllm)
    _log "  Profile               : vllm (dual GPU stack)"
    _log "  Generator             : $VLLM_MODEL @ port 7100"
    _log "  Judge                 : $VLLM_JUDGE_MODEL @ port 7101"
    if [[ -n "${QAG_VLLM_COMPOSE_EXTRA}" ]]; then
      _log "  vLLM compose override : ${QAG_VLLM_COMPOSE_EXTRA}"
    fi
    ;;
  kubeflow)
    _log "  Profile               : kubeflow (single all-in-one image)"
    _log "  Models dir (host)     : ${QAG_MODELS_DIR}  →  /opt/ollama/models"
    _log "  Generator model       : ${OLLAMA_MODEL}"
    _log "  Judge model           : ${OLLAMA_JUDGE_MODEL}"
    _log "  GPUs requested        : ${QAG_GPU_COUNT}"
    ;;
  ollama|*)
    _log "  Profile               : ollama (host Ollama)"
    _log "  Ollama port           : ${OLLAMA_HOST_PORT}"
    _log "  Generator model       : ${OLLAMA_MODEL}"
    _log "  Judge model           : ${OLLAMA_JUDGE_MODEL}"
    ;;
esac
_log "  Container user        : UID=$HOST_UID GID=$HOST_GID"
_log "  Compose files         : ${COMPOSE_ARGS[*]}"
_log "==========================================="

mkdir -p "$HOST_DIR/hf_cache_judge" 2>/dev/null || true

if [[ "${QAG_PROFILE}" == "kubeflow" ]]; then
  _log "Kubeflow profile: starting/reusing persistent in-container Ollama runner ..."
  # Reuse preloaded image (do not rebuild by default).
  # If you need a rebuild, run:
  #   docker compose "${COMPOSE_ARGS[@]}" build qag
  # Keep Ollama/GPU allocations warm until `bash run.sh --down`.
  docker compose "${COMPOSE_ARGS[@]}" up -d qag
elif [[ "${QAG_PROFILE}" == "vllm" ]]; then
  _vllm_up all
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
if [[ "${QAG_PROFILE}" == "kubeflow" ]]; then
  _log "Running QAG pipeline ..."
  if [[ "${#PIPELINE_ARGS[@]}" -gt 0 ]]; then
    _log "Pipeline args           : ${PIPELINE_ARGS[*]}"
  fi
  trap fix_host_ownership EXIT
  docker compose "${COMPOSE_ARGS[@]}" exec -T qag \
    python /workspace/run_qa_pipeline.py "${PIPELINE_ARGS[@]}"
  PIPELINE_EXIT=$?
elif [[ "${QAG_PROFILE}" == "vllm" ]]; then
  _run_vllm_pipeline 0
  PIPELINE_EXIT=$?
else
  _log "Running QAG pipeline ..."
  if [[ "${#PIPELINE_ARGS[@]}" -gt 0 ]]; then
    _log "Pipeline args           : ${PIPELINE_ARGS[*]}"
  fi
  trap fix_host_ownership EXIT
  docker compose "${COMPOSE_ARGS[@]}" run --rm qag \
    python /workspace/run_qa_pipeline.py "${PIPELINE_ARGS[@]}"
  PIPELINE_EXIT=$?
fi

if [[ "$PIPELINE_EXIT" -ne 0 ]]; then
  _warn "Pipeline exited with code $PIPELINE_EXIT"
fi

# EXIT trap fires after this point, running fix_host_ownership automatically.

_log "Done! Outputs are in: $HOST_DIR/output/"
echo ""
echo "Next steps:"
echo "  bash run.sh --summarize --latest       Summarise this run"
echo "  bash run.sh --summarize --latest --json Save summary as JSON"
echo "  bash run.sh --minimise                 Export minimal JSON for latest run"
echo "  bash run.sh --finetune-lora [RUN_DIR]  Train LoRA adapter (stop vLLM first)"
echo "  bash run.sh --finetune-dpo [RUN_DIR]   DPO tune (needs SFT adapter + lora_dpo.jsonl)"
echo "  bash run.sh --show-config               Show current settings"
echo "  bash run.sh --down                      Stop all containers"
echo ""
echo "To edit config and re-run:"
echo "  vi ${PROFILE_CONFIG_FILE} && bash run.sh"
echo ""
echo "Full guide: docs/OFFLINE_SETUP_GUIDE.md"
