# QUICKSTART (Offline server, 2× NVIDIA GPU)

This is the **simple layman guide** to run QAGRedo on **one offline server** (no Kubeflow).

You will run **two containers**:
- **A: vLLM** (GPU) → serves the LLM API at `http://localhost:8100/v1`
- **B: QAGRedo** (CPU OK) → runs `python run_qa_pipeline.py` and calls vLLM

## What you need
- **On the OFFLINE server**:
  - Docker + Docker Compose plugin
  - NVIDIA driver + NVIDIA Container Toolkit (so `--gpus all` works)
  - Your LLM model folders available on disk (Qwen and/or Llama)
- **On the ONLINE machine** (internet):
  - Docker + Docker Compose plugin (to build/pull and export images)

## The “host folder” (important)
The “host folder” is just **a folder on your server** where you keep your files.

Example (recommended):
- `~/qagredo_host/`

Inside it, create these folders:
- `config/` (your single `config.yaml`)
- `data/` (your input json/jsonl)
- `output/` (results go here)
- `models_llm/` (your LLM model folders for vLLM)
- `models_embed/` (embedding models for sentence-transformers; e.g. `all-MiniLM-L6-v2/`)
- `hf_cache/` (HF cache; used as fallback for semantic checks offline)

## Step 1 (ONLINE machine): build/pull images
From the QAGRedo repo folder:

```bash
docker compose -f docker-compose.offline.yml build qagredo
docker pull vllm/vllm-openai:v0.5.3.post1
```

Note: this build can be **large** (it may download multiple GB of Python wheels).

## Step 2 (ONLINE machine): create MiniLM cache folder (required)
This produces a portable cache folder you will copy to the offline server.

```bash
mkdir -p hf_cache

docker run --rm \
  -v "$PWD/hf_cache:/opt/hf_cache" \
  -e HF_HOME=/opt/hf_cache \
  -e HUGGINGFACE_HUB_CACHE=/opt/hf_cache/hub \
  -e TRANSFORMERS_CACHE=/opt/hf_cache/hub \
  -e SENTENCE_TRANSFORMERS_HOME=/opt/hf_cache/sentence-transformers \
  python:3.10-slim \
  bash -lc "pip install -U pip sentence-transformers && python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2'); print('OK: MiniLM cached')\""
```

## Step 3 (ONLINE machine): export images for transfer
Create tar files you can copy to the offline server (USB / SCP via jump host / etc).

```bash
docker save -o qagredo-v1.tar qagredo-v1:latest
docker save -o vllm-openai_v0.5.3.post1.tar vllm/vllm-openai:v0.5.3.post1
```

### Optional: create a single `offline_payload_*.tar.zst` + checksum
If you prefer **one file** to transfer (instead of many folders/files), run:

```bash
cd /path/to/qagredo
export QAGREDO_HOST_DIR=~/qagredo_host   # the host folder you will run with offline
bash scripts/offline/make_offline_payload.sh
```

This produces:
- `offline_payload_YYYYMMDD_HHMMSS.tar.zst`
- `offline_payload_YYYYMMDD_HHMMSS.tar.zst.sha256`

### If `docker save` for vLLM is stuck (no-admin fallback)
On some machines, `docker save vllm/vllm-openai:v0.5.3.post1` can hang and produce a 0-byte tar.
If that happens and you **cannot restart Docker (no admin rights)**, use `docker export` instead:

```bash
docker rm -f vllm-export-tmp 2>/dev/null || true
docker create --name vllm-export-tmp vllm/vllm-openai:v0.5.3.post1
docker export -o vllm-openai_v0.5.3.post1.rootfs.tar vllm-export-tmp
docker rm -f vllm-export-tmp
```

This `*.rootfs.tar` will be imported on the offline server (see Step 5).

## Step 4 (OFFLINE server): prepare host folder
On the offline server:

```bash
mkdir -p ~/qagredo_host/{config,data,output,models_llm,models_embed,hf_cache}
```

Copy these into the offline server:
- `qagredo-v1.tar` and either:
  - `vllm-openai_v0.5.3.post1.tar` (preferred, from `docker save`), OR
  - `vllm-openai_v0.5.3.post1.rootfs.tar` (fallback, from `docker export`)
- MiniLM (preferred): copy your embedding model folder into `~/qagredo_host/models_embed/all-MiniLM-L6-v2/`
  - If you don’t have the direct folder, the HF cache method still works:
    - copy your ONLINE `hf_cache/` → OFFLINE `~/qagredo_host/hf_cache/`
- LLM model folders:
  - Example: copy `Meta-Llama-3.1-8B-Instruct/` into `~/qagredo_host/models_llm/`
  - Example: copy `Qwen2.5-7B-Instruct/` into `~/qagredo_host/models_llm/`
- Config files into `~/qagredo_host/config/`:
  - `config.yaml`
- Input data into `~/qagredo_host/data/` (example: `dev-data.jsonl`)
 
### API key + base URL (important)
vLLM requires an API key string, and QAGRedo must send the same key.
In this repo, **docker-compose** exports the correct settings to the QAGRedo container:
- `VLLM_BASE_URL=http://vllm:8100/v1`
- `VLLM_API_KEY=...` (must match what vLLM was started with)

## Step 5 (OFFLINE server): load images
```bash
docker load -i qagredo-v1.tar

# Preferred (if you exported vLLM with docker save):
docker load -i vllm-openai_v0.5.3.post1.tar
```

### Step 5b (OFFLINE server): import vLLM if you used the rootfs export
If you exported `vllm-openai_v0.5.3.post1.rootfs.tar` (fallback method), import it like this:

```bash
docker import \
  --change 'WORKDIR /vllm-workspace' \
  --change 'ENTRYPOINT ["python3","-m","vllm.entrypoints.openai.api_server"]' \
  vllm-openai_v0.5.3.post1.rootfs.tar \
  vllm/vllm-openai:v0.5.3.post1
```

### Optional: helper script to load/import images
If you copy the tar files next to this repo on the offline server, you can run:

```bash
bash scripts/offline/load_images_offline.sh
```

## Step 6 (OFFLINE server): start vLLM (container A)
From the QAGRedo repo folder (or anywhere you have `docker-compose.offline.yml`):

```bash
export QAGREDO_HOST_DIR=~/qagredo_host

# Llama example:
export VLLM_MODEL=/models/Meta-Llama-3.1-8B-Instruct
export VLLM_API_KEY=llama-local

# If you want Qwen instead, use:
# export VLLM_MODEL=/models/Qwen2.5-7B-Instruct
# export VLLM_API_KEY=qwen14b-local

docker compose -f docker-compose.offline.yml up -d vllm
```

Test vLLM is up:

```bash
curl -H "Authorization: Bearer ${VLLM_API_KEY}" http://localhost:8100/v1/models
```

## Step 7 (OFFLINE server): run QAGRedo (container B)
```bash
export QAGREDO_HOST_DIR=~/qagredo_host
docker compose -f docker-compose.offline.yml run --rm qagredo
```

## Optional: update code offline (no rebuild) using code mounts
If you need to change code on the offline server but cannot rebuild images, use the provided compose overlay:

```bash
export QAGREDO_HOST_DIR=~/qagredo_host
docker compose -f docker-compose.offline.yml -f docker-compose.offline.mount-code.yml run --rm qagredo
```

## Optional: verify MiniLM works offline inside the qagredo container
```bash
export QAGREDO_HOST_DIR=~/qagredo_host
docker compose -f docker-compose.offline.yml run --rm qagredo \
  bash -lc "python -c \"import os; os.environ['OFFLINE_MODE']='1'; from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2', device='cpu'); print('OK: MiniLM loads offline')\""
```

