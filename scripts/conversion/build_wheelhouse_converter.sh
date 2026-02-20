#!/usr/bin/env bash
set -euo pipefail

# Build a small offline "wheelhouse" for the conversion script.
#
# This is intended for airgapped/offline servers where you cannot run:
#   pip install pypdf openpyxl
#
# Run this ON AN ONLINE MACHINE (internet required), inside the repo:
#   cd /home/tyewhong/qagredo
#   bash scripts/conversion/build_wheelhouse_converter.sh
#
# Output folder:
#   ./wheelhouse_converter/   (copy/bundle this for offline)

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REQ_FILE="${REPO_DIR}/requirements-converter.txt"
WHEELHOUSE_DIR="${REPO_DIR}/wheelhouse_converter"

die() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
ok() { echo "[OK] $*"; }

[[ -f "${REQ_FILE}" ]] || die "Missing requirements file: ${REQ_FILE}"

python3 -V >/dev/null 2>&1 || die "python3 not found on PATH"
python3 -m pip --version >/dev/null 2>&1 || die "pip not available for python3"

mkdir -p "${WHEELHOUSE_DIR}"

info "Downloading wheels into: ${WHEELHOUSE_DIR}"
# Enforce wheels only (avoid source distributions requiring compilers offline).
python3 -m pip download \
  --only-binary=:all: \
  --dest "${WHEELHOUSE_DIR}" \
  -r "${REQ_FILE}"

ok "Wheelhouse ready."
echo "Next:"
echo "  - Bundle ${WHEELHOUSE_DIR}/ into qagredo_bundle.tar.gz (via scripts/make_qagredo_bundle.sh)"
echo "  - Offline install (inside container or host):"
echo "      bash scripts/conversion/bootstrap_offline_converter.sh"
