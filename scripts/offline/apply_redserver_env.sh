#!/usr/bin/env bash
set -euo pipefail

# Write qag_host/.env from dotenv.redserver.template (redserver preset).
#
# Usage (on redserver):
#   cd /home/tyewhong/qag/qag_host
#   bash scripts/offline/apply_redserver_env.sh
#
# Backs up existing .env to .env.bak.<timestamp> when present.

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE="${HOST_DIR}/scripts/offline/dotenv.redserver.template"
TARGET="${HOST_DIR}/.env"

die() { echo "[apply_redserver_env][ERROR] $*" >&2; exit 1; }
_log() { echo "[apply_redserver_env] $*"; }

[[ -f "$TEMPLATE" ]] || die "Missing template: $TEMPLATE"

_uid="$(id -u)"
_gid="$(id -g)"

if [[ -f "$TARGET" ]]; then
  _bak="${TARGET}.bak.$(date +%Y%m%d_%H%M%S)"
  cp "$TARGET" "$_bak"
  _log "Backed up existing .env → $_bak"
fi

sed \
  -e "s/@HOST_UID@/${_uid}/g" \
  -e "s/@HOST_GID@/${_gid}/g" \
  "$TEMPLATE" > "$TARGET"

_log "Wrote $TARGET (HOST_UID=${_uid} HOST_GID=${_gid})"
_log "Next: bash run.sh --show-config"
_log "      bash run.sh --pipeline-only --num-documents 1"
