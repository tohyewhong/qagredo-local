#!/bin/bash
set -e

# =============================================================================
# docker-entrypoint.sh — make files on your PC belong to you, not root
# =============================================================================
# Docker often creates files as root on shared folders. This script switches
# the in-container user to your Linux UID/GID (from HOST_UID / HOST_GID in
# run.sh / compose), runs the real command as that user, then on exit fixes
# ownership again so you can open or delete outputs on the host.
#
# We avoid `exec gosu` so an EXIT trap can run the final chown.
# =============================================================================

TARGET_UID="${HOST_UID:-1013}"
TARGET_GID="${HOST_GID:-1015}"
USERNAME="qag"

# Must match folders mounted in docker-compose.yml.
# /workspace/data = your input folder (DATA_DIR from .env / run.sh).
WRITABLE_DIRS=(
    /workspace/output
    /workspace/config
    /workspace/data
    /opt/hf_cache
)

# ---------------------------------------------------------------------------
#  Optional: serve Ollama inside the container (Kubeflow / K2-B pattern).
#
#  Enabled when QAG_SERVE_OLLAMA=1. Models are read from $OLLAMA_MODELS
#  (default /opt/ollama/models) — typically a host volume mount such as
#  /home/jovyan/models on Kubeflow, or ./models on the dev server.
# ---------------------------------------------------------------------------
OLLAMA_PID=""

start_inproc_ollama() {
    local host_port bind_addr
    bind_addr="${OLLAMA_HOST:-127.0.0.1:11434}"
    host_port="${bind_addr##*:}"

    mkdir -p "${OLLAMA_MODELS:-/opt/ollama/models}" 2>/dev/null || true
    chown -R "$TARGET_UID:$TARGET_GID" "${OLLAMA_MODELS:-/opt/ollama/models}" 2>/dev/null || true

    if ! command -v ollama >/dev/null 2>&1; then
        echo "[entrypoint][ERROR] QAG_SERVE_OLLAMA=1 but 'ollama' is not installed in the image." >&2
        echo "[entrypoint][ERROR] Rebuild with Dockerfile.kubeflow or mount the ollama binary." >&2
        exit 2
    fi

    echo "[entrypoint] Starting in-container ollama serve on ${bind_addr} (models: ${OLLAMA_MODELS:-/opt/ollama/models})"
    OLLAMA_HOST="${bind_addr}" OLLAMA_MODELS="${OLLAMA_MODELS:-/opt/ollama/models}" \
        gosu "$USERNAME" ollama serve >/workspace/output/ollama.log 2>&1 &
    OLLAMA_PID=$!

    local elapsed=0 timeout="${QAG_OLLAMA_WAIT_TIMEOUT:-180}"
    while true; do
        if curl -sf "http://${bind_addr}/api/tags" >/dev/null 2>&1; then
            echo "[entrypoint] Ollama is ready after ${elapsed}s"
            break
        fi
        if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
            echo "[entrypoint][ERROR] ollama serve exited early; see /workspace/output/ollama.log" >&2
            exit 3
        fi
        if [ "$elapsed" -ge "$timeout" ]; then
            echo "[entrypoint][ERROR] Ollama did not become ready within ${timeout}s" >&2
            exit 4
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    for model in "${OLLAMA_MODEL:-}" "${OLLAMA_JUDGE_MODEL:-}"; do
        [ -z "$model" ] && continue
        if ! curl -sf "http://${bind_addr}/api/tags" | grep -q "\"name\":\"${model}\""; then
            echo "[entrypoint][WARN] Model '${model}' not reported by /api/tags (store: ${OLLAMA_MODELS:-/opt/ollama/models})."
            echo "[entrypoint][WARN] Seed blobs/manifests for this tag, or set OLLAMA_MODEL / OLLAMA_JUDGE_MODEL to match tags from your offline tar / ollama list."
        fi
    done
}

stop_inproc_ollama() {
    if [ -n "$OLLAMA_PID" ] && kill -0 "$OLLAMA_PID" 2>/dev/null; then
        kill "$OLLAMA_PID" 2>/dev/null || true
        wait "$OLLAMA_PID" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
#  fix_ownership — chown all writable dirs to the host user
# ---------------------------------------------------------------------------
fix_ownership() {
    for dir in "${WRITABLE_DIRS[@]}"; do
        if [ -d "$dir" ]; then
            chown -R "$TARGET_UID:$TARGET_GID" "$dir" 2>/dev/null || true
        fi
    done
}

# ---------------------------------------------------------------------------
#  Forward signals to the child process so Ctrl+C works properly
# ---------------------------------------------------------------------------
CHILD_PID=""
forward_signal() {
    if [ -n "$CHILD_PID" ]; then
        kill -"$1" "$CHILD_PID" 2>/dev/null || true
    fi
}

# ---------- Running as root: adjust user and drop privileges ---------
if [ "$(id -u)" = "0" ]; then

    # --- Adjust group ---
    if getent group "$USERNAME" >/dev/null 2>&1; then
        CUR_GID=$(getent group "$USERNAME" | cut -d: -f3)
        if [ "$CUR_GID" != "$TARGET_GID" ]; then
            groupmod -g "$TARGET_GID" "$USERNAME" 2>/dev/null || true
        fi
    else
        groupadd -g "$TARGET_GID" "$USERNAME" 2>/dev/null || true
    fi

    # --- Adjust user ---
    if id "$USERNAME" >/dev/null 2>&1; then
        CUR_UID=$(id -u "$USERNAME")
        if [ "$CUR_UID" != "$TARGET_UID" ]; then
            usermod -u "$TARGET_UID" -g "$TARGET_GID" -d "/home/$USERNAME" "$USERNAME" 2>/dev/null || true
        fi
    else
        useradd -m -s /bin/bash -u "$TARGET_UID" -g "$TARGET_GID" "$USERNAME" 2>/dev/null || true
    fi

    # --- Ensure home directory exists ---
    mkdir -p "/home/$USERNAME"
    chown "$TARGET_UID:$TARGET_GID" "/home/$USERNAME"

    # --- Ensure writable directories exist and are owned by the host user ---
    fix_ownership

    # --- On EXIT: re-chown everything so the host user can always clean up ---
    # This catches files created DURING the run (e.g. new output, hf_cache files).
    trap 'stop_inproc_ollama; fix_ownership' EXIT

    # --- Forward SIGINT and SIGTERM to the child process ---
    trap 'forward_signal TERM' TERM
    trap 'forward_signal INT'  INT

    # --- Optional: serve Ollama inside the container (Kubeflow profile) ---
    if [ "${QAG_SERVE_OLLAMA:-0}" = "1" ]; then
        start_inproc_ollama
    fi

    # --- Drop privileges and run the command ---
    # NOTE: We intentionally do NOT use `exec gosu ...` here.
    # `exec` would replace bash, which would prevent the EXIT trap from firing.
    # Instead, gosu runs as a child process. Bash waits, then runs the trap.
    export HOME="/home/$USERNAME"
    gosu "$USERNAME" "$@" &
    CHILD_PID=$!
    wait "$CHILD_PID" 2>/dev/null
    EXIT_CODE=$?
    # EXIT trap fires here (fix_ownership runs automatically)
    exit "$EXIT_CODE"

# ---------- Already non-root: just run the command ----------
else
    exec "$@"
fi
