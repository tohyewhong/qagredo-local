#!/usr/bin/env bash
set -euo pipefail

# One-time setup on redserver: render Mermaid in ALL .md Markdown previews.
#
# Usage (from qag_host/):
#   bash scripts/offline/install_vscode_diagram_preview.sh
#
# Requires: VS Code `code` CLI on PATH (or set CODE_BIN).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QAG_HOST="$(cd "$SCRIPT_DIR/../.." && pwd)"
EXT_DIR="${QAG_HOST}/docs/vscode-extensions"
CODE_BIN="${CODE_BIN:-code}"

_die() { echo "[ERROR] $*" >&2; exit 1; }
_info() { echo "[INFO]  $*"; }
_ok() { echo "[OK]    $*"; }

_install_vsix() {
  local bin="$1"
  local vsix="$2"
  _info "Installing extension via ${bin} ..."
  "$bin" --install-extension "$vsix"
  _ok "Extension installed (${bin})."
}

VSIX="$(ls "${EXT_DIR}"/bierner.markdown-mermaid-*.vsix 2>/dev/null \
  | sort -V | tail -n1 || true)"
[[ -n "$VSIX" ]] || _die "No VSIX in ${EXT_DIR}. Re-copy qag_bundle or run fetch_vscode_mermaid_vsix.sh on the build host."

mkdir -p "${QAG_HOST}/.vscode"
cp "${EXT_DIR}/extensions.json" "${QAG_HOST}/.vscode/extensions.json"
cp "${EXT_DIR}/settings.json" "${QAG_HOST}/.vscode/settings.json"
_ok "Wrote ${QAG_HOST}/.vscode/ (open this folder as workspace root)."

INSTALLED=0
for try_bin in "$CODE_BIN" cursor; do
  if command -v "$try_bin" >/dev/null 2>&1; then
    _install_vsix "$try_bin" "$VSIX" && INSTALLED=1
  fi
done

if [[ "$INSTALLED" -eq 0 ]]; then
  _info "Neither '${CODE_BIN}' nor 'cursor' on PATH — install VSIX manually:"
  _info "  Extensions → ⋯ → Install from VSIX"
  _info "  File: ${VSIX}"
  _info "  Do not use Marketplace on air-gapped hosts."
fi

echo
_ok "Done. Open any docs/*.md → Markdown Preview (Ctrl+Shift+V)."
echo "Guide: ${QAG_HOST}/docs/VIEWING_DIAGRAMS_OFFLINE.md"
