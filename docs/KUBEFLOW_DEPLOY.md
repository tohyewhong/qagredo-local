# QAGRedo — Kubeflow deployment (single-image, swap-friendly models)

This guide explains the **`kubeflow`** profile: one Docker image that bundles the
QAGRedo runner and Ollama, with **no model weights baked in**. Models live on
disk and can be swapped at runtime — including upgrading to 70B — without
rebuilding the image.

The other two profiles:

| Profile    | Use when                                              | Compose file                                                      |
| ---------- | ------------------------------------------------------ | ----------------------------------------------------------------- |
| `ollama`   | Default. Host Ollama + runner container.               | `docker-compose.yml`                                              |
| `kubeflow` | Kubeflow (or any single-image constraint). This guide.| `docker-compose.kubeflow.yml` + `Dockerfile.kubeflow`             |
| `vllm`     | Dual vLLM GPU services (generator + judge).            | `docker-compose.vllm-stack.yml` (+ optional `docker-compose.vllm-siteserver.yml`) |

See **`docs/HANDOVER.md`** for how profiles are selected (`QAGREDO_PROFILE` in `.env`).

---

## 1. Design at a glance

```
┌───────────────────── container: qagredo-kubeflow ─────────────────────┐
│                                                                       │
│   QAGRedo pipeline (Python)                                           │
│        │                                                              │
│        └──► http://127.0.0.1:11434  (in-container Ollama)             │
│                                     │                                 │
│                                     ▼                                 │
│                       OLLAMA_MODELS = /opt/ollama/models              │
│                                     │                                 │
└─────────────────────────────────────┼─────────────────────────────────┘
                                      │ bind-mount
                                      ▼
               host path set via QAGREDO_MODELS_DIR
               e.g. /home/jovyan/models        (Kubeflow)
                    /home/tyewhong/qagredo/models (ollama / kubeflow)
```

Why this layout:

- **1 image** satisfies the Kubeflow constraint.
- Models are external → swap 8B ↔ 70B by replacing files, no rebuild.
- vLLM is kept as a separate profile; no code removed.

---

## 2. Host layout (models directory)

On your dev server:

```
/home/tyewhong/qagredo/models
├── manifests/
└── blobs/
```

On the Kubeflow node:

```
/home/jovyan/models
├── manifests/
└── blobs/
```

These mirror Ollama's native `$OLLAMA_MODELS` layout. Any GGUF you can `ollama pull` locally can be copied here.

### Populating the directory (offline)

On an internet-connected machine:

```bash
# Example: pull tags that match config/config.kubeflow.yaml (adjust to your tags)
ollama pull qwen3.5:9b
ollama pull llama3.1:8b-instruct-fp16

# Copy the whole Ollama model store into a tarball
tar -C ~/.ollama -czf ollama_models.tar.gz models

# Transfer ollama_models.tar.gz to the offline server / Kubeflow PVC, then:
mkdir -p /home/jovyan/models
tar -C /home/jovyan -xzf ollama_models.tar.gz   # produces /home/jovyan/models
```

To later upgrade to a 70B model:

```bash
ollama pull llama3.3:70b           # on an internet machine
tar -C ~/.ollama -czf llama3.3-70b.tar.gz models/manifests/registry.ollama.ai/library/llama3.3 models/blobs
# transfer, extract into the same host models directory, restart the pod.
```

---

## 3. Local reproduction with Docker Compose

From the repo root on your dev server:

```bash
# One-off: put sample models under ./models (or point to an existing dir)
export QAGREDO_MODELS_DIR=/home/tyewhong/qagredo/models

QAGREDO_PROFILE=kubeflow bash run.sh
```

What happens:

1. `run.sh` picks `docker-compose.kubeflow.yml` and reuses `qagredo-kubeflow:latest` (no default rebuild).
2. Compose keeps one warm container up with 2 GPUs (override `QAGREDO_GPU_COUNT`).
3. The entrypoint runs `ollama serve` in the background against `/opt/ollama/models`.
4. Each `bash run.sh` executes the pipeline inside the same warm container; generator + judge both hit `http://127.0.0.1:11434/v1`.
5. `bash run.sh --down` stops the container and releases GPU memory.

Override model tags:

```bash
QAGREDO_PROFILE=kubeflow \
QAGREDO_MODELS_DIR=/home/tyewhong/qagredo/models \
OLLAMA_MODEL=qwen3:8b \
OLLAMA_JUDGE_MODEL=llama3.1:8b \
bash run.sh
```

---

## 4. Running on Kubeflow

### 4.1 Build & push the image (once)

```bash
docker build -f Dockerfile.kubeflow -t <your-registry>/qagredo-kubeflow:latest .
docker push <your-registry>/qagredo-kubeflow:latest
```

Image size: **~5 GB** (no weights baked in).

### 4.2 Minimal Kubeflow pod spec

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: qagredo
spec:
  containers:
    - name: qagredo
      image: <your-registry>/qagredo-kubeflow:latest
      env:
        - name: QAGREDO_SERVE_OLLAMA
          value: "1"
        - name: OLLAMA_MODELS
          value: /opt/ollama/models
        - name: OLLAMA_MODEL
          value: qwen3:8b
        - name: OLLAMA_JUDGE_MODEL
          value: llama3.1:8b
      resources:
        limits:
          nvidia.com/gpu: 2
      volumeMounts:
        - name: models
          mountPath: /opt/ollama/models
        - name: data
          mountPath: /workspace/data
        - name: output
          mountPath: /workspace/output
  volumes:
    - name: models
      hostPath:
        path: /home/jovyan/models
        type: Directory
    - name: data
      hostPath:
        path: /home/jovyan/qagredo-data
    - name: output
      hostPath:
        path: /home/jovyan/qagredo-output
  restartPolicy: Never
```

Replace `hostPath` with PVCs as appropriate for your cluster.

### 4.3 Using two GPUs for parallel generator + judge

Ollama picks GPUs in the order given by `CUDA_VISIBLE_DEVICES`. For maximum throughput, let Ollama place each loaded model on a separate device:

```yaml
env:
  - name: CUDA_VISIBLE_DEVICES
    value: "0,1"
  - name: OLLAMA_SCHED_SPREAD
    value: "1"   # spread models across visible GPUs
```

With 2 × 80 GB GPUs you can keep both 70B models hot simultaneously.
With 2 × 24 GB GPUs, stick to 8B models.

---

## 5. Swapping models later (no rebuild)

1. Drop new GGUF blobs + manifest into the host models directory.
2. Update `llm.model` / `judge.model` in `config/config.kubeflow.yaml` (preferred), or keep pod env model tags aligned if your deployment injects them.
3. Restart the pod.

No image push, no CI round-trip.

---

## 6. When to use each profile

- **Use `ollama`** locally, when you already run `ollama serve` on your machine.
- **Use `kubeflow`** in any environment that accepts a single image (Kubeflow, Argo, SLURM container, etc.) and/or requires air-gapped execution.
- **Use `vllm`** when you need maximum throughput on dedicated GPUs with HuggingFace weights. Default stack: **Qwen3.5-9B** generator + **Llama 3.1** judge on `qagredo-vllm:qwen35-localcuda` (see `docs/Siteserver_vLLM_Change_Guide.md`). Legacy Qwen2.5 + `v0.5.3.post1` remains supported for older CUDA hosts.

Each profile uses its own config file (`config/config.ollama.yaml`, `config/config.kubeflow.yaml`, `config/config.vllm.yaml`). Strict LLM-as-judge remains the default hallucination checker regardless of profile.
