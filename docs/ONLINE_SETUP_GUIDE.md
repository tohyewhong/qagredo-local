# QAGRedo Online Setup Guide

Use this guide on your connected/build machine to:

1. Run locally for validation.
2. Build offline transfer archives.

Maintainer index: **`docs/HANDOVER.md`**.

---

## 1) Local run (quick validation)

From repo root:

```bash
bash run.sh --status
bash run.sh -- --num-documents 2
bash run.sh --minimise
```

Edit only:

- `.env` (profile + host paths)
- `config/config.<profile>.yaml` (model tags, question/doc counts)

---

## 2) Profile model rules

### `ollama` / `kubeflow` (Ollama)

- `llm.model` and `judge.model` are Ollama tags.
- Model storage format is Ollama store (`blobs/` + `manifests/`).

### `vllm`

- Model files are HuggingFace directories (with `config.json`, safetensors).
- `config/config.vllm.yaml` model names must match:
  - `VLLM_SERVED_MODEL_NAME`
  - `VLLM_JUDGE_SERVED_NAME`

Local validation (after `QAGREDO_PROFILE=vllm` in `.env`):

```bash
bash run.sh --status
bash run.sh --vllm-up generator
bash run.sh --vllm-up judge
bash run.sh --pipeline-only --num-documents 1
# or all-in-one: bash run.sh -- --num-documents 1
```

---

## 3) Build offline tarballs

Use the unified script:

```bash
# all artifacts
bash scripts/make_offline_tarballs.sh --all

# ollama (--image-dev = runner image for ollama profile)
bash scripts/make_offline_tarballs.sh --bundle --image-dev --models-ollama

# kubeflow
bash scripts/make_offline_tarballs.sh --bundle --image-kubeflow --models-ollama

# vllm (combined models)
bash scripts/make_offline_tarballs.sh --bundle --image-dev --image-vllm --models-vllm

# vllm (per-model split — e.g. ship only Qwen3.5-9B if judge HF tree exists on target)
bash scripts/make_offline_tarballs.sh --models-vllm-split=Qwen3.5-9B
```

For large-file limits, split Ollama model tarballs:

```bash
bash scripts/make_offline_tarballs.sh \
  --models-ollama-split=qwen3.5:9b,llama3.1:8b-instruct-fp16
```

---

## 4) Output location

Default output directory:

- `/data/tyewhong/qagredo/` (all `.tar` / `.tar.gz`; see `.cursor/rules/archive-output-location.mdc`)

Override:

```bash
QAGREDO_OFFLINE_OUT=/path/to/output bash scripts/make_offline_tarballs.sh --bundle
```

Generated files include matching `.sha256`. Large transfer archives are often staged under **`/data/tyewhong/qagredo/`** (keep home repo free of huge tarballs).

---

## 5) Recommended archive sets by target runtime

### Offline `ollama`

- `qagredo_bundle.tar.gz`
- `qagredo-v1.tar`
- `models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`

### Offline `kubeflow`

- `qagredo_bundle.tar.gz`
- `qagredo-kubeflow.tar`
- `models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`

### Offline `vllm`

- `qagredo_bundle.tar.gz`
- `qagredo-v1.tar`
- `vllm-qwen35-localcuda.rootfs.tar` (from `scripts/save_vllm_qwen35_image.sh`; default `VLLM_IMAGE=qagredo-vllm:qwen35-localcuda`)
- `models_vllm.tar.gz`

---

## 6) Sanity checks before transfer

```bash
# ensure bundle exists
ls -lh /data/tyewhong/qagredo/qagredo_bundle.tar.gz

# verify checksums
cd /data/tyewhong/qagredo
sha256sum -c *.sha256
```

---

## 7) Important compatibility note

If Qwen3.5 fails on vLLM (for example `qwen3_5` not recognized), you are still on an old runtime image. Fix:

- build and set `VLLM_IMAGE=qagredo-vllm:qwen35-localcuda` (`bash scripts/docker_build_vllm_qwen35_compat.sh`), or
- use `ollama`/`kubeflow` with Ollama for Qwen3.5 GGUF, or
- use Qwen2.5-7B-Instruct with `vllm/vllm-openai:v0.5.3.post1` for a faster, older vLLM stack.

---

## 8) Next step on the offline host

Continue with **`docs/OFFLINE_SETUP_GUIDE.md`**.

---

## 9) Loading images on the air-gapped host (reference)

```bash
docker load -i /data/tyewhong/qagredo/qagredo-v1.tar
docker load -i /data/tyewhong/qagredo/qagredo-kubeflow.tar   # kubeflow profile only
```

vLLM image load (when using `vllm` profile with Qwen3.5):

```bash
# Preferred: docker save/load from build machine
docker load -i /data/tyewhong/qagredo/vllm-qwen35-localcuda.rootfs.tar
docker images qagredo-vllm:qwen35-localcuda
```

Build on a connected machine if needed: `bash scripts/docker_build_vllm_qwen35_compat.sh` then `bash scripts/save_vllm_qwen35_image.sh`.

Then extract the bundle and run **`setup_offline.sh`** (see **`docs/OFFLINE_SETUP_GUIDE.md`**).

---

## 10) vLLM diagnostic URLs (host ports from `.env`)

**Generator (`VLLM_HOST_PORT`, default 7100):**

- Health: `http://localhost:${VLLM_HOST_PORT}/health`
- API docs: `http://localhost:${VLLM_HOST_PORT}/docs`

**Judge (`VLLM_JUDGE_HOST_PORT`, default 7101):**

- Health: `http://localhost:${VLLM_JUDGE_HOST_PORT}/health`
- API docs: `http://localhost:${VLLM_JUDGE_HOST_PORT}/docs`

Root URL returning `{"detail":"Not Found"}` is normal.

---

## 11) Common problems (build / transfer)

- **401 Unauthorized** on `/v1/*`: missing or wrong `Authorization` header (match `VLLM_API_KEY` to the server).
- **Connection error**: vLLM still loading weights — wait for Uvicorn “running” in logs.
- **CUDA/driver mismatch**: align the vLLM image tag with the host driver (`VLLM_IMAGE` in `.env`).

---

## 12) Regenerate diagram PNGs from sources

Examples:

```bash
dot -Tpng docs/architecture/diagrams/network_docker_compose_ollama.dot \
  -o docs/architecture/diagrams/network_docker_compose_ollama.png
dot -Tpng docs/qagredo_grading_test_flow.dot \
  -o docs/qagredo_grading_test_flow_16x9.png
```

PlantUML (if installed): render **`docs/architecture/diagrams/QAGRedo_Pipeline_Flowchart.puml`** to PNG/SVG as needed.
