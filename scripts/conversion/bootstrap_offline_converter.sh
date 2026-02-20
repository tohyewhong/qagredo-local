#!/usr/bin/env bash
set -euo pipefail

# Offline bootstrap for the conversion script dependencies.
#
# You can run this:
# - on the OFFLINE SERVER host (if it has python3 + venv), or
# - inside the qagredo container, then run the converter using the created venv.
#
# Typical (inside qagredo container) usage:
#   bash /workspace/scripts/conversion/bootstrap_offline_converter.sh
#   /workspace/.venv_converter/bin/python /workspace/scripts/conversion/convert_to_qagredo_jsonl.py --input ... --output ...
#
# This installs from the local wheelhouse folder:
#   ./wheelhouse_converter/
# and does NOT require internet.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REQ_FILE="${REPO_DIR}/requirements-converter.txt"
WHEELHOUSE_DIR="${REPO_DIR}/wheelhouse_converter"
VENV_DIR="${VENV_DIR:-${REPO_DIR}/.venv_converter}"

die() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
ok() { echo "[OK] $*"; }

python3 -V >/dev/null 2>&1 || die "python3 not found on PATH"
python3 -m venv -h >/dev/null 2>&1 || die "python3 venv module is missing (install python3-venv on the host, or run inside the container)"

[[ -f "${REQ_FILE}" ]] || die "Missing requirements file: ${REQ_FILE}"
[[ -d "${WHEELHOUSE_DIR}" ]] || die "Missing wheelhouse folder: ${WHEELHOUSE_DIR} (run build_wheelhouse_converter.sh on an online machine first)"

info "Creating converter venv at: ${VENV_DIR}"
python3 -m venv "${VENV_DIR}"

info "Installing converter dependencies from wheelhouse (offline)"
"${VENV_DIR}/bin/python" -m pip install \
  --no-index \
  --find-links "${WHEELHOUSE_DIR}" \
  -r "${REQ_FILE}"

info "Verifying imports"
"${VENV_DIR}/bin/python" - <<'PY'
import importlib
for m in ("pypdf", "openpyxl"):
    importlib.import_module(m)
print("OK: converter dependencies import successfully")
PY

ok "Offline converter environment is ready."
echo "Run converter with:"
echo "  ${VENV_DIR}/bin/python ${REPO_DIR}/scripts/conversion/convert_to_qagredo_jsonl.py --input <file> --output <file.jsonl>"
