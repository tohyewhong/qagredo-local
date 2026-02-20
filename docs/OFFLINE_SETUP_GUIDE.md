# QUICKSTART: 5-File Offline Deployment

This guide covers the **5-file approach** for deploying QAGRedo to an air-gapped server.
You transfer **5 independent files** that change at different frequencies,
so you only re-transfer what actually changed.

## The 5 Files

| # | File | Size | Changes | Description |
|---|------|------|---------|-------------|
| 1 | `vllm-openai_v0.5.3.post1.rootfs.tar` | ~15-20 GB | Almost never | vLLM Docker image (docker export) |
| 2 | `qagredo-v1.tar` | ~5-10 GB | Rarely | QAGRedo Docker image (docker save) |
| 3 | `models_llm.tar` | ~30 GB | Rarely | LLM model weights (Llama + Qwen) |
| 4 | `models_embed_all-MiniLM-L6-v2.tar` | ~263 MB | Rarely | Embedding model weights |
| 5 | `qagredo_bundle.tar.gz` | ~few MB | Often | Code, config, data, runner scripts |

**Key benefit**: When you change code/config, you only re-transfer file #5 (~MB), not everything (~50+ GB).

**Hardware**: 2 GPUs (24GB each) — one for Llama (generator), one for Qwen (judge).

---

## What QAGRedo does

The pipeline reads your documents and:

1. **Generates complex questions** -- 10 question types (analysis, aggregation,
   comparison, inference, causal, temporal, multi-hop, synthesis, evaluation,
   counterfactual) that require reasoning across multiple parts of the document
2. **Generates grounded answers** -- structured format with supporting evidence,
   low temperature (0.3), up to 3 retries for ungrounded answers
3. **Verifies grounding** -- hybrid method: fast semantic similarity (MiniLM)
   for most answers, Qwen (LLM-as-judge) fallback for counting/aggregation/inference
4. **Grades** each document (A/B/C/D/F) and saves detailed reasons for any
   ungrounded content

See `docs/ALGORITHM_REPORT.md` for full algorithm details and design rationale.

---

## Architecture: all-in-one `qagredo_host/`

File #5 (`qagredo_bundle.tar.gz`) extracts to a single **`qagredo_host/`** directory
that contains **everything** -- code, config, data, runner scripts, and Docker Compose.
The system runs **three containers**: vLLM (Llama on GPU 0), vLLM-judge (Qwen on GPU 1),
and qagredo (CPU). Docker mounts directly from this directory, so any edit you make
here persists across container restarts.

```
qagredo_host/                          <-- ONE directory, everything is here
|-- run.sh                             # start vLLM + vLLM-judge + run pipeline
|-- setup_offline.sh                   # one-time setup (load images, link models)
|-- jupyter.sh                         # start Jupyter Lab
|-- docker-compose.offline.yml         # Docker Compose (mounts from ./)
|-- run_qa_pipeline.py                 # main Python entry point
|-- config/config.yaml                 # pipeline configuration
|-- utils/                             # Python source code
|-- scripts/                           # helper scripts
|-- data/                              # input JSONL files
|-- output/                            # results (YYYY-MM-DD_HHMMSS folders)
|-- models_llm/                        # LLM weights (Llama + Qwen, linked by setup_offline.sh)
|-- models_embed/                      # embedding model (linked by setup_offline.sh)
|-- hf_cache/                          # HF cache (generator)
|-- hf_cache_judge/                    # HF cache (judge)
|-- docs/                              # documentation
+-- README.md
```

**Edit any file here and re-run** -- Docker picks up changes instantly.
When Docker is down, everything is still here.

---

## ONLINE machine: Create the 5 files

### Files 1 & 2: Docker image tars (create once)

```bash
# QAGRedo image
docker compose -f docker-compose.offline.yml build qagredo
docker save -o qagredo-v1.tar qagredo-v1:latest

# vLLM image (used by BOTH vllm and vllm-judge containers; exported as rootfs for smaller size)
docker pull vllm/vllm-openai:v0.5.3.post1
docker rm -f vllm-export-tmp 2>/dev/null || true
docker create --name vllm-export-tmp vllm/vllm-openai:v0.5.3.post1
docker export -o vllm-openai_v0.5.3.post1.rootfs.tar vllm-export-tmp
docker rm -f vllm-export-tmp
```

### File 3: LLM model tar (create once)

```bash
# Create models_llm.tar from your model folder(s)
# Must include both Llama (generator) and Qwen (judge) model folders
tar cf models_llm.tar models_llm/
```

### File 4: Embedding model tar (create once)

```bash
# Create embedding model tar
tar cf models_embed_all-MiniLM-L6-v2.tar all-MiniLM-L6-v2/
```

### File 5: Code bundle (create every time you change code/config)

```bash
cd /path/to/qagredo
bash scripts/make_qagredo_bundle.sh
# Optional: include input data files
bash scripts/make_qagredo_bundle.sh --include-data
```

This produces:
- `qagredo_bundle.tar.gz` (~few MB) -- extracts to `qagredo_host/`
- `qagredo_bundle.tar.gz.sha256`

---

## OFFLINE server: Deploy

### Step 1: Copy all 5 files to one staging directory

```
/home/user/offline20260209/
|-- vllm-openai_v0.5.3.post1.rootfs.tar   (file 1)
|-- qagredo-v1.tar                          (file 2)
|-- models_llm.tar                          (file 3)
|-- models_embed_all-MiniLM-L6-v2.tar      (file 4)
+-- qagredo_bundle.tar.gz                   (file 5)
```

### Step 2: Extract models and bundle

```bash
cd /home/user/offline20260209

# Extract models
tar xf models_llm.tar
mkdir -p models_embed
tar xf models_embed_all-MiniLM-L6-v2.tar -C models_embed

# Extract bundle (creates qagredo_host/)
tar xzf qagredo_bundle.tar.gz
cd qagredo_host
```

### Step 3: Run setup (first time, or after updating images/models)

```bash
bash setup_offline.sh
```

This will:
- **Auto-discover** the Docker image tars and model directories (searches parent/sibling directories)
- **Load Docker images** (idempotent -- skips if already loaded)
- **Link models** into `qagredo_host/` (symlinks to avoid copying GBs)
- **Fix permissions** so the Docker container user can read/write
- **Run smoke tests** to verify everything is ready

Options:
- `--skip-images`: Skip Docker image loading (if already loaded)
- `--force`: Overwrite existing model symlinks

You can override auto-discovery with environment variables:
```bash
VLLM_ROOTFS_TAR=/custom/path/vllm.tar \
QAGREDO_TAR=/custom/path/qagredo.tar \
MODELS_LLM_DIR=/custom/path/models_llm \
MODELS_EMBED_DIR=/custom/path/models_embed \
bash setup_offline.sh
```

### Step 3b (optional): Convert input files to JSONL

If your input data is in JSON, PDF, TXT, or XLSX format, convert it to JSONL first.

```bash
cd /home/user/offline20260209/qagredo_host

# Convert JSON / PDF / TXT / XLSX to JSONL
python3 scripts/conversion/convert_to_qagredo_jsonl.py \
  --input data/your-file.json \
  --output data/your-file.jsonl
```

**Press/news JSON handling**: For press-style JSON files with `"english"` / `"native"` language
wrappers, the converter extracts **only English articles** into the content.
All `"native"` content is skipped (`null`, `{}`, or actual text).

Then edit `config/config.yaml`:
```yaml
run:
  input_file: your-file.jsonl
```

### Step 4: Run the pipeline

```bash
bash run.sh
```

This will:
1. Start vLLM (Llama on GPU 0) and vLLM-judge (Qwen on GPU 1) in the background
2. Wait for both vLLM health checks
3. Run the QAGRedo pipeline
4. Output results to `output/vllm/<model>/YYYY-MM-DD_HHMMSS/`

Each run creates a **unique timestamped folder** (date + time to the second),
so multiple runs per day do not overwrite each other.

**Pipeline details**:
- 10 question types with few-shot examples (advanced complexity by default)
- Structured answers with supporting evidence (temp=0.3)
- Up to 3 retries for ungrounded answers
- Hybrid hallucination checking: semantic first, Qwen (LLM-as-judge) fallback

Change settings in `config/config.yaml`:
```yaml
hallucination:
  method: "hybrid"    # or "semantic", "llm", "keyword"

question_generation:
  complexity: "advanced"    # or "basic", "moderate"

answer_generation:
  temperature: 0.3
```

**To use a different model:**
```bash
export VLLM_MODEL=/models/<YourModelFolder>
export VLLM_SERVED_MODEL_NAME=<org/YourModelName>
bash run.sh
```

**Other run.sh commands:**
```bash
bash run.sh --down      # stop all three containers
bash run.sh --logs      # tail vLLM logs (generator + judge)
bash run.sh --status    # show container status
```

### Step 4b: Summarize the run results

After the pipeline completes, summarize all analysis files:

```bash
# Auto-find latest run folder (easiest)
bash scripts/utils/summarize_run.sh --latest

# Specific run folder
bash scripts/utils/summarize_run.sh output/vllm/meta-llama-meta-llama-3.1-8b-instruct/2026-02-13_143025/

# All runs combined
bash scripts/utils/summarize_run.sh --all

# Also save summary as JSON (for detailed analysis)
bash scripts/utils/summarize_run.sh --latest --json
```

The terminal summary shows Generator, Judge, and Provider. The **run_summary.json** includes:
- `generator_model` and `judge_model` (separate fields)
- Per-document statistics (grade, confidence, grounded/ungrounded counts)
- Per-QA details with grounding method and confidence
- **For ungrounded answers**: specific reasons, ungrounded sentences, and
  LLM judge verdict with explanation
- **Ungrounded highlights**: flat list of all failed QA pairs for quick scanning

### Step 4 (alternative): Start Jupyter Lab

```bash
bash jupyter.sh
```

If running on a **remote** offline server, create an SSH tunnel from your local machine:

```bash
# Run this on your LOCAL machine (not the offline server):
ssh -L 8899:localhost:8899 user@offline-server
```

Then open `http://localhost:8899` in your browser (no token/password required).

**Options:**
```bash
bash jupyter.sh --no-vllm   # Jupyter only, no GPU
bash jupyter.sh --down       # stop all containers
```

---

## Day-to-day workflow (after first deployment)

Once files 1-4 are on the server, your typical workflow is:

### Updating code/config (on dev machine):

1. Edit code/config in the repo, then:
   ```bash
   bash scripts/make_qagredo_bundle.sh --include-data
   ```

2. Transfer just `qagredo_bundle.tar.gz` (~few MB) to the offline server

3. On the offline server:
   ```bash
   cd /home/user/offline20260209
   tar xzf qagredo_bundle.tar.gz
   cd qagredo_host
   bash setup_offline.sh --skip-images   # re-link models, skip docker load
   bash run.sh
   ```

### Editing directly on the offline server (no transfer needed):

Since everything is in `qagredo_host/` and Docker mounts it directly,
you can edit files on the offline server and re-run immediately:

```bash
cd /home/user/offline20260209/qagredo_host

# Edit config
vi config/config.yaml

# Edit code
vi utils/answer_generator.py

# Re-run (Docker picks up changes instantly)
bash run.sh
```

All changes persist across Docker restarts. No rebuild required.

---

## Permission model

QAGRedo uses a three-layer permission model to ensure all files are always
owned by the host user:

| Layer | Where | What |
|-------|-------|------|
| Entrypoint startup | Inside container | `chown` writable dirs to HOST_UID:HOST_GID |
| Entrypoint EXIT trap | Inside container | `chown` on exit (catches files created during run) |
| Post-run safety net | Host side (run.sh) | Docker `chown` with `--privileged --userns=host` |

All Docker volume mounts use `:rw` (read-write). The `--privileged --userns=host`
flags bypass Docker user namespace remapping, which is required on servers
where Docker maps container root to an unprivileged host UID.

---

## Troubleshooting

### vLLM won't start or crashes
```bash
docker logs qagredo-vllm --tail 100
docker logs qagredo-vllm-judge --tail 100
```
Common issues:
- **CUDA version mismatch**: vLLM v0.5.3.post1 requires CUDA 12.2. Check `nvidia-smi`.
- **Not enough GPU memory**: Requires **2 GPUs (24GB each)** — one for Llama, one for Qwen. Reduce `VLLM_GPU_UTIL=0.7` or `VLLM_MAX_MODEL_LEN=1024` if needed.
- **Wrong GPU count**: Set `VLLM_TP_SIZE=1` for single GPU per container.
- **`pynvml.NVMLError_InvalidArgument`**: Do **not** set `CUDA_VISIBLE_DEVICES` in docker-compose. GPU assignment is handled by Docker's `deploy.resources.reservations.devices.device_ids` — Docker maps the reserved GPU as device 0 inside the container.

### QAGRedo pipeline fails
```bash
# Check the config matches the vLLM model name
grep model config/config.yaml
echo $VLLM_SERVED_MODEL_NAME   # must match config.yaml llm.model
```

### Permission denied on output/, hf_cache/, or hf_cache_judge/

```bash
# Re-run setup with --force (uses Docker to fix permissions)
bash setup_offline.sh --force
```

### Cannot delete hf_cache or hf_cache_judge files (root-owned by vLLM)

```bash
# Generator cache
docker run --rm --privileged --userns=host -u 0 --entrypoint bash \
  -v "$(pwd)/hf_cache:/hf" vllm/vllm-openai:v0.5.3.post1 \
  -c "rm -rf /hf/modules /hf/hub"

# Judge cache
docker run --rm --privileged --userns=host -u 0 --entrypoint bash \
  -v "$(pwd)/hf_cache_judge:/hf" vllm/vllm-openai:v0.5.3.post1 \
  -c "rm -rf /hf/modules /hf/hub"
```

### Docker images not found
```bash
docker images | grep -E 'qagredo|vllm'
# If missing, re-run:
bash setup_offline.sh   # will re-load from tar files
```
