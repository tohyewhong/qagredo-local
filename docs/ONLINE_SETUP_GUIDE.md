# QAG Online Setup Guide

Use this guide on your connected/build machine to:

1. Run locally for validation.
2. Build offline transfer archives.

Maintainer index: **`docs/HANDOVER.md`**. Architecture overview:
**`docs/ARCHITECTURE.md`**.

---

## 1) Local run (quick validation)

From repo root:

```bash
bash run.sh --status
bash run.sh -- --num-documents 2
bash run.sh --minimise
# split-only commands (optional)
bash run.sh --minimise-good
bash run.sh --minimise-bad
```

`--minimise` writes these per-document outputs:
- `*_analysis_minimal.json`
- `*_analysis_minimal_good_pairs.json`
- `*_analysis_minimal_bad_pairs.json`

It also writes `lora_sft.jsonl`, the optional eval split, and dataset info.
`lora_dpo.jsonl` is conditional on a gate-passing answer with a rejected retry
for the same question; old runs cannot reconstruct discarded attempts.

Optional host finetune (stop vLLM first):

```bash
bash run.sh --down
bash run.sh --finetune-lora
```

Adapter-only; see root `README.md` §7.

Edit only:

- `.env` (profile + host paths)
- the YAML printed by `bash run.sh --show-config` (model tags and run counts)

---

## 2) Profile model rules

### `ollama` / `kubeflow` (Ollama)

- `llm.model` and `judge.model` are Ollama tags.
- Model storage format is Ollama store (`blobs/` + `manifests/`).

### `vllm` (local)

- Model files are HuggingFace directories (with `config.json`, safetensors).
- `config/config.vllm.yaml` model names must match:
  - `VLLM_SERVED_MODEL_NAME`
  - `VLLM_JUDGE_SERVED_NAME`

Local validation (after `QAG_PROFILE=vllm` in `.env`):

```bash
# Local dual-vLLM: redserver config/URLs/compose extra must be empty.
bash run.sh --show-config  # must report config/config.vllm.yaml
bash run.sh --status
bash run.sh --vllm-up generator
bash run.sh --vllm-up judge
bash run.sh --pipeline-only --num-documents 1
# or all-in-one: bash run.sh -- --num-documents 1
```

If the health check still targets gpuserver, save `.env` and unset the
redserver variables in the current terminal; shell-exported values override
`.env`.

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

# redserver (external vLLM already on gpuserver)
bash scripts/make_offline_tarballs.sh --bundle --image-dev
```

For redserver, copy only the bundle and `qag-v1.tar` (if the runner image is
missing or old). Do not copy the vLLM rootfs or HuggingFace model archives for
**inference** (gpuserver serves vLLM). For **host LoRA finetune**, also copy
`models_vllm_Qwen3_5-9B.tar.gz` and `lora_venv.tar.gz` — see
[`REDSERVER_ONSITE_SETUP.md`](REDSERVER_ONSITE_SETUP.md) §1.4 and §8.5.

For large-file limits, split Ollama model tarballs:

```bash
bash scripts/make_offline_tarballs.sh \
  --models-ollama-split=qwen3.5:9b,llama3.1:8b-instruct-fp16
```

---

## 4) Output location

Default output directory:

- `/data/tyewhong/qag/` (active `.tar` / `.tar.gz` in the **root**; see
  `.cursor/rules/archive-output-location.mdc`)

Retired or other-profile archives may be moved to `zz_old_qag/` under the same
path (not scanned by `setup_offline.sh` until moved back to root).

Override:

```bash
QAG_OFFLINE_OUT=/path/to/output bash scripts/make_offline_tarballs.sh --bundle
```

Generated files include matching `.sha256`. Large transfer archives are often staged under **`/data/tyewhong/qag/`** (keep home repo free of huge tarballs).

---

## 5) Recommended archive sets by target runtime

### Offline `ollama`

- `qag_bundle.tar.gz`
- `qag-v1.tar`
- `models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`

### Offline `kubeflow`

- `qag_bundle.tar.gz`
- `qag-kubeflow.tar`
- `models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`

### Offline `vllm`

- `qag_bundle.tar.gz`
- `qag-v1.tar`
- `vllm-qwen35-localcuda.rootfs.tar` (from `scripts/save_vllm_qwen35_image.sh`; default `VLLM_IMAGE=qag-vllm:qwen35-localcuda`)
- `models_vllm.tar.gz` **or** split `models_vllm_Qwen3_5-9B.tar.gz` +
  `models_vllm_Meta-Llama-3_1-8B-Instruct.tar.gz`

---

## 6) Sanity checks before transfer

```bash
# ensure bundle exists
ls -lh /data/tyewhong/qag/qag_bundle.tar.gz

# verify checksums
cd /data/tyewhong/qag
sha256sum -c *.sha256
```

---

## 7) Important compatibility note

If Qwen3.5 fails on vLLM (for example `qwen3_5` not recognized), you are still on an old runtime image. Fix:

- build and set `VLLM_IMAGE=qag-vllm:qwen35-localcuda` (`bash scripts/docker_build_vllm_qwen35_compat.sh`), or
- use `ollama`/`kubeflow` with Ollama for Qwen3.5 GGUF, or
- use Qwen2.5-7B-Instruct with `vllm/vllm-openai:v0.5.3.post1` for a faster, older vLLM stack.

---

## 8) Next step on the offline host

Continue with **`docs/OFFLINE_SETUP_GUIDE.md`**.

---

## 9) Loading images on the air-gapped host (reference)

```bash
docker load -i /data/tyewhong/qag/qag-v1.tar
docker load -i /data/tyewhong/qag/qag-kubeflow.tar   # kubeflow profile only
```

vLLM image load (when using `vllm` profile with Qwen3.5):

```bash
# Preferred: docker save/load from build machine
docker load -i /data/tyewhong/qag/vllm-qwen35-localcuda.rootfs.tar
docker images qag-vllm:qwen35-localcuda
```

Build on a connected machine if needed: `bash scripts/docker_build_vllm_qwen35_compat.sh` then `bash scripts/save_vllm_qwen35_image.sh`.

Then extract the bundle and run **`setup_offline.sh`** (see **`docs/OFFLINE_SETUP_GUIDE.md`**).

---

## 10) vLLM diagnostic URLs (default local stack)

The current `docker-compose.vllm-stack.yml` publishes fixed host ports
`7100` and `7101`; changing similarly named `.env` values does not remap this
compose file.

**Generator:**

- Health: `http://localhost:7100/health`
- API docs: `http://localhost:7100/docs`

**Judge:**

- Health: `http://localhost:7101/health`
- API docs: `http://localhost:7101/docs`

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
dot -Tpng docs/qag_grading_test_flow.dot \
  -o docs/qag_grading_test_flow_16x9.png
```

PlantUML (if installed): render **`docs/architecture/diagrams/QAG_Pipeline_Flowchart.puml`** to PNG/SVG as needed.
