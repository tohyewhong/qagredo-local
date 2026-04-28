# CLI-first image:
# - Official Python 3.10 (Debian bookworm) base
# - Install OS deps as root
# - Copy code + requirements
# - (Optional) Install corporate CA bundle and set pip to use it
# - Install Python requirements
#
# Notes for this repo:
# - Default CMD runs the pipeline; Docker Compose can override the command.
# - We keep offline/cache env vars because the runner is designed for airgapped use.
FROM python:3.10-bookworm

USER root

# Synchronize container runtime user/group with host (so mounted folders are writable).
# docker-compose.yml passes these build args.
ARG UID=10005
ARG GID=10006
ARG USERNAME=qagredo

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    ca-certificates \
    antiword \
    sudo \
    gosu \
    libffi-dev \
    libssl-dev \
    tmux \
    vim \
    && rm -rf /var/lib/apt/lists/*

# Offline + HF cache defaults (compose can override)
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYDANTIC_DISABLE_PLUGIN_LOADING=1 \
    OFFLINE_MODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/opt/hf_cache \
    HUGGINGFACE_HUB_CACHE=/opt/hf_cache/hub

WORKDIR /workspace/

COPY certbundle /workspace/certbundle
COPY requirements.txt .
COPY run_qa_pipeline.py .
COPY utils ./utils
COPY scripts ./scripts
COPY config ./config
COPY docs ./docs
COPY README.md .
COPY docs/OFFLINE_SETUP_GUIDE.md ./OFFLINE_SETUP_GUIDE.md

# Optional corporate CA bundle
#
# If your network MITMs TLS (common in enterprises), `pip install` may fail with:
#   SSLCertVerificationError: unable to get local issuer certificate
#
# Put your corporate root CA at:
#   certbundle/certbundle.crt
#
# (See `docs/certbundle/README.md` for details.)
RUN if [ -f /workspace/certbundle/certbundle.crt ]; then \
      echo "[INFO] Installing custom CA from /workspace/certbundle/certbundle.crt"; \
      mkdir -p /usr/local/share/ca-certificates; \
      cp /workspace/certbundle/certbundle.crt /usr/local/share/ca-certificates/qagredo-custom.crt; \
      update-ca-certificates; \
      pip config set global.cert /etc/ssl/certs/ca-certificates.crt; \
    else \
      echo "[INFO] No certbundle/certbundle.crt provided; using default system CAs"; \
    fi

# ensure runtime mountpoints exist
RUN mkdir -p /opt/hf_cache /opt/models_embed

# Create or update the runtime user to match the requested UID/GID, then set ownership.
RUN set -eux; \
    if ! getent group "${GID}" >/dev/null; then groupadd -g "${GID}" "${USERNAME}"; fi; \
    if id -u "${USERNAME}" >/dev/null 2>&1; then \
      usermod -u "${UID}" -g "${GID}" "${USERNAME}"; \
    else \
      useradd -m -s /bin/bash -u "${UID}" -g "${GID}" "${USERNAME}"; \
    fi; \
    mkdir -p "/home/${USERNAME}" /workspace /opt/hf_cache /opt/models_embed; \
    chown -R "${UID}:${GID}" "/home/${USERNAME}" /workspace /opt/hf_cache /opt/models_embed

# install the requirements
#
# If your network blocks TLS inspection fixes, but you cannot install a corporate CA,
# you can build with trusted-host overrides (less secure):
#   docker build --build-arg PIP_TRUSTED_HOSTS="pypi.org files.pythonhosted.org" -t qagredo-v1:latest .
ARG PIP_TRUSTED_HOSTS=
RUN set -eux; \
    trusted_args=""; \
    if [ -n "${PIP_TRUSTED_HOSTS}" ]; then \
      for h in ${PIP_TRUSTED_HOSTS}; do trusted_args="${trusted_args} --trusted-host ${h}"; done; \
    fi; \
    python -m pip install ${trusted_args} --no-cache-dir -r requirements.txt

# Fail the build if any requirement is missing, wrong version, or pip check fails.
RUN python /workspace/scripts/docker_verify_requirements.py /workspace/requirements.txt

# Copy the entrypoint script that adjusts UID/GID at runtime.
# This is the proper Docker pattern: the container starts as root,
# the entrypoint matches the container user to the host user's UID/GID,
# then drops privileges with gosu.  All files created inside bind-mounted
# volumes (output/, hf_cache/) are owned by the host user.
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV HOME=/home/${USERNAME}

# The entrypoint runs as root, adjusts UID/GID, then drops to ${USERNAME}.
# CMD is passed as arguments to the entrypoint.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "/workspace/run_qa_pipeline.py"]
