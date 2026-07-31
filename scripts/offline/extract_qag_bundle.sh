#!/usr/bin/env bash
set -euo pipefail

# Extract qag_bundle.tar.gz without overwriting site .env / config YAML.
#
# Usage (on Opserver or Redserver after copying the bundle):
#   cd /home/tyewhong/qag
#   bash qag_host/scripts/offline/extract_qag_bundle.sh --code-only
#
# Or from the repo / build host (tests):
#   bash scripts/offline/extract_qag_bundle.sh \
#     --bundle /data/tyewhong/qag/qag_bundle.tar.gz \
#     --into /home/tyewhong/qag \
#     --code-only
#
# First install: omit --code-only (or use it — missing .env/config come from
# the bundle automatically).

BUNDLE=""
INTO=""
CODE_ONLY=0
PRESERVE_ENV=0
PRESERVE_CONFIG=0
DRY_RUN=0

die() { echo "[extract-bundle][ERROR] $*" >&2; exit 1; }
_log() { echo "[extract-bundle] $*"; }

usage() {
  cat <<'USAGE'
extract_qag_bundle.sh — unpack qag_bundle.tar.gz with optional site-config keep.

Options:
  --bundle PATH     Archive (default: <into>/qag_bundle.tar.gz)
  --into PATH       Parent dir for qag_host/ (default: /home/tyewhong/qag)
  --code-only       Update code/compose/docs; keep existing .env and config/
                    when present (recommended upgrade on Opserver/Redserver)
  --preserve-env    Do not replace qag_host/.env if it already exists
  --preserve-config Do not replace qag_host/config/ if it already exists
  --dry-run         Print actions only
  -h, --help        Show this message

Examples:
  # Upgrade: refresh code, keep site .env + YAML
  bash scripts/offline/extract_qag_bundle.sh --code-only

  # Full extract (first install — overwrites .env from bundle)
  bash scripts/offline/extract_qag_bundle.sh

  # Keep .env only (take new config/*.yaml from bundle)
  bash scripts/offline/extract_qag_bundle.sh --preserve-env
USAGE
}

while (($#)); do
  case "$1" in
    --bundle) shift; BUNDLE="${1:-}" ;;
    --bundle=*) BUNDLE="${1#*=}" ;;
    --into) shift; INTO="${1:-}" ;;
    --into=*) INTO="${1#*=}" ;;
    --code-only) CODE_ONLY=1 ;;
    --preserve-env) PRESERVE_ENV=1 ;;
    --preserve-config) PRESERVE_CONFIG=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
  shift
done

if [[ "$CODE_ONLY" -eq 1 ]]; then
  PRESERVE_ENV=1
  PRESERVE_CONFIG=1
fi

INTO="${INTO:-/home/tyewhong/qag}"
BUNDLE="${BUNDLE:-$INTO/qag_bundle.tar.gz}"
HOST_DIR="$INTO/qag_host"

[[ -f "$BUNDLE" ]] || die "Bundle not found: $BUNDLE"
command -v tar >/dev/null 2>&1 || die "Missing: tar"
command -v rsync >/dev/null 2>&1 || die "Missing: rsync"

_skip_env=0
_skip_config=0
if [[ "$PRESERVE_ENV" -eq 1 && -f "$HOST_DIR/.env" ]]; then
  _skip_env=1
fi
if [[ "$PRESERVE_CONFIG" -eq 1 && -d "$HOST_DIR/config" ]]; then
  _cfg_count="$(find "$HOST_DIR/config" -maxdepth 1 -type f 2>/dev/null \
    | wc -l | tr -d ' ')"
  if [[ "${_cfg_count:-0}" -gt 0 ]]; then
    _skip_config=1
  fi
fi

if [[ "$_skip_env" -eq 0 && "$_skip_config" -eq 0 ]]; then
  _log "Full extract → $HOST_DIR (no site .env/config to preserve)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    _log "[dry-run] tar xzf $BUNDLE -C $INTO"
    exit 0
  fi
  tar xzf "$BUNDLE" -C "$INTO"
  _log "Done. Next: cd $HOST_DIR && bash run.sh --show-config"
  exit 0
fi

_log "Selective extract → $HOST_DIR"
[[ "$_skip_env" -eq 1 ]] && _log "  keep: .env"
[[ "$_skip_config" -eq 1 ]] && _log "  keep: config/"

STAGING="$(mktemp -d)"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

if [[ "$DRY_RUN" -eq 1 ]]; then
  _log "[dry-run] tar xzf $BUNDLE -C $STAGING"
  _log "[dry-run] rsync staging/qag_host/ → $HOST_DIR/"
  [[ "$_skip_env" -eq 1 ]] && _log "[dry-run]   exclude .env"
  [[ "$_skip_config" -eq 1 ]] && _log "[dry-run]   exclude config/"
  exit 0
fi

tar xzf "$BUNDLE" -C "$STAGING"
[[ -d "$STAGING/qag_host" ]] || die "Bundle missing qag_host/ top-level folder"

mkdir -p "$HOST_DIR"
RSYNC_OPTS=(-a)
[[ "$_skip_env" -eq 1 ]] && RSYNC_OPTS+=(--exclude='.env')
[[ "$_skip_config" -eq 1 ]] && RSYNC_OPTS+=(--exclude='config/')

rsync "${RSYNC_OPTS[@]}" "$STAGING/qag_host/" "$HOST_DIR/"
_log "Done. Site config preserved."
_log "Next: cd $HOST_DIR && bash run.sh --show-config"
