# QAGRedo Offline Setup Guide (Current)

This is the current, profile-based offline deployment guide.

If anything here conflicts with older notes, follow this file. New owners should read **`docs/HANDOVER.md`** first.

---

## 1) Pick your runtime profile first

QAGRedo supports three profiles (default in this guide: `vllm`):

- `vllm` - Dual vLLM containers (generator + judge)
- `ollama` - Host Ollama (requires `ollama` installed on offline host)
- `kubeflow` - In-container Ollama (no host Ollama binary required)

For the greenserver, Opserver, and siteserver mapping, see
`docs/SERVER_MODEL_PROFILES.md`.

Set in `.env`:

```bash
QAGREDO_PROFILE=vllm       # or ollama / kubeflow
```

---

## 2) Which archives to copy offline

**Build output directory (online host):** `/data/tyewhong/qagredo/` (`QAGREDO_ARCHIVE_DIR` / `QAGREDO_OFFLINE_OUT` in `.env`). Copy `.tar` / `.tar.gz` and matching `.sha256` from there to the offline server (e.g. siteserver) — do not rely on `offline_out/` or the home repo for large archives.

Copy only the archives needed for your selected profile.

| Profile | Required archives |
|---|---|
| `ollama` | `qagredo_bundle.tar.gz`, `qagredo-v1.tar`, and Ollama store (`models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`) |
| `kubeflow` | `qagredo_bundle.tar.gz`, `qagredo-kubeflow.tar`, and Ollama store (`models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`) |
| `vllm` | `qagredo_bundle.tar.gz`, `qagredo-v1.tar`, `vllm-qwen35-localcuda.rootfs.tar`, and HF weights: **either** `models_vllm.tar.gz` **or** split pair e.g. `models_vllm_Qwen3_5-9B.tar.gz` + `models_llama.tar.gz` (§4.7) |

Each archive should have matching `.sha256`.

---

## 3) Build archives on online machine

Use one command:

```bash
# all artifacts
bash scripts/make_offline_tarballs.sh --all

# ollama package set (--image-dev = runner image for ollama profile)
bash scripts/make_offline_tarballs.sh --bundle --image-dev --models-ollama

# ollama split model files (recommended for <40G file limits)
bash scripts/make_offline_tarballs.sh --bundle --image-dev \
  --models-ollama-split=qwen3.5:9b,llama3.1:8b-instruct-fp16

# kubeflow ollama package set
bash scripts/make_offline_tarballs.sh --bundle --image-kubeflow --models-ollama

# vllm package set
bash scripts/make_offline_tarballs.sh --bundle --image-dev --image-vllm --models-vllm
```

Outputs land in `/data/tyewhong/qagredo/` (list with `ls -lh /data/tyewhong/qagredo/`).

---

## 4) Install archives on the offline server

This section explains **how to install** every `.tar` and `.tar.gz` after you copy them to the offline host (e.g. siteserver). Staging directory used below: **`/data/tyewhong/qagredo/`** (same as the online build host).

### 4.1 Transfer archives to the offline host

Copy only the files for your profile (§2) plus matching `.sha256` files. Example:

```bash
# from online build host
rsync -avP /data/tyewhong/qagredo/qagredo_bundle.tar.gz \
           /data/tyewhong/qagredo/qagredo_bundle.tar.gz.sha256 \
           <user>@<offline-host>:/data/tyewhong/qagredo/

# vllm example — add the other archives you need
rsync -avP /data/tyewhong/qagredo/qagredo-v1.tar \
           /data/tyewhong/qagredo/vllm-qwen35-localcuda.rootfs.tar \
           /data/tyewhong/qagredo/models_vllm.tar.gz \
           <user>@<offline-host>:/data/tyewhong/qagredo/
```

Use `scp` or USB if `rsync` is not available. Large files (10–90 GB) take time; resume with `rsync -P`.

### 4.2 Verify checksums (before install)

On the **offline** host:

```bash
cd /data/tyewhong/qagredo
sha256sum -c qagredo_bundle.tar.gz.sha256
sha256sum -c qagredo-v1.tar.sha256              # if present
sha256sum -c vllm-qwen35-localcuda.rootfs.tar.sha256   # vllm profile
sha256sum -c models_vllm.tar.gz.sha256          # if you copied the combined file
sha256sum -c models_vllm_Qwen3_5-9B.tar.gz.sha256   # if you copied split archives only
# repeat for each archive you copied
```

Fix or re-copy any file that fails verification before continuing.

### 4.3 What each archive does

| Archive | Type | Install action | Result on disk |
|---------|------|----------------|----------------|
| `qagredo_bundle.tar.gz` | gzip tarball | `tar xzf` → target directory | `qagredo_host/` (code, compose, configs, `setup_offline.sh`) |
| `qagredo-v1.tar` | Docker save | `docker load -i` | Image `qagredo-v1:latest` (pipeline runner; **ollama** and **vllm**) |
| `qagredo-kubeflow.tar` | Docker save | `docker load -i` | Image `qagredo-kubeflow:latest` (**kubeflow** only) |
| `vllm-qwen35-localcuda.rootfs.tar` | Docker save | `docker load -i` | Image `qagredo-vllm:qwen35-localcuda` (**vllm**; symlink to `qagredo-vllm_qwen35-localcuda.rootfs.tar` is OK) |
| `models_ollama.tar.gz` | gzip tarball | `tar xzf` → model store dir | Ollama `blobs/` + `manifests/` (**ollama** / **kubeflow**) |
| `models_ollama_<tag>.tar.gz` | gzip tarball (split) | `tar xzf` per file (see §7) | Same as combined Ollama store |
| `models_vllm.tar.gz` | gzip tarball | `tar xzf` → HF model root | Folders like `Qwen3.5-9B/`, `Meta-Llama-3.1-8B-Instruct/` (**vllm**) |
| `models_vllm_<name>.tar.gz` | gzip tarball (split) | `tar xzf` each into same root | One HF folder (e.g. `Qwen3.5-9B`) |
| `models_llama.tar.gz` | gzip tarball (legacy split name) | `tar xzf` into same `/data/models` root | Usually **`Meta-Llama-3.1-8B-Instruct`** (vLLM judge) |

**Install order:** (1) verify checksums → (2) extract **bundle** → (3) `docker load` **images** → (4) extract **model** archives → (5) run `setup_offline.sh` (optional but recommended).

### 4.4 Step-by-step install (manual)

#### A) Extract the code bundle (all profiles)

Creates the runnable tree `qagredo_host/`:

```bash
mkdir -p /home/tyewhong/qagredo
tar xzf /data/tyewhong/qagredo/qagredo_bundle.tar.gz -C /home/tyewhong/qagredo
cd /home/tyewhong/qagredo/qagredo_host
```

(`qagredo_bundle.tar.gz` unpacks to `qagredo_host/` inside the `-C` directory.)

#### B) Load Docker images (`.tar` files)

Run on the offline host (requires Docker). Load only what your profile needs (§2).

```bash
ARCH=/data/tyewhong/qagredo

# ollama or vllm — pipeline runner container
docker load -i "$ARCH/qagredo-v1.tar"
docker images | grep qagredo-v1

# kubeflow only — all-in-one image with in-container Ollama
docker load -i "$ARCH/qagredo-kubeflow.tar"
docker images | grep qagredo-kubeflow

# vllm only — Qwen3.5-compatible vLLM runtime (generator + judge use this image)
docker load -i "$ARCH/vllm-qwen35-localcuda.rootfs.tar"
# same file if you only have the long name:
# docker load -i "$ARCH/qagredo-vllm_qwen35-localcuda.rootfs.tar"
docker images | grep qagredo-vllm
```

`docker load` is idempotent: safe to re-run; already-loaded layers are skipped.

#### C) Extract model stores (`.tar.gz` files)

**Ollama store (ollama / kubeflow)** — either use `setup_offline.sh` (§4.5), which extracts into `qagredo_host/models/`, or install under a shared path:

```bash
mkdir -p /data/models/models_ollama
tar xzf /data/tyewhong/qagredo/models_ollama.tar.gz -C /data/models/models_ollama --strip-components=1
ls -ld /data/models/models_ollama/blobs /data/models/models_ollama/manifests
```

For **split** Ollama archives, see §7.

**vLLM HuggingFace trees (vllm)** — extract so model dirs match `.env` paths (`VLLM_MODEL=/models/Qwen3.5-9B` means host folder `Qwen3.5-9B` under `QAGREDO_MODELS_LLM_HOST`).

*Combined archive (both generator + judge in one file):*

```bash
mkdir -p /data/models
tar xzf /data/tyewhong/qagredo/models_vllm.tar.gz -C /data/models
ls /data/models
# expect: Qwen3.5-9B  Meta-Llama-3.1-8B-Instruct
```

*Split archives (generator + judge as separate files):* see **§4.7** for the full copy-paste block (`ARCH=…`, both `tar xzf`, both `ls …/config.json`). Typical pair: `models_vllm_Qwen3_5-9B.tar.gz` + `models_llama.tar.gz`.

*Single split file only (generator):*

```bash
mkdir -p /data/models
tar xzf /data/tyewhong/qagredo/models_vllm_Qwen3_5-9B.tar.gz -C /data/models
ls /data/models/Qwen3.5-9B/config.json
```

Add judge archives into the **same** `/data/models` root (do not use `--strip-components`).

`setup_offline.sh` also accepts split files: place `models_vllm_*.tar.gz` next to `qagredo_host` (or under `/data/tyewhong/qagredo/`) and run `bash setup_offline.sh --profile vllm` — it extracts each split into `qagredo_host/models_llm/`.

If you extract with `setup_offline.sh` instead of `/data/models`, models land in `qagredo_host/models_llm/` — then set in `.env`:

```bash
QAGREDO_MODELS_LLM_HOST=/home/tyewhong/qagredo/qagredo_host/models_llm
```

(or symlink `/data/models` → that directory).

**ollama profile without `models_ollama.tar.gz`:** skip C; ensure host Ollama already has the tags from `config/config.ollama.yaml` (`ollama list`).

### 4.5 Automated install (`setup_offline.sh`)

After the bundle is extracted (§4.4 A), run the bundled installer from `qagredo_host`. It discovers archives in the **parent** of `qagredo_host` or next to it (e.g. `/data/tyewhong/qagredo/`), then loads images and extracts models.

**First-time install** (loads Docker images + unpacks models):

```bash
cd /home/tyewhong/qagredo/qagredo_host

# optional: copy or symlink archives next to qagredo_host so discovery finds them
# ln -sf /data/tyewhong/qagredo/*.tar* /home/tyewhong/qagredo/

bash setup_offline.sh --profile vllm    # or ollama | kubeflow
```

Use `--force` to re-extract models over an existing store. Use `--skip-images` only when images are **already** loaded and you only need model unpack / permission fix.

What `setup_offline.sh` does:

| Phase | Action |
|-------|--------|
| 1 | Find `qagredo_bundle.tar.gz`, `*.tar`, `models_*.tar.gz` near `qagredo_host` |
| 2 | `docker load -i` for profile-specific images |
| 3 | Extract Ollama or vLLM model archives into `qagredo_host/models/` or `qagredo_host/models_llm/` |
| 4 | Fix ownership on `output/`, `data/`, caches |
| 5 | Create starter `.env` if missing |
| 6 | Smoke tests (images present, model dirs, compose files) |

### 4.6 Profile install checklists

**`ollama`**

1. `tar xzf` `qagredo_bundle.tar.gz` → `qagredo_host/`
2. `docker load -i qagredo-v1.tar`
3. Extract `models_ollama.tar.gz` **or** use host Ollama with tags already pulled
4. `bash setup_offline.sh --profile ollama`
5. Edit `.env`: `QAGREDO_PROFILE=ollama`, data path (§5)

**`kubeflow`**

1. `tar xzf` `qagredo_bundle.tar.gz`
2. `docker load -i qagredo-kubeflow.tar`
3. Extract Ollama store → set `QAGREDO_MODELS_DIR` (§6 B)
4. `bash setup_offline.sh --profile kubeflow`
5. Edit `.env`: `QAGREDO_PROFILE=kubeflow`

**`vllm`** (siteserver — see also `docs/Siteserver_vLLM_Change_Guide.md`)

1. `tar xzf` `qagredo_bundle.tar.gz`
2. `docker load -i qagredo-v1.tar`
3. `docker load -i vllm-qwen35-localcuda.rootfs.tar`
4. Extract model archive(s) → `/data/models` — **if you bring `models_vllm_Qwen3_5-9B.tar.gz` + `models_llama.tar.gz`, use the exact block in §4.7**
5. `bash setup_offline.sh --profile vllm` (optional if you installed manually in steps 2–4)
6. Edit `.env`: `QAGREDO_PROFILE=vllm`, `VLLM_IMAGE=qagredo-vllm:qwen35-localcuda`, model paths (§6 C)

### 4.7 vLLM models without `models_vllm.tar.gz` (split + `models_llama.tar.gz`)

Use this when you ship **separate** archives instead of one combined `models_vllm.tar.gz`.

| Archive you bring | Typical HF folder after extract | vLLM role (default `.env`) |
|-------------------|----------------------------------|----------------------------|
| `models_vllm_Qwen3_5-9B.tar.gz` | `Qwen3.5-9B/` | Generator (GPU 0, port 7100) |
| `models_llama.tar.gz` | `Meta-Llama-3.1-8B-Instruct/` | Judge (GPU 1, port 7101) |
| `models_vllm_Meta-Llama-3_1-8B-Instruct.tar.gz` | same judge folder | Same as `models_llama.tar.gz` (newer naming from `make_offline_tarballs.sh`) |

**Your case (Qwen split + `models_llama.tar.gz`) — install both into the same root:**

```bash
ARCH=/data/tyewhong/qagredo
mkdir -p /data/models

# Generator — Qwen3.5
tar xzf "$ARCH/models_vllm_Qwen3_5-9B.tar.gz" -C /data/models

# Judge — Llama 3.1 (legacy archive name; same layout as vLLM split)
tar xzf "$ARCH/models_llama.tar.gz" -C /data/models

# Both must exist before starting vLLM
ls /data/models/Qwen3.5-9B/config.json
ls /data/models/Meta-Llama-3.1-8B-Instruct/config.json
```

Extract order does not matter. Do **not** use `--strip-components` unless you know the tarball has an extra top-level wrapper (these archives usually unpack directly to `Qwen3.5-9B/` and `Meta-Llama-3.1-8B-Instruct/` under `/data/models`).

**`.env` (default stack — no change needed if paths match):**

```bash
QAGREDO_MODELS_LLM_HOST=/data/models
VLLM_MODEL=/models/Qwen3.5-9B
VLLM_SERVED_MODEL_NAME=Qwen/Qwen3.5-9B
VLLM_JUDGE_MODEL=/models/Meta-Llama-3.1-8B-Instruct
VLLM_JUDGE_SERVED_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct
```

If `ls` after extract shows a **different** folder name inside `models_llama.tar.gz`, update `VLLM_JUDGE_MODEL` and `judge.model` in `config/config.vllm.yaml` to match that directory.

**Other split naming (`make_offline_tarballs.sh`):**

| Source folder | Archive name |
|---------------|--------------|
| `Qwen3.5-9B` | `models_vllm_Qwen3_5-9B.tar.gz` |
| `Meta-Llama-3.1-8B-Instruct` | `models_vllm_Meta-Llama-3_1-8B-Instruct.tar.gz` |

```bash
# online build — either legacy pair or script split:
tar czf /data/tyewhong/qagredo/models_llama.tar.gz -C models_llm Meta-Llama-3.1-8B-Instruct
bash scripts/make_offline_tarballs.sh --models-vllm-split=Qwen3.5-9B,Meta-Llama-3.1-8B-Instruct
```

---

## 5) Configure data path (your environment)

Your offline data paths:

- text: `/data/local/tyewhong/Data/txt`
- json: `/data/local/tyewhong/Data/json`

Use preset mode in `.env`:

```bash
QAGREDO_OFFLINE_HOST=data
QAGREDO_OFFLINE_INPUT=txt          # or json
QAGREDO_SHARED_DATA_ROOT=/data/local/tyewhong/Data
```

Or use direct mode:

```bash
# comment out QAGREDO_OFFLINE_HOST / QAGREDO_OFFLINE_INPUT
QAGREDO_DATA_DIR=/data/local/tyewhong/Data/txt
```

---

## 6) Configure models by profile

After §4 installs images and model files, tune **names and paths** here (not the install commands).

### A) `ollama` (host Ollama)

Requirements (from §4):

- `qagredo-v1.tar` loaded
- host has `ollama` command installed
- Ollama API reachable on `127.0.0.1:11434`
- model tags available (from `models_ollama.tar.gz` or existing host store)

Set profile:

```bash
QAGREDO_PROFILE=ollama
```

Set model tags in `config/config.ollama.yaml`:

```yaml
llm:
  model: "qwen3.5:9b"
judge:
  model: "llama3.1:8b-instruct-fp16"
```

### B) `kubeflow` (in-container Ollama)

Requirements (from §4):

- `qagredo-kubeflow.tar` loaded on host
- Ollama store extracted (§4.4 C or `setup_offline.sh`)
- `QAGREDO_MODELS_DIR` points to Ollama store path containing `blobs/` and `manifests/`

Set profile and model store path in `.env`:

```bash
QAGREDO_PROFILE=kubeflow
QAGREDO_MODELS_DIR=/data/models/models_ollama
```

Set tags in `config/config.kubeflow.yaml`:

```yaml
llm:
  model: "qwen3.5:9b"
judge:
  model: "llama3.1:8b-instruct-fp16"
```

### C) `vllm` (default profile)

Requirements (from §4):

- `qagredo-v1.tar` and `vllm-qwen35-localcuda.rootfs.tar` loaded
- HuggingFace weights under `QAGREDO_MODELS_LLM_HOST` (default `/data/models`): from `models_vllm.tar.gz` **or** split `models_vllm_*.tar.gz` (§4.7)
- **Generator** folder `Qwen3.5-9B` and **judge** folder `Meta-Llama-3.1-8B-Instruct` unless you changed judge settings

Set profile and model root in `.env`:

```bash
QAGREDO_PROFILE=vllm
QAGREDO_MODELS_LLM_HOST=/data/models
```

Set model paths in `.env`:

```bash
VLLM_MODEL=/models/Qwen3.5-9B
VLLM_SERVED_MODEL_NAME=Qwen/Qwen3.5-9B
VLLM_JUDGE_MODEL=/models/Meta-Llama-3.1-8B-Instruct
VLLM_JUDGE_SERVED_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct
# Qwen3.5 requires the upgraded vLLM image (not v0.5.3.post1):
VLLM_IMAGE=qagredo-vllm:qwen35-localcuda
# Build online: bash scripts/docker_build_vllm_qwen35_compat.sh
# Install on offline host: see §4.4 B and §4.6 (vllm checklist)
```

Set served names and URLs in `config/config.vllm.yaml`:

```yaml
llm:
  model: "Qwen/Qwen3.5-9B"
  base_url: "http://vllm:7100/v1"
judge:
  model: "meta-llama/Meta-Llama-3.1-8B-Instruct"
  base_url: "http://vllm-judge:7101/v1"
```

Do not use `localhost` in `config.vllm.yaml` base URLs.

#### Split vLLM startup (dual GPU)

Instead of one `bash run.sh` that starts both vLLM containers and the pipeline,
you can run three steps (generator on GPU 0, judge on GPU 1, then pipeline only):

```bash
bash run.sh --vllm-up generator
bash run.sh --vllm-up judge
bash run.sh --pipeline-only --num-documents 1
```

Manual compose equivalent (from `qagredo_host`):

```bash
docker compose -f docker-compose.vllm-stack.yml up -d vllm
curl -sf http://localhost:7100/health
docker compose -f docker-compose.vllm-stack.yml up -d vllm-judge
curl -sf http://localhost:7101/health
docker compose -f docker-compose.vllm-stack.yml run --rm --no-deps qagredo \
  python /workspace/run_qa_pipeline.py --config /workspace/config/config.vllm.yaml
```

`bash run.sh` (no flags) still starts both vLLM services and runs the pipeline in one command.

---

## 7) Handle Ollama split tar extraction correctly

If split files include top-level `models/`, extracting inside `/data/models` creates `/data/models/models/...`.

Preferred extraction:

```bash
mkdir -p /data/models/models_ollama
tar xzf models_ollama_qwen3.5_9b.tar.gz -C /data/models/models_ollama --strip-components=1
tar xzf models_ollama_llama3.1_8b-instruct-fp16.tar.gz -C /data/models/models_ollama --strip-components=1
```

Verify:

```bash
ls -ld /data/models/models_ollama/blobs /data/models/models_ollama/manifests
```

---

## 8) First run

From `qagredo_host` (after §4 install and §5–6 config):

```bash
# If you have NOT run setup_offline.sh yet, run it once (loads images + models):
# bash setup_offline.sh --profile <ollama|kubeflow|vllm>

# If images are already loaded and you only re-extracted models:
# bash setup_offline.sh --skip-images --force --profile <ollama|kubeflow|vllm>

bash run.sh --show-config
```

**`ollama` / `kubeflow`:**

```bash
bash run.sh -- --num-documents 2
```

**`vllm`** — split (recommended) or all-in-one:

```bash
# Split: GPU 0 generator, GPU 1 judge, then pipeline only
bash run.sh --vllm-up generator
bash run.sh --vllm-up judge
bash run.sh --pipeline-only --num-documents 2

# All-in-one: starts both vLLM + pipeline
# bash run.sh -- --num-documents 2
```

**Resume / skip documents that already have output** (any profile; vLLM example):

```bash
# Reuse latest run folder under output/<provider>/<model>/; skip existing *_analysis.json
bash run.sh --pipeline-only -- --resume

# Skip only (new timestamp folder for newly processed docs)
bash run.sh --pipeline-only -- --skip-existing-outputs

# Pin a specific prior run folder (name or path)
bash run.sh --pipeline-only -- --resume --resume-run-dir 2026-05-26_093000
```

YAML (`config/config.<profile>.yaml` → `run:`): `skip_existing_outputs`, `resume`, `resume_run_dir`.

For `kubeflow` profile, `run.sh` reuses the loaded `qagredo-kubeflow` image (no default rebuild) and keeps in-container Ollama warm until:

```bash
bash run.sh --down
```

Minimal output during a run (only `document.content` + `qa_pairs.question/answer`):

```bash
bash run.sh -- --minimal-qa-output
```

**Post-run** — `--minimise` converts existing full `*_analysis.json` to `*_analysis_minimal.json` and writes per-doc good/bad split files (no LLM/vLLM rerun; works with `vllm` after `--pipeline-only` or a full `bash run.sh`):

```bash
bash run.sh --minimise
# or:
bash run.sh --minimise "output/vllm/qwen-qwen3.5-9b/2026-05-21_171511"
# or path under output/<provider>/<model>/<run_timestamp>/
```

Split-only commands (optional, when you only want one side):

```bash
bash run.sh --minimise-good
bash run.sh --minimise-bad
# or target a specific run folder:
bash run.sh --minimise-good "output/vllm/qwen-qwen3.5-9b/2026-05-21_171511"
bash run.sh --minimise-bad  "output/vllm/qwen-qwen3.5-9b/2026-05-21_171511"
```

These commands write per document:
- `*_analysis_minimal.json`
- `*_analysis_minimal_good_pairs.json`
- `*_analysis_minimal_bad_pairs.json`

**Resume tip:** `--num-documents N` counts records from the start of the sorted input list (including skips). Use a large `N` (or `0` for all) to reach unprocessed docs after many short-file or already-done skips.

**Summarise a run:**

```bash
bash run.sh --summarize --latest --json
# or: bash run.sh --summarize "output/vllm/qwen-qwen3.5-9b/<run_timestamp>"
```

## 9) Common errors and exact meaning

### `ollama: command not found`

Host Ollama not installed. `ollama` profile cannot run.

- use `kubeflow` profile with `qagredo-kubeflow.tar`, or
- install host Ollama.

### `Ollama not reachable on port 11434 within 300s`

`ollama` profile is active but host Ollama API unavailable.

### `[Fail] kubeflow image present`

`qagredo-kubeflow.tar` not loaded, or wrong image tag.

### `[Fail] ollama store present at ./models`

For `kubeflow` check, setup expects valid store path/symlink with `blobs` + `manifests`.

### vLLM `Connection error` with healthy host ports

Usually `config/config.vllm.yaml` uses `localhost` URLs instead of service names.

Use:

- `http://vllm:7100/v1`
- `http://vllm-judge:7101/v1`

### `model type qwen3_5 not recognized` in vLLM

Your vLLM image is too old for Qwen3.5. Set `VLLM_IMAGE=qagredo-vllm:qwen35-localcuda`
(`scripts/docker_build_vllm_qwen35_compat.sh`), or use Ollama for Qwen3.5, or Qwen2.5 + `v0.5.3.post1`.

---

## 10) Switching profiles later (quick checklist)

1. Change `QAGREDO_PROFILE` in `.env`.
2. Ensure required archives for that profile are installed (§4.6).
3. Ensure correct model format exists for that profile.
4. Edit only matching config file:
   - `config.ollama.yaml` / `config.kubeflow.yaml` / `config.vllm.yaml`
5. `bash run.sh --show-config`, then run (vllm: Part **8** split or all-in-one; ollama/kubeflow: `bash run.sh`).

