# QAGRedo

**Question-Answer Generation with Grounding Verification (Redo)**

QAGRedo is an automated pipeline that reads documents, generates complex
questions and grounded answers using an LLM, and verifies that every answer
is factually supported by the source document.

## Documentation

| Document | Description |
|----------|-------------|
| `OFFLINE_GUIDE.md` | **Start here** if working on the offline server |
| `docs/ONLINE_SETUP_GUIDE.md` | Step-by-step guide for running on the online/dev machine and creating offline bundles |
| `docs/VISUAL_REPORT.html` | **Visual report** with diagrams -- open in any browser |
| `docs/ALGORITHM_REPORT.md` | Full algorithm details, design decisions, and rationale |
| `docs/OFFLINE_SETUP_GUIDE.md` | 5-file offline deployment (recommended) |
| `docs/architecture/NETWORK_DIAGRAM.md` | Container networking, ports, URLs |

---

## Purpose

QAGRedo reads your documents (JSON/JSONL), generates **questions + answers**
using **Llama** (vLLM), then checks whether answers are grounded in the source
document (using **MiniLM** for semantic similarity and **Qwen** as LLM-as-judge
for complex reasoning — a separate model avoids self-evaluation bias).

### What makes QAGRedo different

1. **Complex questions** -- 10 question types that require reasoning across
   multiple parts of the document, not simple fact-lookup:

   | # | Type | What the LLM is asked to do | Example pattern |
   |---|------|-----------------------------|-----------------|
   | 1 | **Analysis** | Break down information into parts and examine relationships | *What are the separate factors that contributed to [event]?* |
   | 2 | **Aggregation / Counting** | Count, sum, or aggregate information scattered across different parts of the document | *How many [people/events/items] are mentioned in total across the document?* |
   | 3 | **Comparison** | Compare or contrast two or more entities, events, or viewpoints | *How does [A]'s role differ from [B]'s role?* |
   | 4 | **Inference / Deduction** | Draw conclusions or make logical inferences from stated facts | *Based on the information provided, what can be inferred about [topic]?* |
   | 5 | **Causal Reasoning** | Identify cause-and-effect relationships between events or actions | *What was the likely consequence of [action] on [outcome]?* |
   | 6 | **Temporal / Sequence** | Analyze chronological order, timeline, or sequence of events | *What is the sequence of events that led to [outcome]?* |
   | 7 | **Multi-hop Reasoning** | Connect information from multiple separate parts of the document | *Given that [fact A] and [fact B], what does this imply about [topic]?* |
   | 8 | **Synthesis** | Combine 3+ pieces of information from different parts into a comprehensive answer no single sentence provides | *Drawing from the financial data, leadership changes, and market conditions, what overall picture emerges?* |
   | 9 | **Evaluation / Critical Assessment** | Assess the strength, adequacy, or consistency of claims or evidence in the document | *How well-supported is the claim that [assertion]?* |
   | 10 | **Counterfactual / Hypothetical** | Reason about what would change if a stated fact or condition were different | *What would likely have been different if [condition] had not occurred?* |
2. **Grounded answers** -- structured prompts force the LLM to cite supporting
   evidence, with up to 3 retries if answers contain hallucinations.
3. **Hybrid verification** -- fast semantic similarity for clear cases, LLM
   fallback for counting, aggregation, and inference.
4. **Audit trail** -- every answer includes supporting evidence quotes and
   detailed grounding reasons in the output.
5. **Air-gapped deployment** -- runs entirely offline on GPU servers with no
   internet access.
6. **Semi-agentic design** -- self-correcting retry loops and adaptive hybrid
   routing give the pipeline agentic traits while keeping execution
   deterministic (see `docs/ALGORITHM_REPORT.md` Section 11 for the full
   analysis).

### How it works (high-level)

```
Input documents (JSONL)
        |
        v
  +-- Question Generation ------+
  |   10 question types          |
  |   Few-shot examples          |    LLM (vLLM, temp=0.7)
  |   Deduplication (MiniLM)     |
  |   Validation + retry         |
  +-----------------------------+
        |
        v
  +-- Answer Generation ---------+
  |   Structured format           |
  |   "List then count"           |    LLM (vLLM, temp=0.3)
  |   Supporting evidence         |
  |   Validation + 3 retries      |
  +-----------------------------+
        |
        v
  +-- Hallucination Grading -----+
  |   Hybrid method:              |
  |   1. Semantic (MiniLM,        |    MiniLM (CPU)
  |      sliding window)          |      +
  |   2. LLM-as-judge fallback    |    Qwen (GPU 1)
  |      (separate model avoids   |
  |       self-evaluation bias)   |
  +-----------------------------+
        |
        v
  Output JSON per document
  (questions, answers, evidence,
   grade A-F, confidence %)
```

**Temperature (`temp`) controls how random the LLM's output is** (0.0 = deterministic,
1.0 = very creative). QAGRedo uses different temperatures for each stage:

| Stage | temp | Why |
|-------|------|-----|
| Question generation | **0.7** | Higher creativity produces diverse, non-repetitive questions across the 10 types |
| Answer generation | **0.3** | Lower creativity keeps answers factual and close to the source document |
| Hallucination grading (Qwen judge) | **0.0** | Fully deterministic so grading verdicts are consistent and reproducible |

See `docs/ALGORITHM_REPORT.md` for the full algorithm details and design rationale.

---

## Architecture: three containers

| Container | Role | Resource | Port |
|-----------|------|----------|------|
| **vLLM** (`qagredo-vllm`) | Llama-3.1-8B — generates questions & answers | GPU 0 | 8100 |
| **vLLM-judge** (`qagredo-vllm-judge`) | Qwen2.5-7B — independent LLM-as-judge for hallucination checking | GPU 1 | 8101 |
| **QAGRedo** (`qagredo-runner`) | Pipeline orchestration + MiniLM for semantic similarity | CPU | (none) |

Judging uses a **separate model** (Qwen) from generation (Llama) to avoid self-evaluation bias. To start both vLLM services: `docker compose -f docker-compose.offline.yml up -d vllm vllm-judge`

All files live in a single **`qagredo_host/`** directory. Docker mounts
from it directly. Any edit you make persists across container restarts.

```
qagredo_host/
├── run.sh                             # start vLLM + vLLM-judge + run pipeline
├── setup_offline.sh                   # one-time setup (load images, link models)
├── jupyter.sh                         # start Jupyter Lab
├── docker-compose.offline.yml         # Docker Compose (mounts from ./)
├── run_qa_pipeline.py                 # main entry point
├── config/config.yaml                 # pipeline configuration
├── utils/                             # Python source code
│   ├── question_generator.py          #   question generation (10 types)
│   ├── answer_generator.py            #   answer generation + retry
│   ├── hallucination_checker.py       #   grounding verification
│   ├── output_manager.py              #   timestamped output folders
│   └── ...
├── scripts/                           # helper scripts
│   ├── conversion/                    #   JSON/PDF/TXT/XLSX -> JSONL converter
│   └── utils/                         #   summarize_run.sh
├── data/                              # input JSONL files
├── output/                            # results (timestamped: YYYY-MM-DD_HHMMSS)
├── models_llm/                        # LLM weights (linked by setup)
├── models_embed/                      # embedding model (MiniLM)
├── hf_cache/                          # HF cache
├── docs/                              # documentation
└── README.md
```

**Edit any file here and re-run** -- Docker picks up changes instantly.

---

## Glossary

| Term | Meaning |
|------|---------|
| **Host** | Your normal shell prompt (e.g., `tyewhong@server1:~$`). Run `docker ...` here |
| **Container** | Prompt looks like `qagredo@...:/workspace$`. Do NOT run `docker` inside |
| **`qagredo_host/`** | Single directory containing everything. Docker mounts from here. All edits persist |
| **Grounded** | An answer sentence that can be verified against the source document |
| **Ungrounded** | An answer sentence that is not supported by the document (hallucination) |
| **Semantic similarity** | Measuring how close two pieces of text are *in meaning*, not just matching exact words. E.g. "the company fired 200 staff" and "200 employees were let go" share no words but are semantically very similar. QAGRedo uses MiniLM to convert each sentence into a numeric vector (embedding), then compares vectors with cosine similarity (1.0 = identical meaning, 0.0 = unrelated). This is how the pipeline checks whether an answer's claims appear in the source document without requiring exact word matches |
| **Hybrid** | Default grading method: semantic similarity first, LLM fallback for edge cases |

---

## Part A -- Run on this server (online machine)

### A1) One command (recommended)

```bash
cd /home/tyewhong/qagredo
bash scripts/run_online.sh            # safe mode (no overwrite)
# bash scripts/run_online.sh --overwrite
```

### A2) Where is my output?

```bash
ls -lt output/ | head -10
```

Output folders are timestamped: `output/vllm/<model>/YYYY-MM-DD_HHMMSS/`

### A3) Run WITHOUT Docker (host-only, advanced)

You still need an LLM server. Verify vLLM is running on the host:

```bash
curl -i http://localhost:8100/health   # generator (Llama)
curl -i http://localhost:8101/health   # judge (Qwen)
```

If not running, start both via Docker Compose:

```bash
cd /home/tyewhong/qagredo
docker compose -f docker-compose.offline.yml up -d vllm vllm-judge
```

Ensure `config/config.yaml` has `llm.base_url: "http://localhost:8100/v1"`, then:

```bash
cd /home/tyewhong/qagredo
.venv/bin/python run_qa_pipeline.py --config config/config.yaml
```

---

## Convert your files into QAGRedo input

QAGRedo reads **JSONL** (one JSON object per line).

### Supported input formats

| Format | Extension | Notes |
|--------|-----------|-------|
| **JSON** | `.json` | Arrays of objects, single objects, or nested wrappers |
| **JSON (press/news)** | `.json` | Nested press-style schema -- auto-detected. Only `"english"` articles used; `"native"` content skipped |
| **JSONL** | `.jsonl` | One JSON object per line |
| **PDF** | `.pdf` | Requires `pypdf` |
| **Plain text** | `.txt` | Entire file becomes one document |
| **Excel** | `.xlsx` | Requires `openpyxl` |

**JSON repair**: auto-fixes missing commas, unterminated strings, trailing commas.

### Convert your file

```bash
cd /path/to/qagredo_host

python3 scripts/conversion/convert_to_qagredo_jsonl.py \
  --input data/your-file.json \
  --output data/your-file.jsonl
```

Then edit `config/config.yaml`:
```yaml
run:
  input_file: your-file.jsonl
```

---

## Question generation complexity

Control what types of questions are generated in `config/config.yaml`:

```yaml
question_generation:
  num_questions: 3
  complexity: "advanced"    # "basic", "moderate", or "advanced"
```

| Complexity | Question types | Use case |
|-----------|---------------|----------|
| `basic` | Simple factual | Quick testing |
| `moderate` | Analysis, comparison, inference | Balanced |
| **`advanced`** | All 10 types: analysis, aggregation, comparison, inference, causal, temporal, multi-hop, synthesis, evaluation, counterfactual | **Recommended** -- tests deep understanding |

**Advanced** generates questions that:
- Require reasoning across at least 2 different parts of the document
- Cannot be answered by copying a single sentence
- Include synthesis (combining 3+ facts), evaluation (assessing evidence
  strength), and counterfactual (reasoning about hypothetical changes)

**Few-shot examples** (good + bad patterns) are included in the prompt to
steer the LLM toward correct question types and format, reducing failed
generations and improving question diversity.

**Why these question types:** See `docs/ALGORITHM_REPORT.md` Section 2.2 for
the full rationale including Bloom's Taxonomy alignment.

## Answer generation

Answer generation uses a **structured format** (Answer + Supporting Evidence)
and a **lower temperature** (0.3 by default) to minimise hallucination:

```yaml
answer_generation:
  temperature: 0.3    # lower = more factual, less creative
```

Key design choices:
- The LLM is asked to **list items before counting** (improves aggregation accuracy by ~30%)
- The LLM must **quote supporting evidence** from the document
- Answers that fail grounding checks are **retried up to 3 times**
- The evidence is saved alongside answers in the output JSON for auditability

**Why 0.3 temperature:** Answers must be factual and deterministic. See
`docs/ALGORITHM_REPORT.md` Section 3.3 for the full rationale.

---

## Hallucination checking methods

QAGRedo verifies that generated answers are grounded in the source document.
Set the method in `config/config.yaml`:

```yaml
hallucination:
  method: "hybrid"    # recommended
```

| Method | How it works | Strengths | Weaknesses |
|--------|-------------|-----------|------------|
| `semantic` | MiniLM cosine similarity + **sliding window** (1/2/3-sentence chunks) | Fast, no GPU needed, captures cross-sentence context | Cannot verify counting, aggregation, inference |
| `keyword` | Key-phrase substring matching | Fast, no GPU needed | Misses paraphrased content |
| `llm` | LLM-as-judge (Qwen; sends answer + document to vLLM-judge, temp=0.0) | Handles counting, aggregation, multi-hop reasoning | Slower, uses GPU |
| **`hybrid`** | Semantic first, LLM fallback for low-confidence (~70-80% fast path) | Best balance of speed and accuracy, 40-60% faster than pure LLM | Requires vLLM running |

**Why hybrid is recommended:** Most answers (~70-80%) pass the fast semantic
check alone. Only the remaining edge cases (counting, inference, multi-hop)
trigger an LLM call. This gives the accuracy of LLM grading at a fraction of
the cost. See `docs/ALGORITHM_REPORT.md` Section 4.3.4 for details.

---

## Output format

Each pipeline run creates a timestamped folder:
```
output/vllm/meta-llama-3.1-8b-instruct/2026-02-13_143025/
```

### Per-document JSON

Each processed document produces an analysis JSON file containing:
- **questions** -- generated questions with type tags
- **answers** -- document-grounded answers
- **supporting_evidence** -- quotes from the document supporting each answer
- **grading** -- per-QA grounding status, confidence, method, and reasons
- **grading_summary** -- overall grade (A/B/C/D/F), confidence, and `judge_model`

### Run summary

```bash
bash run.sh --summarize --latest --json
```

The terminal summary shows:
```
  Generator: meta-llama/Meta-Llama-3.1-8B-Instruct
  Judge    : Qwen/Qwen2.5-7B-Instruct
  Provider : vllm
```

The `run_summary.json` includes:
- `generator_model` and `judge_model` (separate fields)
- Per-document statistics (grade, confidence, grounded/ungrounded counts)
- Per-QA details with grounding reasons for failed answers
- **Ungrounded highlights** -- flat list of all ungrounded QA pairs with
  collected reasons, for quick scanning

### Grade meaning

| Grade | Confidence | Meaning |
|-------|-----------|---------|
| A | >= 90% | Excellent -- answers well-grounded in document |
| B | >= 80% | Good -- mostly grounded |
| C | >= 70% | Fair -- some ungrounded claims |
| D | >= 60% | Poor -- significant grounding issues |
| F | < 60% | Fail -- mostly ungrounded |

---

## Part B -- Offline deployment (5-file approach)

Transfer **5 files** to the air-gapped server. When you change code/config,
only re-transfer file #5 (~few MB), not everything (~50+ GB).

| # | File | Size | Changes |
|---|------|------|---------|
| 1 | `vllm-openai_v0.5.3.post1.rootfs.tar` | ~15-20 GB | Almost never |
| 2 | `qagredo-v1.tar` | ~5-10 GB | Rarely |
| 3 | `models_llm.tar` | ~30 GB | Rarely |
| 4 | `models_embed_all-MiniLM-L6-v2.tar` | ~263 MB | Rarely |
| 5 | `qagredo_bundle.tar.gz` | ~few MB | Often |

**Full step-by-step guide** (creating, transferring, deploying, and running):
`docs/OFFLINE_SETUP_GUIDE.md`

---

## Day-to-day workflow

Once files 1-4 are on the server:

1. **On dev machine**: edit code, then `bash scripts/make_qagredo_bundle.sh --include-data`
2. **Transfer** just `qagredo_bundle.tar.gz` (~few MB)
3. **On offline server**:
   ```bash
   cd /home/user/offline20260209
   tar xzf qagredo_bundle.tar.gz
   cd qagredo_host
   bash setup_offline.sh --skip-images
   bash run.sh
   ```

Or edit directly on the offline server -- changes persist.

---

## Permission model

QAGRedo uses a three-layer permission model to ensure all files are always
owned by the host user (no root-owned files):

| Layer | Where | What |
|-------|-------|------|
| Entrypoint startup | Inside container | `chown` writable dirs to HOST_UID:HOST_GID |
| Entrypoint EXIT trap | Inside container | `chown` on exit (catches files created during run) |
| Post-run safety net | Host side (run.sh) | Docker `chown` with `--privileged --userns=host` |

**Why `--privileged --userns=host`:** Some Docker installations use user
namespace remapping, which prevents `chown` from working inside containers.
These flags bypass the remapping. See `docs/ALGORITHM_REPORT.md` Section 6.2.

All Docker volume mounts use `:rw` (read-write) to allow both the container
and host user to read, write, and delete files.

---

## Models used

| Model | Purpose | Size | Runs on |
|-------|---------|------|---------|
| **Meta-Llama-3.1-8B-Instruct** | Question & answer generation | ~16 GB | GPU 0 (vLLM, port 8100) |
| **Qwen2.5-7B-Instruct** | LLM-as-judge for hallucination checking (separate model avoids self-eval bias) | ~14 GB | GPU 1 (vLLM-judge, port 8101) |
| **all-MiniLM-L6-v2** | Semantic similarity (grounding check, dedup) | ~80 MB | CPU |

---

## Troubleshooting

### vLLM health
```bash
curl -i http://localhost:8100/health   # generator (Llama)
curl -i http://localhost:8101/health   # judge (Qwen)
bash run.sh --logs
```

### Common problems

| Problem | Solution |
|---------|----------|
| CUDA mismatch | vLLM v0.5.3.post1 requires CUDA 12.2. Check `nvidia-smi` |
| Not enough GPU memory | `export VLLM_GPU_UTIL=0.7` or `export VLLM_MAX_MODEL_LEN=1024` |
| Wrong GPU count | `export VLLM_TP_SIZE=1` for single GPU |
| `pynvml.NVMLError_InvalidArgument` | Do **not** set `CUDA_VISIBLE_DEVICES` in docker-compose. GPU assignment is handled by Docker's `deploy.resources.reservations.devices.device_ids` — Docker maps the reserved GPU as device 0 inside the container, so `CUDA_VISIBLE_DEVICES: "1"` would try to find a non-existent second GPU |
| 401 Unauthorized | `export VLLM_API_KEY=llama-local` |
| Permission denied | `bash setup_offline.sh --force` |
| Config not found | Make sure you're running from inside `qagredo_host/` |
| Model name mismatch | `grep model config/config.yaml` must match `$VLLM_SERVED_MODEL_NAME` |
| Cannot delete hf_cache files | `docker run --rm --privileged --userns=host -u 0 --entrypoint bash -v "$(pwd)/hf_cache:/hf" vllm/vllm-openai:v0.5.3.post1 -c "rm -rf /hf/*"` |

### Useful URLs

- Generator (Llama): `http://localhost:8100/health` | Judge (Qwen): `http://localhost:8101/health`
- API docs: `http://localhost:8100/docs` (generator), `http://localhost:8101/docs` (judge)
- Models: `curl -H "Authorization: Bearer llama-local" http://localhost:8100/v1/models`
