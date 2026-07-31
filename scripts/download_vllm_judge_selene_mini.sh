#!/usr/bin/env bash
# Download Atla Selene 1 Mini judge weights into QAG_MODELS_LLM_HOST.
set -euo pipefail

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$HOST_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$HOST_DIR/.env"
  set +a
fi

CERT="${HOST_DIR}/certbundle/certbundle.crt"
if [[ -f "$CERT" ]]; then
  export SSL_CERT_FILE="$CERT"
  export REQUESTS_CA_BUNDLE="$CERT"
fi

MODELS_ROOT="${QAG_MODELS_LLM_HOST:-/data/models}"
DEST="${MODELS_ROOT}/Selene-1-Mini-Llama-3.1-8B"
HF_ID="AtlaAI/Selene-1-Mini-Llama-3.1-8B"

_log() { echo "[download-selene-mini] $*"; }
_die() { echo "[download-selene-mini][ERROR] $*" >&2; exit 1; }

if [[ -f "${DEST}/config.json" ]] \
    && compgen -G "${DEST}/*.safetensors" > /dev/null; then
  _log "Weights already present under ${DEST}"
  exit 0
fi

mkdir -p "${DEST}"

if command -v hf >/dev/null 2>&1; then
  HF_CLI=(hf download)
elif command -v huggingface-cli >/dev/null 2>&1; then
  HF_CLI=(huggingface-cli download)
elif python3 -m huggingface_hub.cli download --help >/dev/null 2>&1; then
  HF_CLI=(python3 -m huggingface_hub.cli download)
else
  _die "Install huggingface_hub: pip install -U huggingface_hub"
fi

_log "Downloading ${HF_ID} -> ${DEST}"
_log "(~16 GB; same footprint as Llama-3.1-8B judge.)"

"${HF_CLI[@]}" "${HF_ID}" \
  --local-dir "${DEST}"

if [[ ! -f "${DEST}/config.json" ]]; then
  _die "Download finished but ${DEST}/config.json is missing"
fi
if ! compgen -G "${DEST}/*.safetensors" > /dev/null; then
  _die "Download finished but no .safetensors found in ${DEST}"
fi

_log "Done. Next:"
_log "  bash run.sh --down"
_log "  bash run.sh --vllm-up judge"
_log "  curl -sf http://localhost:7101/health && echo judge ok"
