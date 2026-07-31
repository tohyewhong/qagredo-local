# QAG Server Model Profiles

This guide maps QAG runtime profiles to four server roles:
greenserver, Opserver, redserver, and siteserver.

For the full documentation index and code layout, see **`docs/HANDOVER.md`**.  
System architecture for technical leads: **`docs/ARCHITECTURE.md`**.

## Profile Decision Flow

```mermaid
flowchart TD
  server["Choose server"] --> greenserver["greenserver: online, 2x24GB"]
  server --> opserver["Opserver: offline, 2x24GB"]
  server --> redserver["redserver: offline orchestrator"]
  server --> siteserver["siteserver: offline, 4 GPUs"]

  greenserver --> greenOllama["Use ollama profile with host Ollama"]
  greenserver --> greenVllm["Use vllm profile for older under-10B HF models"]
  opserver --> opDev["Use ollama profile if host Ollama is installed"]
  opserver --> opKubeflow["Use kubeflow profile for bundled Ollama"]
  opserver --> opVllm["Use local vllm; external variables unset"]
  redserver --> redVllm["Use vllm + redserver config override"]
  siteserver --> redOllama["Use ollama or kubeflow for Ollama comparison"]
  siteserver --> siteVllm["Use vllm plus siteserver override"]

  greenOllama --> run["QAG pipeline"]
  greenVllm --> run
  opDev --> run
  opKubeflow --> run
  opVllm --> local["Local Qwen :7100 + Llama :7101"]
  local --> run
  redVllm --> external["gpuserver :52328 + :53366"]
  external --> run
  redOllama --> run
  siteVllm --> run
  run --> judge["Separate LLM-as-judge model"]
```

![SERVER MODEL PROFILES flowchart 1](SERVER_MODEL_PROFILES_flow_01.png)


What this shows: pick the profile by server constraints first, then confirm
the active YAML with `bash run.sh --show-config`. Redserver uses
`config/config.vllm.redserver.yaml`, selected by the config override, both
external base URLs, and redserver compose extra. Local dual-vLLM leaves those
redserver values unset.
Failure path: if model loading fails, confirm the model format matches the
profile before changing Python code.

## greenserver

greenserver is online and has 2x24GB VRAM. Prefer Ollama for newer GGUF models
because it can run newer model families more comfortably on this hardware.

Use host Ollama:

```bash
QAG_PROFILE=ollama
ollama serve
ollama pull qwen3.5:9b
ollama pull llama3.1:8b-instruct-fp16
bash run.sh --show-config
bash run.sh -- --num-documents 2
```

Edit `config/config.ollama.yaml`:

```yaml
llm:
  provider: "ollama"
  model: "qwen3.5:9b"
judge:
  provider: "ollama"
  model: "llama3.1:8b-instruct-fp16"
```

Use vLLM on greenserver only for compatible smaller HuggingFace models, usually
Qwen or Llama models under 10B that your vLLM image can load.

## Opserver

Opserver is offline and has 2x24GB VRAM. Treat it as the air-gapped validation
server. Do not rely on pulls from Hugging Face or Ollama registries at run time.

For host Ollama offline testing:

```bash
bash setup_offline.sh --profile ollama
QAG_PROFILE=ollama bash run.sh --show-config
QAG_PROFILE=ollama bash run.sh -- --num-documents 2
```

Required artifacts:

- `qag_bundle.tar.gz`
- `qag-v1.tar`
- `models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`

For an all-in-one container where Ollama runs inside QAG:

```bash
bash setup_offline.sh --profile kubeflow
QAG_PROFILE=kubeflow bash run.sh --show-config
QAG_PROFILE=kubeflow bash run.sh -- --num-documents 2
```

Required artifacts:

- `qag_bundle.tar.gz`
- `qag-kubeflow.tar`
- `models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`

Failure path: if the profile is `ollama`, `ollama` must already run on the host.
If the profile is `kubeflow`, `QAG_MODELS_DIR` must point to an Ollama
store containing `blobs/` and `manifests/`. In this profile, `run.sh` reuses
the loaded image (no default rebuild) and keeps Ollama warm until
`bash run.sh --down`.

For local Qwen generation with a Llama judge, use the dual-vLLM stack:

```bash
# Save these values in .env.
QAG_PROFILE=vllm
QAG_VLLM_CONFIG_FILE=
VLLM_BASE_URL=
VLLM_JUDGE_BASE_URL=
QAG_VLLM_COMPOSE_EXTRA=

bash run.sh --show-config  # must report config/config.vllm.yaml
bash run.sh --vllm-up generator
bash run.sh --vllm-up judge
bash run.sh --pipeline-only --num-documents 2
```

If `--show-config` or the health error still mentions gpuserver, save `.env`
and unset those four names in the current terminal. `run.sh` preserves
shell-exported values over `.env`.

## redserver

Redserver runs only the QAG orchestrator. Generator and judge vLLM services
must already be healthy on gpuserver; do not copy local vLLM images or model
weights and do not run `--vllm-up`.

```bash
QAG_PROFILE=vllm
QAG_VLLM_CONFIG_FILE=config/config.vllm.redserver.yaml
VLLM_BASE_URL=http://gpuserver:52328/v1
VLLM_JUDGE_BASE_URL=http://gpuserver:53366/v1
QAG_VLLM_COMPOSE_EXTRA=docker-compose.vllm-redserver.yml

curl -s http://gpuserver:52328/v1/models
curl -s http://gpuserver:53366/v1/models
bash run.sh --show-config
bash run.sh --pipeline-only --num-documents 1
```

The model names returned by `/v1/models` must match `llm.model` and
`judge.model` in `config/config.vllm.redserver.yaml`. If either endpoint is
unhealthy, fix gpuserver connectivity rather than starting local vLLM.

**Host LoRA finetune (optional):** `qag_bundle.tar.gz` includes `scripts/lora/`.
Copy `models_vllm_Qwen3_5-9B.tar.gz` to redserver for training weights and
pre-built `lora_venv.tar.gz` for offline pip. Full guide:
[`REDSERVER_ONSITE_SETUP.md`](REDSERVER_ONSITE_SETUP.md) §8.5.

Full checklist: [`REDSERVER_ONSITE_SETUP.md`](REDSERVER_ONSITE_SETUP.md).

### Input conversion on Opserver/siteserver (JSON/JSONL -> TXT)

Use this when your source is JSON/JSONL and you want one `.txt` file per
document for `run.input_folder` + `run.input_glob: "*.txt"` runs.

```mermaid
flowchart LR
  A[input.json] --> B[convert_to_qag_jsonl.py]
  B --> C[input.jsonl]
  C --> D[split_jsonl_to_txt.py]
  D --> E[data/txt/*.txt]
  E --> F[bash run.sh]
```

![SERVER MODEL PROFILES flowchart 2](SERVER_MODEL_PROFILES_flow_02.png)


Commands:

```bash
# 1) JSON -> JSONL (skip this step if you already have .jsonl)
python3 scripts/conversion/convert_to_qag_jsonl.py \
  --input "/path/to/input.json" \
  --output "/path/to/input.jsonl"

# 2) JSONL -> one TXT per record
python3 scripts/utils/split_jsonl_to_txt.py \
  --input "/path/to/input.jsonl" \
  --output-dir "/home/tyewhong/qag/data/txt"
```

Then run with folder mode (example in `config/config.vllm.yaml`):

```yaml
run:
  input_folder: "."
  input_glob: "*.txt"
  input_file: ""
```

If you do not need TXT files, you can point `run.input_file` directly to one
`.json` or `.jsonl` and skip the split step.

## siteserver

siteserver is offline and has 4 GPUs. Use it for the main performance comparison:
newer GGUF models through Ollama versus HuggingFace models through vLLM.

For Ollama comparison runs, use the same `ollama` or `kubeflow` guidance as
Opserver, but choose the newer model tags in `config/config.ollama.yaml` or
`config/config.kubeflow.yaml`.

For 4-GPU vLLM runs, use the siteserver compose override:

```bash
QAG_PROFILE=vllm \
QAG_VLLM_COMPOSE_EXTRA=docker-compose.vllm-siteserver.yml \
VLLM_TP_SIZE=2 \
VLLM_JUDGE_TP_SIZE=2 \
bash run.sh --show-config
```

Then run (pick one mode):

**Split** (start each vLLM service, then pipeline — same image on GPUs 0–1 and 2–3):

```bash
QAG_PROFILE=vllm \
QAG_VLLM_COMPOSE_EXTRA=docker-compose.vllm-siteserver.yml \
VLLM_TP_SIZE=2 VLLM_JUDGE_TP_SIZE=2 \
bash run.sh --vllm-up generator
bash run.sh --vllm-up judge
bash run.sh --pipeline-only --num-documents 2
```

**All-in-one:**

```bash
QAG_PROFILE=vllm \
QAG_VLLM_COMPOSE_EXTRA=docker-compose.vllm-siteserver.yml \
VLLM_TP_SIZE=2 VLLM_JUDGE_TP_SIZE=2 \
bash run.sh -- --num-documents 2
```

```bash
bash run.sh --minimise    # optional: minimal pairs + LoRA SFT/DPO export
bash run.sh --export-lora # optional: LoRA JSONL only
bash run.sh --finetune-lora  # optional: host LoRA SFT (stop vLLM first)
bash run.sh --minimise-good   # optional: per-doc good pairs split
bash run.sh --minimise-bad    # optional: per-doc bad pairs split
```

**Default 2-GPU siteserver** (no compose override): use the same split commands without
`QAG_VLLM_COMPOSE_EXTRA` — generator on GPU 0, judge on GPU 1. Details: **`docs/Siteserver_vLLM_Change_Guide.md`** Part D.

The override maps:

- generator vLLM service to GPUs `0,1`
- judge vLLM service to GPUs `2,3`

`VLLM_TP_SIZE` must match the number of generator GPUs, and
`VLLM_JUDGE_TP_SIZE` must match the number of judge GPUs.

Required artifacts (build on online host under **`/data/tyewhong/qag/`**, then copy to siteserver):

- `qag_bundle.tar.gz`
- `qag-v1.tar`
- `vllm-qwen35-localcuda.rootfs.tar` (Qwen3.5 stack; `VLLM_IMAGE=qag-vllm:qwen35-localcuda`)
- `models_vllm.tar.gz`

On siteserver after copy: `docker load -i /data/tyewhong/qag/vllm-qwen35-localcuda.rootfs.tar` (and `qag-v1.tar` if needed). Runbook: **`docs/Siteserver_vLLM_Change_Guide.md`**.

Failure path: if vLLM reports `qwen3_5` or similar model-type errors, you need
`qag-vllm:qwen35-localcuda` (see `docs/Siteserver_vLLM_Change_Guide.md`), or
switch that test to Ollama, or use Qwen2.5 with the legacy `v0.5.3.post1` image.

## Model Format Rules

```mermaid
flowchart LR
  ollamaStore["Ollama store: blobs and manifests"] --> ollamaProfiles["ollama or kubeflow"]
  hfDirs["HuggingFace model directories"] --> vllmProfile["vllm"]
  wrongFormat["Wrong model format"] --> fail["Model not found or load failure"]
```

![SERVER MODEL PROFILES flowchart 3](SERVER_MODEL_PROFILES_flow_03.png)


What this shows: Ollama archives and vLLM archives are not interchangeable.
Failure path: when a run fails before generation starts, check model format,
profile, and served model names first.

Build common offline artifact sets on the online/build machine:

```bash
# Opserver or siteserver Ollama package
bash scripts/make_offline_tarballs.sh --bundle --image-dev --models-ollama

# Opserver or siteserver in-container Ollama package
bash scripts/make_offline_tarballs.sh --bundle --image-kubeflow --models-ollama

# siteserver vLLM package
bash scripts/make_offline_tarballs.sh --bundle --image-dev --image-vllm --models-vllm
```

For size-limited transfers:

```bash
bash scripts/make_offline_tarballs.sh \
  --bundle --image-dev \
  --models-ollama-split=qwen3.5:9b,llama3.1:8b-instruct-fp16
```
