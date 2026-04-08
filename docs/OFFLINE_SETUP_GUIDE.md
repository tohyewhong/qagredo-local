# QUICKSTART: 6-File Offline Deployment

This guide covers the **6-file approach** for deploying QAGRedo to an air-gapped server.
You transfer **6 independent files** that change at different frequencies,
so you only re-transfer what actually changed.

## The 6 Files

| # | File | Size | Changes | Description |
|---|------|------|---------|-------------|
| 1 | `vllm-openai_v0.5.3.post1.rootfs.tar` | ~15-20 GB | Almost never | vLLM Docker image (docker export) |
| 2 | `qagredo-v1.tar` | ~5-10 GB | Rarely | QAGRedo Docker image (docker save) |
| 3 | `models_llama.tar.gz` | ~12-16 GB | Rarely | Generator LLM weights (Llama) |
| 4 | `models_qwen.tar.gz` | ~12-14 GB | Rarely | Judge LLM weights (Qwen) |
| 5 | `models_embed_all-MiniLM-L6-v2.tar` | ~263 MB | Rarely | Embedding model weights |
| 6 | `qagredo_bundle.tar.gz` | ~few MB | Often | Code, config, data, runner scripts |

**Key benefit**: When you change code/config, you only re-transfer file #6 (~MB), not everything (~50+ GB).

**Hardware**: 2 GPUs (24GB each) — one for Llama (generator), one for Qwen (judge).

## Causal deployment model (why this works)

Use this mental model during setup:

- If image/model artifacts are correct, then container startup can succeed.
- If startup succeeds and health checks pass, then pipeline API calls can succeed.
- If pipeline API calls succeed and input is valid, then outputs are produced.
- If any upstream dependency fails, downstream stages fail regardless of retries.

Dependency chain:

1. Artifacts (`*.tar`, models, bundle) -> 2. `setup_offline.sh` linking/loading ->
3. vLLM health -> 4. `run.sh` pipeline -> 5. output JSON

When debugging, always move left-to-right on this chain.

---

## What QAGRedo does

The pipeline reads your documents and:

1. **Generates complex questions** -- 10 question types (analysis, aggregation,
   comparison, inference, causal, temporal, multi-hop, synthesis, evaluation,
   counterfactual) that require reasoning across multiple parts of the document,
   with per-question comprehensiveness check to reject trivial questions
2. **Generates grounded answers** -- structured format with supporting evidence,
   low temperature (0.3), per-slot policy of up to 3 total answer trials per
   question then replacement question for that slot (bounded by
   `max_question_regeneration_rounds`), plus one targeted rewrite when question
   coverage is weak
3. **Verifies grounding** -- hybrid method: fast semantic similarity (MiniLM)
   for most answers, Qwen (LLM-as-judge) fallback for counting/aggregation/inference
4. **Grades** each document (A/B/C/D/F) and saves detailed reasons for any
   ungrounded content
5. **Uses required framework mode** -- LangChain (prompt templates + structured parsing)
   and LangGraph (per-document orchestration + dynamic routing)

See `docs/ALGORITHM_REPORT.md` for full algorithm details and design rationale.

---

## Architecture: all-in-one `qagredo_host/`

File #6 (`qagredo_bundle.tar.gz`) extracts to a single **`qagredo_host/`** directory
that contains **everything** -- code, config, data, runner scripts, and Docker Compose.
The system runs **three containers**: vLLM (Llama on GPU 0), vLLM-judge (Qwen on GPU 1),
and qagredo (CPU). Docker mounts directly from this directory, so any edit you make
here persists across container restarts.

```
qagredo_host/                          <-- ONE directory, everything is here
|-- run.sh                             # start vLLM + vLLM-judge + run pipeline
|-- setup_offline.sh                   # one-time setup (load images, link models)
|-- verify_offline_deployment.sh       # confirm image matches requirements.txt
|-- jupyter.sh                         # start Jupyter Lab
|-- docker-compose.yml         # Docker Compose (mounts from ./)
|-- run_qa_pipeline.py                 # main Python entry point
|-- requirements.txt                   # Python deps (must match qagredo-v1.tar)
|-- config/config.yaml                 # pipeline configuration
|-- utils/                             # Python source code
|-- scripts/                           # helper scripts
|-- data/                              # input documents (auto-converted to JSONL at runtime when configured)
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

## Verify `requirements.txt` vs `qagredo-v1.tar` (avoid repeat failures)

The pipeline runs **inside** the Docker image. If file **#6** (`qagredo_bundle.tar.gz`) has a **newer** `requirements.txt` than the Python packages inside file **#2** (`qagredo-v1.tar`), you get errors such as `ModuleNotFoundError: langchain_core`.

**Rules:**

1. **Keep #2 and #6 in sync.** After any change to `requirements.txt`, rebuild the image on an online machine, `docker save` a new `qagredo-v1.tar`, and re-transfer it. Then reload on the offline host (`docker rmi qagredo-v1:latest` before `docker load` so the tag is not stale).
2. **Online — bundle build checks the image.** `bash scripts/make_qagredo_bundle.sh` verifies that **local** `qagredo-v1:latest` satisfies the **exact** `requirements.txt` placed in the bundle. If verification fails, no `qagredo_bundle.tar.gz` is produced until you rebuild the image.
3. **Offline — run the verifier after load.** From `qagredo_host/`:
   ```bash
   bash verify_offline_deployment.sh
   ```
   This runs `pip check` and confirms every line in `requirements.txt` is installed at the pinned version.

**Quick online sanity check (after `docker compose build`):**

```bash
docker run --rm --entrypoint "" qagredo-v1:latest \
  /opt/conda/bin/python /workspace/scripts/docker_verify_requirements.py /workspace/requirements.txt
```

---

## ONLINE machine: Create the 6 files

### Files 1 & 2: Docker image tars (create once)

```bash
# QAGRedo image (offline-safe default: do NOT bake MiniLM)
docker compose -f docker-compose.yml build --build-arg BAKE_MINILM=0 qagredo
mkdir -p /data/tyewhong/qagredo
docker save -o /data/tyewhong/qagredo/qagredo-v1.tar qagredo-v1:latest

# vLLM image (used by BOTH vllm and vllm-judge containers; exported as rootfs for smaller size)
docker pull vllm/vllm-openai:v0.5.3.post1
docker rm -f vllm-export-tmp 2>/dev/null || true
docker create --name vllm-export-tmp vllm/vllm-openai:v0.5.3.post1
docker export -o /data/tyewhong/qagredo/vllm-openai_v0.5.3.post1.rootfs.tar vllm-export-tmp
docker rm -f vllm-export-tmp
```

### Files 3 & 4: Split LLM model tars (create once)

```bash
# Create split model archives from models_llm/
tar czf /data/tyewhong/qagredo/models_llama.tar.gz -C models_llm Meta-Llama-3.1-8B-Instruct
tar czf /data/tyewhong/qagredo/models_qwen.tar.gz  -C models_llm Qwen2.5-7B-Instruct

# Optional legacy format (still supported by setup_offline.sh):
# tar cf models_llm.tar models_llm/
```

### File 5: Embedding model tar (create once)

```bash
# Create embedding model tar
tar cf /data/tyewhong/qagredo/models_embed_all-MiniLM-L6-v2.tar all-MiniLM-L6-v2/
```

### File 6: Code bundle (create every time you change code/config)

```bash
cd /path/to/qagredo
bash scripts/make_qagredo_bundle.sh
# Optional: include input data files
bash scripts/make_qagredo_bundle.sh --include-data
mv -f qagredo_bundle.tar.gz qagredo_bundle.tar.gz.sha256 /data/tyewhong/qagredo/
sha256sum -c /data/tyewhong/qagredo/qagredo_bundle.tar.gz.sha256
```

This produces:
- `/data/tyewhong/qagredo/qagredo_bundle.tar.gz` (~few MB) -- extracts to `qagredo_host/`
- `/data/tyewhong/qagredo/qagredo_bundle.tar.gz.sha256`

---

## OFFLINE server: Deploy

### Step 1: Copy all 6 files from `/data/tyewhong/qagredo/` to one staging directory

```
/home/tyewhong/qagredo_staging/
|-- vllm-openai_v0.5.3.post1.rootfs.tar   (file 1)
|-- qagredo-v1.tar                          (file 2)
|-- models_llama.tar.gz                     (file 3)
|-- models_qwen.tar.gz                      (file 4)
|-- models_embed_all-MiniLM-L6-v2.tar      (file 5)
+-- qagredo_bundle.tar.gz                   (file 6)
+-- qagredo_bundle.tar.gz.sha256            (optional but recommended)
```

### Step 2: Verify and extract models and bundle

```bash
cd /home/tyewhong/qagredo_staging
sha256sum -c qagredo_bundle.tar.gz.sha256

# Extract models
mkdir -p models_llm
tar xzf models_llama.tar.gz -C models_llm
tar xzf models_qwen.tar.gz  -C models_llm
mkdir -p models_embed
tar xf models_embed_all-MiniLM-L6-v2.tar -C models_embed

# Extract bundle (creates qagredo_host/)
tar xzf qagredo_bundle.tar.gz
cd qagredo_host
```

#### If `tar` / `gzip` reports “unexpected end of file” or “Unexpected EOF in archive”

That almost always means **`qagredo_bundle.tar.gz` is incomplete or corrupted** on the offline machine (truncated copy, bad USB, wrong FTP mode, etc.). The archive is **not** recoverable from that file — copy it again.

1. **Check gzip integrity** (on the offline server, same directory as the file):
   ```bash
   gzip -t qagredo_bundle.tar.gz && echo "gzip OK" || echo "gzip BAD — file truncated or corrupt"
   ```
2. **Compare size** with the machine that built the bundle (e.g. `ls -l qagredo_bundle.tar.gz`). Sizes must match **exactly**.
3. **Verify SHA256** (recommended). The `.sha256` file from the build machine may list a **full path**; if `sha256sum -c` complains about a missing path, run:
   ```bash
   sha256sum qagredo_bundle.tar.gz
   ```
   and compare the hash to the value in `qagredo_bundle.tar.gz.sha256` (first column).
4. **Re-copy using binary-safe transfer**: SCP/rsync/USB “copy as file”, not email/chat; for FTP use **binary** mode. After copy, repeat steps 1–3 before `tar xzf`.

### Step 3: Run setup (first time, or after updating images/models)

```bash
bash setup_offline.sh
```

### Step 3a (recommended): Verify the loaded image matches `requirements.txt`

After `docker load` (or any time you suspect an old `qagredo-v1:latest` tag), run:

```bash
bash verify_offline_deployment.sh
```

This runs `pip check` and confirms every pinned line in `requirements.txt` is installed in the image. If it fails, reload a fresh `qagredo-v1.tar` (after `docker rmi qagredo-v1:latest`) — do **not** only refresh the bundle when `requirements.txt` changed.

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

If your input data is in JSON, JSONL, PDF, TXT, DOC, DOCX, XLSX, or CSV format, convert it to JSONL first.

Important:
- Conversion uses `scripts/conversion/convert_to_qagredo_jsonl.py` (parser-based, not LLM-based).
- **`bash run.sh`** uses **`run.input_file`** (`.json` / `.jsonl`) and the **file extension** only; it **does not** read **`run.input_type`**. Convert PDF/TXT/etc. first, then set **`input_file`** to the JSONL.
- Optional semantic enrichment uses `convert_to_qagredo_jsonl.py --semantic-normalize` to add `metadata.semantic_enrichment` while keeping canonical `content` unchanged.
- Manual conversion remains useful when you want a fixed JSONL artifact.

Quickstart (recommended):
1) Install dependencies (one-time): `python3 -m pip install -r requirements.txt`
2) Set **`run.input_file`** in `config/config.yaml` (and `input_folder: ""`)
3) Run `bash run.sh`

```yaml
# config/config.yaml (excerpt)
run:
  input_folder: ""
  input_file: data/your-file.jsonl    # or .json
  input_type: auto                      # not wired to converter; use CLI --input-type
  max_files: 50                        # not read by current scripts
  num_documents: 10                    # 0 = all loaded records
  min_content_words: 20                 # not enforced by run_qa_pipeline (reserved)
  min_content_chars: 0
  semantic_normalization:              # not read — use converter --semantic-normalize
    enable: false
    max_content_chars: 5000
```

**Press/news JSON handling**: For press-style JSON files with `"english"` / `"native"` language
wrappers, the converter extracts **only English articles** into the content.
All `"native"` content is skipped (`null`, `{}`, or actual text).

Optional manual conversion:

```bash
python3 scripts/conversion/convert_to_qagredo_jsonl.py \
  --input data/your-file.json \
  --output data/your-file.jsonl

# Optional semantic enrichment (keeps content/text as-is)
python3 scripts/conversion/convert_to_qagredo_jsonl.py \
  --input data/your-file.json \
  --output data/your-file.semantic.jsonl \
  --semantic-normalize \
  --semantic-max-content-chars 5000
```

Then set:
```yaml
run:
  input_file: your-file.jsonl
```

### Step 4: Run the pipeline

Important: in container runs, `run.input_file` must point to files under `data/` inside `qagredo_host/` (mounted at `/workspace/data`).

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
- Per-question comprehensiveness check (rejects trivial questions, regenerates with guidance)
- Structured answers with supporting evidence (temp=0.3)
- Per-question-slot policy: up to 3 total answer trials (`max_answer_attempts`)
  for each slot, then replacement question for that slot (bounded by
  `max_question_regeneration_rounds`)
- Final output keeps exactly `num_questions` QA pairs (default `3`)
- Coverage validation checks if each answer fully addresses the question
- One targeted rewrite pass for low-coverage answers (kept only if grounded)
- Hybrid hallucination checking: semantic first, Qwen (LLM-as-judge) fallback

Change settings in `config/config.yaml`:
```yaml
framework:
  use_langchain: true
  use_langgraph: true
  langchain:
    structured_json_output: true
  langgraph:
    enable_dynamic_routing: true
    semantic_fallback_threshold: 0.7

hallucination:
  method: "hybrid"    # or "semantic", "llm", "keyword"

question_generation:
  complexity: "advanced"    # or "basic", "moderate"

answer_generation:
  temperature: 0.3
  multi_turn:
    enable_rejection: true
    min_confidence_threshold: 0.7
    max_answer_attempts: 3
    max_regeneration_attempts: 2
    max_question_regeneration_rounds: 3
```

**To use a different model:**
```bash
export VLLM_MODEL=/models/<YourModelFolder>
export VLLM_SERVED_MODEL_NAME=<org/YourModelName>
bash run.sh
```

**On-site example (offline, external model store `/mnt/usr/models`):**
1) In `docker-compose.yml`, for `vllm` and `vllm-judge`, switch model mount:
   - comment `- ./models_llm:/models:ro`
   - uncomment `- /mnt/usr/models:/models:ro`
2) In `.env`, uncomment:

```bash
VLLM_MODEL=/models/Meta-Llama-3.3-70B-Instruct
VLLM_SERVED_MODEL_NAME=meta-llama/Meta-Llama-3.3-70B-Instruct
VLLM_TP_SIZE=2

VLLM_JUDGE_MODEL=/models/Qwen3.5-27B
VLLM_JUDGE_SERVED_NAME=Qwen/Qwen3.5-27B
VLLM_JUDGE_TP_SIZE=2
```

3) In `config/config.yaml`, set:

```yaml
llm:
  model: "meta-llama/Meta-Llama-3.3-70B-Instruct"
judge:
  model: "Qwen/Qwen3.5-27B"
```

4) GPU rule: per service, tensor-parallel size must equal GPU id count
(`device_ids`). If both services use 2 GPUs each, you usually need 4 GPUs.

**Other run.sh commands:**
```bash
bash run.sh --down      # stop all three containers
bash run.sh --logs      # tail vLLM logs (generator + judge)
bash run.sh --status    # show container status
bash run.sh -- --input-file data/run.jsonl --num-documents 5
bash run.sh --convert data/report.pdf data/report.jsonl
```

### Step 4a: Select output mode (full vs minimal)

Edit `config/config.yaml` under `run:`:

```yaml
# Full output (default)
save_grounded_qa_pairs_only: false
minimal_qa_output: false
```

```yaml
# Full schema, grounded rows only
save_grounded_qa_pairs_only: true
minimal_qa_output: false
```

```yaml
# Minimal output
save_grounded_qa_pairs_only: false   # or true
minimal_qa_output: true
```

Minimal output JSON shape:

```json
{
  "document": {"content": "..."},
  "qa_pairs": [{"question": "...", "answer": "..."}]
}
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
- Run-level timing metrics (question/answer/grading totals + averages)
- Quality counters (question retries, answer retries, coverage rewrites)
- **For ungrounded answers**: specific reasons, ungrounded sentences, and
  LLM judge verdict with explanation
- **Ungrounded highlights**: flat list of all failed QA pairs for quick scanning

### Step 4 (alternative): Start Jupyter Lab

Port configuration is centralized in `.env` (`VLLM_HOST_PORT`, `VLLM_JUDGE_HOST_PORT`, `JUPYTER_PORT`).
Change ports there only.

```bash
bash jupyter.sh
```

If running on a **remote** offline server, create an SSH tunnel from your local machine:

```bash
# Run this on your LOCAL machine (not the offline server):
source .env
ssh -L ${JUPYTER_PORT}:localhost:${JUPYTER_PORT} user@offline-server
```

Then open `http://localhost:${JUPYTER_PORT}` in your browser (no token/password required).

**Options:**
```bash
bash jupyter.sh --no-vllm   # Jupyter only, no GPU
bash jupyter.sh --down       # stop all containers
```

### Step 4c (optional): Verify service endpoints quickly

```bash
source .env
curl -sf "http://localhost:${VLLM_HOST_PORT}/health" && echo " generator OK"
curl -sf "http://localhost:${VLLM_JUDGE_HOST_PORT}/health" && echo " judge OK"
```

---

## Day-to-day workflow (after first deployment)

Once files 1-4 are on the server, your typical workflow is:

### Updating code/config (on dev machine):

1. Edit code/config in the repo, then:
   ```bash
   bash scripts/make_qagredo_bundle.sh --include-data
   mv -f qagredo_bundle.tar.gz qagredo_bundle.tar.gz.sha256 /data/tyewhong/qagredo/
   ```

2. Transfer just `qagredo_bundle.tar.gz` (~few MB) to the offline server

3. On the offline server:
   ```bash
   cd /home/tyewhong/qagredo_staging
   tar xzf qagredo_bundle.tar.gz
   cd qagredo_host
   bash setup_offline.sh --skip-images   # re-link models, skip docker load
   bash run.sh
   ```

### Editing directly on the offline server (no transfer needed):

Since everything is in `qagredo_host/` and Docker mounts it directly,
you can edit files on the offline server and re-run immediately:

```bash
cd /home/tyewhong/qagredo_staging/qagredo_host

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

### Fast decision tree (symptom -> likely cause -> first action)

| Symptom | Likely cause | First action |
|---|---|---|
| `setup_offline.sh` cannot find files | staging paths or filenames mismatch | run with explicit env overrides (`VLLM_ROOTFS_TAR`, `QAGREDO_TAR`, `MODELS_*`) |
| vLLM health timeout | model path, GPU allocation, or CUDA mismatch | inspect `docker logs qagredo-vllm --tail 100` and `qagredo-vllm-judge` |
| `401 Unauthorized` or model-not-found | API key or served-model-name mismatch | verify `.env`, `config/config.yaml`, and `curl /v1/models` |
| run starts but output missing | wrong slice or empty input | check `run.input_file`, `num_documents` (0 = all), JSONL record count, prior conversion |
| `grading_summary` fields are null | grading path failed for that run | inspect log for `Could not grade`, deploy latest bundle, then rerun pipeline once |
| permission denied under `output/` or caches | host/container ownership drift | run `bash setup_offline.sh --force` |

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

If startup is slow on first boot (cold cache/model load), increase the health
wait timeout before running:

```bash
HEALTH_TIMEOUT=900 bash run.sh
```

`run.sh` defaults to 600 seconds and checks `http://localhost:7100/health` and
`http://localhost:7101/health` in a loop.

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
