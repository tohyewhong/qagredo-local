#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# make_offline_tarballs.sh  --  Build every tarball needed for an offline /
#                                Kubeflow server, in one place.
# ============================================================================
#
# Produces (in /data/tyewhong/qagredo/ by default):
#
#   qagredo_bundle.tar.gz          Repo code + configs + compose files
#   qagredo-v1.tar                 Docker image for the ollama profile
#   qagredo-kubeflow.tar           Docker image for the kubeflow profile
#   models_ollama.tar.gz           Ollama GGUF store (for dev / kubeflow)
#   models_vllm.tar.gz             HuggingFace model dirs (for vllm profile)
#   <tag>.rootfs.tar               vLLM runtime image (for vllm profile; default qwen35-localcuda.rootfs.tar)
#
# Each tarball is optional — pass flags to pick the ones you actually need.
# Which tarballs to ship to the offline server:
#
#   Profile on offline server | Tarballs you must copy over
#   --------------------------+-------------------------------------------------
#   ollama                    | qagredo_bundle.tar.gz, qagredo-v1.tar,
#                             |  models_ollama.tar.gz (unless Ollama on the
#                             |  offline host already has the tags)
#   kubeflow                  | qagredo_bundle.tar.gz, qagredo-kubeflow.tar,
#                             |  models_ollama.tar.gz
#   vllm                      | qagredo_bundle.tar.gz, qagredo-v1.tar,
#                             |  qwen35-localcuda.rootfs.tar (or save_vllm script name), models_vllm.tar.gz
#
# Usage:
#   bash scripts/make_offline_tarballs.sh --all
#   bash scripts/make_offline_tarballs.sh --bundle --image-dev --models-ollama
#   bash scripts/make_offline_tarballs.sh --help
# ============================================================================

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_DIR="${QAGREDO_ARCHIVE_DIR:-/data/tyewhong/qagredo}"
OUT_DIR="${QAGREDO_OFFLINE_OUT:-$ARCHIVE_DIR}"

DO_BUNDLE=0
DO_IMAGE_DEV=0
DO_IMAGE_KUBEFLOW=0
DO_MODELS_OLLAMA=0
DO_MODELS_OLLAMA_SPLIT=0
DO_MODELS_VLLM=0
DO_MODELS_VLLM_SPLIT=0
DO_IMAGE_VLLM=0

# Defaults for model sources (override with env vars or flags).
OLLAMA_STORE_SRC="${OLLAMA_STORE_SRC:-/data/ollama/models}"
# Must match llm.model / judge.model in config/config.ollama.yaml and config/config.kubeflow.yaml
OLLAMA_SPLIT_TAGS_DEFAULT=("qwen3.5:9b" "llama3.1:8b-instruct-fp16")
VLLM_MODELS_SRC="${VLLM_MODELS_SRC:-/home/tyewhong/qagredo/models_llm}"
VLLM_MODEL_DIRS_DEFAULT=("Qwen3.5-9B" "Meta-Llama-3.1-8B-Instruct")

QAGREDO_IMAGE="${QAGREDO_IMAGE:-qagredo-v1:latest}"
QAGREDO_KUBEFLOW_IMAGE="${QAGREDO_KUBEFLOW_IMAGE:-qagredo-kubeflow:latest}"
VLLM_IMAGE="${VLLM_IMAGE:-qagredo-vllm:qwen35-localcuda}"

_log()  { echo "[make-offline] $*"; }
_warn() { echo "[make-offline][WARN] $*" >&2; }
_die()  { echo "[make-offline][ERROR] $*" >&2; exit 1; }

usage() {
  cat <<USAGE
make_offline_tarballs.sh — build every tarball needed for an offline / Kubeflow server.

Output directory:  ${OUT_DIR}
  (override with QAGREDO_OFFLINE_OUT=/your/path or --out PATH)

Flags (select what to build — use --all for everything):
  --all                    Build every tarball below
  --bundle                 qagredo_bundle.tar.gz        (repo code + configs)
  --image-dev              qagredo-v1.tar (ollama profile runner; legacy flag name)
  --image-kubeflow         qagredo-kubeflow.tar         (docker save ${QAGREDO_KUBEFLOW_IMAGE})
  --image-vllm             <tag>.rootfs.tar from VLLM_IMAGE (default qagredo-vllm:qwen35-localcuda)
  --models-ollama          models_ollama.tar.gz         (from ${OLLAMA_STORE_SRC})
  --models-ollama-split[=TAGS]
                           models_ollama_<tag>.tar.gz per tag (from ${OLLAMA_STORE_SRC})
                           TAGS is optional comma-separated list (default:
                           ${OLLAMA_SPLIT_TAGS_DEFAULT[*]}).
  --models-vllm[=LIST]     models_vllm.tar.gz           (from ${VLLM_MODELS_SRC})
                            LIST is an optional comma-separated list of
                            subdirectories to include (default:
                            ${VLLM_MODEL_DIRS_DEFAULT[*]}).
  --models-vllm-split[=LIST]
                           models_vllm_<dir>.tar.gz per HF folder (same LIST
                           syntax; default ${VLLM_MODEL_DIRS_DEFAULT[*]}).
                           Use when the offline host already has some models.

Other:
  --out PATH               Write outputs to PATH (default ${OUT_DIR})
  -h, --help               Show this message

Environment overrides:
  OLLAMA_STORE_SRC         Source Ollama store (must contain blobs/ + manifests/)
  VLLM_MODELS_SRC          Parent folder holding HuggingFace model subdirs
  QAGREDO_IMAGE            Docker tag for dev-profile image
  QAGREDO_KUBEFLOW_IMAGE   Docker tag for kubeflow-profile image
  VLLM_IMAGE               Docker tag for the vLLM runtime image

Examples:
  # Full rebuild (takes a long time because models are big):
  bash scripts/make_offline_tarballs.sh --all

  # Just the bundle (fastest — rebuild after a code change):
  bash scripts/make_offline_tarballs.sh --bundle

  # Kubeflow-only shipment (single combined Ollama store archive):
  bash scripts/make_offline_tarballs.sh --bundle --image-kubeflow --models-ollama

  # Kubeflow-only shipment (split Ollama archives by model tag):
  bash scripts/make_offline_tarballs.sh --bundle --image-kubeflow --models-ollama-split

  # vLLM-only shipment:
  bash scripts/make_offline_tarballs.sh --bundle --image-dev --image-vllm --models-vllm
USAGE
}

MODELS_VLLM_OVERRIDE=""
MODELS_VLLM_SPLIT_OVERRIDE=""
MODELS_OLLAMA_SPLIT_OVERRIDE=""

_safe_vllm_dir_slug() {
  echo "$1" | tr '.:/' '_' | tr -cd 'A-Za-z0-9._-'
}
while (($#)); do
  case "$1" in
    --all)
      DO_BUNDLE=1; DO_IMAGE_DEV=1; DO_IMAGE_KUBEFLOW=1
      DO_MODELS_OLLAMA=1; DO_MODELS_VLLM=1; DO_IMAGE_VLLM=1
      ;;
    --bundle)           DO_BUNDLE=1 ;;
    --image-dev)        DO_IMAGE_DEV=1 ;;
    --image-kubeflow)   DO_IMAGE_KUBEFLOW=1 ;;
    --image-vllm)       DO_IMAGE_VLLM=1 ;;
    --models-ollama)    DO_MODELS_OLLAMA=1 ;;
    --models-ollama-split) DO_MODELS_OLLAMA_SPLIT=1 ;;
    --models-ollama-split=*) DO_MODELS_OLLAMA_SPLIT=1; MODELS_OLLAMA_SPLIT_OVERRIDE="${1#*=}" ;;
    --models-vllm)      DO_MODELS_VLLM=1 ;;
    --models-vllm=*)    DO_MODELS_VLLM=1; MODELS_VLLM_OVERRIDE="${1#*=}" ;;
    --models-vllm-split) DO_MODELS_VLLM_SPLIT=1 ;;
    --models-vllm-split=*) DO_MODELS_VLLM_SPLIT=1; MODELS_VLLM_SPLIT_OVERRIDE="${1#*=}" ;;
    --out)              shift; OUT_DIR="$1" ;;
    --out=*)            OUT_DIR="${1#*=}" ;;
    -h|--help)          usage; exit 0 ;;
    *) _die "Unknown flag: $1 (run with --help)" ;;
  esac
  shift
done

if [[ $((DO_BUNDLE + DO_IMAGE_DEV + DO_IMAGE_KUBEFLOW + DO_IMAGE_VLLM + DO_MODELS_OLLAMA + DO_MODELS_OLLAMA_SPLIT + DO_MODELS_VLLM + DO_MODELS_VLLM_SPLIT)) -eq 0 ]]; then
  usage
  _die "No build flag given (pick one or use --all)."
fi

mkdir -p "$OUT_DIR"

# ---------------------------------------------------------------------------
# 1) Bundle
# ---------------------------------------------------------------------------
if [[ "$DO_BUNDLE" -eq 1 ]]; then
  _log "Building qagredo_bundle.tar.gz ..."
  ( cd "$REPO_DIR" && QAGREDO_ARCHIVE_DIR="$OUT_DIR" bash scripts/make_qagredo_bundle.sh )
  _log "  -> $OUT_DIR/qagredo_bundle.tar.gz"
fi

# ---------------------------------------------------------------------------
# 2) Docker image tarballs
# ---------------------------------------------------------------------------
_save_image_if_present() {
  local tag="$1" out="$2" label="$3"
  if ! docker image inspect "$tag" >/dev/null 2>&1; then
    _warn "Image not found locally: $tag — skipping $label."
    _warn "  Build it first:"
    case "$label" in
      "dev image")
        _warn "    docker build -t ${tag} -f Dockerfile ${REPO_DIR}"
        ;;
      "kubeflow image")
        _warn "    docker build -t ${tag} -f Dockerfile.kubeflow ${REPO_DIR}"
        ;;
      "vllm image")
        _warn "    docker pull ${tag}"
        ;;
    esac
    return 0
  fi
  _log "Saving ${label}: ${tag} -> $(basename "$out")"
  docker save -o "$out" "$tag"
  _log "  -> $out  ($(du -h "$out" | cut -f1))"
  sha256sum "$out" > "${out}.sha256"
}

if [[ "$DO_IMAGE_DEV" -eq 1 ]]; then
  _save_image_if_present "$QAGREDO_IMAGE" "$OUT_DIR/qagredo-v1.tar" "dev image"
fi

if [[ "$DO_IMAGE_KUBEFLOW" -eq 1 ]]; then
  _save_image_if_present "$QAGREDO_KUBEFLOW_IMAGE" "$OUT_DIR/qagredo-kubeflow.tar" "kubeflow image"
fi

if [[ "$DO_IMAGE_VLLM" -eq 1 ]]; then
  # Normalize to a filename: qagredo-vllm:qwen35-localcuda -> qwen35-localcuda.rootfs.tar
  _vllm_fname="$(echo "${VLLM_IMAGE##*/}" | tr ':' '_').rootfs.tar"
  _save_image_if_present "$VLLM_IMAGE" "$OUT_DIR/$_vllm_fname" "vllm image"
fi

# ---------------------------------------------------------------------------
# 3) Ollama GGUF store (for dev / kubeflow profiles)
# ---------------------------------------------------------------------------
_verify_ollama_store() {
  if [[ ! -d "$OLLAMA_STORE_SRC" ]]; then
    _die "Ollama store not found at OLLAMA_STORE_SRC=${OLLAMA_STORE_SRC}.
Set OLLAMA_STORE_SRC=/path/to/ollama/models and retry, or run:
  ollama list        # to confirm which machine has the tags"
  fi
  if [[ ! -d "$OLLAMA_STORE_SRC/blobs" || ! -d "$OLLAMA_STORE_SRC/manifests" ]]; then
    _die "OLLAMA_STORE_SRC=${OLLAMA_STORE_SRC} does not look like an Ollama store
(missing blobs/ or manifests/). On systemd installs this is usually
/data/ollama/models or /usr/share/ollama/.ollama/models — readable only
by the 'ollama' user, so run this step with sudo or as the ollama user."
  fi
}

_tar_models_dir() {
  local source_models_dir="$1"
  local output_tar="$2"
  if command -v pigz >/dev/null 2>&1; then
    tar --use-compress-program=pigz -cf "$output_tar" \
      -C "$(dirname "$source_models_dir")" \
      "$(basename "$source_models_dir")"
  else
    _warn "pigz not found — falling back to single-threaded gzip (slower)."
    tar -czf "$output_tar" \
      -C "$(dirname "$source_models_dir")" \
      "$(basename "$source_models_dir")"
  fi
}

if [[ "$DO_MODELS_OLLAMA" -eq 1 ]]; then
  _verify_ollama_store
  _out="$OUT_DIR/models_ollama.tar.gz"
  _store_leaf="$(basename "$OLLAMA_STORE_SRC")"
  # Ollama may keep identity keys under the store root; they are often mode 600
  # and owned by root/ollama, so a normal user cannot read them. They are NOT
  # required for offline inference (only blobs/ + manifests/ matter).
  _ollama_excludes=(
    --exclude="${_store_leaf}/id_ed25519"
    --exclude="${_store_leaf}/id_ed25519.pub"
  )
  _log "Creating Ollama store archive from: $OLLAMA_STORE_SRC"
  _log "  (excluding id_ed25519* — identity keys, not needed for air-gapped run)"
  _log "  (this is slow — GGUF blobs are large; use pigz for faster compression)"
  if command -v pigz >/dev/null 2>&1; then
    tar "${_ollama_excludes[@]}" --use-compress-program=pigz -cf "$_out" \
      -C "$(dirname "$OLLAMA_STORE_SRC")" "$_store_leaf"
  else
    _warn "pigz not found — falling back to single-threaded gzip (slower)."
    tar "${_ollama_excludes[@]}" -czf "$_out" \
      -C "$(dirname "$OLLAMA_STORE_SRC")" "$_store_leaf"
  fi
  _log "  -> $_out  ($(du -h "$_out" | cut -f1))"
  sha256sum "$_out" > "${_out}.sha256"
fi

if [[ "$DO_MODELS_OLLAMA_SPLIT" -eq 1 ]]; then
  _verify_ollama_store
  if [[ -n "$MODELS_OLLAMA_SPLIT_OVERRIDE" ]]; then
    IFS=',' read -r -a _ollama_tags <<<"$MODELS_OLLAMA_SPLIT_OVERRIDE"
  else
    _ollama_tags=("${OLLAMA_SPLIT_TAGS_DEFAULT[@]}")
  fi
  _log "Creating split Ollama archives from: $OLLAMA_STORE_SRC"
  _log "  (tags: ${_ollama_tags[*]})"
  for _tag in "${_ollama_tags[@]}"; do
    _safe_tag="$(echo "$_tag" | tr ':/' '__' | tr -cd 'A-Za-z0-9._-')"
    _tmp_dir="$(mktemp -d)"
    _tmp_models="${_tmp_dir}/models"
    mkdir -p "$_tmp_models/blobs" "$_tmp_models/manifests"

    python3 - "$OLLAMA_STORE_SRC" "$_tag" "$_tmp_models" <<'PY'
import json, pathlib, shutil, sys
store = pathlib.Path(sys.argv[1])
tag = sys.argv[2]
out_models = pathlib.Path(sys.argv[3])
if ":" not in tag:
    raise SystemExit(f"Invalid tag '{tag}', expected model:tag")
model, tag_name = tag.rsplit(":", 1)
manifests_root = store / "manifests"
blobs_root = store / "blobs"
matches = []
for p in manifests_root.rglob("*"):
    if p.is_file() and len(p.parts) >= 2:
        if p.parts[-2] == model and p.parts[-1] == tag_name:
            matches.append(p)
if not matches:
    raise SystemExit(f"No manifest found for tag '{tag}' under {manifests_root}")
manifest = matches[0]
target_manifest = out_models / "manifests" / manifest.relative_to(manifests_root)
target_manifest.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(manifest, target_manifest)

raw = manifest.read_text(encoding="utf-8")
data = json.loads(raw)
digests = set()
stack = [data]
while stack:
    cur = stack.pop()
    if isinstance(cur, dict):
        dg = cur.get("digest")
        if isinstance(dg, str) and dg.startswith("sha256:"):
            digests.add(dg.split(":", 1)[1])
        stack.extend(cur.values())
    elif isinstance(cur, list):
        stack.extend(cur)
for h in sorted(digests):
    src = blobs_root / f"sha256-{h}"
    if src.exists():
        dst = out_models / "blobs" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(src, dst)
    else:
        raise SystemExit(f"Missing blob in store for tag {tag}: {src}")
PY

    _out="$OUT_DIR/models_ollama_${_safe_tag}.tar.gz"
    _tar_models_dir "$_tmp_models" "$_out"
    _log "  -> $_out  ($(du -h "$_out" | cut -f1))"
    sha256sum "$_out" > "${_out}.sha256"
    rm -rf "$_tmp_dir"
  done
fi

# ---------------------------------------------------------------------------
# 4) vLLM HuggingFace model dirs (for vllm profile)
# ---------------------------------------------------------------------------
if [[ "$DO_MODELS_VLLM" -eq 1 ]]; then
  if [[ ! -d "$VLLM_MODELS_SRC" ]]; then
    _die "vLLM models source not found: VLLM_MODELS_SRC=${VLLM_MODELS_SRC}"
  fi
  if [[ -n "$MODELS_VLLM_OVERRIDE" ]]; then
    IFS=',' read -r -a _dirs <<<"$MODELS_VLLM_OVERRIDE"
  else
    _dirs=("${VLLM_MODEL_DIRS_DEFAULT[@]}")
  fi
  _missing=()
  for d in "${_dirs[@]}"; do
    [[ -d "$VLLM_MODELS_SRC/$d" ]] || _missing+=("$d")
  done
  if (( ${#_missing[@]} )); then
    _die "Missing vLLM model dirs under ${VLLM_MODELS_SRC}: ${_missing[*]}
Adjust via --models-vllm=dir1,dir2 or VLLM_MODELS_SRC."
  fi
  _out="$OUT_DIR/models_vllm.tar.gz"
  _log "Creating vLLM model archive from: $VLLM_MODELS_SRC"
  _log "  (dirs: ${_dirs[*]})"
  if command -v pigz >/dev/null 2>&1; then
    tar --use-compress-program=pigz -cf "$_out" \
      -C "$VLLM_MODELS_SRC" "${_dirs[@]}"
  else
    _warn "pigz not found — using single-threaded gzip (slower)."
    tar -czf "$_out" -C "$VLLM_MODELS_SRC" "${_dirs[@]}"
  fi
  _log "  -> $_out  ($(du -h "$_out" | cut -f1))"
  sha256sum "$_out" > "${_out}.sha256"
fi

if [[ "$DO_MODELS_VLLM_SPLIT" -eq 1 ]]; then
  if [[ ! -d "$VLLM_MODELS_SRC" ]]; then
    _die "vLLM models source not found: VLLM_MODELS_SRC=${VLLM_MODELS_SRC}"
  fi
  if [[ -n "$MODELS_VLLM_SPLIT_OVERRIDE" ]]; then
    IFS=',' read -r -a _dirs <<<"$MODELS_VLLM_SPLIT_OVERRIDE"
  else
    _dirs=("${VLLM_MODEL_DIRS_DEFAULT[@]}")
  fi
  _log "Creating split vLLM model archives from: $VLLM_MODELS_SRC"
  _log "  (dirs: ${_dirs[*]})"
  for d in "${_dirs[@]}"; do
    [[ -d "$VLLM_MODELS_SRC/$d" ]] || _die "Missing vLLM model dir: $VLLM_MODELS_SRC/$d"
    _slug="$(_safe_vllm_dir_slug "$d")"
    _out="$OUT_DIR/models_vllm_${_slug}.tar.gz"
    _log "  packing $d -> $(basename "$_out")"
    if command -v pigz >/dev/null 2>&1; then
      tar --use-compress-program=pigz -cf "$_out" -C "$VLLM_MODELS_SRC" "$d"
    else
      tar -czf "$_out" -C "$VLLM_MODELS_SRC" "$d"
    fi
    _log "  -> $_out  ($(du -h "$_out" | cut -f1))"
    sha256sum "$_out" > "${_out}.sha256"
  done
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
_log "Done. Outputs:"
ls -lh "$OUT_DIR" | tail -n +2
echo ""
_log "Next steps:"
echo "  1. rsync / scp the files you need from $OUT_DIR/ to the offline server."
echo "  2. On the offline server, extract the bundle: tar xzf qagredo_bundle.tar.gz"
echo "  3. cd qagredo_host/ && bash setup_offline.sh"
echo "  4. Edit .env, then: bash run.sh"
echo ""
echo "Full guide: docs/OFFLINE_SETUP_GUIDE.md"
