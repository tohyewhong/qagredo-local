#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# verify_offline_deployment.sh  --  Confirm the offline image matches the
#                                    repo's requirements.txt
# ============================================================================
#
# Runs `pip check` and import smoke tests inside whichever Docker image is
# appropriate for the configured profile.
#
# Run from qag_host/ AFTER:
#   - bash setup_offline.sh
#   - docker images show qag-v1:latest (or qag-kubeflow:latest)
#
# Exits non-zero if the image is missing packages or has broken deps.
#
# Usage:
#   bash verify_offline_deployment.sh
#   bash verify_offline_deployment.sh --profile kubeflow
# ============================================================================

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUESTED_PROFILE=""

while (($#)); do
  case "$1" in
    --profile)   shift; REQUESTED_PROFILE="${1:-}" ;;
    --profile=*) REQUESTED_PROFILE="${1#*=}" ;;
    -h|--help)
      echo "Usage: bash verify_offline_deployment.sh [--profile ollama|kubeflow|vllm]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

[[ -f "$HOST_DIR/.env" ]] && { set -a; source "$HOST_DIR/.env"; set +a; }
PROFILE="${REQUESTED_PROFILE:-${QAG_PROFILE:-ollama}}"
case "$PROFILE" in
  dev)
    echo "[WARN] QAG_PROFILE=dev is the old name for ollama; using ollama." >&2
    echo "[WARN] Update .env: QAG_PROFILE=ollama" >&2
    PROFILE="ollama"
    ;;
  ollama|kubeflow|vllm) ;;
  *) echo "[ERROR] Unknown profile: $PROFILE" >&2; exit 2 ;;
esac

case "$PROFILE" in
  kubeflow) COMPOSE_FILE="$HOST_DIR/docker-compose.kubeflow.yml" ;;
  vllm)     COMPOSE_FILE="$HOST_DIR/docker-compose.vllm-stack.yml" ;;
  ollama|*) COMPOSE_FILE="$HOST_DIR/docker-compose.yml" ;;
esac

[[ -f "$COMPOSE_FILE" ]] || {
  echo "[ERROR] Missing $COMPOSE_FILE" >&2
  exit 2
}

_current_uid="$(id -u)"
_current_gid="$(id -g)"
export HOST_UID="${HOST_UID:-${_current_uid}}"
export HOST_GID="${HOST_GID:-${_current_gid}}"
if [[ "${HOST_UID}" != "${_current_uid}" || "${HOST_GID}" != "${_current_gid}" ]]; then
  if [[ "${QAG_ALLOW_FOREIGN_OWNERSHIP:-0}" != "1" ]]; then
    echo "[WARN] HOST_UID/HOST_GID (${HOST_UID}:${HOST_GID}) do not match current user (${_current_uid}:${_current_gid}). Auto-correcting to current user."
    echo "[WARN] To keep foreign ownership mapping, set QAG_ALLOW_FOREIGN_OWNERSHIP=1."
    export HOST_UID="${_current_uid}"
    export HOST_GID="${_current_gid}"
  fi
fi

echo "[INFO] Profile      : $PROFILE"
echo "[INFO] Compose file : $COMPOSE_FILE"
echo "[INFO] Verifying requirements.txt inside the runner image ..."

docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T qag \
  python /workspace/scripts/docker_verify_requirements.py /workspace/requirements.txt

echo "[OK] Image matches requirements.txt and pip check passed."
