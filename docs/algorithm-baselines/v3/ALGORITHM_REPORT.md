# QAG Algorithm Report

Maintainer documentation index: **`docs/HANDOVER.md`**.  
Technical lead architecture overview: **`docs/ARCHITECTURE.md`**.

**Documentation baselines:** after algorithm changes, say **baseline now** in
Cursor to code-audit the implementation and snapshot a verified version under
[`docs/algorithm-baselines/`](algorithm-baselines/README.md). Compare releases
with **compare baseline v1 and v2**.

This document provides a comprehensive description of the algorithms, design
rationale, and architectural decisions in the QAG pipeline. It covers
question generation, answer generation, hallucination grading, output
management, and the Docker permission model.

> **Current default policy (final):** strict `llm` judge mode.
> Any references to `semantic` or `hybrid` in this report describe legacy or compatibility-only paths and are **not** the production default.

---

## 1. Pipeline Overview

Input conversion from `pdf/txt/doc/docx/xlsx/csv/json/jsonl` to canonical JSONL is parser-based via `scripts/conversion/convert_to_qag_jsonl.py` (not an LLM reasoning step). The **main pipeline** (`run_qa_pipeline.py`, invoked by `bash run.sh`) ingests **one** path: `run.input_file`, and chooses **JSON vs JSONL** from the **file extension** only — it does **not** read `run.input_type`. Use the converter CLI (or `bash run.sh --convert`, which forwards to the same script) to build JSONL from PDF/TXT/etc. YAML keys such as `run.input_type`, `run.input_folder`, and `run.max_files` are **not** passed into the converter; use **`--input-type`** on the converter when you need to override detection. With `--input-type auto` (default), type is inferred from each **`--input`** file’s extension. Optional **`--semantic-normalize`** can populate `metadata.semantic_enrichment` while preserving canonical `content`.

### 1.1 Implementation sketch (runtime auto input preparation)

Picture-first explanation:

![Input preparation simple workflow](qag_input_prep_explained_16x9.png)

Plain-language summary:
- **Pipeline run**: set `run.input_file` to `.json` or `.jsonl` (and `run.input_folder: ""`). Extension selects the loader.
- **Converter** (separate step): one **`--input`** file per run; use **`--input-type`** when you must override extension-based detection (YAML `run.input_type` is not wired to this script).
- Process each source file; determine its type (`auto` detect or forced type) in the converter.
- If it is already JSON/JSONL and semantic enrichment is off, use fast path.
- Otherwise, convert to canonical JSONL.
- Optionally add semantic metadata, then feed the JSONL path to `run.input_file`.

Detailed decision flow is shown in the image above.

You do not need Mermaid software to read this report.
Mermaid is only needed if you want to edit/render Mermaid source diagrams locally.

```
Document (JSONL)
     |
     v
 +-------------------------------+
|  0. Document orchestrator      |  <-- run_qa_pipeline.py slot loop
 |     (_process_one_document)    |      (LangGraph module exists but is
 |     - per-slot routing           |       not wired to the main pipeline)
 |     - replacement on gate fail   |
 +---------------+---------------+
                |
                v
+-------------------------------+
|  1. Question Generator         |  <-- LLM (vLLM / OpenAI), temperature=0.7
|     - LangChain prompt template|
|     - Multi-type prompt         |
|     - Few-shot examples         |
|     - Deduplication (LLM default) |
|     - Grounding validation      |
|     - Comprehensiveness check   |
|     - Answerability check       |
+---------------+---------------+
                |  questions (validated, deduplicated, answerable)
                v
        [per-slot loop — see §3.4]
                |
                v
+-------------------------------+
|  2. Answer Generator           |  <-- LLM (vLLM / OpenAI), temperature=0.3
|     - Answerability pre-check   |  (per slot, before answer LLM)
|     - LangChain structured parse|
|     - Structured format         |
|     - "List then count"         |
|     - Supporting evidence       |
|     - Validation + retry (x3)   |
+---------------+---------------+
                |  answers + evidence
                v
+-------------------------------+
|  3. Hallucination Grader       |  <-- strict LLM judge (required)
|     - LLM verdict required      |
|     - Fail-fast on invalid      |
|       judge response            |
+---------------+---------------+
                |  graded results + reasons
                v
+-------------------------------+
|  4. Output Manager             |
|     - Per-run timestamped       |
|       folders (YYYY-MM-DD_      |
|       HHMMSS)                   |
|     - run_summary.json with     |
|       ungrounded highlights     |
+-------------------------------+
```

**Diagram layers:** the ASCII sketch above is the stage overview. **Behavioral
flowcharts** (Mermaid, with failure paths and retries) live in §2–§7 for each
pipeline stage. Exported PNG/PPTX sources are listed in `docs/README.md`.

**Source files:**

| File | Responsibility |
|------|---------------|
| `run_qa_pipeline.py` | Pipeline orchestration |
| `utils/question_generator.py` | Question generation |
| `utils/answer_generator.py` | Answer generation |
| `utils/hallucination_checker.py` | Grounding verification & grading |
| `utils/duplicate_detector.py` | Question deduplication |
| `utils/output_manager.py` | Output path management & timestamping |
| `utils/config_manager.py` | Configuration loading & validation |
| `utils/langchain_components.py` | LangChain prompt/parsing adapters |
| `utils/langgraph_pipeline.py` | LangGraph state graph (**module present; not called by main pipeline**) |
| `scripts/utils/summarize_run.sh` | Run summary with ungrounded reasons |

### 1.2 Structural causal assumptions (for diagnosis)

QAG behaves as a staged causal system:

1. Input normalization quality affects question quality.
2. Question quality affects answer quality.
3. Answer quality affects grounding/grading outcomes.
4. Grading does not change generated answers; it only evaluates and labels them.

Practical implication:

- If grading quality is poor while answers are also poor, fix generation stages first.
- If answers look strong but grades are unstable, inspect hallucination method/routing and judge configuration.
- Avoid changing many stages at once; otherwise causal attribution is lost.

---

## 2. Question Generation

**File:** `utils/question_generator.py`

Each subsection below includes a **behavioral flowchart** (Mermaid) showing
decisions, retries, and failure paths—not only the happy path.

### 2.1 Design goal

Generate complex, multi-step questions that require **reasoning across multiple
parts** of the document -- not simple fact-lookup questions that can be answered
by copying a single sentence.

**Why complex questions matter:**
- Simple factual questions (e.g., "What is X?") test only retrieval, not
  comprehension. Any LLM can answer these by copying text.
- Complex questions (e.g., "How does X relate to Y given Z?") test whether
  the LLM truly understands the document's content and can synthesise
  information.
- For quality assessment purposes, complex questions are more discriminating --
  they reveal gaps in the LLM's understanding that simple questions miss.

### 2.2 Question types (Bloom's Taxonomy inspired)

The system supports **10 question types**, grouped by the cognitive skill they test:

| Type | Cognitive level | What it tests | Example pattern |
|------|----------------|---------------|-----------------|
| **Analysis** | Analyse | Break down information into parts | "What are the separate factors that contributed to [event]?" |
| **Aggregation** | Apply | Count/sum across document | "How many [people/items] are mentioned in total?" |
| **Comparison** | Analyse | Compare/contrast entities | "How does [A]'s role differ from [B]'s?" |
| **Inference** | Evaluate | Draw conclusions from facts | "Based on the information, what can be inferred about [topic]?" |
| **Causal** | Analyse | Cause-and-effect relationships | "What was the consequence of [action] on [outcome]?" |
| **Temporal** | Understand | Timeline and sequence | "What is the sequence of events that led to [outcome]?" |
| **Multi-hop** | Evaluate | Connect multiple separate facts | "Given [fact A] and [fact B], what does this imply?" |
| **Synthesis** | Create | Combine 3+ facts into analysis | "Drawing from X, Y, and Z, what overall picture emerges?" |
| **Evaluation** | Evaluate | Assess strength of claims/evidence | "How well-supported is the claim that [assertion]?" |
| **Counterfactual** | Create | Reason about hypothetical changes | "What would have changed if [condition] had not occurred?" |

**Why these 10 types:**
- The first 7 (analysis through multi-hop) cover the standard analytical
  question categories that test document comprehension.
- **Synthesis** was added because many real-world documents require integrating
  information scattered across multiple paragraphs -- no single paragraph
  contains the full answer.
- **Evaluation** was added to test whether the LLM can critically assess claims
  rather than just repeat them.
- **Counterfactual** was added to test deeper reasoning -- understanding the
  causal structure well enough to reason about what would change.

### 2.3 Complexity presets

| Preset | Question types used | Use case |
|--------|-------------------|----------|
| `basic` | Simple factual comprehension only | Quick testing |
| `moderate` | Analysis, comparison, inference | Balanced |
| **`advanced`** (default) | All 10 types | **Recommended** -- tests deep understanding |

### 2.4 Prompt construction

The prompt includes:

1. **Role instruction** -- "You are an expert analyst creating COMPLEX questions"
2. **Type definitions** -- Each type with its instruction and example pattern
3. **Few-shot examples** -- Concrete good and bad examples from a fictitious document
4. **Complexity requirements** -- 9 strict rules, including:
   - Every question MUST require reasoning across at least 2 different parts
   - NEVER ask a question answerable by copying a single sentence
   - Prefer "how", "why", "what does X imply about Y"
   - Synthesis questions must integrate 3+ facts
   - Counterfactual questions must reason about what would change
5. **Distribution note** -- Distribute questions across types evenly
6. **Format instruction** -- One per line, with type tag in parentheses

**Why few-shot examples:**
- LLMs produce significantly better output when shown examples (in-context
  learning). Without examples, the model defaults to simple factual questions
  even when instructed otherwise.
- The "bad" examples explicitly demonstrate what to avoid (trivial lookups,
  speculation), reducing regeneration cycles.
- Few-shot examples consume ~300 extra tokens per prompt, but this is minimal
  compared to the document content and greatly reduces failed generations.

### 2.5 Generation loop

```
for each document:
    all_questions = []
    attempts = 0

    while len(all_questions) < num_questions AND attempts < 5:
        1. Build prompt with complexity-aware instructions + few-shot examples
        2. Call LLM (temperature=0.7 for diversity)
        3. Parse response into individual questions
        4. Remove ALL trailing type tags (e.g. "(analysis) (comparison)")
        5. Deduplicate against existing questions (LLM semantic judge, threshold=0.85)
        6. Add unique questions to all_questions

    for each question:
        1. Validate & regenerate if not grounded (see 2.6)
        2. Check comprehensiveness & regenerate if too simple (see 2.7)
        3. Check answerability & regenerate if not fully answerable (see 2.8)
```

**Behavioral flowchart** (question batch + per-question validation chain):

```mermaid
flowchart TD
  D[For each document] --> W{len questions < N AND attempts < 5?}
  W -->|Yes| P[Prompt + LLM temp 0.7]
  P --> PAR[Parse + strip type tags]
  PAR --> DD[Dedup LLM default threshold 0.85]
  DD --> ADD[Add unique questions]
  ADD --> W
  W -->|No| E[For each question in batch]
  E --> G[Grounding check §2.6]
  G --> C[Comprehensiveness check §2.7]
  C --> A[Answerability check §2.8]
  A --> OUT[Question batch → per-slot loop §3.4]
```

![ALGORITHM REPORT flowchart 1](ALGORITHM_REPORT_flow_01.png)


**Why temperature=0.7 for questions:**
- Questions benefit from diversity -- we want varied question types and phrasings.
- Too low (0.0-0.3) produces repetitive, formulaic questions.
- Too high (>0.9) produces incoherent or overly creative questions.
- 0.7 is the empirical sweet spot for diverse yet coherent questions.

### 2.6 Question validation and retry

Each generated question is checked for grounding in the document.

**Behavioral flowchart** (`_validate_and_regenerate_question` in
`utils/question_generator.py`):

```mermaid
flowchart TD
  Q[Question] --> CHK[LLM judge grounding check]
  CHK -->|is_grounded AND conf >= 0.7| KEEP[Keep question]
  CHK -->|fail| R{regeneration attempts left?}
  R -->|Yes| RG[Regenerate grounded-only prompt]
  RG --> CHK
  R -->|No| LAST[Keep last version]
```

![ALGORITHM REPORT flowchart 2](ALGORITHM_REPORT_flow_02.png)


1. **Check**: Run hallucination checker on the question against the document
   (`question_generation.validation.method`, default **`llm`**).
2. **If grounded** (confidence >= 0.7): keep the question
3. **If not grounded**: regenerate up to `max_regeneration_attempts` times (default: 2)
   - Send a new prompt: "This question was REJECTED. Generate a NEW question
     grounded ONLY in the document."
   - Re-check grounding after each regeneration
   - If regeneration returns empty, keep the previous question

**Validation method**: Shipped profiles set `method: "llm"` (strict judge,
same family as answer grading). Code default is also `"llm"` when unset.

**Why LLM judge for question validation (not semantic-only):**
- Production profiles use the judge model for question grounding, not embedding
  similarity alone.
- Short questions can look lexically similar to the document while still being
  unanswerable or over-reaching; the LLM judge catches those cases.
- For CPU-only smoke tests, `method: "semantic"` remains available (§4.3.1);
  it is not the shipped default.

### 2.7 Comprehensiveness check

After grounding validation, each question undergoes a **comprehensiveness check**
to ensure it is not a trivial fact-lookup question.

**Behavioral flowchart** (`_check_question_comprehensiveness`):

```mermaid
flowchart TD
  Q[Question] --> EV[LLM comprehensiveness eval]
  EV -->|score >= min AND is_comprehensive| KEEP[Keep question]
  EV -->|fail| ATT{attempts < max_attempts?}
  ATT -->|Yes| REG[Regenerate with weakness guidance]
  REG --> EV
  ATT -->|No| ST{comprehensiveness_strict?}
  ST -->|Yes| REJ[Reject slot — no answer generated]
  ST -->|No| BEST[Keep best version below threshold]
  EV -->|parse or LLM error| FO[Fail open — keep question score 0.5]
```

![ALGORITHM REPORT flowchart 3](ALGORITHM_REPORT_flow_03.png)


1. **Evaluate**: Send the question + document to the LLM with a structured
   evaluation prompt. The LLM scores the question (0.0–1.0) on:
   - **Depth**: requires reasoning across multiple parts of the document
   - **Clarity**: self-contained and clearly worded
   - **Complexity**: requires analysis, inference, comparison, synthesis, or
     multi-step reasoning
   - **Answerability**: can be fully answered from the document
2. **If comprehensive** (score >= `comprehensiveness_min_score`, default 0.6):
   keep the question
3. **If not comprehensive**: regenerate with guidance from the identified
   weakness (e.g. "too simple — requires only single-sentence lookup")
   - Up to `comprehensiveness_max_attempts` (default: 2) regeneration attempts
   - Each regeneration prompt includes the weakness and explicit instructions
     for producing a more complex question
4. **After all attempts**:
   - **Default (`comprehensiveness_strict: false`)**: keep the best version
     (even if still below threshold — reported in output metadata).
   - **Strict (`comprehensiveness_strict: true`)**: **reject** the slot — the
     question is not added to `questions`, no answer is generated, and
     `question_validation` records `accepted: false` with
     `rejection_reason: comprehensiveness_check_failed`.

**Why this check is needed:**
- The prompt instructions ("must reason across 2+ parts") are soft guidelines.
  The LLM sometimes ignores them and produces simple "What is X?" questions.
- The comprehensiveness check catches these failures; with
  `comprehensiveness_strict: true` it rejects failed slots (no answer).
- Combined with grounding validation, each question must be both **grounded in
  the document** and **complex enough to test real understanding**.

**Output metadata:** Each question's `comprehensiveness_check` includes:
- `score`: 0.0–1.0
- `is_comprehensive`: boolean
- `attempts`: number of evaluation rounds
- `was_regenerated`: whether the question was replaced
- `reason`: the LLM's explanation

**Configuration:**
```yaml
question_generation:
  validation:
    enable_comprehensiveness_check: true     # default: true
    comprehensiveness_min_score: 0.6         # default: 0.6
    comprehensiveness_max_attempts: 2        # default: 2
    # profile-dependent: true in ollama/kubeflow; false in vllm
    comprehensiveness_strict: false
```

### 2.8 Answerability check

After grounding and comprehensiveness checks, each question can undergo an
**answerability check** to ensure it can be **fully answered** using only
explicit facts in the document (not merely grounded or complex).

**File:** `utils/question_generator.py` (`_check_question_answerability`,
`evaluate_question_answerability`); per-slot pre-check in `run_qa_pipeline.py`.

**Behavioral flowchart** (question-stage check + per-slot pre-check; full
slot loop with grounding gate in §3.4):

```mermaid
flowchart TD
  subgraph QST["Question stage — generate_questions"]
    A1[Answerability LLM eval] -->|fail| R1[Regenerate up to answerability_max_attempts]
    R1 --> A1
    A1 -->|pass| OUT[Validated batch]
    A1 -->|fail + answerability_strict| X[Reject slot at question stage]
    A1 -->|parse error| FO[Fail open — treat as pass]
  end

  OUT --> P

  subgraph SLOT["Per-slot pre-check — run_qa_pipeline.py"]
    P{Answerable from document?}
    P -->|No| S[Synthetic pair method answerability_precheck]
    S --> SKIP[Skip answer + judge LLM calls]
    P -->|Yes| AG[Answer Generator §3]
  end
```

![ALGORITHM REPORT flowchart 4](ALGORITHM_REPORT_flow_04.png)


1. **Evaluate**: LLM returns JSON:
   `is_answerable`, `score` (0.0–1.0), `reason`, `missing_facts[]`.
   Fails when the question compares periods/entities but a compared value is
   missing, asks for a delta without both sides stated, or needs facts not in
   the document.
2. **Pass rule** (`answerability_passed`): `is_answerable` is true **and**
   `score >= answerability_min_score` (default **0.8**).
3. **Question-generation stage** (`generate_questions`): when
   `enable_answerability_check: true`, run after comprehensiveness; regenerate
   up to `answerability_max_attempts` (default: 2) with missing-fact
   guidance. Document text is truncated to `answerability_max_doc_chars`
   (default: 6000) for the evaluator prompt.
4. **Strict at question stage** (`answerability_strict: true`): reject the
   slot before answer generation — `question_validation` records
   `accepted: false`, `rejection_reason: answerability_check_failed`.
5. **Per-slot pre-check** (`run_qa_pipeline.py` §3.4): before each
   `generate_answers` call, `evaluate_question_answerability` runs again on
   the current slot question. On failure:
   - Skip answer and judge LLM calls.
   - Emit `_synthetic_unanswerable_slot_pair` (empty answer,
     `hallucination_check.method: answerability_precheck`).
   - Increment `run_metrics.quality_counters.answerability_precheck_failures`.
6. **Parse/LLM errors**: evaluator parse failures **fail open** (treat as
   pass) so a broken check does not block the pipeline.

**`answerability_strict` at slot save** (independent of question-stage strict):

- When **true** (shipped **`vllm`** profile): slots with failed answerability
  pre-check, failed grounding gate, or insufficient-information answers are
  **omitted** from `qa_pairs` (not kept for `--minimise-bad`).
- When **false** (`ollama` / `kubeflow`): failed slots are **kept** in
  `qa_pairs` for `--minimise-bad` unless `run.save_grounded_qa_pairs_only`
  filters them.

**Why this check:**

- Comprehensiveness catches trivial questions; answerability catches
  **comparison / multi-period** questions where one side is missing from the
  document (a common hallucination trigger).
- The per-slot pre-check avoids wasted answer + judge calls when the final
  slot question still cannot be answered from the text.

**Output metadata:** `question_validation[].answerability_check` includes
`score`, `is_answerable`, `attempts`, `was_regenerated`, `reason`,
`missing_facts`, `accepted`.

**Configuration:**

```yaml
question_generation:
  validation:
    enable_answerability_check: true     # all shipped profiles
    answerability_min_score: 0.8
    answerability_max_attempts: 2
    answerability_max_doc_chars: 6000    # optional; default 6000
    # question-stage strict: false ollama/kubeflow; slot-save strict: true vllm
    answerability_strict: false
```

### 2.9 Deduplication

Default dedup uses an LLM semantic judge (`deduplication_method: "llm"`).

**Behavioral flowchart** (during question batch loop, §2.5):

```mermaid
flowchart TD
  CAND[Candidate questions from LLM] --> LOOP[Each candidate vs existing batch]
  LOOP --> M{deduplication_method}
  M -->|llm default| J[LLM duplicate verdict strict]
  M -->|jaccard| JAC[Lexical overlap fallback]
  J -->|duplicate| SKIP[Skip candidate]
  J -->|unique| ADD[Add to batch]
  JAC --> ADD
```

![ALGORITHM REPORT flowchart 5](ALGORITHM_REPORT_flow_05.png)


- **Threshold**: 0.85 (provided to the judge prompt as strictness guidance)
- Each candidate question is compared against existing questions.
- The judge returns JSON verdict `{"duplicate": true|false}`.
- In strict mode, malformed verdicts fail fast to avoid silent quality drift.

**Why this design:** Jaccard catches lexical overlap but misses paraphrases.
LLM dedup is slower but aligns better with quality-first operation.

### 2.10 Configuration

```yaml
question_generation:
  num_questions: 3
  complexity: "advanced"              # "basic", "moderate", "advanced"
  # question_types: [...]             # optional: override which types
  duplicate_similarity_threshold: 0.85
  deduplication_method: "llm"
  dedup_llm:
    use_judge_model: true
    strict: true
    max_tokens: 80
  validation:
    enable_rejection: true
    min_confidence_threshold: 0.7
    max_regeneration_attempts: 2
    method: "llm"
    enable_comprehensiveness_check: true  # check each question for depth/complexity
    comprehensiveness_min_score: 0.6     # minimum score to pass (0.0-1.0)
    comprehensiveness_max_attempts: 2    # max regeneration attempts if too simple
    enable_answerability_check: true     # fully answerable from document only
    answerability_min_score: 0.8
    answerability_max_attempts: 2
    answerability_strict: false          # see §2.8; vllm uses true for slot omission
```

---

## 3. Answer Generation

**File:** `utils/answer_generator.py`

### 3.1 Design goal

Generate factual, document-grounded answers with supporting evidence. Minimise
hallucination through structured prompting, low temperature, and validation
with retry.

### 3.2 Structured answer prompt

The prompt asks the LLM for a **structured response**:

```
Document:
{document_content}

Question: {question}

Instructions:
1. Answer using ONLY information found in the document above.
2. If the answer requires counting or aggregating, list the items first,
   then state the total.
3. After your answer, provide a "Supporting evidence" section quoting
   the key phrases from the document that support your answer.
4. If the document does not contain sufficient information,
   say "Insufficient information in the document."

Format your response as:
Answer: [your answer]
Supporting evidence: [relevant quotes from document]
```

**Why this design:**

| Design choice | Rationale |
|--------------|-----------|
| "Answer using ONLY the document" | Prevents the LLM from using its training data, forcing document grounding |
| "List items first, then count" | LLMs frequently miscount when asked to aggregate directly. Listing first forces step-by-step reasoning and produces more auditable count answers |
| "Supporting evidence" section | Forces the LLM to cite specific text, creating an audit trail. Reviewers can verify answers without re-reading the full document |
| "Insufficient information" option | Prevents the LLM from fabricating answers when the document doesn't contain enough information. Honest "I don't know" is better than hallucination |

### 3.3 Lower temperature for answers

| Parameter | Question generation | Answer generation |
|-----------|-------------------|-------------------|
| Temperature | 0.7 (creative, diverse) | **0.3** (factual, deterministic) |

**Why 0.3 for answers:**
- Answers must be factual and deterministic -- the same question about the same
  document should produce the same answer.
- Lower temperature suppresses creative drift where the LLM adds plausible-
  sounding but unsupported information.
- 0.3 (not 0.0) was chosen because some flexibility is needed for natural
  phrasing. Pure greedy decoding (0.0) can produce degenerate repetitive text.

### 3.4 Per-slot answer loop (orchestrated in `run_qa_pipeline.py`)

After the **initial question batch** (`generate_questions` with
`num_questions = N`), the pipeline processes **one slot at a time**. Each
slot is an index `1..N` with a current question text. Answer generation,
grading, and replacement questions are **per slot**, not batched across
failed slots.

```mermaid
flowchart TD
  A[Initial question batch N] --> B{For each slot 1..N}
  B --> P{Answerability pre-check enabled?}
  P -->|Yes| Q{Question answerable from doc?}
  Q -->|No| S[Synthetic pair answerability_precheck]
  Q -->|Yes| C[generate_answers x1 + coverage rewrite]
  P -->|No| C
  C --> D[grade + build_qa_pair]
  D --> E{Grounding gate passes?}
  E -->|Yes| F[Keep or omit per answerability_strict]
  E -->|No| G{Replacements left?}
  G -->|Yes| H[generate_questions x1 for this slot]
  H --> P
  G -->|No| I[Keep or omit per answerability_strict]
  S --> E
  F --> B
  I --> B
```

![ALGORITHM REPORT flowchart 6](ALGORITHM_REPORT_flow_06.png)


**Answerability pre-check** (when `enable_answerability_check: true`):

- Runs **before** `generate_answers` for each slot attempt via
  `evaluate_question_answerability` in `run_qa_pipeline.py`.
- On failure: skip answer and judge calls; build
  `_synthetic_unanswerable_slot_pair` (`method: answerability_precheck`).
- The synthetic pair **fails the grounding gate** (empty answer, zero
  confidence).

**Inside `generate_answers` (per slot attempt, when pre-check passed):**

**Behavioral flowchart** (`utils/answer_generator.py`):

```mermaid
flowchart TD
  GEN[Generate answer LLM temp 0.3] --> VAL[Grounding check per attempt]
  VAL -->|is_grounded AND conf >= threshold| COV{Coverage enabled?}
  VAL -->|fail| CAP[Append rejected answer + score to answer_attempts]
  CAP --> RET{attempts < max_answer_attempts?}
  RET -->|Yes| REGEN[Regeneration prompt + re-check]
  REGEN --> VAL
  RET -->|No| DISC{reject_ungrounded_after_retries?}
  DISC -->|Yes| EMPTY[Return empty final answer]
  DISC -->|No| COV
  COV -->|low coverage| RW[One rewrite pass + re-ground]
  COV -->|ok or skipped| DONE[Return answer to slot loop]
  RW --> DONE
```

![ALGORITHM REPORT flowchart 7](ALGORITHM_REPORT_flow_07.png)

**Slot-level gate** (`_pair_passes_grounding_gate` in `run_qa_pipeline.py`):

- Requires `is_grounded`, `confidence >= min_confidence_threshold`, and
  answer text must **not** contain the insufficient-information phrase.
- When the final gate passes after retries, the highest-confidence rejected
  attempt for that exact question is paired with the accepted answer in
  `dpo_pairs`.
  Failed attempts followed by a replacement question are not paired.
- If the gate fails and `replace_idx < max_question_regeneration_rounds`,
  call `generate_questions` with `num_questions: 1` for a **replacement
  question on the same slot**, then re-answer and re-grade.
- Loop allows **`max_question_regeneration_rounds + 1`** attempts per slot
  (initial question plus up to `max_question_regeneration_rounds`
  replacements). Profile defaults: **`vllm`** `5` → up to 6 question texts
  per slot; **`ollama`** / **`kubeflow`** `3` → up to 4.

**Why answer retries + per-slot replacement (not batch rounds):**

- Answer retries (`max_answer_attempts`) fix wording while the question
  stays fixed.
- Per-slot replacement fixes bad questions without waiting for other slots
  to finish or regenerating a batch sized to the failure count.
- Batching replacements across slots was removed: saved `qa_pairs` always
  align with slot indices `1..N` (after comprehensiveness strict trimming).

**Saved output policy:**

- One row per slot in `qa_pairs` (length ≤ `num_questions`). Passing and
  failing slots are both saved unless filtered (see below).
- After the final slot grounding gate passes, `dpo_pairs` can contain the
  accepted answer and highest-confidence rejected retry for that exact
  document/question, plus both confidence values. It may be empty.
- When `question_generation.validation.answerability_strict: true`
  (**`vllm`** profile): slots that fail the answerability pre-check, fail the
  grounding gate, or end with an insufficient-information answer are
  **omitted** from `qa_pairs` (log: `omitted (answerability_strict)`).
- The shipped profiles currently use `answerability_strict: false`, so failed
  final slots are **kept** in `qa_pairs` for `--minimise-bad`.
- `grading_summary.overall_confidence` is the mean across **all saved**
  pairs (`aggregate_grounded_only=False`), so low-confidence failures
  lower the document grade.
- When `run.save_grounded_qa_pairs_only` is **true**, slots that fail the
  gate are **dropped** before save; documents with no grounded pairs
  produce **no** analysis file.
- When `run.reject_insufficient_answers` is **true** (default in repo
  configs), answers containing **"Insufficient information in the
  document."** **fail the grounding gate** (triggering replacement
  questions while rounds remain). If the slot still ends insufficient,
  the pair is **kept** in `qa_pairs` for `--minimise-bad`, with
  `question_validation.rejection_reason: insufficient_information_answer`.
- When `run.minimal_qa_output` is **true**, the saved analysis JSON contains
  minimal `document`, `qa_pairs`, and `dpo_pairs` blocks (no
  `hallucination_check`, citations, timings, or other run metadata).
- The same minimal shape can be produced **after the fact** from full
  `*_analysis.json` files (no LLM rerun) via
  `scripts/utils/export_analysis_minimal.py` — see `README.md` and
  `docs/OFFLINE_SETUP_GUIDE.md`.

### 3.5 Coverage validation and targeted rewrite

Coverage validation catches answers that are grounded but incomplete (for
example, answers that address only one side of a comparison question).

**Behavioral flowchart** (`_check_question_coverage`,
`_rewrite_for_question_coverage` in `utils/answer_generator.py`):

```mermaid
flowchart TD
  A[Grounded answer accepted] --> CV[LLM coverage eval JSON]
  CV -->|is_covered AND score >= min| OK[Keep answer]
  CV -->|gap| RW[Rewrite with missing_points feedback]
  RW --> RG[Re-run hallucination check on rewrite]
  RG -->|grounded AND conf >= threshold| OK2[Accept rewrite]
  RG -->|fail| ORIG[Keep original answer]
  CV -->|eval error| SKIP[Fail open — keep answer]
```

![ALGORITHM REPORT flowchart 8](ALGORITHM_REPORT_flow_08.png)


Design details:
- Uses an LLM evaluator prompt that returns JSON:
  `is_covered`, `coverage_score`, `reason`, `missing_points`
- Runs at most one rewrite pass per answer (keeps runtime predictable)
- Uses targeted missing-point feedback to rewrite only the weak parts
- Applies a grounding gate after rewrite, so coverage improvement does not
  introduce hallucinations

### 3.6 Configuration

```yaml
answer_generation:
  temperature: 0.3                    # lower = more factual
  multi_turn:
    enable_rejection: true
    min_confidence_threshold: 0.7
    max_answer_attempts: 5            # total answer trials per slot (vLLM default)
    max_regeneration_attempts: 2      # legacy fallback key
    max_question_regeneration_rounds: 3  # ollama/kubeflow; vllm uses 5 in config.vllm.yaml
    reject_ungrounded_after_retries: true
  coverage_validation:
    enable: true
    min_score_threshold: 0.7
    max_doc_chars: 5000
```

---

## 4. Hallucination Checking & Grading

**File:** `utils/hallucination_checker.py`

### 4.1 Design goal

Verify that every sentence in a generated answer is grounded in the source
document. Provide a confidence score, grade, and human-readable reasons
for any ungrounded content.

**Production path** (shipped profiles: `hallucination.method: "llm"`):

```mermaid
flowchart TD
  A[Answer text] --> SPLIT[Protected sentence split §4.2]
  SPLIT --> J[LLM judge temp 0.0 — separate model]
  J -->|SUPPORTED conf >= threshold| G[Grounded]
  J -->|NOT_SUPPORTED or low conf| U[Ungrounded]
  J -->|invalid verdict + judge_strict_verdict| FAIL[Fail fast — pipeline error]
```

![ALGORITHM REPORT flowchart 9](ALGORITHM_REPORT_flow_09.png)


Legacy `semantic`, `keyword`, and `hybrid` paths are documented in §4.3 for
compatibility only; they are not the production default.

### 4.2 Sentence splitting

Before checking, the answer is split into individual sentences. This is a
critical step because grounding is checked **per sentence**.

The `_split_into_sentences` function handles:

```
1. Protect abbreviations (Dr., Mr., Mrs., Ms., Prof., etc.)
   -> Replace "." with placeholder to prevent splitting
2. Protect numbered list items (1. First item, 2. Second item)
   -> Prevents "1" from becoming a standalone sentence
3. Protect decimal numbers (3.5, $1.2M)
   -> Prevents splitting at decimal points
4. Protect ellipsis (...)
   -> Preserves ellipsis as single token
5. Split on sentence-ending punctuation ([.!?]) followed by whitespace
6. Split on newlines (paragraph boundaries)
7. Restore all placeholders
8. Filter out fragments shorter than 3 characters
   -> Prevents standalone numbers ("1", "2") from being flagged
```

**Why this complexity:**
- Naive splitting on "." would break on "Dr. Smith" (creating "Dr" as a
  standalone sentence) and "3.5 million" (creating "3" and "5 million").
- Numbered lists ("1. First item") would split into "1" which gets flagged
  as ungrounded. The numbered-list protection prevents this.
- Short fragments (< 3 chars) are filtered because they carry no meaningful
  content and would be incorrectly flagged as ungrounded.

### 4.3 Available methods

#### 4.3.1 Semantic similarity with sliding window (`method="semantic"`)

```
For each answer sentence:
    1. Encode answer sentence -> 384-dim vector (MiniLM)
    2. Build document chunks:
       - All individual document sentences
       - All 2-sentence sliding windows (sentence[i] + sentence[i+1])
       - All 3-sentence sliding windows (sentence[i..i+2])
    3. Encode all document chunks -> vectors
    4. Compute cosine similarity against every chunk
    5. Take max similarity score

    If max_similarity >= 0.5:  -> GROUNDED
    If max_similarity <  0.5:
        If generic statement:  -> GROUNDED (auto-waived)
        Else:                  -> UNGROUNDED

Confidence = len(grounded_sentences) / (len(grounded_sentences) + len(ungrounded_sentences))
is_grounded = confidence >= 0.7 AND len(ungrounded_sentences) == 0
```

**Why sliding window (not single-sentence comparison):**
- Single-sentence comparison misses information that spans consecutive
  sentences. Example: Document says "John was arrested." (sentence 1) and
  "Peter was also arrested." (sentence 2). An answer like "Both John and
  Peter were arrested" only matches well against the *combination* of
  sentences 1 and 2, not either alone.
- Window sizes 1+2+3 capture increasingly wide context while keeping
  computation manageable.
- Trade-off: ~3x more document embeddings, but MiniLM is fast on CPU
  (typically <1 second per document).

**Model:** `all-MiniLM-L6-v2` (22M parameters, 384 dimensions, runs on CPU)
**Threshold:** 0.5 cosine similarity

**Strengths:** Fast, no GPU needed, captures cross-sentence context.
**Weaknesses:** Cannot verify counting, aggregation, inference, or negation.

#### 4.3.2 Keyword-based (`method="keyword"`)

```
For each answer sentence:
    1. Extract 2-gram and 3-gram key phrases (exclude stop words)
    2. Check if each phrase exists as substring in document text
    3. If any phrase found:  -> GROUNDED
    4. If no phrases found AND not generic:  -> UNGROUNDED

Special handling:
    - "not in the document" phrases -> auto-grounded
    - "I don't know" / "cannot determine" -> confidence boost (+0.2)
```

**Strengths:** Very fast, no model needed.
**Weaknesses:** Misses paraphrased content, relies on exact substring matching.

#### 4.3.3 LLM-as-judge (`method="llm"`)

The judge uses a **different** model from the generator to avoid
self-evaluation bias (for example **Qwen3.5-9B** generator with
**Meta-Llama-3.1-8B-Instruct** judge in `config/config.vllm.yaml`, or
Ollama tags such as `qwen3.5:9b` + `llama3.1:8b-instruct-fp16` in
`ollama`/`kubeflow`). A model should not grade its own outputs.

```
1. Build structured prompt with:
   - Full document text (truncated to ~6000 chars if needed)
   - The question
   - The answer
   - Instructions to check for:
     * Numbers, counts, aggregations
     * Inferences and conclusions
     * Negations and qualifiers

2. Send to LLM with temperature=0.0 (fully deterministic)

3. Parse LLM response:
   Expected: {"verdict": "SUPPORTED"/"NOT_SUPPORTED",
              "confidence": 0.0-1.0,
              "reason": "brief explanation"}
   Fallback: regex extraction if JSON parsing fails

4. Map verdict to grounded/ungrounded
```

**Why temperature=0.0 for judging:**
- The judge must be deterministic -- the same answer should receive the same
  grade every time. Unlike generation, we don't want creativity.

**Strengths:** Handles counting, aggregation, inference, multi-hop, negation.
**Weaknesses:** Slower (requires LLM call), uses GPU time.

#### 4.3.4 Hybrid (`method="hybrid"`) -- Optional compatibility mode

```
PASS 1 -- Semantic with sliding window (fast, free):
    Run semantic similarity check on all answer sentences
    If ALL sentences grounded:
        -> Return result immediately (no LLM call needed)
        -> Method: "hybrid (semantic only -- all passed)"

PASS 2 -- LLM fallback (only if Pass 1 found ungrounded sentences):
    Send FULL answer + document to LLM-as-judge

    If LLM says SUPPORTED (confidence >= 0.7):
        -> Override semantic's ungrounded verdict
        -> Mark ALL sentences as grounded
        -> Use LLM confidence score
        -> Method: "hybrid (semantic + LLM override)"

    If LLM also says NOT_SUPPORTED:
        -> Keep semantic's verdict
        -> Use min(semantic_confidence, llm_confidence)
        -> Add LLM's reason to issues list
        -> Method: "hybrid (semantic + LLM confirmed)"
```

**Why hybrid was used historically (not the production default):**

Shipped profile YAML sets `hallucination.method: "llm"` with
`allow_semantic_fallback: false`. Hybrid remains documented for compatibility
and optional tuning only.

| Factor | Hybrid (legacy) |
|--------|-----------------|
| **Speed** | Many answers pass semantic alone (no LLM call needed) |
| **Accuracy** | Remaining edge cases get full LLM evaluation |
| **Cost** | Fewer judge-model GPU calls than pure `llm` mode |
| **Robustness** | If LLM is unavailable, can degrade to semantic-only when fallback enabled |

**When LLM override is critical:**
- **Aggregation**: "3 people total" -- no single sentence says this, but the
  LLM can count mentions across the document.
- **Inference**: "The company's strategy was successful" -- requires combining
  facts about strategy and outcome from different paragraphs.
- **Multi-hop**: "Given that A leads to B, and B was observed, A must have
  occurred" -- sentence-level similarity cannot verify logical chains.

### 4.4 Grading scale

After checking all final saved Q&A pairs for a document:

Grade mapping (letters **A, B, C, D, F** only — there is no **E** grade):

```
overall_confidence = average(confidence of all saved Q&A pairs)
(Pipeline uses aggregate_grounded_only=False in build_grading_summary_block.)

Grade mapping:
    >= 0.90  ->  A  (Excellent -- answers are well-grounded)
    >= 0.80  ->  B  (Good -- mostly grounded, minor issues)
    >= 0.70  ->  C  (Fair -- some ungrounded claims)
    >= 0.60  ->  D  (Poor -- significant grounding issues)
    <  0.60  ->  F  (Fail -- mostly ungrounded)
```

### 4.5 Generic statement detection

Sentences matching these patterns are auto-grounded because they are
meta-statements about the document, not factual claims:

- "The document states/mentions/describes..."
- "According to the document..."
- "As stated in the document..."
- "This is/refers to/means..."

**Why:** Penalising these would unfairly lower the confidence score. They
carry no factual claims that could be hallucinated.

### 4.6 Configuration

```yaml
hallucination:
  method: "llm"       # strict default
  judge_required: true
  judge_strict_verdict: true
  allow_semantic_fallback: false
```

### 4.7 Testing grading (flow)

Use this when you need to **validate grading** without guessing which script
path applies.

![Grading test entry points](qag_grading_test_flow_16x9.png)

**Source:** [`qag_grading_test_flow.dot`](qag_grading_test_flow.dot) —
regenerate the raster:

```bash
dot -Tpng -o docs/qag_grading_test_flow_16x9.png \
  docs/qag_grading_test_flow.dot
```

| Entry point | Exercises | Typical use |
|-------------|-----------|-------------|
| `scripts/utils/smoke_semantic_five_docs.py` | `check_hallucination` only, `method=semantic` | Quick CPU/embed smoke; no `grade_qa_results`. |
| `scripts/utils/grade_qa_results.py` | `grade_qa_results` on a list JSON | Re-grade saved `*qa_results*`; ensure each dict has body text in a field `_document_text_for_grading` reads. |
| `run_qa_pipeline.py` / `run.sh` | Full LLM-judge pipeline + `set_llm_config` | Ground truth for production; inspect `*_analysis.json`. |
| `summarize_run.sh --json` | Aggregates per-QA `grading` | Run-wide triage after pipeline tests. |
| `scripts/utils/quick_test.py` | `evaluate_document_quality` on **synthetic** `grading` | Threshold / band logic only — **not** the hallucination checker. |

See **§3.4** for the canonical per-slot loop. Quick reference: initial
question batch → for each slot, answerability pre-check → `generate_answers`
(with `max_answer_attempts` and coverage rewrite) → grade → grounding gate →
on pass, optional same-question `dpo_pairs` capture →
optional `generate_questions` ×1 replacement on the same slot (up to
`max_question_regeneration_rounds`). Failed slots are **kept** in `qa_pairs`
for `--minimise-bad` unless `answerability_strict: true` (optional) or
`run.save_grounded_qa_pairs_only: true` omits them.

---

## 5. Output Management

**File:** `utils/output_manager.py`

### 5.1 Per-run timestamped folders

Each pipeline run creates a unique output folder.

**Behavioral flowchart** (`utils/output_manager.py`):

```mermaid
flowchart LR
  RUN[run_pipeline start] --> LOCK[Lock run timestamp]
  LOCK --> DIR[output provider model YYYY-MM-DD_HHMMSS]
  DIR --> DOC[per-doc analysis JSON]
  DIR --> META[generation metadata in each file]
  SUM[summarize_run.sh optional] --> RS[run_summary.json]
  DOC --> SUM
```

![ALGORITHM REPORT flowchart 10](ALGORITHM_REPORT_flow_10.png)


Example paths:

```
output/ollama/qwen3.5-9b/2026-02-13_143025/
output/ollama/qwen3.5-9b/2026-02-13_160512/
```

(`<provider>` is `ollama`, `vllm`, `openai`, etc., from effective config;
`<model>` is sanitized for paths.)

Format: `YYYY-MM-DD_HHMMSS` (date + time to the second).

**Why date+time (not just date):**
- Multiple runs per day are common during testing and evaluation.
- With date-only folders, later runs would overwrite earlier results.
- The timestamp is locked once at the start of `run_pipeline()` so all files
  from the same run land in the same folder, even if the run takes minutes.

### 5.2 Run summary with ungrounded reasons

The terminal summary shows Generator, Judge, and Provider. The
`run_summary.json` (generated by `bash run.sh --summarize --latest --json`)
includes:

1. **`generator_model` and `judge_model`** -- separate fields (previously just
   `model`)
2. **Per-document statistics** -- grade, confidence, grounded/ungrounded counts
3. **Per-QA details** (`qa_details`) -- question, answer, grounding status,
   confidence, method, and for ungrounded answers:
   - `issues` -- human-readable reasons (e.g., "Low similarity (0.32): '...'")
   - `ungrounded_sentences` -- the specific sentences that failed grounding
   - `llm_verdict` -- the LLM judge's full verdict with reason
4. **Ungrounded highlights** (`ungrounded_highlights`) -- a flat array of
   all ungrounded QA pairs across all documents with collected reasons,
   for quick scanning without drilling into each document.
5. **Defensive parsing** -- summary generation tolerates null/malformed numeric
   fields and falls back to per-QA grading confidence when document-level
   values are missing.

**Why include reasons:**
- An analyst reading the summary needs to understand *why* an answer was
  marked ungrounded, not just that it was. Without reasons, the analyst
  would need to manually inspect each analysis JSON file.
- The `ungrounded_highlights` section provides a quick executive summary
  of all problems across the entire run.

### 5.3 Output JSON structure (per document)

Each `*_analysis.json` file saved by `run_qa_pipeline.py` uses this top-level layout:

```json
{
  "document": { "...": "..." },
  "qa_pairs": [ { "...": "..." } ],
  "dpo_pairs": [ { "...": "..." } ],
  "question_generation": { "...": "..." },
  "answer_generation": { "...": "..." },
  "grading_summary": { "...": "..." },
  "run_metrics": { "...": "..." }
}
```

### 5.4 Output Field Reference (per-document analysis JSON)

This section is the field-by-field reference for daily debugging and interpretation.

#### 5.4.1 `document` block

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `document.id` | string | Input record passthrough in `run_qa_pipeline.py` | `"doc_001"` | Primary document identifier used in logs and file naming. If missing, fallback ID (e.g. `doc_1`) is used. |
| `document.title` | string \| null | Input record passthrough | `"Q4 Risk Update"` | Human-readable title. Can be null if source data does not provide it. |
| `document.source` | string \| null | `run_qa_pipeline.py` snapshot (`source` or fallback `sources`) | `"internal_report.pdf"` | Source provenance. Helpful when tracing bad QA back to original files. |
| `document.type` | string \| null | `run_qa_pipeline.py` snapshot (`type` / `doc_type` / `metadata.type`) | `"report"` | Optional source type/category. |
| `document.content` | string \| null | `run_qa_pipeline.py` `_extract_text_content()` (same priority as question generation) | `"..."` | Canonical body text in analysis output. Input lookup order: `content`, `text`, `body`, `document`, `article`, `passage`; list values joined. Used for generation. **`grade_qa_results()`** resolves the live grading string the same way via `_document_text_for_grading()` on the merged QA dict (same ordered fields only—never `questions`/`answers`). Null = no extractable text. |

#### 5.4.2 `qa_pairs[]` block

One row per **slot** after the per-slot loop in `run_qa_pipeline.py` (§3.4).
Each slot keeps the **last** question/answer attempt for that index.
Intermediate replacement attempts are not written separately. Slots that
**pass** the grounding gate and slots that **still fail** after max
replacements are both persisted unless filtered: `run.save_grounded_qa_pairs_only`
drops gate failures; `question_generation.validation.answerability_strict: true`
(when enabled) omits failed pre-check, gate, and insufficient-info slots.
`grading_summary` averages confidence over **all saved** rows, including
failures kept for `--minimise-bad`.

Rejected **answer retries** differ from replacement-question attempts: when a
later retry for the same question passes, QAG retains one highest-confidence
rejected answer in the top-level `dpo_pairs` block.

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `qa_pairs[].question` | string | `build_qa_pairs()` in `run_qa_pipeline.py` | `"What sequence of events led to ...?"` | Final question after validation/regeneration steps. |
| `qa_pairs[].answer` | string | `generate_answers_from_results()` output | `"The sequence was ..."` | Final answer after grounding retries and optional coverage rewrite. |
| `qa_pairs[].hallucination_check` | object \| null | `grade_qa_results()` mapped by index into per-slot pair payload | `{...}` | Hallucination/grounding verdict for this QA pair. Null means all grading paths failed for that QA. |
| `qa_pairs[].citation_spans` | object[] | `build_qa_pairs()` → `_evidence_to_citation_spans()` | `[{"start":40,"end":92,"text":"..."}]` | Offsets into `document.content` for each **unique** evidence fragment that matched (verbatim or whitespace-relaxed). See §5.4.2b. |
| `qa_pairs[].citation_notes` | string[] | same | `["..."]` | Fragments with **no** match (paraphrase, typo, or missing in doc). Same normalization as spans: list-prefix strip + dedupe so repeated model lines do not inflate the array. |

#### 5.4.2a `dpo_pairs[]` block

This optional training block is produced only when a rejected answer attempt
is followed by an accepted retry for the same document and exact question.
Question replacements never form cross-question preference pairs.
`answer_attempts[]` lives in the answer-generation validation metadata while
the slot is processed; the saved analysis retains the selected preference as
`dpo_pairs`, not the full retry history.

| Field | Type | Meaning |
|------|------|---------|
| `dpo_pairs[].question` | string | Exact prompt question shared by both answers. |
| `dpo_pairs[].chosen` | string | Final grounded answer that passed the slot gate. |
| `dpo_pairs[].rejected` | string | Highest-confidence rejected retry candidate. |
| `dpo_pairs[].chosen_confidence` | number | Grounding confidence for the accepted answer. |
| `dpo_pairs[].rejected_confidence` | number | Grounding confidence for the rejected answer. |

The block can be empty when every initial answer passes or no retry recovers.
Old analysis files cannot reconstruct attempts that were not saved.

#### 5.4.2b Citation resolution (`supporting_evidence` → spans / notes)

Answer generation returns `supporting_evidence` in parallel with answers; it is **not** persisted on the analysis JSON. When assembling `qa_pairs`, `run_qa_pipeline.py` maps that text onto `document.content`:

1. **Split** — newlines, then semicolons within a line (`_split_evidence_fragments`).
2. **Normalize** — strip leading `-`, `*`, `•`, and numeric list markers such as `1.` / `1)` (stacked prefixes allowed via `_strip_evidence_line_prefixes`).
3. **Dedupe** — drop fragments whose whitespace-collapsed text was already seen (first occurrence wins).
4. **Match** — `_find_quote_span`: exact substring, else regex with flexible whitespace between tokens.

```mermaid
flowchart LR
  EV["supporting_evidence (in-memory)"] --> SP["_split_evidence_fragments"]
  SP --> MT["_find_quote_span per fragment"]
  MT --> CS["citation_spans"]
  MT --> CN["citation_notes"]
```

![ALGORITHM REPORT flowchart 11](ALGORITHM_REPORT_flow_11.png)


#### 5.4.3 `qa_pairs[].hallucination_check` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `is_grounded` | bool | `check_hallucination()` | `true` | Final grounding verdict for the answer. |
| `confidence` | number (0.0-1.0) | `check_hallucination()` | `0.85` | Confidence in grounding. Scores `< 0.7` are treated as weak. |
| `method` | string | Hallucination checker | `"hybrid (semantic + LLM override)"` | Method path used (`semantic`, `keyword`, `llm`, hybrid, or `answerability_precheck` when slot pre-check failed before answer gen). |
| `issues` | string[] | Hallucination checker | `["Low similarity (0.41): '...'"]` | Why grounding was flagged. First place to inspect on failures. |
| `grounded_sentences` | string[] | Hallucination checker | `["..."]` | Sentences judged supported by document. |
| `ungrounded_sentences` | string[] | Hallucination checker | `["..."]` | Sentences judged unsupported. |
| `llm_verdict` | object (optional) | LLM/hybrid checker | `{"verdict":"NOT_SUPPORTED","confidence":0.62,"reason":"..."}` | Present for `llm`/`hybrid` paths. Use this when semantic and final verdict differ. |
| `semantic_flags_overridden_by_llm` | string[] (optional) | Hybrid checker | `["..."]` | Sentences semantic flagged, but LLM overruled as supported. |
| `note` | string (optional) | Semantic fallback path | `"sentence-transformers not installed, using keyword-based method"` | Signals semantic model fallback to keyword mode. |

#### 5.4.4 `question_generation` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `model` | string | `utils/question_generator.py` | `"qwen3.5:9b"` or HF served name | Generator model / tag. |
| `provider` | string | Question generator | `"ollama"` or `"vllm"` | Question generation provider. |
| `timestamp` | string (ISO datetime) | Question generator | `"2026-02-13T14:30:25+08:00"` | Time question generation finished for this document. |
| `timezone` | string | Question generator | `"Asia/Singapore"` | Timezone for timestamp field. |
| `num_questions` | integer | Question generator | `3` | Final count of saved `qa_pairs` for this document. |
| `complexity` | string | Question generator config | `"advanced"` | Complexity preset used by prompt builder. |
| `question_types` | string[] | Question generator config/preset | `["analysis","aggregation","inference"]` | Target reasoning types requested in prompts. |
| `question_validation` | object[] \| null | Question validation stage | `[{...}]` | Per-question grounding, comprehensiveness, and answerability audit. Null if all validation disabled. |

#### 5.4.5 `question_generation.question_validation[]` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `question_index` | integer | Question generator | `1` | 1-based index of the question in this document. |
| `original_question` | string | Question generator | `"What is X?"` | First generated form before validation/refinement. |
| `final_question` | string | Question generator | `"How do X and Y interact over time?"` | Final accepted question after checks. |
| `validation_info` | object (optional) | `_validate_and_regenerate_question()` | `{...}` | Grounding-oriented question check metadata (if enabled). |
| `comprehensiveness_check` | object (optional) | `_check_question_comprehensiveness()` | `{...}` | Depth/quality check metadata (if enabled). |
| `answerability_check` | object (optional) | `_check_question_answerability()` | `{...}` | Fully-answerable-from-document check (if enabled). |
| `accepted` | bool | Question validation stage | `true` | Final slot acceptance after all checks. |
| `rejection_reason` | string (optional) | Question validation stage | `"comprehensiveness_check_failed"` | `comprehensiveness_check_failed`, `answerability_check_failed`, or `insufficient_information_answer` (slot loop). |

#### 5.4.6 `validation_info` fields (question grounding check)

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `confidence` | number | Question validation | `0.74` | Grounding confidence for question text against document content. |
| `attempts` | integer | Question validation | `2` | Number of validation/regeneration rounds used. |
| `was_regenerated` | bool | Question validation | `true` | Whether question was rewritten at least once. |
| `is_grounded` | bool | Question validation | `true` | Final grounding status for the question prompt itself. |
| `issues` | string[] | Question validation | `["Low similarity ..."]` | Why question failed earlier attempts. |

#### 5.4.7 `comprehensiveness_check` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `score` | number (0.0-1.0) | Comprehensiveness checker | `0.78` | LLM-assessed quality/depth score. |
| `is_comprehensive` | bool | Comprehensiveness checker | `true` | **Not only complexity**: true means multi-part reasoning + self-contained wording + non-trivial lookup + answerable from document. |
| `attempts` | integer | Comprehensiveness checker | `2` | Evaluation/regeneration rounds run. |
| `was_regenerated` | bool | Comprehensiveness checker | `true` | Whether checker requested a rewritten question. |
| `reason` | string | Comprehensiveness checker | `"Requires synthesis across sections 2 and 5"` | Human-readable rationale from evaluator. |
| `accepted` | bool | Comprehensiveness checker | `true` | Same pass rule as early exit: `is_comprehensive` and `score >= comprehensiveness_min_score`. |

#### 5.4.8 `answerability_check` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `score` | number (0.0-1.0) | Answerability checker | `0.45` | LLM confidence that the question is fully answerable. |
| `is_answerable` | bool | Answerability checker | `false` | False when required facts or compared values are missing. |
| `attempts` | integer | Answerability checker | `2` | Evaluation/regeneration rounds at question-generation stage. |
| `was_regenerated` | bool | Answerability checker | `true` | Whether a replacement question was tried. |
| `reason` | string | Answerability checker | `"2017 salary not in document"` | Human-readable failure explanation. |
| `missing_facts` | string[] | Answerability checker | `["2017-18 salary"]` | Facts the document lacks for this question. |
| `accepted` | bool | Answerability checker | `false` | `is_answerable` and `score >= answerability_min_score`. |

Per-slot pre-check failures appear on `qa_pairs[].hallucination_check` with
`method: answerability_precheck` (not in `answerability_check` metadata).

#### 5.4.9 `answer_generation` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `model` | string | `run_qa_pipeline.py` from `answer_metadata` | `"qwen3.5:9b"` (example) | Model used for final answers. |
| `provider` | string | Answer generation metadata | `"ollama"` / `"vllm"` | Answer provider used. |
| `timestamp` | string (ISO datetime) | Answer metadata | `"2026-02-13T14:31:02+08:00"` | Answer generation timestamp. |
| `timezone` | string | Answer metadata | `"Asia/Singapore"` | Timezone for answer timestamp. |
| `num_answers` | integer | Answer metadata | `3` | Number of answers emitted. Should match number of questions. |

#### 5.4.10 `grading_summary` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `overall_grade` | string (`A`-`F`) \| null | `grade_qa_results()` or `build_grading_summary_block()` in `run_qa_pipeline.py` | `"B"` | Letter from mean confidence. Null only when no usable per-slot confidence exists. |
| `overall_confidence` | number (0.0-1.0) \| null | same as `overall_grade` | `0.84` | Mean confidence over all saved QA pairs (failing slots lower the average). Null if nothing to average. |
| `grading_method` | string | primary judge path or roll-up label | `"hybrid"` | Verifier mode when batch judge returned a full summary. **`average_of_each_qa_pair`** when the document summary is the average of each saved QA pair (no usable batch summary). Older runs may still show `recomputed_from_qa_pairs` (same meaning). |
| `judge_model` | string \| null | `grade_qa_results()` / fallback | `"llama3.1:8b"` (example) | Judge model for llm/hybrid mode; semantic/fallback paths may store non-judge labels. |

### 5.5 Output Field Reference (`run_summary.json`)

Generated by: `bash scripts/utils/summarize_run.sh --json`

| Field | Type | Example | Meaning / troubleshooting |
|------|------|---------|---------------------------|
| `generated_at` | string (ISO datetime) | `"2026-02-13T14:40:11.128392"` | Time the summary file was generated. |
| `total_documents` | integer | `12` | Number of analyzed document JSON files included. |
| `total_qa_pairs` | integer | `36` | Total QA pairs across documents. |
| `grounded_answers` | integer | `31` | Count of grounded QA answers across run. |
| `ungrounded_answers` | integer | `5` | Count of ungrounded QA answers across run. |
| `avg_confidence` | number \| null | `0.83` | Mean of per-document `overall_confidence`. |
| `grade_distribution` | object | `{"A":4,"B":6,"C":2}` | Grade histogram across documents. |
| `generator_model` | string \| null | `"qwen3.5:9b"` (example) | Primary generation model from first document summary. |
| `judge_model` | string \| null | `"llama3.1:8b"` (example) | Judge model for run summaries. |
| `provider` | string \| null | `"ollama"` | Provider used for generation. |
| `good_pairs` | integer | `72` | Count of QA pairs in `*_analysis_minimal_good_pairs.json` across the run folder. |
| `bad_pairs` | integer | `0` | Count of QA pairs in `*_analysis_minimal_bad_pairs.json` across the run folder. |
| `good_plus_bad` | integer | `72` | Convenience total used for split ratio calculations. |
| `bad_ratio` | number | `0.0` | `bad_pairs / (good_pairs + bad_pairs)` rounded to 4 decimals in JSON. |
| `good_ratio` | number | `1.0` | `good_pairs / (good_pairs + bad_pairs)` rounded to 4 decimals in JSON. |
| `run_metrics` | object | `{"timings_seconds":{...},"quality_counters":{...}}` | Run-level timing and retry/rewrite counters aggregated by `summarize_run.sh`. |
| `ungrounded_highlights` | object[] | `[{...}]` | Flat list of all ungrounded QA items with reasons. Fast triage view. |
| `documents` | object[] | `[{...}]` | Per-document summary entries (see below). |

#### 5.5.1 `run_summary.json.documents[]` fields

| Field | Type | Example | Meaning / troubleshooting |
|------|------|---------|---------------------------|
| `file` | string | `"20260213_143031_doc_doc_001_analysis.json"` | Source analysis file name. |
| `document_id` | string | `"doc_001"` | ID copied from document block. |
| `title` | string | `"Q4 Risk Update"` | Document title. |
| `num_qa_pairs` | integer | `3` | Number of QA pairs for that document. |
| `grounded` | integer | `2` | Grounded QA count for this document. |
| `ungrounded` | integer | `1` | Ungrounded QA count for this document. |
| `avg_confidence` | number \| null | `0.81` | Mean confidence across this document's QA pairs. |
| `overall_grade` | string | `"B"` | Copied from document `grading_summary`. |
| `overall_confidence` | number \| null | `0.81` | Copied from document `grading_summary`. |
| `grading_method` | string | `"hybrid"` or `"average_of_each_qa_pair"` | Copied from document `grading_summary`. |
| `model` | string | `"qwen3.5:9b"` (example) | Generator model for this document. |
| `judge_model` | string | `"llama3.1:8b"` (example) | Judge model for this document summary. |
| `provider` | string | `"ollama"` | Provider used for this document. |
| `timestamp` | string | `"2026-02-13T14:30:25+08:00"` | Generation timestamp (question or answer metadata fallback). |
| `timings_seconds` | object | `{"question_generation":2.41,"answer_generation":4.02,"grading":1.33}` | Per-document stage timings emitted by `run_qa_pipeline.py`. |
| `quality_counters` | object | `{"question_grounding_retries":1,"answerability_precheck_failures":2,"answer_grounding_retries":0,"coverage_rewrites":0}` | Per-document counters from `run_qa_pipeline.py`; `answerability_precheck_failures` counts slots that failed the pre-check before answer generation. |
| `qa_details` | object[] | `[{...}]` | Per-QA condensed debugging entries. |

#### 5.5.2 `run_summary.json.documents[].qa_details[]` fields

| Field | Type | Example | Meaning / troubleshooting |
|------|------|---------|---------------------------|
| `question` | string | `"How many incidents ...?"` | Question text for this QA pair. |
| `answer` | string | `"There were 3 incidents..."` | Final answer text for this QA pair. |
| `is_grounded` | bool \| null | `false` | Grounding verdict. Null if grading unavailable. |
| `confidence` | number \| null | `0.48` | Confidence for this QA pair. |
| `method` | string | `"hybrid (semantic + LLM confirmed)"` | Grading path/method used. |
| `issues` | string[] (optional) | `["Low similarity ..."]` | Present for failing QA pairs. |
| `ungrounded_sentences` | string[] (optional) | `["..."]` | Unsupported answer spans. |
| `llm_verdict` | object (optional) | `{"verdict":"NOT_SUPPORTED","reason":"..."}` | Judge explanation when available. |

#### 5.5.3 `run_summary.json.ungrounded_highlights[]` fields

| Field | Type | Example | Meaning / troubleshooting |
|------|------|---------|---------------------------|
| `document` | string | `"doc_001"` | Document ID where failure occurred. |
| `title` | string | `"Q4 Risk Update"` | Document title for quick triage. |
| `question` | string | `"How many incidents ...?"` | Failing question. |
| `answer` | string | `"There were 5 incidents..."` | Failing answer text. |
| `confidence` | number \| null | `0.48` | QA confidence score. |
| `reasons` | string[] | `["Low similarity ...","LLM verdict (NOT_SUPPORTED): ..."]` | Collated human-readable failure reasons. |

#### 5.5.4 Fast debugging path (recommended)

1. Start at `run_summary.json -> ungrounded_highlights` to quickly see what failed.
2. Open the corresponding `*_analysis.json` and inspect
   `qa_pairs[].hallucination_check`.
3. If question quality seems weak, inspect
   `question_generation.question_validation[].comprehensiveness_check` and
   `answerability_check` (`missing_facts`, `score`).
4. If `is_comprehensive` is false repeatedly, tune:
   - `question_generation.validation.comprehensiveness_min_score`
   - `question_generation.validation.comprehensiveness_max_attempts`
5. If `answerability_precheck_failures` is high or `method` is
   `answerability_precheck`, tune `answerability_min_score` or inspect
   `missing_facts` in question_validation.
6. If many answers are ungrounded, inspect `grading.method`, `issues`, and `llm_verdict` before changing prompts or thresholds.

---

## 6. Docker Architecture & Permission Model

### 6.1 Docker layout when `QAG_PROFILE=vllm` (local dual GPU)

**Local stack (`docker-compose.vllm-stack.yml`):** two vLLM services
(`vllm` generator + `vllm-judge`) plus **`qag-runner`**. The runner
calls `http://vllm:7100/v1` and `http://vllm-judge:7101/v1` over compose
internal DNS.

```
Host machine
|
+-- vLLM generator :7100 (served-model-name for llm.model)
+-- vLLM judge     :7101 (served-model-name for judge.model)
|
+-- qag_host/ bind-mounted into qag-runner - Pipeline (strict llm judge); output/, data/, config/, hf_cache (optional)
```

**Alternative (`docker-compose.yml` / `docker-compose.kubeflow.yml`):**
Ollama profiles (`ollama` host Ollama, `kubeflow` in-container Ollama).

**Redserver external vLLM:** the runner is orchestrator-only and calls
gpuserver through the config override, both external base URLs, and redserver
compose extra. It does not start local `:7100` / `:7101` services; see
`docs/architecture/NETWORK_DIAGRAM.md` Diagram C.

**Why split LLM roles:** a **different judge model** than the generator reduces self-evaluation bias (same whether using Ollama tags or vLLM served names).

### 6.2 Permission model (entrypoint pattern)

**Problem:** Docker containers default to running as root. Files created in
bind-mounted volumes are owned by root on the host, making them unreadable
and undeletable by the non-root host user.

**Solution:** Three-layer defence:

**Behavioral flowchart** (host file ownership):

```mermaid
flowchart TD
  START[Container start] --> L1[Layer 1 entrypoint chown writable dirs]
  L1 --> RUN[Pipeline run creates files]
  RUN --> L2[Layer 2 EXIT trap chown on container exit]
  L2 --> HOST[Host run.sh post-run chown privileged userns host]
  HOST --> OK[Files owned by HOST_UID HOST_GID]
```

![ALGORITHM REPORT flowchart 12](ALGORITHM_REPORT_flow_12.png)


| Layer | Where | What it does |
|-------|-------|-------------|
| 1. Entrypoint startup | Inside qag container | `chown` all writable dirs to HOST_UID:HOST_GID before running |
| 2. Entrypoint EXIT trap | Inside qag container | `chown` all writable dirs on exit (catches files created during run) |
| 3. Post-run safety net | Host side (run.sh) | Docker-based `chown` with `--privileged --userns=host` after container exits |

**Why `--privileged --userns=host`:**
- Some Docker installations use **user namespace remapping**, which maps
  container root (UID 0) to an unprivileged host UID. This means `chown`
  inside the container runs as an unprivileged user on the host and fails
  with "Operation not permitted".
- `--userns=host` bypasses this remapping, ensuring `chown` runs as real
  root on the host filesystem.
- `--privileged` grants full capabilities, ensuring `chown` works regardless
  of security profiles (AppArmor, seccomp).

**Why all mounts are `:rw`:**
- Read-only mounts prevent the host user from editing files and cause
  container failures if the application needs to write (e.g., vLLM's
  tokenizer cache in hf_cache).
- Since the entrypoint ensures all files are owned by the host user,
  `:rw` is safe -- the host user can always read, write, and delete.

### 6.3 Volume mounts

All volumes in `docker-compose.yml`:

| Host path | Container path | Mode | Why |
|-----------|---------------|------|-----|
| `./run_qa_pipeline.py` | `/workspace/run_qa_pipeline.py` | rw | Code -- edit on host, changes picked up instantly |
| `./utils/` | `/workspace/utils/` | rw | Code |
| `./scripts/` | `/workspace/scripts/` | rw | Helper scripts |
| `./config/` | `/workspace/config/` | rw | Pipeline config |
| `./data/` | `/workspace/data/` | rw | Input documents |
| `./output/` | `/workspace/output/` | rw | Pipeline results |
| `./hf_cache/` | `/opt/hf_cache` | rw | HuggingFace model cache |
| `./models_embed/` | `/opt/models_embed` | rw | Optional semantic embedding model |

---

## 7. End-to-end flow for one document

**Behavioral flowchart** (one document through save):

```mermaid
flowchart TD
  LOAD[Load JSONL document] --> QG[Question batch §2.5]
  QG --> VQ[Per-question validation §2.6–2.8]
  VQ --> LOOP[Per-slot loop §3.4]
  subgraph SLOTLOOP["Per-slot loop"]
    PRE[Answerability pre-check] --> ANS[generate_answers + coverage §3]
    ANS --> GR[LLM judge grade §4]
    GR --> GATE{Grounding gate passes?}
    GATE -->|Yes| PREF{Rejected answer attempts?}
    PREF -->|Yes| DPO[Capture highest-confidence rejected + chosen]
    PREF -->|No| SLOTDONE[Slot complete]
    DPO --> SLOTDONE
    GATE -->|No replacements left| SLOTDONE
    GATE -->|No + replacements remain| REP[Replacement question x1]
    REP --> PRE
  end
  LOOP --> PRE
  SLOTDONE --> SAVE[Aggregate grade + save analysis JSON §5]
```

![ALGORITHM REPORT flowchart 13](ALGORITHM_REPORT_flow_13.png)


```
1. LOAD document from normalized JSONL (auto-converted from supported formats when needed)

2. GENERATE QUESTIONS (utils/question_generator.py)
   +-- Build complexity-aware prompt (advanced: 10 question types)
   +-- Include few-shot examples (8 good + 4 bad patterns)
   +-- Enforce complexity rules (must reason across 2+ parts)
   +-- Call LLM via configured provider API (this report is vLLM-first;
   |   Ollama/kubeflow are supported) (temperature=0.7 for diversity)
   +-- Parse response, strip ALL trailing type tags
   +-- Deduplicate (LLM judge default, threshold=0.85 guidance)
   +-- Validate each question (LLM judge grounding check — §2.6)
   |   +-- If ungrounded: regenerate (up to 2 attempts)
   +-- Comprehensiveness check (LLM evaluates depth/complexity)
   |   +-- If too simple: regenerate with weakness guidance (up to 2 attempts)
   +-- Answerability check (LLM: fully answerable from document only)
       +-- If not answerable: regenerate with missing-fact guidance (up to 2 attempts)
       +-- If answerability_strict: reject slot at question stage

3. SLOT LOOP (run_qa_pipeline.py + utils/answer_generator.py) — see §3.4
   +-- For each slot 1..N (initial questions from step 2):
   |   +-- Answerability pre-check (when enable_answerability_check)
   |   |   +-- If fail: synthetic pair (answerability_precheck); skip answer+judge
   |   +-- generate_answers for current question (structured prompt,
   |   |   temperature=0.3, coverage rewrite inside generator)
   |   +-- Answer retries up to max_answer_attempts; optional discard when
   |   |   reject_ungrounded_after_retries is true; retain answer_attempts
   |   +-- grade_qa_results + build_qa_pairs for this slot
   |   +-- Grounding gate: is_grounded, confidence >= threshold, not
   |   |   insufficient-information phrase
   |   +-- On gate pass with rejected retries: append highest-confidence
   |   |   rejected + chosen answer to top-level dpo_pairs
   |   +-- If gate fails and replacements remain:
   |       generate_questions (num_questions=1) → retry answer for same slot
   +-- Append final pair per slot; omit failed slots when answerability_strict

4. AGGREGATE GRADE (utils/hallucination_checker.py + run_qa_pipeline.py)
   +-- Per-slot checks already stored on qa_pairs[].hallucination_check
   +-- grading_summary: mean confidence across all saved pairs
   +-- Map to letter grade (A/B/C/D/F)

5. SAVE output JSON to timestamped folder:
   output/<provider>/<model>/YYYY-MM-DD_HHMMSS/
   - Document metadata
   - Q&A pairs with per-pair grounding status and reasons
   - Conditional dpo_pairs with chosen/rejected answers and confidences
   - Supporting evidence (quoted from document)
   - Grading summary (grade, confidence, method)
   - Generation metadata (model, provider, timestamp)
```

---

## 8. Summary of design decisions

### Failure localization protocol

When a run looks bad, localize in this order:

```mermaid
flowchart TD
  BAD[Run looks bad] --> IN[1 Input — JSONL loader extension converter]
  IN --> Q[2 Question — comprehensiveness answerability precheck]
  Q --> A[3 Answer — retries coverage evidence]
  A --> G[4 Grading — judge method auth model]
  G --> FIX[Change one layer rerun compare run_metrics]
```

![ALGORITHM REPORT flowchart 14](ALGORITHM_REPORT_flow_14.png)


1. **Input stage**: malformed/short documents, wrong **file for the loader** (extension), converter mis-configuration, over-filtering.
2. **Question stage**: repetitive/trivial questions, low comprehensiveness scores,
   answerability pre-check failures (`missing_facts`, `answerability_precheck`).
3. **Answer stage**: retries high, weak evidence sections, low coverage.
4. **Grading stage**: semantic/LLM disagreement, key/auth/model mismatch.

Change one layer at a time, rerun, and compare `run_metrics` and `ungrounded_highlights`.

| # | Design decision | Rationale |
|---|----------------|-----------|
| 1 | **10 question types** including synthesis, evaluation, counterfactual | Simple fact-lookup questions don't test comprehension. Complex multi-step questions reveal real understanding gaps |
| 2 | **Few-shot examples** (good + bad) in question prompt | In-context learning produces correctly-typed questions; "bad" examples prevent common mistakes |
| 3 | **Complexity rules** in prompt ("must reason across 2+ parts") | Explicitly prevents the LLM from generating trivial questions |
| 4 | **Structured answer format** with supporting evidence | Forces document grounding and makes aggregation answers easier to verify |
| 5 | **Separate temperatures** (0.7 questions, 0.3 answers) | Questions need diversity; answers need factual accuracy |
| 6 | **Per-slot loop** (answer retries + replacement question) | Answer retries fix wording; slot-level replacement fixes bad questions without batch round logic |
| 7 | **Strict LLM judge grading** | Fail-fast reliability and consistent judge-only behavior in production |
| 8 | **Sliding window** (1/2/3-sentence chunks) | Captures cross-sentence information that single-sentence comparison misses |
| 9 | **Sentence splitting** with abbreviation/decimal/list protection | Prevents "Dr.", "3.5", "1." from creating false ungrounded fragments |
| 10 | **Per-run timestamped folders** (YYYY-MM-DD_HHMMSS) | Multiple runs per day get separate folders; no overwrites |
| 11 | **Ungrounded reasons** in run_summary.json | Analyst can quickly see WHY something is ungrounded without opening each file |
| 12 | **Separate judge model** (second tag or endpoint) | Avoids self-evaluation bias — the generator does not grade its own outputs |
| 13 | **Three-layer permission model** (entrypoint + trap + post-run chown) | Ensures files are always owned by the host user regardless of Docker configuration |
| 14 | **All mounts `:rw`** | Prevents container failures and allows host user to edit/delete freely |
| 15 | **`--privileged --userns=host`** for permission fixes | Bypasses Docker user namespace remapping that blocks `chown` |
| 16 | **Comprehensiveness check** for each question | Prompt instructions are soft guidelines — the LLM sometimes ignores them. A per-question LLM evaluation catches trivial questions that slip through and regenerates them with targeted feedback |
| 17 | **Answerability check** (question stage + per-slot pre-check) | Catches comparison/multi-period questions with missing facts before answer and judge LLM calls; `answerability_strict` on vllm omits unrecoverable slots from output |

---

## 9. Models used

| Model (examples) | Purpose | Runs on |
|-------|---------|---------|
| **Ollama tag e.g. `qwen3.5:9b`** | Question & answer generation | Host Ollama (GPU) |
| **Ollama tag e.g. `llama3.1:8b`** | LLM-as-judge | Same Ollama process, different tag |
| **Legacy HF + vLLM** | Same roles via two containers | See `docker-compose.vllm-stack.yml` |
| **all-MiniLM-L6-v2** | Optional semantic similarity (non-strict paths) | CPU (inside runner; `models_embed/`) |

---

## 10. Configuration reference

```yaml
# config/config.<profile>.yaml — edit the file matching QAG_PROFILE
# (ollama | kubeflow | vllm). See config/README.md.

run:
  input_folder: ""                  # pipeline uses input_file only; leave empty
  input_file: dev-data.jsonl        # .json or .jsonl — extension selects parser in run_qa_pipeline
  input_type: auto                  # not wired to converter CLI; use --input-type there
  max_files: 10                     # not read by run_qa_pipeline or converter
  num_documents: 2                  # 0 = all loaded records
  min_content_words: 500            # skip shorter documents (0 = no minimum)
  min_content_chars: 0              # optional character floor (both can apply)
  semantic_normalization:          # not read — use converter --semantic-normalize
    enable: false
    max_content_chars: 5000

llm:
  provider: "ollama"
  model: "qwen3.5:9b"
  temperature: 0.7               # used for question generation
  max_tokens: 500
  max_retries: 3
  retry_delay: 1.0
  api_key: "ollama-local"
  base_url: "http://localhost:11434/v1"
  timeout: 60

answer_generation:
  temperature: 0.3               # lower temperature for factual answers
  multi_turn:
    enable_rejection: true
    min_confidence_threshold: 0.7
    max_answer_attempts: 5         # total answer trials per slot (vLLM default)
    max_regeneration_attempts: 2   # legacy fallback key
    max_question_regeneration_rounds: 3  # ollama/kubeflow; vllm uses 5
    reject_ungrounded_after_retries: true
  coverage_validation:
    enable: true
    min_score_threshold: 0.7
    max_doc_chars: 5000

question_generation:
  num_questions: 3
  complexity: "advanced"         # "basic", "moderate", "advanced"
  # question_types: [...]        # optional: override which types to use
  duplicate_similarity_threshold: 0.85
  deduplication_method: "llm"
  dedup_llm:
    use_judge_model: true
    strict: true
    max_tokens: 80
  validation:
    enable_rejection: true
    min_confidence_threshold: 0.7
    max_regeneration_attempts: 2
    method: "llm"
    enable_comprehensiveness_check: true  # check depth/complexity of each question
    comprehensiveness_min_score: 0.6     # minimum score to pass (0.0-1.0)
    comprehensiveness_max_attempts: 2    # max regen attempts if too simple
    # comprehensiveness_strict: true ollama/kubeflow; false vllm
    enable_answerability_check: true
    answerability_min_score: 0.8
    answerability_max_attempts: 2
    answerability_max_doc_chars: 6000
    answerability_strict: false  # shipped profiles keep final failed slots

judge:
  provider: "ollama"
  model: "llama3.1:8b"
  base_url: "http://localhost:11434/v1"
  api_key: "ollama-local"
  temperature: 0.0               # deterministic for judging
  max_tokens: 200
  timeout: 60
  max_retries: 3
  retry_delay: 1.0

hallucination:
  method: "llm"                  # strict default
  judge_required: true
  judge_strict_verdict: true
  allow_semantic_fallback: false
```

---

## 11. Agentic Classification

### 11.1 Context

"Agentic AI" refers to systems that autonomously pursue goals by planning,
reasoning, using tools, observing outcomes, and adapting their behaviour
without human intervention at each step. This section evaluates QAG
against established agentic characteristics to clarify what it is and
what it is not.

### 11.2 Agentic traits QAG exhibits

| Trait | Where in QAG | Section |
|-------|-----------------|---------|
| **Self-correction** | Questions undergo grounding, comprehensiveness, and answerability checks. Answers use per-slot retries plus replacement questions, coverage rewrite, and a grounding gate; successful same-question recovery can add `dpo_pairs`. | 2.6, 2.7, 2.8, 3.4, 3.5 |
| **Multi-model tool orchestration** | Coordinates two runtime LLM roles (generator + judge). MiniLM is optional for semantic-only paths | 4.3.4 |
| **Autonomous multi-step execution** | Once started, the full pipeline (generate questions -> generate answers -> grade -> output) runs end-to-end without human intervention | 7 |
| **Adaptive routing (legacy compatibility)** | Optional compatibility routing can delegate edge cases through an alternate grading path; strict llm mode keeps routing disabled by default | 4.3.4 |

### 11.3 Traits QAG does not exhibit

| Trait | What a full agent would do | What QAG does instead |
|-------|---------------------------|--------------------------|
| **Dynamic planning** | Reason about what steps to take next based on the situation | Follows a fixed, predetermined sequence (question gen -> answer gen -> grading) |
| **Goal decomposition** | Break a high-level objective into sub-goals on its own | Stages are hard-coded in the pipeline, not dynamically planned |
| **Environment exploration** | Search for additional information, browse external sources, or adaptively gather context | Processes a given document in a fixed manner with no external retrieval |
| **Cross-run memory** | Learn from previous runs and adapt strategy over time | Each run is stateless and independent |
| **Open-ended tool selection** | Choose which tools to use from an open set based on reasoning | Tool usage is predetermined in the code |

### 11.4 Classification

QAG is best described as a **pipeline with agentic elements** -- it
sits between a simple prompt chain and a fully autonomous agent:

| Characteristic | Simple chain | **QAG** | Full agent |
|----------------|-------------|-------------|------------|
| Fixed steps | Yes | **Yes** | No (dynamic) |
| Self-correction | No | **Yes (retries + coverage rewrite)** | Yes |
| Multi-tool use | No | **Yes (3 models)** | Yes |
| Dynamic planning | No | **No** | Yes |
| Environment interaction | No | **No** | Yes |
| Open-ended reasoning | No | **No** | Yes |

The retry/regeneration loops (Sections 2.6 and 3.4) and adaptive hybrid
routing (Section 4.3.4) are the most agentic features. The pipeline does
not, however, dynamically plan its own execution, explore its environment,
or maintain memory across runs.

### 11.5 What would make QAG more agentic

These are potential extensions, not current features:

- **Dynamic question count** -- let the LLM assess document complexity
  and decide how many questions to generate, rather than using a fixed
  `num_questions` config value.
- **Adaptive temperature** -- adjust generation temperature based on
  grading results from earlier documents in the same run.
- **Strategy switching** -- if retries consistently fail for a question
  type, switch to a different question type or simplify the question
  rather than retrying the same approach.
- **Planning step** -- before generating, have the LLM reason about the
  document's structure and decide which question types would be most
  informative.

---

## Appendix A: Ollama Behavior Differences

This report is vLLM-first. The items below summarize where behavior differs
when using the `ollama` profile.

### A.1 Runtime topology

- Backend is host Ollama (default `http://localhost:11434/v1`).
- Generator and judge can share the same Ollama runtime endpoint, but may
  use different model tags.
- No dual-vLLM container split (`vllm` + `vllm-judge`) in this profile.

### A.2 Operational expectations

- Host must have `ollama` installed and running (`ollama serve`).
- Required model tags must exist in local Ollama store before pipeline run.
- Startup and first-token latency may differ from vLLM due to local model
  warmup behavior.

### A.3 Algorithm invariants (same as vLLM)

- Slot targeting, question/answer retries, grounding gate semantics, and
  output contracts remain the same.
- `num_questions` is still a target slot count, not an absolute final
  guarantee.
- Good/bad splits and retry-based `dpo_pairs` use the same output contract as
  vLLM; `lora_dpo.jsonl` remains conditional.

### A.4 Typical failure modes

- Ollama daemon unreachable on configured port.
- Missing model tags for generator or judge.
- Host-level resource contention (CPU/RAM/GPU) affecting throughput.

---

## Appendix B: Kubeflow Deployment Notes

The `kubeflow` profile wraps pipeline and Ollama behavior into a
containerized deployment style. Core algorithm semantics remain unchanged.

### B.1 Runtime topology

- Ollama runs in-container (single-image workflow).
- Model storage is mounted via `QAG_MODELS_DIR`.
- Container lifecycle can be kept warm across runs until explicit teardown
  (`run.sh --down`).

### B.2 Deployment-specific concerns

- Ensure mounted model store has expected `blobs/` and `manifests/` layout.
- Verify GPU visibility and resource requests are aligned with
  cluster/runtime settings.
- Confirm path and UID/GID mapping if writing outputs to mounted host paths.

### B.3 Algorithm invariants (same as vLLM)

- Question generation, comprehensiveness checks, answer retries, and
  grounding gate are unchanged.
- `*_analysis.json` remains source-of-truth output.
- Good/bad splits and LoRA files are generated via minimise commands;
  retry-based DPO is written only when captured rows exist.
- Host finetuning: `bash run.sh --finetune-lora [RUN_DIR]` (adapter only;
  base model read-only).

### B.4 Typical failure modes

- Missing/incomplete mounted model store.
- Container starts but Ollama service inside is not healthy.
- Mismatch between configured model tags and available in-container models.

---

## Appendix C: Legacy and Compatibility Notes

This section documents historical behavior differences that may affect
interpretation of older runs.

### C.1 Slot count interpretation

- Historical confusion often came from treating `num_questions` as guaranteed
  final pairs.
- Current interpretation: `num_questions` is a target; strict validation and
  runtime outcomes can reduce final pairs.

### C.2 Comprehensiveness and answerability strictness

- `comprehensiveness_strict: true` (`ollama` / `kubeflow`) can reduce slot
  count before answer generation.
- `comprehensiveness_strict: false` (`vllm`) preserves slot progression more
  consistently at question stage, but may lower aggregate quality metrics.
- `enable_answerability_check: true` (all profiles) adds question-stage and
  per-slot pre-checks. The shipped profiles use `answerability_strict: false`;
  enabling it omits failed slots from saved `qa_pairs`.

### C.3 Summary grade vs training split

- `grading_summary.overall_grade` is a document-level aggregate quality
  signal.
- Training curation should use pair-level split outputs:
  - `*_analysis_minimal_good_pairs.json`
  - `*_analysis_minimal_bad_pairs.json`
- Preference training should use captured `dpo_pairs` exported as the
  conditional `lora_dpo.jsonl`.
- It is valid for `overall_grade` to drop while good-pair volume increases,
  depending on retained marginal pairs.

### C.4 Output evolution

- `*_analysis.json` remains source-of-truth.
- Minimal split outputs are post-processing artifacts derived from saved
  analysis.
- For SFT ingestion, use `lora_sft.jsonl`; for DPO, use `lora_dpo.jsonl`.
  The exporter reads captured `dpo_pairs` first and retains an exact-question
  legacy good/bad fallback. Pre-capture runs must be rerun because discarded
  answer attempts cannot be reconstructed.
- Host finetuning: `bash run.sh --finetune-lora [RUN_DIR]` trains a LoRA
  adapter only (Option A). Base weights stay read-only; output goes to
  `QAG_LORA_OUTPUT_DIR` (default `/data/models/Qwen3.5-9B-qag-lora`). Stop
  vLLM before training. Default precision is fp16 with `device_map` sharding
  across `QAG_LORA_GPUS`; set `QAG_LORA_QUANTIZATION_BIT=4` if OOM.

### C.5 Command compatibility (current)

- `bash run.sh --minimise`
- `bash run.sh --minimise-good`
- `bash run.sh --minimise-bad`
- `bash run.sh --export-lora [RUN_DIR]`
- `bash run.sh --finetune-lora [RUN_DIR]`
- `bash run.sh --finetune-dpo [RUN_DIR]`

Always verify command help against current `run.sh --help` for
environment-specific updates.

### C.6 Validated strict vLLM batch (2026-06-26)

Reference run after `answerability_strict` and slot-loop hardening:

| Metric | Result |
|--------|--------|
| Profile | `vllm` (`config/config.vllm.yaml`) |
| Documents | 42 |
| Pipeline errors | 0 |
| Document `overall_grade` | 39 A, 2 D, 1 F (no E grade in scale) |
| Slot omission | Failed pre-check / grounding gate slots omitted from `qa_pairs` |
| Output folder | `output/vllm/qwen-qwen3.5-9b/2026-05-28_145345/` |

Low-grade documents in that batch: `alexis_nchez_nchez_5` (D),
`boles_aw_le_4` (D), `cristiano_ronaldo_goih_3` (F). Use
`bash run.sh --summarize RUN_DIR --json` and per-doc `*_analysis.json` for
follow-up review.

---

## Appendix D: Algorithm documentation baselines

This report is the live source of truth. **Baselines** are frozen, code-verified
copies stored under `docs/algorithm-baselines/vN/` after each major upgrade.

### D.1 Purpose

- Prevent stale or wrong algorithm descriptions in handover and training material.
- Give a versioned diff (`v1` → `v2`) when pipeline behavior changes.

### D.2 Workflow (maintainer)

1. Say **baseline now** in Cursor (or follow
   [`docs/algorithm-baselines/CODE_AUDIT_CHECKLIST.md`](algorithm-baselines/CODE_AUDIT_CHECKLIST.md)).
2. Agent reads `run_qa_pipeline.py` and `utils/*` **before** trusting this report.
3. Mismatches are fixed in this file, `HANDOVER.md`, and diagram sources.
4. `verify_docs_links.py` and confirmation tests must pass.
5. Bundle is copied to `docs/algorithm-baselines/vN/` with `code_audit.json`.

```mermaid
flowchart TD
  upgrade[Pipeline upgrade] --> baseline[baseline now]
  baseline --> audit[Code audit checklist]
  audit --> sync[Update ALGORITHM_REPORT + HANDOVER]
  sync --> snap[Snapshot vN]
  snap --> compare[compare baseline vN-1 and vN]
```

![ALGORITHM REPORT flowchart 15](ALGORITHM_REPORT_flow_15.png)


### D.3 What is snapshotted

- This report (`ALGORITHM_REPORT.md`)
- `HANDOVER.md`
- Grading and pipeline diagram sources (see
  [`docs/algorithm-baselines/README.md`](algorithm-baselines/README.md))

### D.4 Commands

```bash
bash scripts/snapshot_algorithm_baseline.sh --create --summary "after upgrade"
bash scripts/snapshot_algorithm_baseline.sh --list
bash scripts/snapshot_algorithm_baseline.sh --diff v1 v2
```

Rule: `.cursor/rules/algorithm-baseline.mdc`.
