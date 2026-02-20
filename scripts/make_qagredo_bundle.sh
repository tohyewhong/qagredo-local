#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# make_qagredo_bundle.sh  --  Create the qagredo_host bundle (all-in-one directory)
# ============================================================================
#
# Creates a single archive containing everything needed to run QAGRedo:
#   - Runner scripts (run.sh, setup_offline.sh, jupyter.sh, etc.)
#   - Docker Compose file
#   - Application code (run_qa_pipeline.py, utils/, scripts/)
#   - Configuration (config/config.yaml)
#   - Input data (data/)
#   - Documentation (docs/, README.md)
#
# After extraction on the offline server, qagredo_host/ is the ONE directory.
# All code, config, data, and output live there.  Docker mounts from it
# directly, so any edit persists across container restarts.
#
# Usage:
#   cd /path/to/qagredo
#   bash scripts/make_qagredo_bundle.sh
#   bash scripts/make_qagredo_bundle.sh --include-data    # include data/ files
#
# Output:
#   ./qagredo_bundle.tar.gz           (extracts to qagredo_host/)
#   ./qagredo_bundle.tar.gz.sha256
# ============================================================================

INCLUDE_HOST_DATA=0
for arg in "$@"; do
  case "$arg" in
    --include-data) INCLUDE_HOST_DATA=1 ;;
    -h|--help)
      echo "Usage: bash scripts/make_qagredo_bundle.sh [--include-data]"
      echo ""
      echo "  --include-data   Include data/*.jsonl in the bundle"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_HOST_DIR="${QAGREDO_HOST_DIR:-$REPO_DIR/qagredo_host}"
BUNDLE_NAME="qagredo_host"
# Use a temp staging dir to avoid colliding with SRC_HOST_DIR
STAGING_DIR="${REPO_DIR}/.bundle_staging_${BUNDLE_NAME}"
OUTPUT_TGZ="${REPO_DIR}/qagredo_bundle.tar.gz"
OUTPUT_SHA="${OUTPUT_TGZ}.sha256"

die()  { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO]  $*"; }
ok()   { echo "[OK]    $*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

# Strip the 'build:' block from compose files (not needed on offline server)
_strip_build_block() {
  python3 -c "
import re, sys
text = sys.stdin.read()
text = re.sub(r'\n    build:\n(      .*\n)*', '\n', text)
sys.stdout.write(text)
"
}

main() {
  require_cmd tar
  require_cmd sha256sum

  [[ -d "$REPO_DIR" ]] || die "Repo folder not found: $REPO_DIR"
  [[ -f "$REPO_DIR/docker-compose.offline.yml" ]] || die "Missing: $REPO_DIR/docker-compose.offline.yml"

  info "Repo dir : $REPO_DIR"
  info "Output   : $OUTPUT_TGZ"

  # ---- clean previous staging ----
  rm -rf "$STAGING_DIR"
  mkdir -p "$STAGING_DIR"

  # ---- copy runner scripts (top-level, run from qagredo_host/) ----
  info "Copying runner scripts..."
  cp "$REPO_DIR/scripts/offline/setup_offline.sh"        "$STAGING_DIR/setup_offline.sh"
  cp "$REPO_DIR/scripts/offline/run.sh"                   "$STAGING_DIR/run.sh"
  cp "$REPO_DIR/scripts/offline/jupyter.sh"               "$STAGING_DIR/jupyter.sh"
  chmod +x "$STAGING_DIR"/*.sh

  # ---- copy docker compose file (strip build: block for offline use) ----
  info "Copying Docker Compose file..."
  _strip_build_block < "$REPO_DIR/docker-compose.offline.yml" > "$STAGING_DIR/docker-compose.offline.yml"

  # ---- copy application code ----
  info "Copying application code..."
  cp "$REPO_DIR/run_qa_pipeline.py"  "$STAGING_DIR/"
  cp "$REPO_DIR/requirements.txt"    "$STAGING_DIR/"
  cp -a "$REPO_DIR/utils"            "$STAGING_DIR/utils"
  find "$STAGING_DIR/utils" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

  # ---- copy scripts ----
  info "Copying helper scripts..."
  cp -a "$REPO_DIR/scripts" "$STAGING_DIR/scripts"
  find "$STAGING_DIR/scripts" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

  # Remove online-only scripts (not needed on the offline server)
  rm -f "$STAGING_DIR/scripts/run_online.sh"
  rm -f "$STAGING_DIR/scripts/make_qagredo_bundle.sh"

  # ---- copy config ----
  info "Copying config..."
  mkdir -p "$STAGING_DIR/config"
  if [[ -f "$SRC_HOST_DIR/config/config.yaml" ]]; then
    cp "$SRC_HOST_DIR/config/config.yaml" "$STAGING_DIR/config/config.yaml"
    info "  (used config from host dir: $SRC_HOST_DIR/config/config.yaml)"
  elif [[ -f "$REPO_DIR/config/config.yaml" ]]; then
    cp "$REPO_DIR/config/config.yaml" "$STAGING_DIR/config/config.yaml"
    info "  (used config from repo: $REPO_DIR/config/config.yaml)"
  else
    die "No config.yaml found in $SRC_HOST_DIR/config/ or $REPO_DIR/config/"
  fi

  # ---- copy data ----
  mkdir -p "$STAGING_DIR/data"
  if [[ "$INCLUDE_HOST_DATA" -eq 1 && -d "$SRC_HOST_DIR/data" ]]; then
    info "Copying input data from host dir..."
    cp -a "$SRC_HOST_DIR/data/"* "$STAGING_DIR/data/" 2>/dev/null || true
  elif [[ -d "$REPO_DIR/data" ]]; then
    info "Copying input data from repo..."
    cp -a "$REPO_DIR/data/"* "$STAGING_DIR/data/" 2>/dev/null || true
  else
    info "No input data found (data/ will be empty)"
  fi

  # ---- create empty output and cache dirs ----
  mkdir -p "$STAGING_DIR/output"
  mkdir -p "$STAGING_DIR/hf_cache"

  # ---- copy certbundle (if present) ----
  mkdir -p "$STAGING_DIR/certbundle"
  if [[ -f "$REPO_DIR/certbundle/certbundle.crt" ]]; then
    cp "$REPO_DIR/certbundle/certbundle.crt" "$STAGING_DIR/certbundle/"
    info "Included certbundle/certbundle.crt"
  else
    info "No certbundle.crt found (skipping)"
  fi

  # ---- copy docs ----
  if [[ -d "$REPO_DIR/docs" ]]; then
    info "Copying docs..."
    cp -a "$REPO_DIR/docs" "$STAGING_DIR/docs"
    # Remove online-only docs (not needed on the offline server)
    rm -f "$STAGING_DIR/docs/ONLINE_SETUP_GUIDE.md"
  fi

  # ---- copy misc ----
  [[ -f "$REPO_DIR/README.md" ]]     && cp "$REPO_DIR/README.md"     "$STAGING_DIR/"
  [[ -f "$REPO_DIR/OFFLINE_GUIDE.md" ]] && cp "$REPO_DIR/OFFLINE_GUIDE.md" "$STAGING_DIR/"
  # .env is auto-generated by setup_offline.sh on the offline server; no template needed

  # ---- optional: wheelhouse for converter ----
  if [[ -f "$REPO_DIR/requirements-converter.txt" ]]; then
    cp "$REPO_DIR/requirements-converter.txt" "$STAGING_DIR/"
  fi
  if [[ -d "$REPO_DIR/wheelhouse_converter" ]]; then
    info "Copying wheelhouse_converter/..."
    cp -a "$REPO_DIR/wheelhouse_converter" "$STAGING_DIR/"
  fi

  # ---- create archive ----
  # Rename staging dir to final name for the archive
  FINAL_DIR="${REPO_DIR}/${BUNDLE_NAME}"
  rm -rf "$FINAL_DIR"
  mv "$STAGING_DIR" "$FINAL_DIR"

  info "Creating archive: $OUTPUT_TGZ"
  tar \
    --exclude='**/__pycache__' \
    --exclude='**/__pycache__/**' \
    --exclude='**/*.pyc' \
    -czf "$OUTPUT_TGZ" \
    -C "$REPO_DIR" \
    "$BUNDLE_NAME"

  # ---- checksum ----
  info "Generating SHA256 checksum..."
  sha256sum "$OUTPUT_TGZ" > "$OUTPUT_SHA"

  # ---- cleanup staging ----
  rm -rf "$FINAL_DIR"

  # ---- summary ----
  echo ""
  ok "Bundle created successfully!"
  echo ""
  echo "  Archive  : $OUTPUT_TGZ"
  echo "  Checksum : $OUTPUT_SHA"
  echo "  Size     : $(du -h "$OUTPUT_TGZ" | cut -f1)"
  echo "  Extracts : qagredo_host/   (all-in-one directory)"
  echo ""
  echo "Transfer these 5 files to the offline server:"
  echo "  1) vllm-openai_v0.5.3.post1.rootfs.tar   (Docker image, ~15-20 GB)"
  echo "  2) qagredo-v1.tar                          (Docker image, ~5-10 GB)"
  echo "  3) models_llm.tar                          (LLM model, ~30 GB)"
  echo "  4) models_embed_all-MiniLM-L6-v2.tar      (embedding model, ~263 MB)"
  echo "  5) qagredo_bundle.tar.gz                   (this bundle, $(du -h "$OUTPUT_TGZ" | cut -f1))"
  echo ""
  echo "On the offline server:"
  echo "  tar xzf qagredo_bundle.tar.gz              # extracts to qagredo_host/"
  echo "  cd qagredo_host"
  echo "  bash setup_offline.sh                       # loads images, links models"
  echo "  bash run.sh                                 # runs the pipeline"
  echo ""
  echo "Everything (code, config, data) lives in qagredo_host/."
  echo "Edit anything there and re-run -- changes persist across Docker restarts."
}

main
