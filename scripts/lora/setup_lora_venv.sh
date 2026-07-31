#!/usr/bin/env bash
set -euo pipefail

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${QAG_LORA_VENV:-$HOST_DIR/.venv-lora}"
# cu124 wheels run on NVIDIA drivers reporting CUDA 12.9 or 13.0.
TORCH_INDEX="${QAG_TORCH_CUDA_INDEX:-https://download.pytorch.org/whl/cu124}"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[lora] Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python3 -m pip install --upgrade pip wheel
echo "[lora] Installing torch from ${TORCH_INDEX}"
python3 -m pip install "torch>=2.5.0" --index-url "$TORCH_INDEX"
python3 -m pip install -r "$HOST_DIR/scripts/lora/requirements-lora.txt"

python3 - <<'PY'
import jinja2
import torch

print(f"[lora] torch={torch.__version__} cuda={torch.version.cuda}")
print(f"[lora] jinja2={jinja2.__version__}")
if not torch.version.cuda or not torch.version.cuda.startswith("12."):
    raise SystemExit(
        "[lora][ERROR] Expected a CUDA 12.x torch build (cu124). "
        f"Got cuda={torch.version.cuda!r}."
    )
if tuple(map(int, jinja2.__version__.split(".")[:2])) < (3, 1):
    raise SystemExit(
        f"[lora][ERROR] jinja2>=3.1.0 required; got {jinja2.__version__}."
    )
PY

echo "[lora] Ready: source $VENV_DIR/bin/activate"
