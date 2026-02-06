#!/usr/bin/env bash
set -euo pipefail

# Loads the offline runner image (docker load) and imports vLLM (docker import)
# when `docker save` of the vLLM image tar is not possible on the export machine.
#
# Usage:
#   cd /path/to/qagredo-repo
#   QAGREDO_TAR=../qagredo-v1.tar \
#   VLLM_ROOTFS_TAR=../vllm-openai_v0.5.3.post1.rootfs.tar \
#   bash scripts/offline/load_images_offline.sh

QAGREDO_TAR="${QAGREDO_TAR:-qagredo-v1.tar}"
QAGREDO_IMAGE="${QAGREDO_IMAGE:-qagredo-v1:latest}"

# Backward-compat: accept old tar name if present
if [[ ! -f "$QAGREDO_TAR" && -f "qagredo-airgap_v5.tar" ]]; then
  QAGREDO_TAR="qagredo-airgap_v5.tar"
fi

VLLM_ROOTFS_TAR="${VLLM_ROOTFS_TAR:-vllm-openai_v0.5.3.post1.rootfs.tar}"
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

_log "Checking input tar files..."
_require_file "$QAGREDO_TAR"
_require_file "$VLLM_ROOTFS_TAR"

_log "Loading QAGRedo runner image from: $QAGREDO_TAR"
docker load -i "$QAGREDO_TAR" >/dev/null

_log "Importing vLLM rootfs tar into image tag: $VLLM_IMAGE"
docker import \
  --change 'WORKDIR /vllm-workspace' \
  --change 'ENTRYPOINT ["python3","-m","vllm.entrypoints.openai.api_server"]' \
  "$VLLM_ROOTFS_TAR" \
  "$VLLM_IMAGE" \
  >/dev/null

if _image_exists "$QAGREDO_IMAGE"; then _log "OK: $QAGREDO_IMAGE"; else _log "WARN: missing tag $QAGREDO_IMAGE"; fi
if _image_exists "$VLLM_IMAGE"; then _log "OK: $VLLM_IMAGE"; else _log "ERROR: missing tag $VLLM_IMAGE"; exit 1; fi

_log "Done."
