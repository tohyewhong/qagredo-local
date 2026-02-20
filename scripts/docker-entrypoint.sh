#!/bin/bash
set -e

# =============================================================================
# docker-entrypoint.sh
# =============================================================================
# Proper UID/GID mapping for Docker containers.
#
# Problem:  Docker containers run as root by default.  Files created in
#           bind-mounted volumes (output/, hf_cache/, config/, data/) are
#           owned by root, making them unreadable/undeletable by the host user.
#
# Solution: This entrypoint runs as root, adjusts the container user's
#           UID/GID to match the host user, ensures all writable directories
#           are owned correctly, then drops privileges with `gosu` to run
#           the actual command as that user.
#
#           On EXIT, it re-chowns everything so the host user always owns
#           all files — even those created during the run.
#
# IMPORTANT: We do NOT use `exec gosu ...` because exec replaces the bash
#            process and the EXIT trap would never fire.  Instead, gosu runs
#            as a child process; bash waits for it and then runs the trap.
#
# Environment variables (set by run.sh / docker-compose):
#   HOST_UID   — host user's UID  (default: 1000)
#   HOST_GID   — host user's GID  (default: 1000)
#
# This script is set as the ENTRYPOINT in the Dockerfile.
# The CMD (e.g. python pipeline, jupyter lab) is passed as "$@".
# =============================================================================

TARGET_UID="${HOST_UID:-1000}"
TARGET_GID="${HOST_GID:-1000}"
USERNAME="qagredo"

# All bind-mounted writable directories.
# These MUST match the volume mounts in docker-compose.offline.yml and jupyter.sh.
WRITABLE_DIRS=(
    /workspace/output
    /workspace/config
    /workspace/data
    /workspace/.jupyter
    /opt/hf_cache
)

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
    trap fix_ownership EXIT

    # --- Forward SIGINT and SIGTERM to the child process ---
    trap 'forward_signal TERM' TERM
    trap 'forward_signal INT'  INT

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
