#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# setup_offline.sh  --  One-time setup on the offline / Kubeflow server
# ============================================================================
#
# Run this ONCE (or whenever you bring a new bundle or new model archive) to:
#   Phase 1   Discover the tarballs you copied over
#   Phase 2   Load Docker images (idempotent — skips if already loaded)
#   Phase 3   Unpack / link the model stores into a known host location
#   Phase 4   Make sure host dirs are writable by your user
#   Phase 5   Write a starter .env from scripts/offline/dotenv.template
#   Phase 6   Smoke tests
#
# Expected files (copy whichever apply to the profile you will run):
#
#   qag_bundle.tar.gz              Always needed
#   qag-v1.tar                     ollama / vllm profile (runner image)
#   qag-kubeflow.tar               kubeflow profile  (all-in-one image)
#   models_ollama.tar.gz               ollama / kubeflow profiles (combined store)
#   models_ollama_<tag>.tar.gz         ollama / kubeflow profiles (split by model tag)
#   models_vllm.tar.gz                 vllm local only (combined)
#   models_vllm_<model>.tar.gz         vllm local only (per-model split)
#   models_llama.tar.gz                vllm local judge (legacy name)
#   Archives are also searched under QAG_ARCHIVE_DIR (default /data/tyewhong/qag).
#   vllm-qwen35-localcuda.rootfs.tar   vllm local only (Qwen3.5 runtime image)
#   qwen35-localcuda.rootfs.tar        alternate vLLM image tar name
#   vllm-openai_*.rootfs.tar           legacy vLLM image
#
#   vllm external (redserver → gpuserver): only qag_bundle + qag-v1.tar.
#   Set VLLM_BASE_URL / VLLM_JUDGE_BASE_URL in .env, or pass --vllm-external.
#
# Typical workflow on the offline host:
#     tar xzf qag_bundle.tar.gz      # → qag_host/
#     cd qag_host
#     bash setup_offline.sh              # auto-discovers anything alongside
#     vi .env                            # pick profile + data path
#     bash run.sh
#
# Options:
#     --profile <ollama|kubeflow|vllm>   Tell setup which profile you will run
#                                     (controls which checks are required).
#                                     Default: auto-detect from .env.
#     --vllm-external                 vllm profile: skip local vLLM image/HF
#                                     weights (orchestrator / gpuserver URLs).
#                                     Auto-detected when .env has VLLM_BASE_URL
#                                     or QAG_VLLM_CONFIG_FILE=*redserver*.
#     --skip-images                   Skip docker load (images already loaded)
#     --force                         Overwrite any existing model links/dirs
#     -h, --help                      Show this message
# ============================================================================

SKIP_IMAGES=0
FORCE=0
VLLM_EXTERNAL_FLAG=0
REQUESTED_PROFILE=""

while (($#)); do
  case "$1" in
    --skip-images) SKIP_IMAGES=1 ;;
    --force)       FORCE=1 ;;
    --vllm-external) VLLM_EXTERNAL_FLAG=1 ;;
    --profile)     shift; REQUESTED_PROFILE="${1:-}" ;;
    --profile=*)   REQUESTED_PROFILE="${1#*=}" ;;
    -h|--help)
      sed -n '1,/^# ==/ { /^# / s/^# \?//; p; }' "$0" | sed '$d'
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "$HOST_DIR/.." && pwd)"

ARCHIVE_DIR="${QAG_ARCHIVE_DIR:-/data/tyewhong/qag}"
if [[ -f "$HOST_DIR/.env" ]]; then
  _ad="$(grep -E '^[[:space:]]*QAG_ARCHIVE_DIR=' "$HOST_DIR/.env" \
        | tail -n1 | cut -d= -f2- | tr -d '[:space:]"'"'"'')"
  [[ -n "$_ad" ]] && ARCHIVE_DIR="$_ad"
fi

_pass() { echo "[PASS]  $*"; }
_fail() { echo "[FAIL]  $*"; }
_info() { echo "[INFO]  $*"; }
_warn() { echo "[WARN]  $*"; }
_step() { echo; echo "======== $* ========"; }

TESTS_PASSED=0
TESTS_FAILED=0
_check() {
  local desc="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    _pass "$desc"
    TESTS_PASSED=$((TESTS_PASSED + 1))
  else
    _fail "$desc"
    TESTS_FAILED=$((TESTS_FAILED + 1))
  fi
}

_find_file() {
  local name="$1"
  local _models_dir
  _models_dir="$(_vllm_models_host_dir)"
  for candidate in \
      "$ARCHIVE_DIR/$name" \
      "$PARENT_DIR/$name" \
      "$HOST_DIR/$name" \
      "$_models_dir/$name" \
      "$PARENT_DIR"/*/"$name" \
      "$(cd "$PARENT_DIR/.." 2>/dev/null && pwd)/$name" \
  ; do
    if [[ -f "$candidate" ]]; then echo "$candidate"; return 0; fi
  done
  return 1
}

_find_dir() {
  local name="$1"
  for candidate in \
      "$PARENT_DIR/$name" \
      "$HOST_DIR/$name" \
      "$(cd "$PARENT_DIR/.." 2>/dev/null && pwd)/$name" \
  ; do
    if [[ -d "$candidate" ]]; then echo "$candidate"; return 0; fi
  done
  return 1
}

_image_exists() {
  docker image inspect "$1" >/dev/null 2>&1
}

_vllm_models_host_dir() {
  local d="$HOST_DIR/models_llm"
  if [[ -f "$HOST_DIR/.env" ]]; then
    local hm
    hm="$(grep -E '^[[:space:]]*QAG_MODELS_LLM_HOST=' "$HOST_DIR/.env" \
          | tail -n1 | cut -d= -f2- | tr -d '[:space:]"'"'"'')"
    [[ -n "$hm" ]] && d="$hm"
  elif [[ "${PROFILE:-}" == "vllm" ]]; then
    d="/data/models"
  fi
  printf '%s' "$d"
}

_vllm_models_populated() {
  local d
  d="$(_vllm_models_host_dir)"
  [[ -n "$(ls -A "$d" 2>/dev/null)" ]]
}

_dotenv_get() {
  local key="$1"
  [[ -f "$HOST_DIR/.env" ]] || return 1
  grep -E "^[[:space:]]*${key}=" "$HOST_DIR/.env" | tail -n1 \
    | sed -e 's/^[^=]*=//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
          -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

_resolve_config_yaml() {
  local cfg
  cfg="$(_dotenv_get QAG_VLLM_CONFIG_FILE 2>/dev/null || true)"
  if [[ -z "$cfg" ]]; then
    if [[ "${VLLM_EXTERNAL:-0}" -eq 1 ]] \
        && [[ -f "$HOST_DIR/config/config.vllm.redserver.yaml" ]]; then
      cfg="config/config.vllm.redserver.yaml"
    else
      cfg="config/config.${PROFILE}.yaml"
    fi
  fi
  printf '%s' "$cfg"
}

_vllm_external_mode() {
  [[ "$PROFILE" == "vllm" ]] || return 1
  if [[ "$VLLM_EXTERNAL_FLAG" -eq 1 ]]; then
    return 0
  fi
  local base judge cfg extra
  base="$(_dotenv_get VLLM_BASE_URL 2>/dev/null || true)"
  judge="$(_dotenv_get VLLM_JUDGE_BASE_URL 2>/dev/null || true)"
  cfg="$(_dotenv_get QAG_VLLM_CONFIG_FILE 2>/dev/null || true)"
  extra="$(_dotenv_get QAG_VLLM_COMPOSE_EXTRA 2>/dev/null || true)"
  [[ -n "$base" || -n "$judge" ]] && return 0
  [[ "$cfg" == *redserver* ]] && return 0
  [[ "$extra" == *vllm-redserver* ]] && return 0
  [[ "$extra" == *vllm-external* ]] && return 0
  return 1
}

# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------
resolve_profile() {
  local prof="${REQUESTED_PROFILE:-}"
  if [[ -z "$prof" && -f "$HOST_DIR/.env" ]]; then
    prof="$(grep -E '^[[:space:]]*QAG_PROFILE=' "$HOST_DIR/.env" \
             | tail -n1 | cut -d= -f2- | tr -d '[:space:]"'"'"'')"
  fi
  if [[ -z "$prof" ]]; then
    _warn "QAG_PROFILE not set; defaulting to ollama for setup."
    prof="ollama"
  fi
  case "$prof" in
    ollama|kubeflow|vllm) echo "$prof" ;;
    dev)
      _warn "QAG_PROFILE=dev is the old name for ollama; using ollama."
      _warn "  Update .env: QAG_PROFILE=ollama"
      echo "ollama"
      ;;
    *)
      echo "[ERROR] Unknown QAG_PROFILE='${prof}' (expected ollama | kubeflow | vllm)" >&2
      exit 2
      ;;
  esac
}

PROFILE="$(resolve_profile)"
_step "Profile: ${PROFILE}"
_info "Override with: bash setup_offline.sh --profile <ollama|kubeflow|vllm>"

VLLM_EXTERNAL=0
if _vllm_external_mode; then
  VLLM_EXTERNAL=1
  _info "vLLM mode: external (orchestrator — local vLLM image/weights skipped)"
elif [[ "$PROFILE" == "vllm" ]]; then
  _info "vLLM mode: local (compose starts vLLM on this host)"
fi

# ---------------------------------------------------------------------------
# Phase 1: Discover tarballs (profile-scoped listing)
# ---------------------------------------------------------------------------
_step "Phase 1: Discovering tarballs (profile=${PROFILE})"

BUNDLE_TGZ="$(_find_file qag_bundle.tar.gz || true)"
QAG_TAR="$(_find_file qag-v1.tar || true)"
KUBEFLOW_TAR="$(_find_file qag-kubeflow.tar || true)"
MODELS_OLLAMA_TGZ="$(_find_file models_ollama.tar.gz || true)"
MODELS_OLLAMA_SPLIT_TGZ=()
for _cand in \
    "$ARCHIVE_DIR"/models_ollama_*.tar.gz \
    "$PARENT_DIR"/models_ollama_*.tar.gz \
    "$HOST_DIR"/models_ollama_*.tar.gz; do
  [[ -f "$_cand" ]] && MODELS_OLLAMA_SPLIT_TGZ+=("$_cand")
done
MODELS_VLLM_TGZ="$(_find_file models_vllm.tar.gz || true)"
MODELS_VLLM_SPLIT_TGZ=()
_vllm_split_dirs=("$ARCHIVE_DIR" "$PARENT_DIR" "$HOST_DIR")
_vllm_models_dir="$(_vllm_models_host_dir)"
[[ -n "$_vllm_models_dir" ]] && _vllm_split_dirs+=("$_vllm_models_dir")
for _vdir in "${_vllm_split_dirs[@]}"; do
  for _cand in "$_vdir"/models_vllm_*.tar.gz; do
    [[ -f "$_cand" ]] || continue
    [[ "$(basename "$_cand")" == "models_vllm.tar.gz" ]] && continue
    MODELS_VLLM_SPLIT_TGZ+=("$_cand")
  done
done
MODELS_LLAMA_TGZ="$(_find_file models_llama.tar.gz || true)"
VLLM_ROOTFS_TAR="$(_find_file "vllm-qwen35-localcuda.rootfs.tar" || true)"
if [[ -z "$VLLM_ROOTFS_TAR" ]]; then
  VLLM_ROOTFS_TAR="$(_find_file "qwen35-localcuda.rootfs.tar" || true)"
fi
if [[ -z "$VLLM_ROOTFS_TAR" ]]; then
  VLLM_ROOTFS_TAR="$(_find_file "vllm-openai_v0.5.3.post1.rootfs.tar" || true)"
fi
if [[ -z "$VLLM_ROOTFS_TAR" ]]; then
  # Legacy: any vllm-openai_*.rootfs.tar lying next to us.
  VLLM_ROOTFS_TAR="$(ls "$PARENT_DIR"/vllm-openai_*.rootfs.tar 2>/dev/null | head -1 || true)"
fi

_info "Archive search dir : ${ARCHIVE_DIR}"
_info "Model weights dir  : $(_vllm_models_host_dir) (extracted HF + optional .tar.gz)"
_info "Bundle             : ${BUNDLE_TGZ:-<not found>}"

case "$PROFILE" in
  ollama)
    _info "qag-v1.tar         : ${QAG_TAR:-<not found>}"
    _info "models_ollama.tar  : ${MODELS_OLLAMA_TGZ:-<not found>}"
    if [[ ${#MODELS_OLLAMA_SPLIT_TGZ[@]} -gt 0 ]]; then
      _info "models_ollama_*    : ${#MODELS_OLLAMA_SPLIT_TGZ[@]} split archive(s)"
    else
      _info "models_ollama_*    : <none> (OK if host ollama list has tags)"
    fi
    ;;
  kubeflow)
    _info "qag-kubeflow.tar   : ${KUBEFLOW_TAR:-<not found>}"
    _info "models_ollama.tar  : ${MODELS_OLLAMA_TGZ:-<not found>}"
    if [[ ${#MODELS_OLLAMA_SPLIT_TGZ[@]} -gt 0 ]]; then
      _info "models_ollama_*    : ${#MODELS_OLLAMA_SPLIT_TGZ[@]} split archive(s)"
    else
      _info "models_ollama_*    : <none found>"
    fi
    ;;
  vllm)
    _info "qag-v1.tar         : ${QAG_TAR:-<not found>}"
    if [[ "$VLLM_EXTERNAL" -eq 1 ]]; then
      _info "vllm rootfs tar    : not required (external vLLM)"
      _info "models_vllm*       : not required (weights on remote host)"
    else
      _info "vllm rootfs tar    : ${VLLM_ROOTFS_TAR:-<not found>}"
      _info "models_vllm.tar    : ${MODELS_VLLM_TGZ:-<not found>}"
      if [[ ${#MODELS_VLLM_SPLIT_TGZ[@]} -gt 0 ]]; then
        _info "models_vllm_*      : ${#MODELS_VLLM_SPLIT_TGZ[@]} split archive(s)"
        for _s in "${MODELS_VLLM_SPLIT_TGZ[@]}"; do
          _info "  - $(basename "$_s")"
        done
      else
        _info "models_vllm_*      : <none found>"
      fi
      _info "models_llama.tar   : ${MODELS_LLAMA_TGZ:-<not found>}"
    fi
    ;;
esac

# ---------------------------------------------------------------------------
# Phase 2: Load Docker images
# ---------------------------------------------------------------------------
_step "Phase 2: Loading Docker images"

QAG_IMAGE="${QAG_IMAGE:-qag-v1:latest}"
QAG_KUBEFLOW_IMAGE="${QAG_KUBEFLOW_IMAGE:-qag-kubeflow:latest}"
VLLM_IMAGE="${VLLM_IMAGE:-qag-vllm:qwen35-localcuda}"
if [[ -f "$HOST_DIR/.env" ]]; then
  _qi="$(_dotenv_get QAG_IMAGE 2>/dev/null || true)"
  [[ -n "$_qi" ]] && QAG_IMAGE="$_qi"
  _vi="$(_dotenv_get VLLM_IMAGE 2>/dev/null || true)"
  [[ -n "$_vi" ]] && VLLM_IMAGE="$_vi"
fi
_info "Docker tags to verify: QAG_IMAGE=${QAG_IMAGE}  VLLM_IMAGE=${VLLM_IMAGE}"

if [[ "$SKIP_IMAGES" -eq 1 ]]; then
  _info "Skipping image loads (--skip-images)"
else
  # ollama / vllm image
  if [[ "$PROFILE" == "ollama" || "$PROFILE" == "vllm" ]]; then
    if _image_exists "$QAG_IMAGE"; then
      _info "${QAG_IMAGE} already loaded."
    elif [[ -n "$QAG_TAR" ]]; then
      _info "Loading $QAG_TAR ..."
      docker load -i "$QAG_TAR"
    else
      _warn "qag-v1.tar not found and ${QAG_IMAGE} not loaded."
    fi
  fi

  # kubeflow image
  if [[ "$PROFILE" == "kubeflow" ]]; then
    if _image_exists "$QAG_KUBEFLOW_IMAGE"; then
      _info "${QAG_KUBEFLOW_IMAGE} already loaded."
    elif [[ -n "$KUBEFLOW_TAR" ]]; then
      _info "Loading $KUBEFLOW_TAR ..."
      docker load -i "$KUBEFLOW_TAR"
    else
      _warn "qag-kubeflow.tar not found and ${QAG_KUBEFLOW_IMAGE} not loaded."
    fi
  fi

  # vllm runtime image (local vLLM only)
  if [[ "$PROFILE" == "vllm" && "$VLLM_EXTERNAL" -eq 0 ]]; then
    if _image_exists "$VLLM_IMAGE"; then
      _info "${VLLM_IMAGE} already loaded."
    elif [[ -n "$VLLM_ROOTFS_TAR" ]]; then
      _info "Loading $VLLM_ROOTFS_TAR ..."
      docker load -i "$VLLM_ROOTFS_TAR"
    else
      _warn "No vLLM image tar found and ${VLLM_IMAGE} not loaded."
      _warn "  Copy vllm-qwen35-localcuda.rootfs.tar, or use external vLLM:"
      _warn "  set VLLM_BASE_URL in .env and re-run with --vllm-external."
    fi
  elif [[ "$PROFILE" == "vllm" && "$VLLM_EXTERNAL" -eq 1 ]]; then
    _info "Skipping vLLM runtime image load (external endpoints)."
  fi
fi

# ---------------------------------------------------------------------------
# Phase 3: Model store unpack / link
# ---------------------------------------------------------------------------
_step "Phase 3: Preparing model stores for profile '${PROFILE}'"

# Ensure host-side dirs exist.
mkdir -p "$HOST_DIR/output" "$HOST_DIR/hf_cache" "$HOST_DIR/hf_cache_judge" \
         "$HOST_DIR/data" 2>/dev/null || true

if [[ "$PROFILE" == "ollama" || "$PROFILE" == "kubeflow" ]]; then
  # --- Ollama GGUF store ---
  OLLAMA_DEST="$HOST_DIR/models"
  if [[ -d "$OLLAMA_DEST/blobs" && -d "$OLLAMA_DEST/manifests" && "$FORCE" -ne 1 ]]; then
    _info "Ollama store already present at $OLLAMA_DEST (use --force to overwrite)."
  elif [[ -n "$MODELS_OLLAMA_TGZ" ]]; then
    _info "Extracting $MODELS_OLLAMA_TGZ into $HOST_DIR ..."
    rm -rf "$OLLAMA_DEST" 2>/dev/null || true
    tar -xzf "$MODELS_OLLAMA_TGZ" -C "$HOST_DIR"
    # The archive contains a top-level 'models/' dir by convention.
    if [[ -d "$HOST_DIR/models/blobs" && -d "$HOST_DIR/models/manifests" ]]; then
      _info "Ollama store ready: $OLLAMA_DEST"
    else
      # Fallback: find the blobs/ dir and move it into place.
      _found="$(find "$HOST_DIR" -maxdepth 3 -type d -name blobs 2>/dev/null | head -1)"
      if [[ -n "$_found" ]]; then
        _parent="$(dirname "$_found")"
        if [[ "$_parent" != "$OLLAMA_DEST" ]]; then
          rm -rf "$OLLAMA_DEST"
          mv "$_parent" "$OLLAMA_DEST"
        fi
        _info "Ollama store ready (auto-located): $OLLAMA_DEST"
      else
        _warn "Extracted models_ollama.tar.gz but no blobs/ directory found."
      fi
    fi
  elif [[ ${#MODELS_OLLAMA_SPLIT_TGZ[@]} -gt 0 ]]; then
    _info "Extracting ${#MODELS_OLLAMA_SPLIT_TGZ[@]} split Ollama archives into $HOST_DIR/models ..."
    rm -rf "$OLLAMA_DEST" 2>/dev/null || true
    mkdir -p "$OLLAMA_DEST"
    for _split in "${MODELS_OLLAMA_SPLIT_TGZ[@]}"; do
      _info "  extracting $(basename "$_split")"
      tar -xzf "$_split" -C "$HOST_DIR"
    done
    if [[ -d "$HOST_DIR/models/blobs" && -d "$HOST_DIR/models/manifests" ]]; then
      _info "Ollama store ready from split archives: $OLLAMA_DEST"
    else
      _warn "Extracted split models_ollama_*.tar.gz but no blobs/ + manifests/ found."
    fi
  elif [[ "$PROFILE" == "kubeflow" ]]; then
    _warn "No models_ollama.tar.gz/models_ollama_*.tar.gz found and no store at $OLLAMA_DEST."
    _warn "  In Kubeflow the in-container Ollama reads from QAG_MODELS_DIR."
    _warn "  Point QAG_MODELS_DIR at a valid store before 'bash run.sh'."
  else
    _info "No models_ollama*.tar.gz found — assuming Ollama on the host already"
    _info "has the tags listed in config/config.ollama.yaml (check with 'ollama list')."
  fi
fi

if [[ "$PROFILE" == "vllm" && "$VLLM_EXTERNAL" -eq 1 ]]; then
  _info "Skipping local HF weights (generator/judge served outside this host)."
elif [[ "$PROFILE" == "vllm" ]]; then
  # --- vLLM HuggingFace model tree (local vLLM) ---
  VLLM_DEST="$HOST_DIR/models_llm"
  VLLM_HOST_ROOT="$(_vllm_models_host_dir)"
  _vllm_have_archives=0
  [[ -n "$MODELS_VLLM_TGZ" || ${#MODELS_VLLM_SPLIT_TGZ[@]} -gt 0 || -n "$MODELS_LLAMA_TGZ" ]] && _vllm_have_archives=1

  if _vllm_models_populated && [[ "$FORCE" -ne 1 ]] && [[ "$_vllm_have_archives" -eq 0 ]]; then
    _info "vLLM models already present at $VLLM_HOST_ROOT (from manual extract)."
    _info "  No models_vllm*.tar.gz / models_llama.tar.gz needed under $ARCHIVE_DIR."
  elif [[ -d "$VLLM_DEST" && "$(ls -A "$VLLM_DEST" 2>/dev/null)" && "$FORCE" -ne 1 && "$_vllm_have_archives" -eq 0 ]]; then
    _info "vLLM models directory exists at $VLLM_DEST (use --force to overwrite)."
  elif [[ "$_vllm_have_archives" -eq 1 ]]; then
    VLLM_EXTRACT_DEST="$VLLM_HOST_ROOT"
    _info "Extracting vLLM model archive(s) into $VLLM_EXTRACT_DEST ..."
    if [[ "$VLLM_EXTRACT_DEST" == "$VLLM_DEST" && "$FORCE" -eq 1 ]]; then
      rm -rf "$VLLM_DEST"
    fi
    mkdir -p "$VLLM_EXTRACT_DEST"
    if [[ -n "$MODELS_VLLM_TGZ" ]]; then
      _info "  extracting $(basename "$MODELS_VLLM_TGZ")"
      tar -xzf "$MODELS_VLLM_TGZ" -C "$VLLM_EXTRACT_DEST"
    fi
    for _split in "${MODELS_VLLM_SPLIT_TGZ[@]}"; do
      _info "  extracting $(basename "$_split")"
      tar -xzf "$_split" -C "$VLLM_EXTRACT_DEST"
    done
    if [[ -n "$MODELS_LLAMA_TGZ" ]]; then
      _info "  extracting $(basename "$MODELS_LLAMA_TGZ") (judge)"
      tar -xzf "$MODELS_LLAMA_TGZ" -C "$VLLM_EXTRACT_DEST"
    fi
    _info "vLLM models ready: $VLLM_EXTRACT_DEST"
    _info "  Set QAG_MODELS_LLM_HOST=$VLLM_EXTRACT_DEST in .env (template uses this for vllm)."
  elif _vllm_models_populated; then
    _info "vLLM models present at $VLLM_HOST_ROOT (manual install OK)."
  else
    _warn "No models_vllm.tar.gz, models_vllm_*.tar.gz, or models_llama.tar.gz found under:"
    _warn "  $ARCHIVE_DIR, $PARENT_DIR, or $HOST_DIR"
    _warn "  Place archives in $ARCHIVE_DIR (e.g. models_vllm_Qwen3_5-9B.tar.gz), or extract to"
    _warn "  QAG_MODELS_LLM_HOST in .env (e.g. /data/models) and re-run."
    _warn "  Expected empty dir was: $VLLM_DEST (script default); your .env may use $VLLM_HOST_ROOT"
  fi
fi

# ---------------------------------------------------------------------------
# Phase 4: Permissions
# ---------------------------------------------------------------------------
_step "Phase 4: Fixing host directory ownership"

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
_fix_image=""
if [[ "$PROFILE" == "vllm" && "$VLLM_EXTERNAL" -eq 1 ]]; then
  _image_exists "$QAG_IMAGE" && _fix_image="$QAG_IMAGE"
else
  for cand in "$QAG_IMAGE" "$QAG_KUBEFLOW_IMAGE" "$VLLM_IMAGE"; do
    if _image_exists "$cand"; then _fix_image="$cand"; break; fi
  done
fi

if [[ -n "$_fix_image" ]]; then
  _info "Using image $_fix_image to chown host dirs to UID=$HOST_UID GID=$HOST_GID ..."
  docker run --rm --privileged --userns=host -u 0 --entrypoint "" \
    -v "$HOST_DIR:/qhost" "$_fix_image" sh -c "
      set -e
      mkdir -p /qhost/output /qhost/hf_cache /qhost/hf_cache_judge \
               /qhost/config /qhost/data
      chown -R ${HOST_UID}:${HOST_GID} /qhost/output /qhost/hf_cache \
        /qhost/hf_cache_judge /qhost/config /qhost/data 2>/dev/null || true
    " && _info "Permissions fixed." || _warn "Permission fix failed (non-fatal)."
else
  _warn "No Docker image loaded yet — skipping permission fix."
  _warn "run.sh will do this automatically on first run."
fi

# ---------------------------------------------------------------------------
# Phase 5: Write .env starter
# ---------------------------------------------------------------------------
_step "Phase 5: Writing .env"

ENV_FILE="$HOST_DIR/.env"
DOTENV_TEMPLATE="$HOST_DIR/scripts/offline/dotenv.template"

if [[ -f "$ENV_FILE" ]]; then
  _info "$ENV_FILE exists — leaving it alone. Delete it to regenerate."
elif [[ -f "$HOST_DIR/.env.example" ]]; then
  cp "$HOST_DIR/.env.example" "$ENV_FILE"
  _info "Created $ENV_FILE from .env.example. Edit to match your paths."
elif [[ -f "$DOTENV_TEMPLATE" ]]; then
  python3 - "$DOTENV_TEMPLATE" "$ENV_FILE" "$HOST_DIR" "$HOST_UID" "$HOST_GID" "$PROFILE" <<'PY'
import pathlib, sys
template_path, env_path, host_dir, uid, gid, profile = sys.argv[1:7]
text = pathlib.Path(template_path).read_text(encoding="utf-8")
models_llm_host = (
    "/data/models" if profile == "vllm" else f"{host_dir}/models_llm"
)
for k, v in {
    "@QAG_HOST_DIR@": host_dir,
    "@HOST_UID@": uid,
    "@HOST_GID@": gid,
    "@QAG_PROFILE@": profile,
    "@QAG_MODELS_LLM_HOST@": models_llm_host,
}.items():
    text = text.replace(k, v)
pathlib.Path(env_path).write_text(text, encoding="utf-8")
PY
  _info "Created $ENV_FILE from template. Edit to match your paths."
else
  _warn "No .env template found. Create one manually before running."
fi

# ---------------------------------------------------------------------------
# Phase 6: Smoke tests
# ---------------------------------------------------------------------------
_step "Phase 6: Smoke tests"

_check "Docker is available" docker info

case "$PROFILE" in
  ollama)
    _check "ollama/runner image present" _image_exists "$QAG_IMAGE"
    ;;
  kubeflow)
    _check "kubeflow image present" _image_exists "$QAG_KUBEFLOW_IMAGE"
    _check "Ollama store present at ./models" test -d "$HOST_DIR/models/blobs"
    ;;
  vllm)
    _check "runner image present" _image_exists "$QAG_IMAGE"
    if [[ "$VLLM_EXTERNAL" -eq 1 ]]; then
      _CFG_FILE="$(_resolve_config_yaml)"
      _check "pipeline config present (${_CFG_FILE})" \
        test -f "$HOST_DIR/${_CFG_FILE}"
      _VLLM_BASE="$(_dotenv_get VLLM_BASE_URL 2>/dev/null || true)"
      _VLLM_JUDGE="$(_dotenv_get VLLM_JUDGE_BASE_URL 2>/dev/null || true)"
      if [[ -n "$_VLLM_BASE" && -n "$_VLLM_JUDGE" ]]; then
        _info "External URLs configured in .env (generator + judge)."
      elif [[ -n "$_VLLM_BASE" || -n "$_VLLM_JUDGE" ]]; then
        _info "Partial URL config — set both VLLM_BASE_URL and"
        _info "VLLM_JUDGE_BASE_URL in .env before bash run.sh."
      else
        _info "Set VLLM_BASE_URL and VLLM_JUDGE_BASE_URL in .env"
        _info "(see config/config.vllm.redserver.yaml)."
      fi
    else
      _check "vLLM runtime image present (${VLLM_IMAGE})" _image_exists "$VLLM_IMAGE"
      _check "vLLM models directory populated ($(_vllm_models_host_dir))" \
        _vllm_models_populated
    fi
    ;;
esac

for d in config utils scripts; do
  _check "Dir: $d/" test -d "$HOST_DIR/$d"
done
if [[ "$PROFILE" != "vllm" || "$VLLM_EXTERNAL" -eq 0 ]]; then
  _check "config/config.${PROFILE}.yaml exists" \
    test -f "$HOST_DIR/config/config.${PROFILE}.yaml"
fi
_check "run.sh exists"               test -f "$HOST_DIR/run.sh"
_check "run_qa_pipeline.py exists"   test -f "$HOST_DIR/run_qa_pipeline.py"
_check "docker-compose.yml"          test -f "$HOST_DIR/docker-compose.yml"
_check "docker-compose.kubeflow.yml" test -f "$HOST_DIR/docker-compose.kubeflow.yml"
_check "docker-compose.vllm-stack.yml" test -f "$HOST_DIR/docker-compose.vllm-stack.yml"
_check "docker-compose.vllm-siteserver.yml" test -f "$HOST_DIR/docker-compose.vllm-siteserver.yml"

if command -v nvidia-smi >/dev/null 2>&1; then
  if [[ "$PROFILE" == "vllm" && "$VLLM_EXTERNAL" -eq 1 ]]; then
    _info "NVIDIA GPU on this host (optional for external vLLM orchestrator)."
    nvidia-smi >/dev/null 2>&1 && _pass "nvidia-smi OK" || true
  else
    _check "NVIDIA GPU available" nvidia-smi
  fi
else
  if [[ "$PROFILE" == "vllm" && "$VLLM_EXTERNAL" -eq 0 ]]; then
    _warn "nvidia-smi not found — local vllm profile needs NVIDIA GPU."
  else
    _info "nvidia-smi not found (OK for external vLLM or CPU Ollama)."
  fi
fi

_step "Summary"
echo
echo "  Profile       : $PROFILE"
if [[ "$PROFILE" == "vllm" && "$VLLM_EXTERNAL" -eq 1 ]]; then
  echo "  vLLM mode     : external (orchestrator only)"
fi
echo "  Tests passed  : $TESTS_PASSED"
echo "  Tests failed  : $TESTS_FAILED"
echo
if [[ "$TESTS_FAILED" -gt 0 ]]; then
  _warn "Some tests failed. Review the output above before running."
else
  _pass "All required tests passed."
fi

echo
echo "Next steps:"
echo "  vi $ENV_FILE                  # pick profile + data path + model dir"
echo "  bash run.sh --show-config     # print the effective config"
if [[ "$PROFILE" == "vllm" && "$VLLM_EXTERNAL" -eq 1 ]]; then
  echo "  bash run.sh --pipeline-only --num-documents 1"
else
  echo "  bash run.sh                   # run the pipeline"
fi
echo
echo "Full guide: $HOST_DIR/docs/OFFLINE_SETUP_GUIDE.md"
