#!/usr/bin/env bash
set -euo pipefail

# Export the Qwen3.5-compatible vLLM image for offline hosts (server1, air-gapped).
#
# Prerequisite: image already built:
#   bash scripts/docker_build_vllm_qwen35_compat.sh
#
# Usage:
#   bash scripts/save_vllm_qwen35_image.sh
#   OUT_DIR=/data/tyewhong/qag bash scripts/save_vllm_qwen35_image.sh

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${VLLM_IMAGE:-qag-vllm:qwen35-localcuda}"
ARCHIVE_DIR="${QAG_ARCHIVE_DIR:-/data/tyewhong/qag}"
OUT_DIR="${OUT_DIR:-$ARCHIVE_DIR}"
OUT_FILE="${OUT_FILE:-$OUT_DIR/vllm-qwen35-localcuda.rootfs.tar}"
mkdir -p "$OUT_DIR"

if ! docker image inspect "$TAG" >/dev/null 2>&1; then
  echo "[save] Image not found: $TAG" >&2
  echo "[save] Build first: bash scripts/docker_build_vllm_qwen35_compat.sh" >&2
  exit 1
fi

echo "[save] Saving $TAG -> $OUT_FILE (this may take several minutes) ..."
docker save -o "$OUT_FILE" "$TAG"
sha256sum "$OUT_FILE" > "${OUT_FILE}.sha256"
echo "[save] Done."
echo "  Archive : $OUT_FILE"
echo "  Checksum: ${OUT_FILE}.sha256"
echo "  Load on offline host: docker load -i $(basename "$OUT_FILE")"
echo "  Then in .env: VLLM_IMAGE=$TAG"
