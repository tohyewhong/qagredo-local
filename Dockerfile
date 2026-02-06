# This Dockerfile is the default build entrypoint.
# It mirrors the contents of Dockerfile.airgap for convenience.
#
# If you previously built with:
#   docker build -f Dockerfile.airgap ...
# you can now simply run:
#   docker build ...
#
# (We still keep Dockerfile.airgap for backward compatibility.)

# Boss-proposed pattern:
# - Jupyter base image (already includes user `jovyan`)
# - Install OS deps as root
# - Copy code + requirements
# - (Optional) Install corporate CA bundle and set pip to use it
# - Install Python requirements
#
# Notes for this repo:
# - Docker Compose overrides the command to run the CLI pipeline.
# - We keep offline/cache env vars because the runner is designed for airgapped use.
FROM jupyter/base-notebook:python-3.10

# change to install stuff
USER root

# Synchronize container runtime user/group with host (so mounted folders are writable).
# docker-compose.offline.yml passes these build args.
ARG UID=1001
ARG GID=1001
ARG USERNAME=qagredo

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    ca-certificates \
    sudo \
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
    TRANSFORMERS_CACHE=/opt/hf_cache/hub \
    HUGGINGFACE_HUB_CACHE=/opt/hf_cache/hub \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf_cache/sentence-transformers \
    SENTENCE_TRANSFORMERS_MODEL_PATH=/opt/models_embed/all-MiniLM-L6-v2

# copy the files (not optimised but did not want to break anything)
WORKDIR /workspace/

COPY certbundle /workspace/certbundle
COPY requirements.txt .
COPY run_qa_pipeline.py .
COPY utils ./utils
COPY scripts ./scripts
COPY config ./config
COPY README.md .
COPY QUICKSTART_OFFLINE.md .

# Optional corporate CA bundle
#
# If your network MITMs TLS (common in enterprises), `pip install` may fail with:
#   SSLCertVerificationError: unable to get local issuer certificate
#
# Put your corporate root CA at:
#   certbundle/certbundle.crt
#
# (The repo includes `certbundle/README.md` with details.)
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

# Run pip as root so it doesn't depend on /opt/conda write permissions.

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
    pip install ${trusted_args} --no-cache-dir -r requirements.txt

# Bake `all-MiniLM-L6-v2` into the image at build time (DEFAULT).
# This requires internet access on the build machine.
#
# If you ever need to skip this (faster build, or no internet), build with:
#   docker build --build-arg BAKE_MINILM=0 -t qagredo-v1:latest .
ARG BAKE_MINILM=1
RUN if [ "$BAKE_MINILM" = "1" ]; then \
      python -c "from pathlib import Path; from sentence_transformers import SentenceTransformer; dst=Path('/opt/models_embed/all-MiniLM-L6-v2'); dst.parent.mkdir(parents=True, exist_ok=True); model=SentenceTransformer('all-MiniLM-L6-v2', device='cpu'); model.save(str(dst)); print(f'[OK] Saved all-MiniLM-L6-v2 to: {dst}')" \
    ; else \
      echo "[INFO] Skipping BAKE_MINILM=0 (will rely on mounting host/models_embed at runtime)"; \
    fi

EXPOSE 8888

# start jupyter lab (compose overrides for CLI runs)
USER ${USERNAME}
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--ServerApp.token=", "--ServerApp.password="]

