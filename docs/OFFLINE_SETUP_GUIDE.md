# QAGRedo Offline Setup Guide (Current)

This is the current, profile-based offline deployment guide.

If anything here conflicts with older notes, follow this file. New owners should read **`docs/HANDOVER.md`** first.

---

## 1) Pick your runtime profile first

QAGRedo supports three profiles:

- `dev` - Host Ollama (requires `ollama` installed on offline host)
- `kubeflow` - In-container Ollama (no host Ollama binary required)
- `vllm` - Dual vLLM containers (generator + judge)

For the greenserver, Opserver, and redserver mapping, see
`docs/SERVER_MODEL_PROFILES.md`.

Set in `.env`:

```bash
QAGREDO_PROFILE=dev        # or kubeflow / vllm
```

---

## 2) Which archives to copy offline

Copy only the archives needed for your selected profile.

| Profile | Required archives |
|---|---|
| `dev` | `qagredo_bundle.tar.gz`, `qagredo-v1.tar`, and Ollama store (`models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`) |
| `kubeflow` | `qagredo_bundle.tar.gz`, `qagredo-kubeflow.tar`, and Ollama store (`models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`) |
| `vllm` | `qagredo_bundle.tar.gz`, `qagredo-v1.tar`, `vllm-openai_*.rootfs.tar`, `models_vllm.tar.gz` |

Each archive should have matching `.sha256`.

---

## 3) Build archives on online machine

Use one command:

```bash
# all artifacts
bash scripts/make_offline_tarballs.sh --all

# dev ollama package set
bash scripts/make_offline_tarballs.sh --bundle --image-dev --models-ollama

# dev ollama split model files (recommended for <40G file limits)
bash scripts/make_offline_tarballs.sh --bundle --image-dev \
  --models-ollama-split=qwen3.5:9b,llama3.1:8b-instruct-fp16

# kubeflow ollama package set
bash scripts/make_offline_tarballs.sh --bundle --image-kubeflow --models-ollama

# vllm package set
bash scripts/make_offline_tarballs.sh --bundle --image-dev --image-vllm --models-vllm
```

---

## 4) Extract on offline host

Example staging:

```bash
mkdir -p /home/tyewhong/qagredo_new
tar xzf qagredo_bundle.tar.gz -C /home/tyewhong/qagredo_new
cd /home/tyewhong/qagredo_new/qagredo_host
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

### A) `dev` (host Ollama)

Requirements:

- host has `ollama` command installed
- Ollama API reachable on `127.0.0.1:11434`

Set profile:

```bash
QAGREDO_PROFILE=dev
```

Set model tags in `config/config.dev.yaml`:

```yaml
llm:
  model: "qwen3.5:9b"
judge:
  model: "llama3.1:8b-instruct-fp16"
```

### B) `kubeflow` (in-container Ollama)

Requirements:

- `qagredo-kubeflow.tar` loaded on host
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

### C) `vllm`

Requirements:

- `models_vllm.tar.gz` extracted as HF model directories
- vLLM image tar loaded

Set profile and model root in `.env`:

```bash
QAGREDO_PROFILE=vllm
QAGREDO_MODELS_LLM_HOST=/data/models
```

Set model paths in `.env`:

```bash
VLLM_MODEL=/models/Qwen2.5-7B-Instruct
VLLM_SERVED_MODEL_NAME=Qwen/Qwen2.5-7B-Instruct
VLLM_JUDGE_MODEL=/models/Meta-Llama-3.1-8B-Instruct
VLLM_JUDGE_SERVED_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct
```

Set served names and URLs in `config/config.vllm.yaml`:

```yaml
llm:
  model: "Qwen/Qwen2.5-7B-Instruct"
  base_url: "http://vllm:7100/v1"
judge:
  model: "meta-llama/Meta-Llama-3.1-8B-Instruct"
  base_url: "http://vllm-judge:7101/v1"
```

Do not use `localhost` in `config.vllm.yaml` base URLs.

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

## 8) Run setup + first run

From `qagredo_host`:

```bash
bash setup_offline.sh --skip-images --force --profile <dev|kubeflow|vllm>
bash run.sh --show-config
bash run.sh --num-documents 2
```

Minimal output (only `document.content` + `qa_pairs.question/answer`):

```bash
bash run.sh -- --minimal-qa-output
```

Convert existing full outputs to minimal JSON (no LLM re-run):

```bash
python3 scripts/utils/export_analysis_minimal.py "/path/to/output/<provider>/<model>/<run_timestamp>"
```

---

## 9) Common errors and exact meaning

### `ollama: command not found`

Host Ollama not installed. `dev` profile cannot run.

- use `kubeflow` profile with `qagredo-kubeflow.tar`, or
- install host Ollama.

### `Ollama not reachable on port 11434 within 300s`

`dev` profile is active but host Ollama API unavailable.

### `[Fail] kubeflow image present`

`qagredo-kubeflow.tar` not loaded, or wrong image tag.

### `[Fail] ollama store present at ./models`

For `kubeflow` check, setup expects valid store path/symlink with `blobs` + `manifests`.

### vLLM `Connection error` with healthy host ports

Usually `config/config.vllm.yaml` uses `localhost` URLs instead of service names.

Use:

- `http://vllm:7100/v1`
- `http://vllm-judge:7101/v1`

### `model type qwen3 not recognized` in vLLM

Your vLLM image/transformers stack is too old for Qwen3.
Use Qwen2.5 for vLLM, or run Qwen3 via Ollama.

---

## 10) Switching profiles later (quick checklist)

1. Change `QAGREDO_PROFILE` in `.env`.
2. Ensure required image tar for that profile is loaded.
3. Ensure correct model format exists for that profile.
4. Edit only matching config file:
   - `config.dev.yaml` / `config.kubeflow.yaml` / `config.vllm.yaml`
5. `bash run.sh --show-config` then `bash run.sh`.

