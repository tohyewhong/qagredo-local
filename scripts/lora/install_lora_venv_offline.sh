#!/usr/bin/env bash
set -euo pipefail

# Install pre-built .venv-lora from lora_venv.tar.gz on an offline host.
# Verifies tarball contents BEFORE replacing the live venv.
#
# Usage (on opserver / redserver):
#   cd /home/tyewhong/qag/qag_host
#   bash scripts/lora/install_lora_venv_offline.sh \
#     /home/tyewhong/qag/lora_venv.tar.gz
#
# Optional:
#   QAG_LORA_VENV=/path/to/.venv-lora bash scripts/lora/...

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARBALL="${1:-}"

die() { echo "[install-lora][ERROR] $*" >&2; exit 1; }
_log() { echo "[install-lora] $*"; }

[[ -n "$TARBALL" ]] || die "Usage: $0 /path/to/lora_venv.tar.gz"
[[ -f "$TARBALL" ]] || die "Tarball not found: $TARBALL"

if [[ -f "$HOST_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$HOST_DIR/.env"
  set +a
fi

TARGET_VENV="${QAG_LORA_VENV:-$HOST_DIR/.venv-lora}"
TARGET_VENV="$(echo "$TARGET_VENV" | tr -d '"')"

SHA_FILE="${TARBALL}.sha256"
if [[ -f "$SHA_FILE" ]]; then
  _log "Checking sha256: $SHA_FILE"
  (cd "$(dirname "$TARBALL")" && sha256sum -c "$(basename "$SHA_FILE")") \
    || die "Checksum failed — re-copy tarball from build host"
else
  _log "No ${SHA_FILE}; skipping checksum"
fi

_log "Inspecting torch inside tarball"
TAR_TORCH="$(
  tar xzf "$TARBALL" -O .venv-lora/lib/python3.10/site-packages/torch/version.py \
    2>/dev/null | sed -n "s/^__version__ = '//p" | sed "s/'.*//"
)"
TAR_JINJA="$(
  tar xzf "$TARBALL" -O .venv-lora/lib/python3.10/site-packages/jinja2/__init__.py \
    2>/dev/null | sed -n 's/^__version__ = "//p' | sed 's/".*//'
)"

[[ -n "$TAR_TORCH" ]] || die "Could not read torch version from tarball"
_log "Tarball contains: torch=$TAR_TORCH jinja2=${TAR_JINJA:-unknown}"

case "$TAR_TORCH" in
  *cu130*|*cu131*)
    die "Tarball has $TAR_TORCH (needs CUDA 13 driver). Use cu124 build."
    ;;
  *cu124*)
    _log "cu124 tarball OK for driver CUDA 12.9 or 13.0"
    ;;
  *)
    die "Unexpected torch build in tarball: $TAR_TORCH"
    ;;
esac

if [[ -n "$TAR_JINJA" ]]; then
  MAJOR="${TAR_JINJA%%.*}"
  MINOR="$(echo "$TAR_JINJA" | cut -d. -f2)"
  if [[ "$MAJOR" -lt 3 || ( "$MAJOR" -eq 3 && "$MINOR" -lt 1 ) ]]; then
    die "Tarball jinja2=$TAR_JINJA (<3.1.0). Re-copy fresh lora_venv.tar.gz."
  fi
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

_log "Extracting to temp: $TMP"
tar xzf "$TARBALL" -C "$TMP"

TMP_PY="$TMP/.venv-lora/bin/python"
[[ -x "$TMP_PY" ]] || die "Missing $TMP_PY in tarball"

read -r LIVE_TORCH LIVE_CUDA LIVE_JINJA LIVE_CUDA_OK <<EOF
$("$TMP_PY" -c "
import jinja2, torch
print(torch.__version__, torch.version.cuda, jinja2.__version__, torch.cuda.is_available())
")
EOF

_log "Temp venv: torch=$LIVE_TORCH cuda=$LIVE_CUDA jinja2=$LIVE_JINJA cuda_ok=$LIVE_CUDA_OK"

case "$LIVE_TORCH" in
  *cu124*) ;;
  *) die "Temp venv torch=$LIVE_TORCH (expected cu124)" ;;
esac

J_MAJOR="${LIVE_JINJA%%.*}"
J_MINOR="$(echo "$LIVE_JINJA" | cut -d. -f2)"
if [[ "$J_MAJOR" -lt 3 || ( "$J_MAJOR" -eq 3 && "$J_MINOR" -lt 1 ) ]]; then
  die "Temp venv jinja2=$LIVE_JINJA (<3.1.0)"
fi

_log "Replacing live venv: $TARGET_VENV"
rm -rf "$TARGET_VENV"
mkdir -p "$(dirname "$TARGET_VENV")"
mv "$TMP/.venv-lora" "$TARGET_VENV"
trap - EXIT
rmdir "$TMP" 2>/dev/null || true

FINAL_PY="$TARGET_VENV/bin/python"
read -r FINAL_TORCH FINAL_CUDA FINAL_JINJA FINAL_CUDA_OK <<EOF
$("$FINAL_PY" -c "
import jinja2, torch
print(torch.__version__, torch.version.cuda, jinja2.__version__, torch.cuda.is_available())
")
EOF

_log "Installed venv: $TARGET_VENV"
_log "  torch=$FINAL_TORCH cuda=$FINAL_CUDA jinja2=$FINAL_JINJA cuda_ok=$FINAL_CUDA_OK"

if [[ "$FINAL_CUDA_OK" != "True" ]]; then
  _log "WARN: cuda not available yet (check nvidia-smi / driver)"
fi

_log "Set in .env:"
_log "  QAG_LORA_VENV=$TARGET_VENV"
_log "Next:"
_log "  bash run.sh --down"
_log "  bash run.sh --finetune-lora \"output/vllm/.../<timestamp>/\""
