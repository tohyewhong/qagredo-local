#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# setup_offline.sh  --  One-time setup on the offline server
# ============================================================================
#
# Run this ONCE (or whenever you bring a new bundle) to:
#   1. Discover/verify Docker image tars and model directories
#   2. Load Docker images (idempotent -- skips if already loaded)
#   3. Link or copy model directories into this folder
#   4. Fix permissions so the Docker container can read/write
#   5. Run smoke tests
#
# Everything lives in this one directory (qagredo_host/).  After setup,
# you can edit config, code, or data here and re-run without rebuilding.
#
# Expected layout after transferring files:
#   /some/staging/
#   ├── vllm-openai_v0.5.3.post1.rootfs.tar   (file 1)
#   ├── qagredo-v1.tar                          (file 2)
#   ├── models_llm/                             (file 3a)
#   ├── models_embed/                           (file 3b)
#   └── qagredo_host/                           (file 5, extracted from bundle)
#       ├── setup_offline.sh   <-- you are here
#       ├── run.sh
#       ├── run_qa_pipeline.py
#       ├── config/config.yaml
#       ├── utils/
#       └── ...
#
# Usage:
#   cd qagredo_host
#   bash setup_offline.sh
#   bash setup_offline.sh --skip-images     # skip docker load
#   bash setup_offline.sh --force           # overwrite existing model links
# ============================================================================

SKIP_IMAGES=0
FORCE=0
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  case "${args[$i]}" in
    --skip-images) SKIP_IMAGES=1 ;;
    --force)       FORCE=1 ;;
    -h|--help)
      echo "Usage: bash setup_offline.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --skip-images   Skip loading Docker images (if already loaded)"
      echo "  --force         Overwrite existing model symlinks/directories"
      echo "  -h, --help      Show this help message"
      exit 0
      ;;
    *) echo "Unknown argument: ${args[$i]}" >&2; exit 2 ;;
  esac
  i=$((i + 1))
done

# ---- paths ----
HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "$HOST_DIR/.." && pwd)"

# ---- logging ----
_pass() { echo "[PASS]  $*"; }
_fail() { echo "[FAIL]  $*"; }
_info() { echo "[INFO]  $*"; }
_warn() { echo "[WARN]  $*"; }
_step() { echo ""; echo "======== $* ========"; }

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

# ---- auto-discover files ----
_find_file() {
  local name="$1"
  for candidate in \
    "$PARENT_DIR/$name" \
    "$HOST_DIR/$name" \
    "$PARENT_DIR"/*/"$name" \
    "$(cd "$PARENT_DIR/.." 2>/dev/null && pwd)/$name" \
  ; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
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
    if [[ -d "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# ============================================================================
# Phase 1: Discover components
# ============================================================================
_step "Phase 1: Discovering components"

# File 1: vLLM rootfs tar
if [[ -n "${VLLM_ROOTFS_TAR:-}" && -f "$VLLM_ROOTFS_TAR" ]]; then
  _info "vLLM tar (env):  $VLLM_ROOTFS_TAR"
elif VLLM_ROOTFS_TAR="$(_find_file "vllm-openai_v0.5.3.post1.rootfs.tar")"; then
  _info "vLLM tar (auto): $VLLM_ROOTFS_TAR"
else
  VLLM_ROOTFS_TAR=""
  _warn "vLLM rootfs tar not found. Set VLLM_ROOTFS_TAR=/path/to/file or place alongside this directory."
fi

# File 2: QAGRedo image tar
if [[ -n "${QAGREDO_TAR:-}" && -f "$QAGREDO_TAR" ]]; then
  _info "QAGRedo tar (env):  $QAGREDO_TAR"
elif QAGREDO_TAR="$(_find_file "qagredo-v1.tar")"; then
  _info "QAGRedo tar (auto): $QAGREDO_TAR"
else
  QAGREDO_TAR=""
  _warn "QAGRedo tar not found. Set QAGREDO_TAR=/path/to/file or place alongside this directory."
fi

# File 3a: LLM models directory
if [[ -n "${MODELS_LLM_DIR:-}" && -d "$MODELS_LLM_DIR" ]]; then
  _info "Models LLM (env):  $MODELS_LLM_DIR"
elif MODELS_LLM_DIR="$(_find_dir "models_llm")"; then
  _info "Models LLM (auto): $MODELS_LLM_DIR"
else
  MODELS_LLM_DIR=""
  _warn "models_llm/ not found. Set MODELS_LLM_DIR=/path/to/dir or place alongside this directory."
fi

# File 3b: Embedding models directory
if [[ -n "${MODELS_EMBED_DIR:-}" && -d "$MODELS_EMBED_DIR" ]]; then
  _info "Models embed (env):  $MODELS_EMBED_DIR"
elif MODELS_EMBED_DIR="$(_find_dir "models_embed")"; then
  _info "Models embed (auto): $MODELS_EMBED_DIR"
else
  MODELS_EMBED_DIR=""
  _warn "models_embed/ not found. Set MODELS_EMBED_DIR=/path/to/dir or place alongside this directory."
fi

_info "Host dir:  $HOST_DIR"

# ============================================================================
# Phase 2: Load Docker images
# ============================================================================
_step "Phase 2: Loading Docker images"

QAGREDO_IMAGE="${QAGREDO_IMAGE:-qagredo-v1:latest}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.5.3.post1}"

_image_exists() {
  docker image inspect "$1" >/dev/null 2>&1
}

if [[ "$SKIP_IMAGES" -eq 1 ]]; then
  _info "Skipping image loading (--skip-images)"
else
  # Load QAGRedo
  if _image_exists "$QAGREDO_IMAGE"; then
    _info "QAGRedo image already loaded: $QAGREDO_IMAGE (skipping)"
  elif [[ -n "$QAGREDO_TAR" ]]; then
    _info "Loading QAGRedo image from: $QAGREDO_TAR"
    docker load -i "$QAGREDO_TAR"
    _image_exists "$QAGREDO_IMAGE" && _pass "QAGRedo image loaded" || _fail "QAGRedo image load failed"
  else
    _warn "Cannot load QAGRedo image: tar file not found"
  fi

  # Import vLLM
  if _image_exists "$VLLM_IMAGE"; then
    _info "vLLM image already loaded: $VLLM_IMAGE (skipping)"
  elif [[ -n "$VLLM_ROOTFS_TAR" ]]; then
    _info "Importing vLLM rootfs from: $VLLM_ROOTFS_TAR"
    docker import \
      --change 'WORKDIR /vllm-workspace' \
      --change 'ENTRYPOINT ["python3","-m","vllm.entrypoints.openai.api_server"]' \
      "$VLLM_ROOTFS_TAR" \
      "$VLLM_IMAGE" \
      >/dev/null
    _image_exists "$VLLM_IMAGE" && _pass "vLLM image imported" || _fail "vLLM image import failed"
  else
    _warn "Cannot import vLLM image: rootfs tar not found"
  fi
fi

# ============================================================================
# Phase 3: Link models into host directory
# ============================================================================
_step "Phase 3: Linking models into $HOST_DIR"

# Ensure writable directories exist
mkdir -p "$HOST_DIR"/{output,hf_cache} 2>/dev/null || true

# Fix permissions early (before file operations) using Docker if needed
_fix_perms_if_needed() {
  if touch "$HOST_DIR/output/.write_test" 2>/dev/null; then
    rm -f "$HOST_DIR/output/.write_test"
    return 0
  fi

  _warn "Directory not writable -- attempting Docker-based permission fix..."

  local fix_image=""
  if _image_exists "$QAGREDO_IMAGE"; then
    fix_image="$QAGREDO_IMAGE"
  elif _image_exists "$VLLM_IMAGE"; then
    fix_image="$VLLM_IMAGE"
  fi

  if [[ -z "$fix_image" ]]; then
    _warn "No Docker image available to fix permissions."
    _warn "Load the QAGRedo image first, then re-run setup_offline.sh"
    return 1
  fi

  local _uid; _uid="$(id -u)"
  local _gid; _gid="$(id -g)"
  docker run --rm --privileged --userns=host -u 0 -v "$HOST_DIR:/qhost" "$fix_image" bash -c "
    set -e
    mkdir -p /qhost/output /qhost/hf_cache
    chown -R ${_uid}:${_gid} /qhost/config /qhost/data /qhost/output /qhost/hf_cache /qhost/utils /qhost/scripts 2>/dev/null || true
  " && _info "Permissions fixed via Docker (chown to UID=$_uid)" || _warn "Docker permission fix failed (non-fatal)"
}
_fix_perms_if_needed

# ---- models_llm ----
if [[ -n "$MODELS_LLM_DIR" ]]; then
  MODELS_LLM_REAL="$(realpath "$MODELS_LLM_DIR" 2>/dev/null || echo "$MODELS_LLM_DIR")"
  HOST_LLM_REAL="$(realpath "$HOST_DIR/models_llm" 2>/dev/null || echo "$HOST_DIR/models_llm")"
  if [[ "$MODELS_LLM_REAL" == "$HOST_LLM_REAL" ]]; then
    _info "LLM models already at host dir location (same path)"
  elif [[ -d "$HOST_DIR/models_llm" && "$FORCE" -ne 1 ]]; then
    _info "models_llm/ already exists (use --force to overwrite)"
  else
    rm -rf "$HOST_DIR/models_llm" 2>/dev/null || true
    ln -sfn "$MODELS_LLM_REAL" "$HOST_DIR/models_llm" 2>/dev/null && {
      _info "Symlinked: $HOST_DIR/models_llm -> $MODELS_LLM_REAL"
    } || {
      mkdir -p "$HOST_DIR/models_llm"
      cp -a "$MODELS_LLM_DIR"/* "$HOST_DIR/models_llm/"
      _info "Copied models_llm into host dir"
    }
  fi
fi

# ---- models_embed ----
if [[ -n "$MODELS_EMBED_DIR" ]]; then
  MODELS_EMBED_REAL="$(realpath "$MODELS_EMBED_DIR" 2>/dev/null || echo "$MODELS_EMBED_DIR")"
  HOST_EMBED_REAL="$(realpath "$HOST_DIR/models_embed" 2>/dev/null || echo "$HOST_DIR/models_embed")"
  if [[ "$MODELS_EMBED_REAL" == "$HOST_EMBED_REAL" ]]; then
    _info "Embedding models already at host dir location (same path)"
  elif [[ -d "$HOST_DIR/models_embed" && "$FORCE" -ne 1 ]]; then
    _info "models_embed/ already exists (use --force to overwrite)"
  else
    rm -rf "$HOST_DIR/models_embed" 2>/dev/null || true
    ln -sfn "$MODELS_EMBED_REAL" "$HOST_DIR/models_embed" 2>/dev/null && {
      _info "Symlinked: $HOST_DIR/models_embed -> $MODELS_EMBED_REAL"
    } || {
      mkdir -p "$HOST_DIR/models_embed"
      cp -a "$MODELS_EMBED_DIR"/* "$HOST_DIR/models_embed/"
      _info "Copied models_embed into host dir"
    }
  fi
fi

# ============================================================================
# Phase 4: Fix permissions
# ============================================================================
_step "Phase 4: Fixing permissions"

# The container entrypoint (docker-entrypoint.sh) maps the container user to
# the host user's UID/GID at startup, so output files are always owned by you.
# Here we just ensure the host directories are writable by the current user.
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

# Use Docker to set ownership to the current host user (works without sudo)
if _image_exists "$QAGREDO_IMAGE"; then
  _info "Setting ownership to UID=$HOST_UID GID=$HOST_GID (your user)"
  docker run --rm --privileged --userns=host -u 0 -v "$HOST_DIR:/qhost" "$QAGREDO_IMAGE" bash -c "
    set -e
    mkdir -p /qhost/output /qhost/hf_cache /qhost/config /qhost/data
    chown -R ${HOST_UID}:${HOST_GID} /qhost/output /qhost/hf_cache /qhost/config /qhost/data 2>/dev/null || true
  " && _info "Permissions fixed via container" || _warn "Permission fix via container failed (non-fatal)"
else
  _warn "QAGRedo image not loaded yet; skipping permission fix"
  _info "Permissions will be handled by the container entrypoint at runtime"
fi

# ============================================================================
# Phase 5: Write .env
# ============================================================================
_step "Phase 5: Writing .env"

ENV_FILE="$HOST_DIR/.env"
cat > "$ENV_FILE" <<ENVEOF
# Auto-generated by setup_offline.sh -- edit as needed
# No QAGREDO_HOST_DIR needed: everything is in this directory.

# Host user identity -- so Docker containers create files owned by you, not root.
HOST_UID=$(id -u)
HOST_GID=$(id -g)
ENVEOF
_info "Wrote $ENV_FILE (HOST_UID=$(id -u), HOST_GID=$(id -g))"

# ============================================================================
# Phase 6: Smoke tests
# ============================================================================
_step "Phase 6: Smoke tests"

_check "Docker is available" docker info
_check "QAGRedo image present ($QAGREDO_IMAGE)" _image_exists "$QAGREDO_IMAGE"
_check "vLLM image present ($VLLM_IMAGE)"       _image_exists "$VLLM_IMAGE"

# Host directory structure
for d in config data output utils scripts; do
  _check "Dir: $d/" test -d "$HOST_DIR/$d"
done
_check "config/config.yaml exists" test -f "$HOST_DIR/config/config.yaml"
_check "run_qa_pipeline.py exists" test -f "$HOST_DIR/run_qa_pipeline.py"
_check "docker-compose.offline.yml exists" test -f "$HOST_DIR/docker-compose.offline.yml"

# Models
_has_llm_model() {
  local found
  found=$(find -L "$HOST_DIR/models_llm" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)
  [[ -n "$found" ]]
}
_check "At least one LLM model in models_llm/" _has_llm_model
_check "all-MiniLM-L6-v2 in models_embed/" test -e "$HOST_DIR/models_embed/all-MiniLM-L6-v2"

# GPU
if command -v nvidia-smi >/dev/null 2>&1; then
  _check "NVIDIA GPU available" nvidia-smi
else
  _warn "nvidia-smi not found (vLLM requires NVIDIA GPU)"
fi

# ---- summary ----
_step "Summary"
echo ""
echo "  Tests passed: $TESTS_PASSED"
echo "  Tests failed: $TESTS_FAILED"
echo ""
if [[ "$TESTS_FAILED" -gt 0 ]]; then
  _warn "Some tests failed. Review the output above and fix before running."
else
  _pass "All tests passed!"
fi
echo ""
echo "Host directory (everything is here): $HOST_DIR"
echo ""
echo "Available LLM models:"
find -L "$HOST_DIR/models_llm" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | xargs -I{} basename {} | sed 's/^/  - /' || echo "  (none found)"
echo ""
echo "Editable files (persist across Docker restarts):"
echo "  $HOST_DIR/config/config.yaml     # pipeline config"
echo "  $HOST_DIR/utils/                  # Python source code"
echo "  $HOST_DIR/run_qa_pipeline.py      # main entry point"
echo "  $HOST_DIR/data/                   # input data"
echo ""
echo "Next steps:"
echo "  bash run.sh              # run the QAGRedo pipeline"
echo "  bash jupyter.sh          # OR: start Jupyter Lab"
echo ""
echo "  # To use a specific model (default: Meta-Llama-3.1-8B-Instruct):"
echo "  export VLLM_MODEL=/models/Meta-Llama-3.1-8B-Instruct"
echo "  export VLLM_SERVED_MODEL_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct"
echo "  bash run.sh"
