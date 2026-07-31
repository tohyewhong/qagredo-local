#!/usr/bin/env bash
set -euo pipefail

# Rebuild .venv-lora and write lora_venv.tar.gz (+ .sha256 + .manifest.json)
# for offline opserver / redserver finetune.
#
# Usage:
#   bash scripts/lora/pack_lora_venv.sh
#   QAG_ARCHIVE_DIR=/data/tyewhong/qag bash scripts/lora/pack_lora_venv.sh

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${QAG_ARCHIVE_DIR:-/data/tyewhong/qag}"
VENV_DIR="${QAG_LORA_VENV:-$HOST_DIR/.venv-lora}"
TARBALL="$OUT_DIR/lora_venv.tar.gz"
MANIFEST="$OUT_DIR/lora_venv.manifest.json"

die() { echo "[pack-lora][ERROR] $*" >&2; exit 1; }
_log() { echo "[pack-lora] $*"; }

[[ -d "$OUT_DIR" ]] || die "Output dir missing: $OUT_DIR"

_log "Removing old venv at $VENV_DIR"
rm -rf "$VENV_DIR"

_log "Building fresh venv (cu124 torch + jinja2>=3.1.0)"
bash "$HOST_DIR/scripts/lora/setup_lora_venv.sh"

LORA_PY="$VENV_DIR/bin/python"
[[ -x "$LORA_PY" ]] || die "venv python missing: $LORA_PY"

read -r TORCH_VER CUDA_VER JINJA_VER <<EOF
$("$LORA_PY" - <<'PY'
import jinja2
import torch
print(torch.__version__, torch.version.cuda, jinja2.__version__)
PY
)
EOF

_log "Verified: torch=$TORCH_VER cuda=$CUDA_VER jinja2=$JINJA_VER"

if [[ "$CUDA_VER" != 12.* ]]; then
  die "Expected CUDA 12.x torch build; got cuda=$CUDA_VER"
fi

_log "Writing tarball (this may take several minutes): $TARBALL"
tar czf "$TARBALL" -C "$(dirname "$VENV_DIR")" "$(basename "$VENV_DIR")"

SHA256="$(sha256sum "$TARBALL" | awk '{print $1}')"
echo "$SHA256  $(basename "$TARBALL")" > "${TARBALL}.sha256"

BUILD_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
python3 - <<PY
import json
from pathlib import Path

payload = {
    "built_at": "${BUILD_TS}",
    "tarball": str(Path("${TARBALL}").name),
    "sha256": "${SHA256}",
    "torch": "${TORCH_VER}",
    "torch_cuda": "${CUDA_VER}",
    "jinja2": "${JINJA_VER}",
    "python": "$("$LORA_PY" -c 'import sys; print(sys.version.split()[0])')",
    "venv_top_level": "$(basename "$VENV_DIR")",
    "install_hint": "bash scripts/lora/install_lora_venv_offline.sh /path/to/lora_venv.tar.gz",
}
Path("${MANIFEST}").write_text(json.dumps(payload, indent=2) + "\\n")
PY

_log "Done."
_log "  tarball : $TARBALL"
_log "  sha256  : ${TARBALL}.sha256"
_log "  manifest: $MANIFEST"
_log "  sha256  : $SHA256"
ls -lh "$TARBALL" "${TARBALL}.sha256" "$MANIFEST"
