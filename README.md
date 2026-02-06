# QAGRedo (Layman Guide — run online + transfer offline)

This README is written for **your exact folder layout** on this server:

- **Repo (code)**: `/home/tyewhong/qagredo/`
- **Host data/models/cache/output**: `/home/tyewhong/qagredo_host/`
- **Offline bundle folder** (transfer files): `/home/tyewhong/qagredo_offline_bundle/`

## Purpose (what you are doing)

QAGRedo reads your documents (JSON/JSONL), generates **questions + answers** using an LLM served by **vLLM**, then checks whether answers are grounded (using **MiniLM**).\
You run **two containers**:

- **vLLM**: provides an OpenAI-style API on port **8100**
- **QAGRedo**: runs the pipeline and calls vLLM

## Glossary (so you don’t get lost later)

- **Host**: your normal shell prompt (example: `tyewhong@server1:~$`). Run `docker ...` here.
- **Container**: prompt looks like `jovyan@...:/workspace$`. Do **not** run `docker` commands inside.
- **Repo folder**: `/home/tyewhong/qagredo/` (code + compose file + scripts)
- **Host folder**: `/home/tyewhong/qagredo_host/` (your config/data/models/cache/output on disk)
- **Offline bundle folder**: `/home/tyewhong/qagredo_offline_bundle/` (the archives you copy by USB)

## Part A — Run on this server (online machine)

### A1) One command (recommended)

Purpose: the easiest way to run end-to-end without copy/pasting many commands.

Prereqs (this is what the script expects on **this server**):

- **Docker** installed, and `docker compose` works.
- **QAGRedo image exists**: `qagredo-v1:latest` (build or load it first).
- **Model zip exists**: `/home/tyewhong/Meta-Llama-3.1-8B-Instruct_hf_cache.zip`
- **Sample input exists**: `/home/tyewhong/Llama328BInstruct/dev-data.jsonl`

```bash
cd /home/tyewhong/qagredo
bash scripts/run_layman.sh            # safe mode (no overwrite)
# bash scripts/run_layman.sh --overwrite
```

#### If you changed `num_documents` (or other run settings)

Purpose: avoid long re-copying/unzipping.

QAGRedo reads the **host** config here:

- `/home/tyewhong/qagredo_host/config/config.yaml`

So for changes like `run.num_documents: 2`, edit:

- `/home/tyewhong/qagredo_host/config/config.yaml`

Then just run:

```bash
cd /home/tyewhong/qagredo
bash scripts/run_layman.sh
```

#### About `--overwrite` (why it can take very long)

Purpose: understand when you should (and should not) use it.

`--overwrite` forces the script to refresh everything into `/home/tyewhong/qagredo_host/`. This can take a long time because it may:

- Re-extract the large model zip into `qagredo_host/hf_cache/`
- Re-copy large model files into `qagredo_host/models_llm/`

Only use `--overwrite` if you really need to rebuild/refresh the host folder from scratch (for example, after changing model files).

If you want the script to overwrite/update existing files (still non-interactive):

```bash
cd /home/tyewhong/qagredo
bash scripts/run_layman.sh --overwrite
```

### A2) Where is my output?

Purpose: confirm it worked and find the result files.

```bash
find /home/tyewhong/qagredo_host/output -name '*.json' | tail -n 5
```

### A3) What `run_layman.sh` does (so you know it’s safe)

Purpose: understand what will be created/modified.

`scripts/run_layman.sh` will:

- Create `/home/tyewhong/qagredo_host/{config,data,output,models_llm,models_embed,hf_cache}`
- Copy `config/config.yaml` into `qagredo_host/config/`
- Copy sample input data into `qagredo_host/data/dev-data.jsonl`
- (If needed) extract `/home/tyewhong/Meta-Llama-3.1-8B-Instruct_hf_cache.zip` and build a real model folder under `qagredo_host/models_llm/Meta-Llama-3.1-8B-Instruct/`
- Set up MiniLM (via `models_embed/` if present, otherwise HF cache) so semantic grading works offline
- Start vLLM (GPU) on port **8100**, wait for `/health`, then run QAGRedo

## Convert your files into QAGRedo input (pdf/txt/xlsx/json/jsonl → JSONL)

QAGRedo reads **JSONL** (one JSON object per line).

Note: this repo no longer bundles the old `scripts/conversion/*` converter + wheelhouse workflow. Prepare your input as JSONL yourself (or using your own internal tooling), then point QAGRedo to it.

### 1) Install dependencies (one-time)

```bash
cd /home/tyewhong/qagredo
/home/tyewhong/qagredo/.venv/bin/pip install -r requirements.txt
```

### 2) Prepare your JSONL file

```bash
# Put your JSONL here (host path), e.g.
/home/tyewhong/qagredo_host/data/your_file.jsonl
```

Each line should be a single JSON object. Minimum useful fields:
- `id` (string)
- `text` (string) or `content` (string)

Output JSONL format (per line):
- `id`, `title`, `content`, `text`, `source`, `type`, optional `metadata`

### 3) Point QAGRedo to the converted JSONL

Edit `config/config.yaml` and set:
- `run.input_file: data/your_file.jsonl`

### A4) Run WITHOUT Docker (host-only, advanced)

Purpose: run `run_qa_pipeline.py` directly on the Linux host (no containers).

Important notes:

- You still need an LLM server. In host-only mode we run **vLLM on the host** (still on port `8100`).
- Use the repo Python environment (`/home/tyewhong/qagredo/.venv/`). System `python3` may miss packages.

#### A4.1) Start (or verify) vLLM on the host

```bash
curl -i http://localhost:8100/health
```

If you do **not** get `HTTP/1.1 200 OK`, start vLLM:

```bash
cd /home/tyewhong/qagredo
bash scripts/start_llama_vllm.sh
```

#### A4.2) Ensure QAGRedo points to `localhost` (host mode)

When running on the host, the vLLM base URL must be:

- `http://localhost:8100/v1`

Check this file:

- `/home/tyewhong/qagredo/config/config.yaml` (see `llm.base_url`)

#### A4.3) Run the pipeline (host mode)

```bash
cd /home/tyewhong/qagredo
/home/tyewhong/qagredo/.venv/bin/python run_qa_pipeline.py --config /home/tyewhong/qagredo/config/config.yaml
```

If you ever see:

- `OpenAI library not installed. Install with: pip install openai`

It means you are running with the wrong Python (usually system `python3`). Use the `.venv` python above, or install dependencies into the environment:

```bash
cd /home/tyewhong/qagredo
/home/tyewhong/qagredo/.venv/bin/pip install -r requirements.txt
```

#### A4.4) Where is host-mode output?

In host mode, outputs default to the repo output folder:

- `/home/tyewhong/qagredo/output/`

```bash
find /home/tyewhong/qagredo/output -name '*.json' | tail -n 5
```

## Part B — Create the offline transfer bundle (repeatable)

### B1) Why you need this

Purpose: the repo folder `/home/tyewhong/qagredo/` can be huge because of `.venv/`, but the offline server does **not** need it (Docker runs the app).\
This step creates **small archives** that contain only what the offline server needs.

### B2) Create the offline payload (`.tar.zst` + `.sha256`)

Purpose: create a **single file** you can copy by USB/SCp, plus a checksum file.

```bash
cd /home/tyewhong/qagredo
bash scripts/offline/make_offline_payload.sh
```

This writes into the repo folder by default.

Outputs (example):

 - `offline_payload_YYYYMMDD_HHMMSS.tar.zst`
 - `offline_payload_YYYYMMDD_HHMMSS.tar.zst.sha256`

By default the payload includes:

- A minimal copy of the repo files needed to run offline (compose + docs + helper scripts)
- Docker images exported as tar files (`qagredo-v1:latest` and `vllm/vllm-openai:v0.5.3.post1`)
- Your host folder (`$QAGREDO_HOST_DIR`) **excluding** `output/` (so the payload is smaller)

Optional: include existing results output (not required to run):

```bash
cd /home/tyewhong/qagredo
bash scripts/offline/make_offline_payload.sh --include-output
```

## Part C — Manual/USB transfer to the offline server

### C1) What files you must copy (the simple rule)

Purpose: don’t copy giant folders; copy a small number of archives.

Copy these two files:

- `offline_payload_YYYYMMDD_HHMMSS.tar.zst`
- `offline_payload_YYYYMMDD_HHMMSS.tar.zst.sha256`

### C2) Optional verification (recommended)

Purpose: confirm the USB copy is not corrupted.

On the offline server, run:

```bash
cd /home/tyewhong   # or wherever you copied the files
sha256sum -c offline_payload_YYYYMMDD_HHMMSS.tar.zst.sha256
```

## Part D — Offline server install + run

### D1) Put the files in a known place

Purpose: make the commands easy (no guessing paths).

Copy the files into:

- `/home/tyewhong/` on the offline server

### D2) Unpack the two archives

Purpose: recreate `/home/tyewhong/qagredo/` and `/home/tyewhong/qagredo_host/`.

```bash
cd /home/tyewhong
zstd -dc offline_payload_YYYYMMDD_HHMMSS.tar.zst | tar -x
```

### D3) Load Docker images (one-time per offline server)

Purpose: make containers runnable without internet.

```bash
docker load -i /home/tyewhong/qagredo_offline_bundle/qagredo-v1.tar
docker load -i /home/tyewhong/qagredo_offline_bundle/vllm-openai_v0.5.3.post1.tar
```

### D4) Run (one command)

Purpose: start vLLM + run QAGRedo.

```bash
cd /home/tyewhong/qagredo
export QAGREDO_HOST_DIR=/home/tyewhong/qagredo_host

export VLLM_IMAGE=vllm/vllm-openai:v0.5.3.post1
export VLLM_MODEL=/models/Meta-Llama-3.1-8B-Instruct
export VLLM_API_KEY=llama-local
export VLLM_SERVED_MODEL_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct

docker compose -f docker-compose.offline.yml up -d vllm
docker compose -f docker-compose.offline.yml run --rm qagredo
```

Note: this uses the extracted `/home/tyewhong/qagredo_host/` from the payload.

### D5) Find output

Purpose: confirm it worked.

```bash
find /home/tyewhong/qagredo_host/output -name '*.json' | tail -n 5
```

## Quick checks / troubleshooting

### vLLM health

Purpose: confirm vLLM is up.

```bash
curl -i http://localhost:8100/health
```

### vLLM models (requires API key)

Purpose: confirm the model is registered in vLLM.

```bash
export VLLM_API_KEY=llama-local
curl -H "Authorization: Bearer ${VLLM_API_KEY}" http://localhost:8100/v1/models
```

### Common problems

- **`>` prompt**: you got stuck in a heredoc. Type `EOF` on its own line or press `Ctrl+C`.
- **Unauthorized**: you didn’t set the API key. Use `export VLLM_API_KEY=llama-local`.
- **Connection error**: vLLM is still loading. Wait until it logs “Uvicorn running…”.
- **Browser can’t open `localhost:8100`**: your browser is on your laptop; use Cursor’s forwarded port. Root path `/` showing `{\"detail\":\"Not Found\"}` is normal.

## Change code on the offline server (no rebuild needed)

Purpose: edit code offline without rebuilding Docker images.

On the offline server, edit `/home/tyewhong/qagredo/docker-compose.offline.yml` and under `qagredo: volumes:` add:

```yaml
      - ./run_qa_pipeline.py:/workspace/run_qa_pipeline.py:ro
      - ./utils:/workspace/utils:ro
```

Then edit code (for example `utils/question_generator.py`) and re-run:

```bash
cd /home/tyewhong/qagredo
docker compose -f docker-compose.offline.yml run --rm qagredo
```

## Browser note (port-forward)

- On the server, vLLM is at `http://localhost:8100`
- In your laptop browser, Cursor may forward it to a different local port (e.g. `http://localhost:50400`)
- The root path `/` shows `{"detail":"Not Found"}` — normal

Useful pages:

- Docs UI: `http://localhost:8100/docs`
- Health: `http://localhost:8100/health`

## Send this working setup to an offline server

### 1) Create a bundle folder (online machine)

```bash
mkdir -p /home/tyewhong/qagredo_offline_bundle
```

### 2) Export Docker images (online machine)

```bash
docker save -o /home/tyewhong/qagredo_offline_bundle/qagredo-v1.tar qagredo-v1:latest
docker save -o /home/tyewhong/qagredo_offline_bundle/vllm-openai_v0.5.3.post1.tar vllm/vllm-openai:v0.5.3.post1
```

### 3) Copy folders to the offline server

Copy these two folders:

- `/home/tyewhong/qagredo/` (code + compose file)
- `/home/tyewhong/qagredo_host/` (config/data/models/cache/output)

### 4) Load images on the offline server

```bash
docker load -i /home/tyewhong/qagredo_offline_bundle/qagredo-v1.tar
docker load -i /home/tyewhong/qagredo_offline_bundle/vllm-openai_v0.5.3.post1.tar
```

### 5) Run on the offline server

```bash
cd /home/tyewhong/qagredo
export QAGREDO_HOST_DIR=/home/tyewhong/qagredo_host

export VLLM_IMAGE=vllm/vllm-openai:v0.5.3.post1
export VLLM_MODEL=/models/Meta-Llama-3.1-8B-Instruct
export VLLM_API_KEY=llama-local
export VLLM_SERVED_MODEL_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct

docker compose -f docker-compose.offline.yml up -d vllm
docker compose -f docker-compose.offline.yml run --rm qagredo
```

## If you must change code on the offline server (no rebuild)

If the offline server cannot rebuild Docker images, you can still edit code by **mounting the repo code into the container**.

Preferred: use the provided compose overlay (no edits needed to the main compose file):

```bash
export QAGREDO_HOST_DIR=~/qagredo_host
docker compose -f docker-compose.offline.yml -f docker-compose.offline.mount-code.yml run --rm qagredo
```

This overlay bind-mounts your local code into the runner container so changes take effect immediately.

If you must do it manually, edit `docker-compose.offline.yml` on the offline server and under `qagredo: volumes:` add:

```yaml
      - ./run_qa_pipeline.py:/workspace/run_qa_pipeline.py:ro
      - ./utils:/workspace/utils:ro
```

## “No sentence-transformers model found … mean pooling” warning (MiniLM)

If you see:

`No sentence-transformers model found with name sentence-transformers/all-MiniLM-L6-v2. Creating a new one with mean pooling.`

It means MiniLM is missing from the mounted cache. Fix (offline-safe):

1) Ensure the HF model is present in your host cache:

```bash
ls ~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2 >/dev/null
```

2) Copy it into the host folder used by Docker Compose:

```bash
mkdir -p "$QAGREDO_HOST_DIR/hf_cache/hub"
cp -a ~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2 "$QAGREDO_HOST_DIR/hf_cache/hub/"
```

3) Create the sentence-transformers cache links (one-time):

```bash
mkdir -p "$QAGREDO_HOST_DIR/hf_cache/sentence-transformers"
ln -sfn ../hub/models--sentence-transformers--all-MiniLM-L6-v2 "$QAGREDO_HOST_DIR/hf_cache/sentence-transformers/models--sentence-transformers--all-MiniLM-L6-v2"
```

Then re-run QAGRedo.

## Useful vLLM URLs

- API docs UI: `http://localhost:8100/docs`
- OpenAPI JSON: `http://localhost:8100/openapi.json`
- Health: `http://localhost:8100/health`

Note: the root URL `http://localhost:8100/` returns `{"detail":"Not Found"}` (normal).

## Common problems (quick)

- **401 Unauthorized** on `/v1/*`: you forgot the `Authorization` header.
- **Connection error**: vLLM is still loading. Wait for the “Uvicorn running…” log line.
- **CUDA/driver mismatch** (mentions CUDA >= X): use an older vLLM image tag via `VLLM_IMAGE` (this repo defaults to `v0.5.3.post1`).

## Send this working setup to an offline server

You need to transfer **(A) Docker images** and **(B) your host folder**.

### A) Export Docker images (run on the ONLINE machine)

```bash
mkdir -p ~/qagredo_offline_bundle
docker save -o ~/qagredo_offline_bundle/qagredo-v1.tar qagredo-v1:latest
docker save -o ~/qagredo_offline_bundle/vllm-openai_v0.5.3.post1.tar vllm/vllm-openai:v0.5.3.post1
```

Copy these `.tar` files (from `~/qagredo_offline_bundle/`) to the offline server (USB / SCP via jump host / etc).

### B) Copy your host folder (run on the ONLINE machine)

Copy the whole folder `"$QAGREDO_HOST_DIR/"` to the offline server. It contains:

- This is a **real folder on disk** (by default: `/home/tyewhong//qagredo_host/`).
- The items below are **subfolders inside** `~/qagredo_host/` (not separate folders elsewhere).

- `config/` (config.yaml)
- `data/` (your input JSON/JSONL)
- `output/` (results)
- `models_llm/` (your LLM model folder)
- `hf_cache/` (HF cache + MiniLM cache)

To confirm you have it on the online machine:

```bash
export QAGREDO_HOST_DIR=~/qagredo_host
ls -la "$QAGREDO_HOST_DIR"
```

To bundle it for easy transfer:

```bash
mkdir -p ~/qagredo_offline_bundle
cp -a "$QAGREDO_HOST_DIR" ~/qagredo_offline_bundle/
```

### C) Load images on the OFFLINE server

On the offline server:

```bash
docker load -i ~/qagredo_offline_bundle/qagredo-v1.tar
docker load -i ~/qagredo_offline_bundle/vllm-openai_v0.5.3.post1.tar
```

### D) Run on the OFFLINE server

From the copied repo folder:

```bash
cd ~/qagredo
export QAGREDO_HOST_DIR=~/qagredo_host

export VLLM_IMAGE=vllm/vllm-openai:v0.5.3.post1
export VLLM_MODEL=/models/Meta-Llama-3.1-8B-Instruct
export VLLM_API_KEY=llama-local
export VLLM_SERVED_MODEL_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct

docker compose -f docker-compose.offline.yml up -d vllm
docker compose -f docker-compose.offline.yml run --rm qagredo
```

### E) Change code on the OFFLINE server (no rebuild needed)

If the offline server cannot rebuild Docker images, you can still edit code by **mounting the repo code into the container**.

Preferred: use the provided compose overlay (no edits needed):

```bash
export QAGREDO_HOST_DIR=~/qagredo_host
docker compose -f docker-compose.offline.yml -f docker-compose.offline.mount-code.yml run --rm qagredo
```

If you must do it manually, edit `docker-compose.offline.yml` and under the `qagredo:` service, add these two lines under `volumes:`:

```yaml
      - ./run_qa_pipeline.py:/workspace/run_qa_pipeline.py:ro
      - ./utils:/workspace/utils:ro
```

2) Now edit code files directly on the offline server (for example `utils/question_generator.py`), then run:

```bash
export QAGREDO_HOST_DIR=~/qagredo_host
docker compose -f docker-compose.offline.yml -f docker-compose.offline.mount-code.yml run --rm qagredo
```

Your code changes will take effect immediately (no `docker build` required).
