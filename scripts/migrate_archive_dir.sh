#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# migrate_archive_dir.sh — move legacy /data/tyewhong/qagredo archives to qag
# ============================================================================
#
# Dry-run (default):  bash scripts/migrate_archive_dir.sh
# Apply:             bash scripts/migrate_archive_dir.sh --execute
#
# Renames qagredo-prefixed bundles to names expected by setup_offline.sh.
# Moves all other *.tar, *.tar.gz, and *.sha256 as-is.
# Skips symlinks; leaves non-archive files (logs, dirs) in the source dir.
# ============================================================================

LEGACY_DIR="${QAG_LEGACY_ARCHIVE_DIR:-/data/tyewhong/qagredo}"
TARGET_DIR="${QAG_ARCHIVE_DIR:-/data/tyewhong/qag}"
EXECUTE=0

while (($#)); do
  case "$1" in
    --execute) EXECUTE=1 ;;
    -h|--help)
      sed -n '1,/^# ==/ { /^# / s/^# \?//; p; }' "$0" | sed '$d'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

_log() { echo "[migrate] $*"; }
_warn() { echo "[migrate][WARN] $*" >&2; }

# Pairs: source_basename destination_basename
RENAMES=(
  "qagredo_bundle.tar.gz|qag_bundle.tar.gz"
  "qagredo-v1.tar|qag-v1.tar"
  "qagredo-kubeflow.tar|qag-kubeflow.tar"
  "qagredo-vllm_qwen35-localcuda.rootfs.tar|vllm-qwen35-localcuda.rootfs.tar"
)

_move_one() {
  local src="$1"
  local dst="$2"
  if [[ ! -e "$src" ]]; then
    return 0
  fi
  if [[ -e "$dst" && ! "$src" -ef "$dst" ]]; then
    _warn "Target exists, skip: $dst"
    return 0
  fi
  if [[ "$EXECUTE" -eq 1 ]]; then
    mkdir -p "$(dirname "$dst")"
    mv "$src" "$dst"
    _log "MOVED  $src -> $dst"
  else
    _log "DRY-RUN mv $src -> $dst"
  fi
}

if [[ ! -d "$LEGACY_DIR" ]]; then
  _warn "Legacy dir not found: $LEGACY_DIR (nothing to do)"
  exit 0
fi

mkdir -p "$TARGET_DIR"

_log "Source : $LEGACY_DIR"
_log "Target : $TARGET_DIR"
_log "Mode   : $([[ "$EXECUTE" -eq 1 ]] && echo execute || echo dry-run)"
echo

# Explicit renames (file + .sha256 sidecar)
for pair in "${RENAMES[@]}"; do
  src_base="${pair%%|*}"
  dst_base="${pair##*|}"
  _move_one "$LEGACY_DIR/$src_base" "$TARGET_DIR/$dst_base"
  _move_one "$LEGACY_DIR/$src_base.sha256" "$TARGET_DIR/$dst_base.sha256"
done

# Remove stale symlinks in legacy dir (canonical name will be the real file)
for link in \
    "$LEGACY_DIR/vllm-qwen35-localcuda.rootfs.tar" \
    "$LEGACY_DIR/vllm-qwen35-localcuda.rootfs.tar.sha256"
do
  if [[ -L "$link" ]]; then
    if [[ "$EXECUTE" -eq 1 ]]; then
      rm -f "$link"
      _log "REMOVED symlink $link"
    else
      _log "DRY-RUN rm symlink $link"
    fi
  fi
done

# Bulk move remaining regular archive files
shopt -s nullglob
for pattern in '*.tar' '*.tar.gz' '*.sha256'; do
  for src in "$LEGACY_DIR"/$pattern; do
    [[ -f "$src" ]] || continue
    [[ -L "$src" ]] && continue
    base="$(basename "$src")"
    dst="$TARGET_DIR/$base"
    _move_one "$src" "$dst"
  done
done
shopt -u nullglob

# Fix .sha256 sidecars: use basename paths (mv does not update them)
if [[ "$EXECUTE" -eq 1 ]]; then
  python3 - "$TARGET_DIR" <<'PYEOF'
import sys
from pathlib import Path

d = Path(sys.argv[1])
for sha in sorted(d.glob('*.sha256')):
    text = sha.read_text(encoding='utf-8').strip()
    if not text:
        continue
    parts = text.split(None, 1)
    if len(parts) != 2:
        continue
    digest, _old = parts
    base = sha.name[:-7]
    if not (d / base).is_file():
        continue
    sha.write_text(f'{digest}  {base}\n', encoding='utf-8')
PYEOF
  _log "Updated .sha256 sidecars to use basename paths"
fi

echo
if [[ "$EXECUTE" -eq 1 ]]; then
  _log "Done. Verify: ls -lah $TARGET_DIR"
  _log "Checksums: sha256sum -c $TARGET_DIR/qag_bundle.tar.gz.sha256"
else
  _log "Dry-run complete. Re-run with --execute to apply."
fi
