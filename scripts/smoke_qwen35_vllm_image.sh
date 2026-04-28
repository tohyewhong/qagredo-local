#!/usr/bin/env bash
# Verify the Qwen3.5 vLLM image can load HF config (qwen3_5) and the vLLM
# qwen3_5 executor module. Does not run the full server (no GPU required for this step).
#
# Usage:
#   MODEL_HOST_DIR=/data/models/Qwen3.5-9B bash scripts/smoke_qwen35_vllm_image.sh
#   IMAGE=qagredo-vllm:qwen35-localcuda MODEL_HOST_DIR=... bash scripts/smoke_qwen35_vllm_image.sh

set -euo pipefail

IMAGE="${IMAGE:-qagredo-vllm:qwen35-localcuda}"
MODEL_HOST_DIR="${MODEL_HOST_DIR:-/data/models/Qwen3.5-9B}"

[[ -d "$MODEL_HOST_DIR" ]] || {
  echo "[smoke] ERROR: model dir not found: $MODEL_HOST_DIR" >&2
  exit 1
}

echo "[smoke] image=$IMAGE model=$MODEL_HOST_DIR"
docker run --rm \
  -v "$MODEL_HOST_DIR:/m:ro" \
  --entrypoint python3 \
  "$IMAGE" \
  -c "
from transformers import AutoConfig
c = AutoConfig.from_pretrained('/m', trust_remote_code=True)
assert c.model_type == 'qwen3_5', c.model_type
from vllm.model_executor.models import qwen3_5
print('[smoke] OK: transformers sees qwen3_5; vLLM qwen3_5 module importable:', qwen3_5.__file__)
"
