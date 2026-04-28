# QAGRedo Server Model Profiles

This guide maps QAGRedo runtime profiles to the three server roles:
greenserver, Opserver, and redserver.

For the full documentation index and code layout, see **`docs/HANDOVER.md`**.

## Profile Decision Flow

```mermaid
flowchart TD
  server["Choose server"] --> greenserver["greenserver: online, 2x24GB"]
  server --> opserver["Opserver: offline, 2x24GB"]
  server --> redserver["redserver: offline, 4 GPUs"]

  greenserver --> greenOllama["Use dev profile with host Ollama"]
  greenserver --> greenVllm["Use vllm profile for older under-10B HF models"]
  opserver --> opDev["Use dev profile if host Ollama is installed"]
  opserver --> opKubeflow["Use kubeflow profile for bundled Ollama"]
  redserver --> redOllama["Use dev or kubeflow for Ollama comparison"]
  redserver --> redVllm["Use vllm plus redserver override"]

  greenOllama --> run["QAGRedo pipeline"]
  greenVllm --> run
  opDev --> run
  opKubeflow --> run
  redOllama --> run
  redVllm --> run
  run --> judge["Separate LLM-as-judge model"]
```

What this shows: pick the profile by server constraints first, then pick
generator and judge model names in the matching `config/config.<profile>.yaml`.
Failure path: if model loading fails, confirm the model format matches the
profile before changing Python code.

## greenserver

greenserver is online and has 2x24GB VRAM. Prefer Ollama for newer GGUF models
because it can run newer model families more comfortably on this hardware.

Use host Ollama:

```bash
QAGREDO_PROFILE=dev
ollama serve
ollama pull qwen3.5:9b
ollama pull llama3.1:8b-instruct-fp16
bash run.sh --show-config
bash run.sh --num-documents 2
```

Edit `config/config.dev.yaml`:

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
bash setup_offline.sh --profile dev
QAGREDO_PROFILE=dev bash run.sh --show-config
QAGREDO_PROFILE=dev bash run.sh --num-documents 2
```

Required artifacts:

- `qagredo_bundle.tar.gz`
- `qagredo-v1.tar`
- `models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`

For an all-in-one container where Ollama runs inside QAGRedo:

```bash
bash setup_offline.sh --profile kubeflow
QAGREDO_PROFILE=kubeflow bash run.sh --show-config
QAGREDO_PROFILE=kubeflow bash run.sh --num-documents 2
```

Required artifacts:

- `qagredo_bundle.tar.gz`
- `qagredo-kubeflow.tar`
- `models_ollama.tar.gz` or split `models_ollama_<tag>.tar.gz`

Failure path: if the profile is `dev`, `ollama` must already run on the host.
If the profile is `kubeflow`, `QAGREDO_MODELS_DIR` must point to an Ollama
store containing `blobs/` and `manifests/`.

## redserver

redserver is offline and has 4 GPUs. Use it for the main performance comparison:
newer GGUF models through Ollama versus HuggingFace models through vLLM.

For Ollama comparison runs, use the same `dev` or `kubeflow` guidance as
Opserver, but choose the newer model tags in `config/config.dev.yaml` or
`config/config.kubeflow.yaml`.

For 4-GPU vLLM runs, use the redserver compose override:

```bash
QAGREDO_PROFILE=vllm \
QAGREDO_VLLM_COMPOSE_EXTRA=docker-compose.vllm-redserver.yml \
VLLM_TP_SIZE=2 \
VLLM_JUDGE_TP_SIZE=2 \
bash run.sh --show-config
```

Then run:

```bash
QAGREDO_PROFILE=vllm \
QAGREDO_VLLM_COMPOSE_EXTRA=docker-compose.vllm-redserver.yml \
VLLM_TP_SIZE=2 \
VLLM_JUDGE_TP_SIZE=2 \
bash run.sh --num-documents 2
```

The override maps:

- generator vLLM service to GPUs `0,1`
- judge vLLM service to GPUs `2,3`

`VLLM_TP_SIZE` must match the number of generator GPUs, and
`VLLM_JUDGE_TP_SIZE` must match the number of judge GPUs.

Required artifacts:

- `qagredo_bundle.tar.gz`
- `qagredo-v1.tar`
- `vllm-openai_*.rootfs.tar`
- `models_vllm.tar.gz`

Failure path: if vLLM reports a model type error for Qwen3 or another newer
architecture, switch that test to Ollama or rebuild the vLLM image with a
compatible Transformers/vLLM stack.

## Model Format Rules

```mermaid
flowchart LR
  ollamaStore["Ollama store: blobs and manifests"] --> ollamaProfiles["dev or kubeflow"]
  hfDirs["HuggingFace model directories"] --> vllmProfile["vllm"]
  wrongFormat["Wrong model format"] --> fail["Model not found or load failure"]
```

What this shows: Ollama archives and vLLM archives are not interchangeable.
Failure path: when a run fails before generation starts, check model format,
profile, and served model names first.

Build common offline artifact sets on the online/build machine:

```bash
# Opserver or redserver Ollama package
bash scripts/make_offline_tarballs.sh --bundle --image-dev --models-ollama

# Opserver or redserver in-container Ollama package
bash scripts/make_offline_tarballs.sh --bundle --image-kubeflow --models-ollama

# redserver vLLM package
bash scripts/make_offline_tarballs.sh --bundle --image-dev --image-vllm --models-vllm
```

For size-limited transfers:

```bash
bash scripts/make_offline_tarballs.sh \
  --bundle --image-dev \
  --models-ollama-split=qwen3.5:9b,llama3.1:8b-instruct-fp16
```
