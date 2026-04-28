#!/usr/bin/env bash
set -euo pipefail

# Build a vLLM image for Qwen3.5 (model_type qwen3_5):
# - Base v0.17.1 (Qwen3.5 in model executor; needs a recent NVIDIA driver stack).
# - Upgrade Transformers to 5.4+ inside the image (stock image ships 4.57.x,
#   which does not register qwen3_5 in AutoConfig).
#
# Older hosts that only run v0.5.3 CUDA stacks should use Qwen2.5 weights with
# vllm/vllm-openai:v0.5.3.post1 instead.
#
# Usage:
#   bash scripts/docker_build_vllm_qwen35_compat.sh
#   # optional custom tag:
#   VLLM_COMPAT_TAG=qagredo-vllm:qwen35-compat-v1 bash scripts/docker_build_vllm_qwen35_compat.sh

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${VLLM_COMPAT_TAG:-qagredo-vllm:qwen35-compat-v1}"

echo "[build] Building ${TAG} ..."
docker build \
  -f "${REPO_DIR}/docker/Dockerfile.vllm-qwen35-compat" \
  -t "${TAG}" \
  "${REPO_DIR}"

echo "[build] Done."
RELAX_TAG="${VLLM_RELAX_TAG:-qagredo-vllm:qwen35-localcuda}"
echo "[build] Building relaxed CUDA metadata tag for driver 535 / CUDA 12.2 hosts: ${RELAX_TAG} ..."
docker build \
  -f "${REPO_DIR}/docker/Dockerfile.vllm-qwen35-relax-cuda-meta" \
  --build-arg "BASE=${TAG}" \
  -t "${RELAX_TAG}" \
  "${REPO_DIR}"
echo "[build] Set in .env (recommended on this class of host):"
echo "  VLLM_IMAGE=${RELAX_TAG}"
