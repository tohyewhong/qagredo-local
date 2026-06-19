#!/usr/bin/env bash
# Download Qwen/Qwen3-14B-FP8 into QAGREDO_MODELS_LLM_HOST for vLLM generator.
set -euo pipefail

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$HOST_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$HOST_DIR/.env"
  set +a
fi

MODELS_ROOT="${QAGREDO_MODELS_LLM_HOST:-/data/models}"
DEST="${MODELS_ROOT}/Qwen3-14B-FP8"
HF_ID="Qwen/Qwen3-14B-FP8"

_log() { echo "[download-qwen3-14b-fp8] $*"; }
_die() { echo "[download-qwen3-14b-fp8][ERROR] $*" >&2; exit 1; }

if [[ -f "${DEST}/config.json" ]] \
    && compgen -G "${DEST}/*.safetensors" > /dev/null; then
  _log "Weights already present under ${DEST}"
  exit 0
fi

mkdir -p "${DEST}"

if command -v huggingface-cli >/dev/null 2>&1; then
  HF_CLI=huggingface-cli
elif python3 -m huggingface_hub.commands.huggingface_cli \
    --help >/dev/null 2>&1; then
  HF_CLI="python3 -m huggingface_hub.cli"
else
  _die "Install huggingface_hub: pip install -U huggingface_hub"
fi

_log "Downloading ${HF_ID} -> ${DEST}"
_log "(This is ~15 GB; may take a while.)"

# shellcheck disable=SC2086
${HF_CLI} download "${HF_ID}" \
  --local-dir "${DEST}" \
  --local-dir-use-symlinks False

if [[ ! -f "${DEST}/config.json" ]]; then
  _die "Download finished but ${DEST}/config.json is missing"
fi
if ! compgen -G "${DEST}/*.safetensors" > /dev/null; then
  _die "Download finished but no .safetensors found in ${DEST}"
fi

_log "Done. Next:"
_log "  bash run.sh --down"
_log "  bash run.sh --vllm-up generator"
_log "  bash run.sh --pipeline-only --resume --num-documents N"
