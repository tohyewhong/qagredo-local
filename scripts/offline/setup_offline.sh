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
#   qagredo_bundle.tar.gz              Always needed
#   qagredo-v1.tar                     dev / vllm profile (runner image)
#   qagredo-kubeflow.tar               kubeflow profile  (all-in-one image)
#   models_ollama.tar.gz               dev / kubeflow profiles (combined store)
#   models_ollama_<tag>.tar.gz         dev / kubeflow profiles (split by model tag)
#   models_vllm.tar.gz                 vllm profile
#   vllm-openai_*.rootfs.tar           vllm profile
#
# Typical workflow on the offline host:
#     tar xzf qagredo_bundle.tar.gz      # → qagredo_host/
#     cd qagredo_host
#     bash setup_offline.sh              # auto-discovers anything alongside
#     vi .env                            # pick profile + data path
#     bash run.sh
#
# Options:
#     --profile <dev|kubeflow|vllm>   Tell setup which profile you will run
#                                     (controls which checks are required).
#                                     Default: auto-detect from .env.
#     --skip-images                   Skip docker load (images already loaded)
#     --force                         Overwrite any existing model links/dirs
#     -h, --help                      Show this message
# ============================================================================

SKIP_IMAGES=0
FORCE=0
REQUESTED_PROFILE=""

while (($#)); do
  case "$1" in
    --skip-images) SKIP_IMAGES=1 ;;
    --force)       FORCE=1 ;;
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
  for candidate in \
      "$PARENT_DIR/$name" \
      "$HOST_DIR/$name" \
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

# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------
resolve_profile() {
  local prof="${REQUESTED_PROFILE:-}"
  if [[ -z "$prof" && -f "$HOST_DIR/.env" ]]; then
    prof="$(grep -E '^[[:space:]]*QAGREDO_PROFILE=' "$HOST_DIR/.env" \
             | tail -n1 | cut -d= -f2- | tr -d '[:space:]"'"'"'')"
  fi
  prof="${prof:-dev}"
  case "$prof" in
    dev|kubeflow|vllm) echo "$prof" ;;
    *) _warn "Unknown QAGREDO_PROFILE='${prof}', falling back to dev."; echo "dev" ;;
  esac
}

PROFILE="$(resolve_profile)"
_step "Profile: ${PROFILE}"
_info "Override with: bash setup_offline.sh --profile <dev|kubeflow|vllm>"

# ---------------------------------------------------------------------------
# Phase 1: Discover tarballs (all optional — warn, don't fail)
# ---------------------------------------------------------------------------
_step "Phase 1: Discovering tarballs"

BUNDLE_TGZ="$(_find_file qagredo_bundle.tar.gz || true)"
QAGREDO_TAR="$(_find_file qagredo-v1.tar || true)"
KUBEFLOW_TAR="$(_find_file qagredo-kubeflow.tar || true)"
MODELS_OLLAMA_TGZ="$(_find_file models_ollama.tar.gz || true)"
MODELS_OLLAMA_SPLIT_TGZ=()
for _cand in "$PARENT_DIR"/models_ollama_*.tar.gz "$HOST_DIR"/models_ollama_*.tar.gz; do
  [[ -f "$_cand" ]] && MODELS_OLLAMA_SPLIT_TGZ+=("$_cand")
done
MODELS_VLLM_TGZ="$(_find_file models_vllm.tar.gz || true)"
VLLM_ROOTFS_TAR="$(_find_file "vllm-openai_v0.5.3.post1.rootfs.tar" || true)"
if [[ -z "$VLLM_ROOTFS_TAR" ]]; then
  # Accept any vllm-openai_*.rootfs.tar lying next to us.
  VLLM_ROOTFS_TAR="$(ls "$PARENT_DIR"/vllm-openai_*.rootfs.tar 2>/dev/null | head -1 || true)"
fi

_info "Bundle             : ${BUNDLE_TGZ:-<not found>}"
_info "qagredo-v1.tar     : ${QAGREDO_TAR:-<not found>}"
_info "qagredo-kubeflow   : ${KUBEFLOW_TAR:-<not found>}"
_info "models_ollama.tar  : ${MODELS_OLLAMA_TGZ:-<not found>}"
if [[ ${#MODELS_OLLAMA_SPLIT_TGZ[@]} -gt 0 ]]; then
  _info "models_ollama_*    : ${#MODELS_OLLAMA_SPLIT_TGZ[@]} split archives found"
else
  _info "models_ollama_*    : <none found>"
fi
_info "models_vllm.tar    : ${MODELS_VLLM_TGZ:-<not found>}"
_info "vllm rootfs tar    : ${VLLM_ROOTFS_TAR:-<not found>}"

# ---------------------------------------------------------------------------
# Phase 2: Load Docker images
# ---------------------------------------------------------------------------
_step "Phase 2: Loading Docker images"

QAGREDO_IMAGE="${QAGREDO_IMAGE:-qagredo-v1:latest}"
QAGREDO_KUBEFLOW_IMAGE="${QAGREDO_KUBEFLOW_IMAGE:-qagredo-kubeflow:latest}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.5.3.post1}"

if [[ "$SKIP_IMAGES" -eq 1 ]]; then
  _info "Skipping image loads (--skip-images)"
else
  # dev / vllm image
  if [[ "$PROFILE" == "dev" || "$PROFILE" == "vllm" ]]; then
    if _image_exists "$QAGREDO_IMAGE"; then
      _info "${QAGREDO_IMAGE} already loaded."
    elif [[ -n "$QAGREDO_TAR" ]]; then
      _info "Loading $QAGREDO_TAR ..."
      docker load -i "$QAGREDO_TAR"
    else
      _warn "qagredo-v1.tar not found and ${QAGREDO_IMAGE} not loaded."
    fi
  fi

  # kubeflow image
  if [[ "$PROFILE" == "kubeflow" ]]; then
    if _image_exists "$QAGREDO_KUBEFLOW_IMAGE"; then
      _info "${QAGREDO_KUBEFLOW_IMAGE} already loaded."
    elif [[ -n "$KUBEFLOW_TAR" ]]; then
      _info "Loading $KUBEFLOW_TAR ..."
      docker load -i "$KUBEFLOW_TAR"
    else
      _warn "qagredo-kubeflow.tar not found and ${QAGREDO_KUBEFLOW_IMAGE} not loaded."
    fi
  fi

  # vllm runtime image
  if [[ "$PROFILE" == "vllm" ]]; then
    if _image_exists "$VLLM_IMAGE"; then
      _info "${VLLM_IMAGE} already loaded."
    elif [[ -n "$VLLM_ROOTFS_TAR" ]]; then
      _info "Loading $VLLM_ROOTFS_TAR ..."
      docker load -i "$VLLM_ROOTFS_TAR"
    else
      _warn "No vllm-openai_*.rootfs.tar found and ${VLLM_IMAGE} not loaded."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Phase 3: Model store unpack / link
# ---------------------------------------------------------------------------
_step "Phase 3: Preparing model stores for profile '${PROFILE}'"

# Ensure host-side dirs exist.
mkdir -p "$HOST_DIR/output" "$HOST_DIR/hf_cache" "$HOST_DIR/hf_cache_judge" \
         "$HOST_DIR/data" 2>/dev/null || true

if [[ "$PROFILE" == "dev" || "$PROFILE" == "kubeflow" ]]; then
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
    _warn "  In Kubeflow the in-container Ollama reads from QAGREDO_MODELS_DIR."
    _warn "  Point QAGREDO_MODELS_DIR at a valid store before 'bash run.sh'."
  else
    _info "No models_ollama*.tar.gz found — assuming Ollama on the host already"
    _info "has the tags listed in config/config.dev.yaml (check with 'ollama list')."
  fi
fi

if [[ "$PROFILE" == "vllm" ]]; then
  # --- vLLM HuggingFace model tree ---
  VLLM_DEST="$HOST_DIR/models_llm"
  if [[ -d "$VLLM_DEST" && "$(ls -A "$VLLM_DEST" 2>/dev/null)" && "$FORCE" -ne 1 ]]; then
    _info "vLLM models directory exists at $VLLM_DEST (use --force to overwrite)."
  elif [[ -n "$MODELS_VLLM_TGZ" ]]; then
    _info "Extracting $MODELS_VLLM_TGZ into $VLLM_DEST ..."
    rm -rf "$VLLM_DEST"
    mkdir -p "$VLLM_DEST"
    tar -xzf "$MODELS_VLLM_TGZ" -C "$VLLM_DEST"
    _info "vLLM models ready: $VLLM_DEST"
  else
    _warn "No models_vllm.tar.gz found and $VLLM_DEST is empty."
    _warn "  The vllm profile needs HuggingFace-format model dirs. Copy them to"
    _warn "  $VLLM_DEST or set QAGREDO_MODELS_LLM_HOST in .env."
  fi
fi

# ---------------------------------------------------------------------------
# Phase 4: Permissions
# ---------------------------------------------------------------------------
_step "Phase 4: Fixing host directory ownership"

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
_fix_image=""
for cand in "$QAGREDO_IMAGE" "$QAGREDO_KUBEFLOW_IMAGE" "$VLLM_IMAGE"; do
  if _image_exists "$cand"; then _fix_image="$cand"; break; fi
done

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
for k, v in {
    "@QAGREDO_HOST_DIR@": host_dir,
    "@HOST_UID@": uid,
    "@HOST_GID@": gid,
    "@QAGREDO_PROFILE@": profile,
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
  dev)
    _check "dev image present" _image_exists "$QAGREDO_IMAGE"
    ;;
  kubeflow)
    _check "kubeflow image present" _image_exists "$QAGREDO_KUBEFLOW_IMAGE"
    _check "Ollama store present at ./models" test -d "$HOST_DIR/models/blobs"
    ;;
  vllm)
    _check "dev/runner image present" _image_exists "$QAGREDO_IMAGE"
    _check "vLLM runtime image present" _image_exists "$VLLM_IMAGE"
    _check "vLLM models directory populated" \
      bash -c '[[ -n "$(ls -A '"$HOST_DIR"'/models_llm 2>/dev/null)" ]]'
    ;;
esac

for d in config utils scripts; do
  _check "Dir: $d/" test -d "$HOST_DIR/$d"
done
_check "config/config.${PROFILE}.yaml exists" test -f "$HOST_DIR/config/config.${PROFILE}.yaml"
_check "run.sh exists"               test -f "$HOST_DIR/run.sh"
_check "run_qa_pipeline.py exists"   test -f "$HOST_DIR/run_qa_pipeline.py"
_check "docker-compose.yml"          test -f "$HOST_DIR/docker-compose.yml"
_check "docker-compose.kubeflow.yml" test -f "$HOST_DIR/docker-compose.kubeflow.yml"
_check "docker-compose.vllm-stack.yml" test -f "$HOST_DIR/docker-compose.vllm-stack.yml"
_check "docker-compose.vllm-redserver.yml" test -f "$HOST_DIR/docker-compose.vllm-redserver.yml"

if command -v nvidia-smi >/dev/null 2>&1; then
  _check "NVIDIA GPU available" nvidia-smi
else
  if [[ "$PROFILE" == "vllm" ]]; then
    _warn "nvidia-smi not found — vllm profile needs NVIDIA drivers/GPU."
  else
    _info "nvidia-smi not found (OK if Ollama runs CPU-only or GPU is remote)."
  fi
fi

_step "Summary"
echo
echo "  Profile       : $PROFILE"
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
echo "  bash run.sh                   # run the pipeline"
echo
echo "Full guide: $HOST_DIR/docs/OFFLINE_SETUP_GUIDE.md"
