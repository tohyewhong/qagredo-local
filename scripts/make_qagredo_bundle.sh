#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# make_qagredo_bundle.sh  --  Build the qagredo_bundle.tar.gz (code + configs)
# ============================================================================
#
# Produces ONE archive (qagredo_bundle.tar.gz) containing everything the
# offline / Kubeflow server needs EXCEPT the Docker image and model weights,
# which ship as separate tarballs (see scripts/make_offline_tarballs.sh).
#
# Contents of the bundle:
#   * run.sh                                      (main launcher)
#   * run_qa_pipeline.py, requirements.txt         (pipeline entry)
#   * utils/, scripts/                             (Python + helper scripts)
#   * config/config.ollama.yaml                    (profile YAML)
#   * config/config.kubeflow.yaml                  (profile YAML)
#   * config/config.vllm.yaml                      (profile YAML)
#   * config/config.<profile>.yaml (ollama, kubeflow, vllm)
#   * config/README.md
#   * docker-compose.yml                           (ollama profile)
#   * docker-compose.kubeflow.yml                  (kubeflow profile)
#   * docker-compose.vllm-stack.yml                (vllm profile)
#   * docker-compose.vllm-siteserver.yml            (optional 4-GPU override)
#   * Dockerfile, Dockerfile.kubeflow              (for rebuilds on-site)
#   * docs/ (includes HANDOVER.md, OFFLINE_SETUP_GUIDE.md), README.md, .env
#   * scripts/offline/setup_offline.sh + run.sh + verify + dotenv.template
#
# NOT in the bundle (ship as separate tars — see make_offline_tarballs.sh):
#   * qagredo-v1.tar                 Docker image for ollama profile
#   * qagredo-kubeflow.tar           Docker image for kubeflow profile
#   * models_ollama.tar.gz           Ollama GGUF store (dev / kubeflow)
#   * models_vllm.tar.gz             HuggingFace model dirs (vllm profile)
#   * vllm-qwen35-localcuda.rootfs.tar  vLLM runtime image (vllm profile; Qwen3.5)
#
# Usage:
#   cd /path/to/qagredo
#   bash scripts/make_qagredo_bundle.sh
#   bash scripts/make_qagredo_bundle.sh --include-data    # also bundle data/
#
# Output (default /data/tyewhong/qagredo/ — override with QAGREDO_ARCHIVE_DIR):
#   qagredo_bundle.tar.gz          (extracts to qagredo_host/)
#   qagredo_bundle.tar.gz.sha256
# ============================================================================

INCLUDE_HOST_DATA=0
for arg in "$@"; do
  case "$arg" in
    --include-data) INCLUDE_HOST_DATA=1 ;;
    -h|--help)
      cat <<USAGE
Usage: bash scripts/make_qagredo_bundle.sh [--include-data]

  --include-data   Also pack the repo data/ folder into the bundle
                   (default: omit — you usually ship data separately)
USAGE
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_DIR="${QAGREDO_ARCHIVE_DIR:-/data/tyewhong/qagredo}"
BUNDLE_NAME="qagredo_host"
STAGING_DIR="${REPO_DIR}/.bundle_staging_${BUNDLE_NAME}"
OUTPUT_TGZ="${ARCHIVE_DIR}/qagredo_bundle.tar.gz"
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
  require_cmd python3

  [[ -d "$REPO_DIR" ]] || die "Repo folder not found: $REPO_DIR"
  [[ -f "$REPO_DIR/docker-compose.yml" ]] || die "Missing: $REPO_DIR/docker-compose.yml"
  [[ -f "$REPO_DIR/docker-compose.kubeflow.yml" ]] || die "Missing: $REPO_DIR/docker-compose.kubeflow.yml"
  [[ -f "$REPO_DIR/docker-compose.vllm-stack.yml" ]] || die "Missing: $REPO_DIR/docker-compose.vllm-stack.yml"
  [[ -f "$REPO_DIR/docker-compose.vllm-siteserver.yml" ]] || die "Missing: $REPO_DIR/docker-compose.vllm-siteserver.yml"
  [[ -f "$REPO_DIR/config/config.ollama.yaml" ]] || die "Missing: config/config.ollama.yaml"
  [[ -f "$REPO_DIR/config/config.kubeflow.yaml" ]] || die "Missing: config/config.kubeflow.yaml"
  [[ -f "$REPO_DIR/config/config.vllm.yaml" ]] || die "Missing: config/config.vllm.yaml"

  info "Repo dir : $REPO_DIR"
  info "Output   : $OUTPUT_TGZ"
  mkdir -p "$ARCHIVE_DIR"

  rm -rf "$STAGING_DIR"
  mkdir -p "$STAGING_DIR"

  info "Copying launcher (run.sh, .env) ..."
  cp "$REPO_DIR/run.sh" "$STAGING_DIR/run.sh"
  chmod +x "$STAGING_DIR/run.sh"
  if [[ -f "$REPO_DIR/.env" ]]; then
    cp "$REPO_DIR/.env" "$STAGING_DIR/.env"
  fi

  info "Copying offline helper scripts ..."
  mkdir -p "$STAGING_DIR/scripts/offline"
  cp "$REPO_DIR/scripts/offline/setup_offline.sh" "$STAGING_DIR/setup_offline.sh"
  cp "$REPO_DIR/scripts/offline/verify_offline_deployment.sh" "$STAGING_DIR/verify_offline_deployment.sh"
  cp "$REPO_DIR/scripts/offline/dotenv.template" "$STAGING_DIR/scripts/offline/dotenv.template"
  chmod +x "$STAGING_DIR"/*.sh

  info "Copying docker-compose files (stripped of build: blocks) ..."
  _strip_build_block < "$REPO_DIR/docker-compose.yml" > "$STAGING_DIR/docker-compose.yml"
  _strip_build_block < "$REPO_DIR/docker-compose.kubeflow.yml" > "$STAGING_DIR/docker-compose.kubeflow.yml"
  _strip_build_block < "$REPO_DIR/docker-compose.vllm-stack.yml" > "$STAGING_DIR/docker-compose.vllm-stack.yml"
  _strip_build_block < "$REPO_DIR/docker-compose.vllm-siteserver.yml" > "$STAGING_DIR/docker-compose.vllm-siteserver.yml"

  info "Copying Dockerfiles (for on-site rebuilds if ever needed) ..."
  cp "$REPO_DIR/Dockerfile" "$STAGING_DIR/Dockerfile"
  cp "$REPO_DIR/Dockerfile.kubeflow" "$STAGING_DIR/Dockerfile.kubeflow"

  info "Copying application code ..."
  cp "$REPO_DIR/run_qa_pipeline.py" "$STAGING_DIR/"
  cp "$REPO_DIR/requirements.txt"   "$STAGING_DIR/"
  cp -a "$REPO_DIR/utils"           "$STAGING_DIR/utils"
  find "$STAGING_DIR/utils" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

  info "Copying helper scripts (conversion, utils, docker-entrypoint) ..."
  cp -a "$REPO_DIR/scripts/conversion"        "$STAGING_DIR/scripts/conversion" 2>/dev/null || true
  cp -a "$REPO_DIR/scripts/utils"             "$STAGING_DIR/scripts/utils"      2>/dev/null || true
  cp    "$REPO_DIR/scripts/docker-entrypoint.sh" "$STAGING_DIR/scripts/"
  cp    "$REPO_DIR/scripts/docker_verify_requirements.py" "$STAGING_DIR/scripts/"
  find "$STAGING_DIR/scripts" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  chmod +x "$STAGING_DIR/scripts/docker-entrypoint.sh" 2>/dev/null || true

  info "Copying profile configs ..."
  mkdir -p "$STAGING_DIR/config"
  cp "$REPO_DIR/config/config.ollama.yaml"   "$STAGING_DIR/config/"
  cp "$REPO_DIR/config/config.kubeflow.yaml" "$STAGING_DIR/config/"
  cp "$REPO_DIR/config/config.vllm.yaml"     "$STAGING_DIR/config/"
  if [[ -f "$REPO_DIR/config/README.md" ]]; then
    cp "$REPO_DIR/config/README.md" "$STAGING_DIR/config/"
  fi

  info "Creating empty output / cache directories ..."
  mkdir -p "$STAGING_DIR/output" "$STAGING_DIR/hf_cache" "$STAGING_DIR/hf_cache_judge"
  mkdir -p "$STAGING_DIR/data"
  if [[ "$INCLUDE_HOST_DATA" -eq 1 && -d "$REPO_DIR/data" ]]; then
    info "  (including repo data/* per --include-data)"
    cp -a "$REPO_DIR/data/"* "$STAGING_DIR/data/" 2>/dev/null || true
  fi

  info "Copying cert bundle (if present) ..."
  mkdir -p "$STAGING_DIR/certbundle"
  if [[ -f "$REPO_DIR/certbundle/certbundle.crt" ]]; then
    cp "$REPO_DIR/certbundle/certbundle.crt" "$STAGING_DIR/certbundle/"
    info "  (included certbundle/certbundle.crt)"
  fi

  info "Copying docs and top-level README ..."
  if [[ -d "$REPO_DIR/docs" ]]; then
    cp -a "$REPO_DIR/docs" "$STAGING_DIR/docs"
  fi
  [[ -f "$REPO_DIR/README.md" ]]        && cp "$REPO_DIR/README.md"        "$STAGING_DIR/"

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

  info "Generating SHA256 checksum ..."
  sha256sum "$OUTPUT_TGZ" > "$OUTPUT_SHA"

  rm -rf "$FINAL_DIR"

  echo ""
  ok "Bundle created successfully!"
  echo ""
  echo "  Archive  : $OUTPUT_TGZ"
  echo "  Checksum : $OUTPUT_SHA"
  echo "  Size     : $(du -h "$OUTPUT_TGZ" | cut -f1)"
  echo "  Extracts : qagredo_host/"
  echo ""
  echo "This bundle does NOT contain the Docker image or model weights."
  echo "Build those with:   bash scripts/make_offline_tarballs.sh"
  echo ""
  echo "Full offline guide: docs/OFFLINE_SETUP_GUIDE.md"
}

main
