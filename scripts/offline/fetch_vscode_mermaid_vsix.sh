#!/usr/bin/env bash
set -euo pipefail

# Download Markdown Preview Mermaid Support VSIX for offline VS Code.
# Run on the online build host before make_qag_bundle.sh.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${REPO_DIR}/docs/vscode-extensions"
EXT_ID="bierner/markdown-mermaid"

mkdir -p "$OUT_DIR"

echo "[INFO] Querying Open VSX for ${EXT_ID} ..."
_meta="$(curl -fsSL "https://open-vsx.org/api/${EXT_ID}/latest")"
_version="$(python3 -c "import json,sys; print(json.load(sys.stdin)['version'])" \
  <<<"$_meta")"
_url="$(python3 -c "import json,sys; print(json.load(sys.stdin)['files']['download'])" \
  <<<"$_meta")"
_out="${OUT_DIR}/bierner.markdown-mermaid-${_version}.vsix"

echo "[INFO] Downloading ${_version} ..."
curl -fsSL -o "$_out" "$_url"
ls -lh "$_out"
echo "[OK]   Wrote $_out"
