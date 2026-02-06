#!/usr/bin/env bash
set -euo pipefail

# Creates a single airgap payload archive:
#   offline_payload_YYYYMMDD_HHMMSS.tar.zst
#   offline_payload_YYYYMMDD_HHMMSS.tar.zst.sha256
#
# Includes by default:
# - Docker image tars (qagredo + vLLM)
# - Minimal repo files needed to run offline (compose + docs + helper scripts)
# - Host folder (config/data/models/cache) via QAGREDO_HOST_DIR (excluding output/ by default)
#
# Usage:
#   cd /path/to/qagredo
#   export QAGREDO_HOST_DIR=/home/tyewhong/qagredo_host   # optional (or use .env)
#   bash scripts/offline/make_offline_payload.sh
#
# Options:
#   --out-dir DIR          Output directory (default: repo root)
#   --name NAME            Base filename (default: offline_payload_YYYYMMDD_HHMMSS.tar.zst)
#   --no-host              Do not include QAGREDO_HOST_DIR contents
#   --include-output       Include host output/ folder (default: excluded)
#   --exclude-llm NAME     Exclude a subfolder under models_llm/ (repeatable)
#   --vllm-image IMAGE     Override vLLM image tag (default: vllm/vllm-openai:v0.5.3.post1)
#   --qagredo-image IMAGE  Override QAGRedo image tag (default: qagredo-v1:latest)
#   --zstd-level N         zstd compression level (default: 19)
#

_log() { echo "[make_offline_payload] $*"; }
_die() { _log "ERROR: $*"; exit 1; }
_sanitize_filename() {
  # Replace path-ish characters so we can safely write tar files.
  echo "$1" | sed 's#[/:]#_#g'
}
_require_nonempty_file() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  [[ -s "$path" ]] || return 1
  return 0
}

OUT_DIR="."
NAME=""
INCLUDE_HOST="1"
INCLUDE_OUTPUT="0"
EXCLUDE_LLM=()
VLLM_IMAGE="vllm/vllm-openai:v0.5.3.post1"
QAGREDO_IMAGE="qagredo-v1:latest"
ZSTD_LEVEL="19"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir) OUT_DIR="${2:-}"; shift 2;;
    --name) NAME="${2:-}"; shift 2;;
    --no-host) INCLUDE_HOST="0"; shift 1;;
    --include-output) INCLUDE_OUTPUT="1"; shift 1;;
    --exclude-llm) EXCLUDE_LLM+=("${2:-}"); shift 2;;
    --vllm-image) VLLM_IMAGE="${2:-}"; shift 2;;
    --qagredo-image) QAGREDO_IMAGE="${2:-}"; shift 2;;
    --zstd-level) ZSTD_LEVEL="${2:-}"; shift 2;;
    -h|--help)
      sed -n '1,120p' "$0"
      exit 0
      ;;
    *)
      _die "Unknown arg: $1 (try --help)"
      ;;
  esac
done

command -v docker >/dev/null 2>&1 || _die "docker not found"
command -v zstd >/dev/null 2>&1 || _die "zstd not found"
command -v sha256sum >/dev/null 2>&1 || _die "sha256sum not found"
command -v tar >/dev/null 2>&1 || _die "tar not found"

if [[ -z "$NAME" ]]; then
  TS="$(date +%Y%m%d_%H%M%S)"
  NAME="offline_payload_${TS}.tar.zst"
fi

mkdir -p "$OUT_DIR"
OUT_PATH="${OUT_DIR%/}/$NAME"
SHA_PATH="${OUT_PATH}.sha256"

if [[ -f "$OUT_PATH" ]]; then _die "output already exists: $OUT_PATH"; fi
if [[ -f "$SHA_PATH" ]]; then _die "output already exists: $SHA_PATH"; fi

# Determine host dir (if enabled). Prefer env, then .env, then repo-local default.
QAGREDO_HOST_DIR="${QAGREDO_HOST_DIR:-}"
if [[ -z "$QAGREDO_HOST_DIR" && -f ".env" ]]; then
  # shellcheck disable=SC1091
  set -a; source ".env"; set +a
fi
QAGREDO_HOST_DIR="${QAGREDO_HOST_DIR:-./qagredo_host}"

if [[ "$INCLUDE_HOST" == "1" ]]; then
  [[ -d "$QAGREDO_HOST_DIR" ]] || _die "QAGREDO_HOST_DIR not found: $QAGREDO_HOST_DIR (or pass --no-host)"
fi

_log "Checking docker images exist..."
docker image inspect "$QAGREDO_IMAGE" >/dev/null 2>&1 || _die "missing image: $QAGREDO_IMAGE"
docker image inspect "$VLLM_IMAGE" >/dev/null 2>&1 || _die "missing image: $VLLM_IMAGE"

STAGE="$(mktemp -d -t qagredo_offline_payload.XXXXXX)"
cleanup() {
  rm -rf "$STAGE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "$STAGE/__images__" "$STAGE/__repo__/scripts/offline" "$STAGE/__extract__/runner" "$STAGE/__extract__/code"

_log "Staging minimal repo files..."
cp -a docker-compose.offline.yml "$STAGE/__repo__/" 2>/dev/null || true
cp -a docker-compose.offline.mount-code.yml "$STAGE/__repo__/" 2>/dev/null || true
cp -a .env.example "$STAGE/__repo__/" 2>/dev/null || true
cp -a QUICKSTART_OFFLINE.md "$STAGE/__repo__/" 2>/dev/null || true
cp -a README.md "$STAGE/__repo__/" 2>/dev/null || true
cp -a scripts/offline/load_images_offline.sh "$STAGE/__repo__/scripts/offline/" 2>/dev/null || true

_log "Staging runnable qagredo_payload_extract folder..."

# Symlinks (avoid duplicating huge host/models + image tar data)
ln -s "../qagredo_host" "$STAGE/__extract__/host"
ln -s "../qagredo_offline_bundle" "$STAGE/__extract__/images"

# Top-level launchers
cat >"$STAGE/__extract__/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
exec bash "$ROOT_DIR/runner/run_offline.sh"
EOF

cat >"$STAGE/__extract__/jupyter.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
exec bash "$ROOT_DIR/runner/start_jupyter.sh"
EOF

cat >"$STAGE/__extract__/EDIT_HERE.txt" <<'EOF'
Edit your code on the offline server here:

  ./code/run_qa_pipeline.py
  ./code/utils/

Then run:
  bash ./run.sh

Or start Jupyter:
  bash ./jupyter.sh
EOF

chmod +x "$STAGE/__extract__/run.sh" "$STAGE/__extract__/jupyter.sh"

# Runner scripts (self-contained; no dependency on the repo copy)
cat >"$STAGE/__extract__/runner/sync_code_from_image.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

_log() { echo "[sync_code] $*"; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODE_DIR="${CODE_DIR:-$ROOT_DIR/code}"
IMAGE="${IMAGE:-qagredo-v1:latest}"
FORCE="${FORCE:-0}"

need_sync=0
[[ -f "$CODE_DIR/run_qa_pipeline.py" ]] || need_sync=1
[[ -d "$CODE_DIR/utils" ]] || need_sync=1
[[ -d "$CODE_DIR/scripts" ]] || need_sync=1
[[ -f "$CODE_DIR/requirements.txt" ]] || need_sync=1
[[ -f "$CODE_DIR/README.md" ]] || need_sync=1
[[ -f "$CODE_DIR/QUICKSTART_OFFLINE.md" ]] || need_sync=1

if [[ "$FORCE" != "1" && "$need_sync" == "0" ]]; then
  _log "OK: payload code already exists at $CODE_DIR (skipping)"
  exit 0
fi

_log "Populating payload code from image: $IMAGE"
mkdir -p "$CODE_DIR"

cid="$(docker create "$IMAGE")"
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT

rm -rf "$CODE_DIR/utils" "$CODE_DIR/scripts" 2>/dev/null || true
docker cp "$cid:/workspace/run_qa_pipeline.py" "$CODE_DIR/run_qa_pipeline.py"
docker cp "$cid:/workspace/utils" "$CODE_DIR/utils"
docker cp "$cid:/workspace/scripts" "$CODE_DIR/scripts"

# Helpful top-level files (make edits persist on host)
docker cp "$cid:/workspace/requirements.txt" "$CODE_DIR/requirements.txt" 2>/dev/null || true
docker cp "$cid:/workspace/README.md" "$CODE_DIR/README.md" 2>/dev/null || true
docker cp "$cid:/workspace/QUICKSTART_OFFLINE.md" "$CODE_DIR/QUICKSTART_OFFLINE.md" 2>/dev/null || true

_log "Done. Edit code in: $CODE_DIR"
EOF

cat >"$STAGE/__extract__/runner/sync_host_from_payload.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

_log() { echo "[sync_host] $*"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The payload-bundled "host" folder (symlink -> ../qagredo_host by default).
PAYLOAD_HOST_DIR="${PAYLOAD_HOST_DIR:-$ROOT_DIR/host}"

# Where you want the host folders to live on THIS machine.
QAGREDO_HOST_DIR="${QAGREDO_HOST_DIR:-${HOST_DIR:-$HOME/qagredo_host}}"

# Populate missing subfolders:
# - symlink (default): fast, no copying huge models/cache
# - copy            : physically copy payload content into QAGREDO_HOST_DIR
HOST_SYNC_MODE="${HOST_SYNC_MODE:-symlink}"

_log "Payload host dir : $PAYLOAD_HOST_DIR"
_log "Target host dir  : $QAGREDO_HOST_DIR"
_log "Sync mode        : $HOST_SYNC_MODE"

mkdir -p "$QAGREDO_HOST_DIR"

_sync_one() {
  local name="$1"
  local src="$PAYLOAD_HOST_DIR/$name"
  local dst="$QAGREDO_HOST_DIR/$name"

  if [[ -e "$dst" || -L "$dst" ]]; then
    return 0
  fi

  if [[ -d "$src" ]]; then
    case "$HOST_SYNC_MODE" in
      symlink) ln -s "$src" "$dst" ;;
      copy) cp -a "$src" "$dst" ;;
      *)
        _log "ERROR: unknown HOST_SYNC_MODE='$HOST_SYNC_MODE' (use symlink|copy)"
        exit 2
        ;;
    esac
  else
    mkdir -p "$dst"
  fi
}

_sync_one config
_sync_one data
_sync_one hf_cache
_sync_one models_embed
_sync_one models_llm
mkdir -p "$QAGREDO_HOST_DIR/output"

_log "OK. Host folder ready:"
_log "  $QAGREDO_HOST_DIR/{config,data,output,hf_cache,models_embed,models_llm}"
EOF

cat >"$STAGE/__extract__/runner/load_images_offline.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# Loads the QAGRedo runner image (docker load/import) and loads vLLM (docker load),
# falling back to vLLM rootfs import (docker import) if needed.

IMAGES_DIR="${IMAGES_DIR:-.}"

QAGREDO_TAR="${QAGREDO_TAR:-$IMAGES_DIR/qagredo-v1.tar}"
QAGREDO_IMAGE="${QAGREDO_IMAGE:-qagredo-v1:latest}"

VLLM_TAR="${VLLM_TAR:-$IMAGES_DIR/vllm-openai_v0.5.3.post1.tar}"
VLLM_ROOTFS_TAR="${VLLM_ROOTFS_TAR:-$IMAGES_DIR/vllm-openai_v0.5.3.post1.rootfs.tar}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.5.3.post1}"

_log() { echo "[load_images_offline] $*"; }

_require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    _log "ERROR: file not found: $path"
    exit 1
  fi
}

_image_exists() {
  local image="$1"
  docker image inspect "$image" >/dev/null 2>&1
}

_log "Docker: $(docker --version 2>/dev/null || true)"

_log "Loading QAGRedo runner image from: $QAGREDO_TAR"
_require_file "$QAGREDO_TAR"
if docker load -i "$QAGREDO_TAR" >/dev/null 2>&1; then
  _log "OK: docker load succeeded for runner image"
else
  _log "WARN: docker load failed; attempting docker import (rootfs tar fallback)"
  docker import \
    --change 'WORKDIR /workspace' \
    --change 'USER jovyan' \
    --change 'ENV PATH=/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
    --change 'EXPOSE 8888' \
    --change 'CMD ["/opt/conda/bin/jupyter","lab","--ip=0.0.0.0","--port=8888","--no-browser","--ServerApp.token=","--ServerApp.password=","--ServerApp.root_dir=/workspace"]' \
    "$QAGREDO_TAR" \
    "$QAGREDO_IMAGE" \
    >/dev/null
fi

if _image_exists "$QAGREDO_IMAGE"; then
  _log "OK: runner image available: $QAGREDO_IMAGE"
else
  _log "WARNING: expected runner image tag not found: $QAGREDO_IMAGE"
fi

if [[ -f "$VLLM_TAR" ]]; then
  _log "Loading vLLM image from docker-save tar: $VLLM_TAR"
  if docker load -i "$VLLM_TAR" >/dev/null 2>&1; then
    _log "OK: docker load succeeded for vLLM image tar"
    exit 0
  else
    _log "WARN: docker load failed for vLLM tar; will try rootfs import if available"
  fi
fi

_log "Importing vLLM rootfs tar into image tag: $VLLM_IMAGE"
_require_file "$VLLM_ROOTFS_TAR"
docker import \
  --change 'WORKDIR /vllm-workspace' \
  --change 'ENTRYPOINT ["python3","-m","vllm.entrypoints.openai.api_server"]' \
  "$VLLM_ROOTFS_TAR" \
  "$VLLM_IMAGE" \
  >/dev/null

if _image_exists "$VLLM_IMAGE"; then
  _log "OK: vLLM image available: $VLLM_IMAGE"
else
  _log "ERROR: vLLM image import did not produce expected tag: $VLLM_IMAGE"
  exit 1
fi
EOF

cat >"$STAGE/__extract__/runner/start_jupyter.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

_log() { echo "[start_jupyter] $*"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGES_DIR="${IMAGES_DIR:-$ROOT_DIR/images}"

PAYLOAD_HOST_DIR="${PAYLOAD_HOST_DIR:-$ROOT_DIR/host}"
QAGREDO_HOST_DIR="${QAGREDO_HOST_DIR:-${HOST_DIR:-$HOME/qagredo_host}}"

CONTAINER_NAME="${CONTAINER_NAME:-qagredo-jupyter}"

# Pick a free host port in [8888..8899]
PORT=""
for p in $(seq 8888 8899); do
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "(:|\\])${p}$"; then
    continue
  fi
  PORT="$p"
  break
done
if [[ -z "${PORT}" ]]; then
  _log "ERROR: no free port found in 8888..8899"
  exit 1
fi

_log "Payload root : $ROOT_DIR"
_log "Images dir   : $IMAGES_DIR"
_log "Host dir     : $QAGREDO_HOST_DIR"
_log "Server port  : $PORT"

_log "Stopping any existing Jupyter container named $CONTAINER_NAME (if present)..."
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

_log "Loading images (if needed)..."
IMAGES_DIR="$IMAGES_DIR" bash "$ROOT_DIR/runner/load_images_offline.sh"

_log "Preparing host folder (create + populate from payload if missing)..."
PAYLOAD_HOST_DIR="$PAYLOAD_HOST_DIR" QAGREDO_HOST_DIR="$QAGREDO_HOST_DIR" bash "$ROOT_DIR/runner/sync_host_from_payload.sh"

_log "Ensuring host output/cache are writable..."
QAGREDO_UID="$(docker run --rm qagredo-v1:latest id -u 2>/dev/null || echo 1000)"
QAGREDO_GID="$(docker run --rm qagredo-v1:latest id -g 2>/dev/null || echo 100)"
docker run --rm -u 0 -v "$QAGREDO_HOST_DIR:/qhost" qagredo-v1:latest bash -lc "
  set -e
  mkdir -p /qhost/output /qhost/hf_cache
  chown -R ${QAGREDO_UID}:${QAGREDO_GID} /qhost/output /qhost/hf_cache 2>/dev/null || true
  chmod -R a+rwX /qhost/output /qhost/hf_cache 2>/dev/null || true
"

_log "Starting Jupyter Lab container..."
docker run -d --name "$CONTAINER_NAME" -p "${PORT}:8888" \
  -v "$ROOT_DIR/runner:/workspace/runner:rw" \
  -v "$ROOT_DIR/code:/workspace/code:rw" \
  -v "$ROOT_DIR/code/run_qa_pipeline.py:/workspace/run_qa_pipeline.py:rw" \
  -v "$ROOT_DIR/code/utils:/workspace/utils:rw" \
  -v "$ROOT_DIR/code/scripts:/workspace/scripts:rw" \
  -v "$ROOT_DIR/code/requirements.txt:/workspace/requirements.txt:rw" \
  -v "$ROOT_DIR/code/README.md:/workspace/README.md:rw" \
  -v "$ROOT_DIR/code/QUICKSTART_OFFLINE.md:/workspace/QUICKSTART_OFFLINE.md:rw" \
  -v "$QAGREDO_HOST_DIR/config:/workspace/config:rw" \
  -v "$QAGREDO_HOST_DIR/data:/workspace/data:rw" \
  -v "$QAGREDO_HOST_DIR/output:/workspace/output:rw" \
  -v "$QAGREDO_HOST_DIR/hf_cache:/opt/hf_cache:rw" \
  -v "$QAGREDO_HOST_DIR/models_embed:/opt/models_embed:ro" \
  qagredo-v1:latest \
  /opt/conda/bin/jupyter lab \
    --ip=0.0.0.0 --port=8888 --no-browser \
    --ServerApp.token= --ServerApp.password= \
    --ServerApp.root_dir=/workspace \
  >/dev/null

_log "OK. Jupyter is starting."
_log "Check logs with: docker logs -f $CONTAINER_NAME"
_log "From your laptop, tunnel with:"
_log "  ssh -L 8899:localhost:${PORT} <user>@<server>"
_log "Then open: http://localhost:8899"
EOF

cat >"$STAGE/__extract__/runner/run_offline.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

_log() { echo "[run_offline] $*"; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

IMAGES_DIR="${IMAGES_DIR:-$ROOT_DIR/images}"
HOST_DIR="${HOST_DIR:-$ROOT_DIR/host}" # payload host (symlink -> ../qagredo_host by default)
export PAYLOAD_ROOT="${PAYLOAD_ROOT:-$ROOT_DIR}"

# Default to a stable host path (outside the payload folder).
export QAGREDO_HOST_DIR="${QAGREDO_HOST_DIR:-$HOME/qagredo_host}"
USE_PAYLOAD_CODE_MOUNT="${USE_PAYLOAD_CODE_MOUNT:-1}"

QAGREDO_TAR="${QAGREDO_TAR:-$IMAGES_DIR/qagredo-v1.tar}"
VLLM_TAR="${VLLM_TAR:-$IMAGES_DIR/vllm-openai_v0.5.3.post1.tar}"
VLLM_ROOTFS_TAR="${VLLM_ROOTFS_TAR:-$IMAGES_DIR/vllm-openai_v0.5.3.post1.rootfs.tar}"

_log "Root dir : $ROOT_DIR"
_log "Images   : $IMAGES_DIR"
_log "Host dir : $QAGREDO_HOST_DIR"
_log "Code dir : $PAYLOAD_ROOT/code (mount=${USE_PAYLOAD_CODE_MOUNT})"

_log "Preparing host folder (create + populate from payload if missing)..."
PAYLOAD_HOST_DIR="$HOST_DIR" QAGREDO_HOST_DIR="$QAGREDO_HOST_DIR" bash "$ROOT_DIR/runner/sync_host_from_payload.sh"

_log "Loading/importing images..."
IMAGES_DIR="$IMAGES_DIR" QAGREDO_TAR="$QAGREDO_TAR" VLLM_TAR="$VLLM_TAR" VLLM_ROOTFS_TAR="$VLLM_ROOTFS_TAR" bash "$ROOT_DIR/runner/load_images_offline.sh"

if [[ "$USE_PAYLOAD_CODE_MOUNT" == "1" ]]; then
  _log "Ensuring payload code exists (so you can edit it on host)..."
  bash "$ROOT_DIR/runner/sync_code_from_image.sh" || true
fi

_log "Ensuring host folder permissions for output/cache..."
QAGREDO_UID="$(docker run --rm qagredo-v1:latest id -u 2>/dev/null || echo 1000)"
QAGREDO_GID="$(docker run --rm qagredo-v1:latest id -g 2>/dev/null || echo 100)"
docker run --rm -u 0 -v "$QAGREDO_HOST_DIR:/qhost" qagredo-v1:latest bash -lc "
  set -e
  mkdir -p /qhost/output /qhost/hf_cache
  chown -R ${QAGREDO_UID}:${QAGREDO_GID} /qhost/output /qhost/hf_cache 2>/dev/null || true
  chmod -R a+rwX /qhost/output /qhost/hf_cache 2>/dev/null || true
"

_log "Starting vLLM..."
_log "IMPORTANT: set these env vars before running if needed:"
_log "  export VLLM_MODEL=/models/<your-llm-folder>"
_log "  export VLLM_SERVED_MODEL_NAME=<must match config.yaml llm.model>"
_log "  export VLLM_API_KEY=<must match runner>"

docker compose -f "$ROOT_DIR/runner/docker-compose.offline.yml" up -d vllm

_log "Running QAGRedo..."
if [[ "$USE_PAYLOAD_CODE_MOUNT" == "1" ]]; then
  docker compose \
    -f "$ROOT_DIR/runner/docker-compose.offline.yml" \
    -f "$ROOT_DIR/runner/docker-compose.offline.payload-code.yml" \
    run --rm qagredo
else
  docker compose -f "$ROOT_DIR/runner/docker-compose.offline.yml" run --rm qagredo
fi

_log "Done. Outputs are in: $QAGREDO_HOST_DIR/output"
EOF

chmod +x \
  "$STAGE/__extract__/runner/"*.sh

# Compose files (copied from this repo + overlay for payload code mounts)
cp -a docker-compose.offline.yml "$STAGE/__extract__/runner/docker-compose.offline.yml" 2>/dev/null || true
cat >"$STAGE/__extract__/runner/docker-compose.offline.payload-code.yml" <<'EOF'
# Overlay: mount the payload's ./code into the runner container.
services:
  qagredo:
    volumes:
      - ${PAYLOAD_ROOT}/code/run_qa_pipeline.py:/workspace/run_qa_pipeline.py:ro
      - ${PAYLOAD_ROOT}/code/utils:/workspace/utils:ro
      - ${PAYLOAD_ROOT}/code/scripts:/workspace/scripts:ro
EOF

_log "Exporting docker images (this can take a while)..."

# If you already have image tar files, re-use them (more reliable than docker save on some hosts).
# Expected conventional names (created earlier by QUICKSTART/README steps):
# - ./qagredo-v1.tar
# - ./vllm-openai_v0.5.3.post1.tar
if _require_nonempty_file "./qagredo-v1.tar"; then
  _log "Reusing existing image tar: ./qagredo-v1.tar"
  cp -a "./qagredo-v1.tar" "$STAGE/__images__/qagredo-v1.tar"
else
  docker save -o "$STAGE/__images__/qagredo-v1.tar" "$QAGREDO_IMAGE"
fi
VLLM_TAR_NAME=""
if [[ "$VLLM_IMAGE" == "vllm/vllm-openai:v0.5.3.post1" ]]; then
  VLLM_TAR_NAME="vllm-openai_v0.5.3.post1.tar"
else
  VLLM_TAR_NAME="$(_sanitize_filename "$VLLM_IMAGE").tar"
fi
if [[ "$VLLM_IMAGE" == "vllm/vllm-openai:v0.5.3.post1" ]] && _require_nonempty_file "./vllm-openai_v0.5.3.post1.tar"; then
  _log "Reusing existing image tar: ./vllm-openai_v0.5.3.post1.tar"
  cp -a "./vllm-openai_v0.5.3.post1.tar" "$STAGE/__images__/${VLLM_TAR_NAME}"
else
  docker save -o "$STAGE/__images__/${VLLM_TAR_NAME}" "$VLLM_IMAGE"
fi

_log "Writing MANIFEST.txt (sha256 of image tars)..."
{
  echo "created_at=$(date -Iseconds)"
  echo "qagredo_image=$QAGREDO_IMAGE"
  echo "vllm_image=$VLLM_IMAGE"
  echo "include_host=$INCLUDE_HOST"
  echo "include_output=$INCLUDE_OUTPUT"
  echo "exclude_llm=${EXCLUDE_LLM[*]:-}"
  echo
  (cd "$STAGE/__images__" && sha256sum *.tar)
} >"$STAGE/__repo__/MANIFEST.txt"

if [[ "$INCLUDE_HOST" == "1" ]]; then
  ln -s "$(readlink -f "$QAGREDO_HOST_DIR")" "$STAGE/__host__"
fi

_log "Staging payload code snapshot (run_qa_pipeline.py + utils + scripts) from the runner image..."
# This lets the offline server bind-mount editable code immediately (no rebuild needed).
cid="$(docker create "$QAGREDO_IMAGE")"
docker cp "$cid:/workspace/run_qa_pipeline.py" "$STAGE/__extract__/code/run_qa_pipeline.py"
docker cp "$cid:/workspace/utils" "$STAGE/__extract__/code/utils"
docker cp "$cid:/workspace/scripts" "$STAGE/__extract__/code/scripts" 2>/dev/null || true
docker cp "$cid:/workspace/requirements.txt" "$STAGE/__extract__/code/requirements.txt" 2>/dev/null || true
docker cp "$cid:/workspace/README.md" "$STAGE/__extract__/code/README.md" 2>/dev/null || true
docker cp "$cid:/workspace/QUICKSTART_OFFLINE.md" "$STAGE/__extract__/code/QUICKSTART_OFFLINE.md" 2>/dev/null || true
docker rm -f "$cid" >/dev/null 2>&1 || true

_log "Creating payload archive: $OUT_PATH"
EXCLUDES=()
if [[ "$INCLUDE_HOST" == "1" && "$INCLUDE_OUTPUT" != "1" ]]; then
  EXCLUDES+=(--exclude="__host__/output")
  EXCLUDES+=(--exclude="__host__/output/**")
fi
if [[ "$INCLUDE_HOST" == "1" && "${#EXCLUDE_LLM[@]}" -gt 0 ]]; then
  for m in "${EXCLUDE_LLM[@]}"; do
    [[ -n "$m" ]] || continue
    EXCLUDES+=(--exclude="__host__/models_llm/${m}")
    EXCLUDES+=(--exclude="__host__/models_llm/${m}/**")
  done
fi

TAR_INPUTS=(__repo__ __images__ __extract__)
if [[ "$INCLUDE_HOST" == "1" ]]; then TAR_INPUTS+=(__host__); fi

# --dereference makes tar follow the __host__ symlink so we archive host contents
tar --dereference \
  "${EXCLUDES[@]}" \
  -C "$STAGE" \
  --transform='s,^__repo__/,qagredo/,' \
  --transform='s,^__images__/,qagredo_offline_bundle/,' \
  --transform='s,^__host__/,qagredo_host/,' \
  --transform='s,^__extract__/,qagredo_payload_extract/,' \
  -cf - \
  "${TAR_INPUTS[@]}" \
  | zstd "-${ZSTD_LEVEL}" -T0 -o "$OUT_PATH"

sha256sum "$OUT_PATH" >"$SHA_PATH"

_log "Done."
_log "Payload: $OUT_PATH"
_log "SHA256 : $SHA_PATH"
