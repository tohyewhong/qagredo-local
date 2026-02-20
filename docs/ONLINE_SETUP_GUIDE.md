# QAGRedo (Layman Guide -- run online + transfer offline)

This README is written for **your exact folder layout** on this server:

- **Repo (code)**: `/home/tyewhong/qagredo/`
- **Host data/models/cache/output**: `/home/tyewhong/qagredo_host/`
- **Offline transfer files** (`.tar` / `.tar.gz`): stored directly in `/home/tyewhong/qagredo/`

## Purpose (what you are doing)

QAGRedo reads your documents (JSON/JSONL), generates **complex questions +
grounded answers** using an LLM served by **vLLM**, then verifies every answer
is supported by the source document (using **MiniLM** for semantic similarity
and **LLM-as-judge** (a separate Qwen model) for complex reasoning, avoiding self-evaluation bias).

You run **three containers**:
- **vLLM** (Llama-3.1-8B on GPU 0, port 8100): generates questions and answers
- **vLLM-judge** (Qwen2.5-7B on GPU 1, port 8101): independent LLM-as-judge for hallucination checking
- **QAGRedo** (CPU): pipeline orchestration + MiniLM for semantic similarity

### Pipeline overview

```
Documents (JSONL)
    |
    v
  Question Generation (10 types, few-shot examples, temp=0.7)
    |
    v
  Answer Generation (structured + evidence, temp=0.3, 3 retries)
    |
    v
  Hallucination Grading (hybrid: semantic + Qwen LLM fallback)
    |
    v
  Output JSON (grade A-F, confidence %, reasons)
```

**Key features**:
- 10 question types including synthesis, evaluation, and counterfactual
- Structured answers with supporting evidence citations
- Up to 3 retries for ungrounded answers
- Hybrid grading: fast semantic check (MiniLM, CPU) + Qwen LLM fallback for
  counting, aggregation, and inference
- Per-run timestamped output folders (YYYY-MM-DD_HHMMSS)
- Run summary (Generator/Judge/Provider) with `generator_model` and `judge_model` in JSON, plus detailed reasons for ungrounded answers

For full algorithm details, see `ALGORITHM_REPORT.md`.

## Glossary (so you don't get lost later)

| Term | Meaning |
|------|---------|
| **Host** | Your normal shell prompt (e.g., `tyewhong@server1:~$`). Run `docker ...` here |
| **Container** | Prompt looks like `qagredo@...:/workspace$`. Do NOT run `docker` inside |
| **Repo folder** | `/home/tyewhong/qagredo/` (code + compose file + scripts) |
| **Host folder** | `/home/tyewhong/qagredo_host/` (config/data/models/cache/output) |
| **Offline bundle** | `.tar` / `.tar.gz` files stored directly in `/home/tyewhong/qagredo/` (archives you copy by USB) |
| **Grounded** | An answer supported by the source document |
| **Ungrounded** | An answer not supported (hallucination) |

## Part A -- Run on this server (online machine)

### A1) One command (recommended)

Purpose: the easiest way to run end-to-end without copy/pasting many commands.

Prereqs (this is what the script expects on **this server**):

- **Docker** installed, and `docker compose` works.
- **QAGRedo image exists**: `qagredo-v1:latest` (build or load it first).
- **Model zip exists**: `/home/tyewhong/Meta-Llama-3.1-8B-Instruct_hf_cache.zip`
- **Sample input exists**: `/home/tyewhong/Llama328BInstruct/dev-data.jsonl`

```bash
cd /home/tyewhong/qagredo
bash scripts/run_online.sh            # safe mode (no overwrite)
# bash scripts/run_online.sh --overwrite
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
bash scripts/run_online.sh
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
bash scripts/run_online.sh --overwrite
```

### A2) Where is my output?

Purpose: confirm it worked and find the result files.

```bash
find /home/tyewhong/qagredo_host/output -name '*.json' | tail -n 5
```

Output folders are timestamped: `output/vllm/<model>/YYYY-MM-DD_HHMMSS/`

### A3) What `run_online.sh` does (so you know it's safe)

Purpose: understand what will be created/modified.

`scripts/run_online.sh` will:

- Create `/home/tyewhong/qagredo_host/{config,data,output,models_llm,models_embed,hf_cache,hf_cache_judge}`
- Copy `config/config.yaml` into `qagredo_host/config/`
- Copy sample input data into `qagredo_host/data/dev-data.jsonl`
- (If needed) extract `/home/tyewhong/Meta-Llama-3.1-8B-Instruct_hf_cache.zip` and build a real model folder under `qagredo_host/models_llm/Meta-Llama-3.1-8B-Instruct/`
- Set up MiniLM (via `models_embed/` if present, otherwise HF cache) so semantic grading works offline
- Start both vLLM services (main on port **8100**, judge on port **8101**), wait for `/health`, then run QAGRedo

## Convert your files into QAGRedo input (pdf/txt/xlsx/json/jsonl to JSONL)

QAGRedo reads **JSONL** (one JSON object per line). The bundled converter
handles the heavy lifting -- it accepts multiple input formats and produces
normalized JSONL that QAGRedo can ingest directly.

### Supported input formats

| Format | Extension | Notes |
|--------|-----------|-------|
| **JSON** | `.json` | Arrays of objects, single objects, or nested wrappers (`{"articles": [...]}`) |
| **JSON (press/news)** | `.json` | Nested press-style schema with `country`, `source_date`, `summary`, and `source[].english.article` -- auto-detected and flattened |
| **JSONL** | `.jsonl` | One JSON object per line |
| **PDF** | `.pdf` | Requires `pypdf` (included in `requirements.txt`) |
| **Plain text** | `.txt` | Entire file becomes one document |
| **Excel** | `.xlsx` | Requires `openpyxl` (included in `requirements.txt`) |

**JSON repair**: The converter auto-fixes common hand-editing mistakes in JSON
files (missing commas between properties, unterminated strings, trailing
commas). No need to manually clean up your JSON before converting.

### 1) Install dependencies (one-time)

```bash
cd /home/tyewhong/qagredo
/home/tyewhong/qagredo/.venv/bin/pip install -r requirements.txt
```

### 2) Convert your file to JSONL

```bash
cd /home/tyewhong/qagredo

# JSON (flat or press/news-style nested)
python3 scripts/conversion/convert_to_qagredo_jsonl.py \
  --input "data/sample press.json" \
  --output data/sample_press.jsonl

# PDF
python3 scripts/conversion/convert_to_qagredo_jsonl.py \
  --input data/report.pdf \
  --output data/report.jsonl

# Excel
python3 scripts/conversion/convert_to_qagredo_jsonl.py \
  --input data/data.xlsx \
  --output data/data.jsonl

# Plain text
python3 scripts/conversion/convert_to_qagredo_jsonl.py \
  --input data/notes.txt \
  --output data/notes.jsonl
```

> **Tip**: File paths with spaces are supported -- just wrap them in quotes.

Output JSONL format (per line):

| Field | Description |
|-------|-------------|
| `id` | Unique document identifier |
| `title` | Document title |
| `content` | Full text (preferred by QAGRedo) |
| `text` | Same as `content` (alias) |
| `source` | Input file path |
| `type` | `text_document` |
| `metadata` | Optional -- preserves `country`, `source_date`, `languages`, etc. |

### 3) Point QAGRedo to the converted JSONL

Copy the output JSONL into the host data folder and update the config:

```bash
cp data/sample_press.jsonl ~/qagredo_host/data/
```

Edit `config/config.yaml` and set:
- `run.input_file: data/sample_press.jsonl`

### A4) Run WITHOUT Docker (host-only, advanced)

Purpose: run `run_qa_pipeline.py` directly on the Linux host (no containers).

Important notes:

- You still need an LLM server. In host-only mode we run **vLLM on the host** (still on port `8100`).
- Use the repo Python environment (`/home/tyewhong/qagredo/.venv/`). System `python3` may miss packages.

#### A4.1) Start (or verify) vLLM on the host

```bash
curl -i http://localhost:8100/health
```

If you do **not** get `HTTP/1.1 200 OK`, start vLLM via Docker Compose:

```bash
cd /home/tyewhong/qagredo
docker compose -f docker-compose.offline.yml up -d vllm vllm-judge
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

Output folders are timestamped: `output/vllm/<model>/YYYY-MM-DD_HHMMSS/`

```bash
find /home/tyewhong/qagredo/output -name '*.json' | tail -n 5
```

## Part B -- Create the offline transfer bundle (repeatable)

### B1) Why you need this

Purpose: the repo folder `/home/tyewhong/qagredo/` can be huge because of `.venv/`, but the offline server does **not** need it (Docker runs the app).\
This step creates **small archives** that contain only what the offline server needs.

### B2) Create the bundle (5-file approach)

Purpose: create a small code/config/data bundle you can re-transfer quickly whenever code changes.

```bash
cd /home/tyewhong/qagredo
bash scripts/make_qagredo_bundle.sh              # code + config + data
bash scripts/make_qagredo_bundle.sh --include-data   # also include data/ files
```

This produces `qagredo_bundle.tar.gz` (~few MB) plus a `.sha256` checksum.

For the full 5-file transfer workflow (bundle + Docker images + models), see:

- `docs/OFFLINE_SETUP_GUIDE.md`

## Part C -- Transfer to the offline server

Copy these 5 files to the offline server (USB / SCP):

| # | File | Size | Re-transfer when |
|---|------|------|-----------------|
| 1 | `vllm-openai_v0.5.3.post1.rootfs.tar` | ~15-20 GB | Rarely (new vLLM version) |
| 2 | `qagredo-v1.tar` | ~5-10 GB | Rarely (new Docker image) |
| 3 | `models_llm.tar` | ~30 GB | Rarely (new model) |
| 4 | `models_embed_all-MiniLM-L6-v2.tar` | ~263 MB | Rarely (new embedding model) |
| 5 | `qagredo_bundle.tar.gz` | ~few MB | Often (code/config/data changes) |

### Optional verification (recommended)

```bash
sha256sum -c qagredo_bundle.tar.gz.sha256
```

## Part D -- Offline server install + run

### D1) Unpack + setup

```bash
tar xzf qagredo_bundle.tar.gz        # extracts to qagredo_host/
cd qagredo_host
bash setup_offline.sh                  # loads images, links models, fixes permissions
```

### D2) Run

```bash
cd qagredo_host
bash run.sh
```

### D3) Find output

Purpose: confirm it worked.

```bash
find qagredo_host/output -name '*.json' | tail -n 5
```

Output folders are timestamped: `output/vllm/<model>/YYYY-MM-DD_HHMMSS/`

## Quick checks / troubleshooting

### vLLM health

Purpose: confirm both vLLM services are up.

```bash
curl -i http://localhost:8100/health
curl -i http://localhost:8101/health
```

### vLLM models (requires API key)

Purpose: confirm the models are registered in vLLM.

```bash
export VLLM_API_KEY=llama-local
curl -H "Authorization: Bearer ${VLLM_API_KEY}" http://localhost:8100/v1/models
curl -H "Authorization: Bearer ${VLLM_API_KEY}" http://localhost:8101/v1/models
```

### Common problems

| Problem | Solution |
|---------|----------|
| `>` prompt | Stuck in a heredoc. Type `EOF` on its own line or press `Ctrl+C` |
| Unauthorized | `export VLLM_API_KEY=llama-local` |
| Connection error | vLLM is still loading. Wait for "Uvicorn running..." |
| Browser can't open `localhost:8100` | Your laptop's `localhost` is not the server. Use SSH tunnel / port-forward |
| Permission denied | `bash setup_offline.sh --force` |
| Cannot delete hf_cache | `docker run --rm --privileged --userns=host -u 0 --entrypoint bash -v "$(pwd)/hf_cache:/hf" vllm/vllm-openai:v0.5.3.post1 -c "rm -rf /hf/modules /hf/hub"` |
| Cannot delete hf_cache_judge | Same as above but use `hf_cache_judge` instead of `hf_cache` |
| `pynvml.NVMLError_InvalidArgument` | Do **not** set `CUDA_VISIBLE_DEVICES` in docker-compose. GPU assignment is handled by Docker's `deploy.resources.reservations.devices.device_ids` |

## Change code on the offline server (no rebuild needed)

Purpose: edit code offline without rebuilding Docker images.

On the dev machine, make your changes, then re-create the bundle:

```bash
cd /home/tyewhong/qagredo
bash scripts/make_qagredo_bundle.sh
```

Transfer just `qagredo_bundle.tar.gz` (~few MB) to the offline server and re-extract:

```bash
tar xzf qagredo_bundle.tar.gz
cd qagredo_host
bash run.sh
```

## Permission model

QAGRedo uses a three-layer permission model:

| Layer | Where | What |
|-------|-------|------|
| Entrypoint startup | Inside container | `chown` writable dirs to HOST_UID:HOST_GID |
| Entrypoint EXIT trap | Inside container | `chown` on exit |
| Post-run safety net | Host side (run.sh) | Docker `chown` with `--privileged --userns=host` |

All Docker volume mounts use `:rw`. The `--privileged --userns=host` flags
bypass Docker user namespace remapping, ensuring files are always owned by
the host user regardless of Docker configuration.

## Browser note (port-forward)

- On the server, vLLM is at `http://localhost:8100`, vLLM-judge at `http://localhost:8101`
- In your laptop browser, Cursor may forward it to a different local port (e.g. `http://localhost:50400`)
- The root path `/` shows `{"detail":"Not Found"}` -- normal

Useful pages:

- Main vLLM: Docs UI `http://localhost:8100/docs`, Health `http://localhost:8100/health`
- Judge vLLM: Docs UI `http://localhost:8101/docs`, Health `http://localhost:8101/health`

## Send this working setup to an offline server

Recommended: use the 5-file bundle approach (see **Part B/C/D** above).
Run `bash scripts/make_qagredo_bundle.sh` to create `qagredo_bundle.tar.gz`, then transfer the
5 files to the offline server. See `docs/OFFLINE_SETUP_GUIDE.md` for the full walkthrough.

### 1) Export Docker images (online machine)

```bash
cd /home/tyewhong/qagredo

docker save -o qagredo-v1.tar qagredo-v1:latest

# Export vLLM as a rootfs tar (smaller than docker save; loaded via docker import)
docker rm -f vllm-export-tmp 2>/dev/null || true
docker create --name vllm-export-tmp vllm/vllm-openai:v0.5.3.post1
docker export -o vllm-openai_v0.5.3.post1.rootfs.tar vllm-export-tmp
docker rm -f vllm-export-tmp
```

### 2) Copy folders to the offline server

Copy these two folders:

- `/home/tyewhong/qagredo/` (code + compose file + `.tar` / `.tar.gz` archives)
- `/home/tyewhong/qagredo_host/` (config/data/models/cache/output)

### 3) Load images on the offline server

```bash
cd /home/tyewhong/qagredo

docker load -i qagredo-v1.tar

# Import vLLM rootfs tar (created via docker export)
docker import \
  --change 'WORKDIR /vllm-workspace' \
  --change 'ENTRYPOINT ["python3","-m","vllm.entrypoints.openai.api_server"]' \
  vllm-openai_v0.5.3.post1.rootfs.tar \
  vllm/vllm-openai:v0.5.3.post1
```

### 4) Run on the offline server

```bash
cd /home/tyewhong/qagredo
export QAGREDO_HOST_DIR=/home/tyewhong/qagredo_host

export VLLM_IMAGE=vllm/vllm-openai:v0.5.3.post1
export VLLM_MODEL=/models/Meta-Llama-3.1-8B-Instruct
export VLLM_API_KEY=llama-local
export VLLM_SERVED_MODEL_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct

# Judge service (Qwen2.5-7B on port 8101)
export VLLM_JUDGE_IMAGE=vllm/vllm-openai:v0.5.3.post1
export VLLM_JUDGE_MODEL=/models/Qwen2.5-7B-Instruct
export VLLM_JUDGE_API_KEY=llama-local
export VLLM_JUDGE_SERVED_MODEL_NAME=Qwen/Qwen2.5-7B-Instruct

docker compose -f docker-compose.offline.yml up -d vllm vllm-judge
docker compose -f docker-compose.offline.yml run --rm qagredo
```

## Useful vLLM URLs

**Main vLLM (port 8100):**
- API docs UI: `http://localhost:8100/docs`
- OpenAPI JSON: `http://localhost:8100/openapi.json`
- Health: `http://localhost:8100/health`

**Judge vLLM (port 8101):**
- API docs UI: `http://localhost:8101/docs`
- OpenAPI JSON: `http://localhost:8101/openapi.json`
- Health: `http://localhost:8101/health`

Note: the root URL `http://localhost:8100/` (and 8101) returns `{"detail":"Not Found"}` (normal).

## Common problems (quick)

- **401 Unauthorized** on `/v1/*`: you forgot the `Authorization` header.
- **Connection error**: vLLM is still loading. Wait for the "Uvicorn running..." log line.
- **CUDA/driver mismatch** (mentions CUDA >= X): use an older vLLM image tag via `VLLM_IMAGE` (this repo defaults to `v0.5.3.post1`).

## "No sentence-transformers model found ... mean pooling" warning (MiniLM)

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
