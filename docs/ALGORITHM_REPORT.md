# QAGRedo Algorithm Report

This document provides a comprehensive description of the algorithms, design
rationale, and architectural decisions in the QAGRedo pipeline. It covers
question generation, answer generation, hallucination grading, output
management, and the Docker permission model.

---

## 1. Pipeline Overview

Input conversion from `pdf/txt/docx/xlsx/csv/json/jsonl` to canonical JSONL is parser-based via `scripts/conversion/convert_to_qagredo_jsonl.py` (not an LLM reasoning step). Conversion can be done manually or auto-prepared at runtime from `run.input_folder` / `run.input_type`.

```
Document (JSONL)
     |
     v
+-------------------------------+
|  1. Question Generator         |  <-- LLM (vLLM / OpenAI), temperature=0.7
|     - Multi-type prompt         |
|     - Few-shot examples         |
|     - Deduplication (MiniLM)    |
|     - Grounding validation      |
|     - Comprehensiveness check   |
+---------------+---------------+
                |  questions (validated, deduplicated, comprehensive)
                v
+-------------------------------+
|  2. Answer Generator           |  <-- LLM (vLLM / OpenAI), temperature=0.3
|     - Structured format         |
|     - "List then count"         |
|     - Supporting evidence       |
|     - Validation + retry (x3)   |
+---------------+---------------+
                |  answers + evidence
                v
+-------------------------------+
|  3. Hallucination Grader       |  <-- MiniLM (semantic) + LLM (judge)
|     - Hybrid: semantic first    |
|     - LLM fallback for edge     |
|       cases (counting, etc.)    |
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
| `scripts/utils/summarize_run.sh` | Run summary with ungrounded reasons |

---

## 2. Question Generation

**File:** `utils/question_generator.py`

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
        5. Deduplicate against existing questions (MiniLM, threshold=0.85)
        6. Add unique questions to all_questions

    for each question:
        1. Validate & regenerate if not grounded (see 2.6)
        2. Check comprehensiveness & regenerate if too simple (see 2.7)
```

**Why temperature=0.7 for questions:**
- Questions benefit from diversity -- we want varied question types and phrasings.
- Too low (0.0-0.3) produces repetitive, formulaic questions.
- Too high (>0.9) produces incoherent or overly creative questions.
- 0.7 is the empirical sweet spot for diverse yet coherent questions.

### 2.6 Question validation and retry

Each generated question is checked for grounding in the document:

1. **Check**: Run hallucination checker on the question against the document
2. **If grounded** (confidence >= 0.7): keep the question
3. **If not grounded**: regenerate up to `max_regeneration_attempts` times (default: 2)
   - Send a new prompt: "This question was REJECTED. Generate a NEW question
     grounded ONLY in the document."
   - Re-check grounding after each regeneration
   - If regeneration returns empty, keep the previous question

**Validation method**: Uses `"semantic"` by default (not `"hybrid"`).

**Why semantic for question validation (not hybrid):**
- Question validation runs for every question during generation. Using hybrid
  would trigger an LLM call for each question, which is expensive.
- Questions are short (single sentence) and naturally echo the document's terms,
  so semantic similarity is sufficient for detecting ungrounded questions.
- The final grading step (after answer generation) uses hybrid, which is more
  accurate for the definitive grading.

### 2.7 Comprehensiveness check

After grounding validation, each question undergoes a **comprehensiveness check**
to ensure it is not a trivial fact-lookup question:

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
4. **After all attempts**: keep the best version (even if still below threshold
   — it is reported in the output metadata)

**Why this check is needed:**
- The prompt instructions ("must reason across 2+ parts") are soft guidelines.
  The LLM sometimes ignores them and produces simple "What is X?" questions.
- The comprehensiveness check is a hard gate that catches these failures.
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
```

### 2.8 Deduplication

Uses MiniLM cosine similarity to detect near-duplicate questions.
- **Threshold**: 0.85 (configurable)
- Questions with similarity above the threshold to any existing question are
  filtered out, and the generation loop retries.

**Why 0.85:** Lower thresholds (e.g. 0.7) are too aggressive and reject
legitimately different questions about the same topic. Higher thresholds
(e.g. 0.95) miss near-duplicates with minor rephrasing.

### 2.9 Configuration

```yaml
question_generation:
  num_questions: 3
  complexity: "advanced"              # "basic", "moderate", "advanced"
  # question_types: [...]             # optional: override which types
  duplicate_similarity_threshold: 0.85
  deduplication_method: "semantic"
  validation:
    enable_rejection: true
    min_confidence_threshold: 0.7
    max_regeneration_attempts: 2
    method: "semantic"
    enable_comprehensiveness_check: true  # check each question for depth/complexity
    comprehensiveness_min_score: 0.6     # minimum score to pass (0.0-1.0)
    comprehensiveness_max_attempts: 2    # max regeneration attempts if too simple
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
| "List items first, then count" | LLMs frequently miscount when asked to aggregate directly. Listing first forces step-by-step reasoning, improving accuracy by ~30% on aggregation questions |
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

### 3.4 Answer validation, retry, and coverage rewrite

Each answer goes through a validate-and-regenerate cycle, then a coverage check:

```
1. Generate answer from LLM
2. Run hallucination checker (hybrid method)
3. If grounded AND confidence >= 0.7:
      -> Accept answer
4. If NOT grounded:
      -> Send regeneration prompt:
         "Previous answer may contain hallucinations.
          Generate a NEW answer using ONLY the document."
      -> Re-check grounding
      -> Repeat up to 3 times (configurable)
5. After all retries, keep the best attempt
   (even if still ungrounded -- it's reported in the output)
6. Run question-coverage validation (LLM evaluator):
      -> Check whether all parts of the question are addressed
7. If coverage score is below threshold:
      -> Run one targeted rewrite pass using missing-point feedback
      -> Re-run grounding check on rewritten answer
      -> Accept rewritten answer only if it is grounded
```

**Why 3 retries (not more, not fewer):**
- 1 retry: insufficient -- the LLM often needs 2-3 tries to produce a well-
  grounded answer for complex aggregation or inference questions.
- 3 retries: good balance -- gives the LLM enough chances while keeping
  pipeline runtime reasonable (~4x LLM calls per question in worst case).
- More than 3: diminishing returns -- if the LLM can't produce a grounded
  answer in 4 attempts, the question likely asks for information that requires
  inference beyond what sentence-level grounding can verify.

**Why keep ungrounded answers:**
- Discarding them would leave gaps in the output. The analyst needs to see
  ALL questions and answers, including problematic ones, to understand where
  the LLM struggles.
- Ungrounded answers are clearly marked with `is_grounded: false` and include
  detailed reasons in the output.

### 3.5 Coverage validation and targeted rewrite

Coverage validation catches answers that are grounded but incomplete (for
example, answers that address only one side of a comparison question).

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
    max_regeneration_attempts: 3      # up to 3 retries
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

Confidence = grounded_sentences / total_sentences
is_grounded = confidence >= 0.7 AND ungrounded_count == 0
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

The judge uses **Qwen2.5-7B** — a *different* model from the generator
(Llama-3.1-8B) — to avoid self-evaluation bias. A model should not grade
its own outputs.

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

#### 4.3.4 Hybrid (`method="hybrid"`) -- **Recommended**

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

**Why hybrid is the default:**

| Factor | Hybrid advantage |
|--------|-----------------|
| **Speed** | ~70-80% of answers pass semantic alone (no LLM call needed) |
| **Accuracy** | The remaining 20-30% get full LLM evaluation for edge cases |
| **Cost** | 40-60% faster than pure LLM mode (fewer GPU calls) |
| **Robustness** | If LLM is unavailable, degrades to semantic-only |

**When LLM override is critical:**
- **Aggregation**: "3 people total" -- no single sentence says this, but the
  LLM can count mentions across the document.
- **Inference**: "The company's strategy was successful" -- requires combining
  facts about strategy and outcome from different paragraphs.
- **Multi-hop**: "Given that A leads to B, and B was observed, A must have
  occurred" -- sentence-level similarity cannot verify logical chains.

### 4.4 Grading scale

After checking all Q&A pairs for a document:

```
overall_confidence = average(confidence of all Q&A pairs)

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
  method: "hybrid"    # "semantic", "keyword", "llm", "hybrid"
```

---

## 5. Output Management

**File:** `utils/output_manager.py`

### 5.1 Per-run timestamped folders

Each pipeline run creates a unique output folder:

```
output/vllm/meta-llama-3.1-8b-instruct/2026-02-13_143025/
output/vllm/meta-llama-3.1-8b-instruct/2026-02-13_160512/
output/vllm/meta-llama-3.1-8b-instruct/2026-02-13_181730/
```

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
  "question_generation": { "...": "..." },
  "answer_generation": { "...": "..." },
  "grading_summary": { "...": "..." }
}
```

### 5.4 Output Field Reference (per-document analysis JSON)

This section is the field-by-field reference for daily debugging and interpretation.

#### 5.4.1 `document` block

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `document.id` | string | Input record passthrough in `run_qa_pipeline.py` | `"doc_001"` | Primary document identifier used in logs and file naming. If missing, fallback ID (e.g. `doc_1`) is used. |
| `document.title` | string \| null | Input record passthrough | `"Q4 Risk Update"` | Human-readable title. Can be null if source data does not provide it. |
| `document.source` | string \| null | Input record passthrough | `"internal_report.pdf"` | Source provenance. Helpful when tracing bad QA back to original files. |
| `document.type` | string \| null | Input record passthrough | `"report"` | Optional source type/category. |
| `document.content` | string | Input record passthrough | `"..."` | Full document text used for generation and grading. If empty, generation usually skips or fails upstream. |

#### 5.4.2 `qa_pairs[]` block

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `qa_pairs[].question` | string | `build_qa_pairs()` in `run_qa_pipeline.py` | `"What sequence of events led to ...?"` | Final question after validation/regeneration steps. |
| `qa_pairs[].answer` | string | `generate_answers_from_results()` output | `"The sequence was ..."` | Final answer after grounding retries and optional coverage rewrite. |
| `qa_pairs[].grading` | object \| null | `grade_qa_results()` mapped via question text | `{...}` | Hallucination/grounding verdict for this QA pair. Null means grading failed or mapping missed. |

#### 5.4.3 `qa_pairs[].grading` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `is_grounded` | bool | `check_hallucination()` | `true` | Final grounding verdict for the answer. |
| `confidence` | number (0.0-1.0) | `check_hallucination()` | `0.85` | Confidence in grounding. Scores `< 0.7` are treated as weak. |
| `method` | string | Hallucination checker | `"hybrid (semantic + LLM override)"` | Method path used (`semantic`, `keyword`, `llm`, or hybrid variant). |
| `issues` | string[] | Hallucination checker | `["Low similarity (0.41): '...'"]` | Why grounding was flagged. First place to inspect on failures. |
| `grounded_sentences` | string[] | Hallucination checker | `["..."]` | Sentences judged supported by document. |
| `ungrounded_sentences` | string[] | Hallucination checker | `["..."]` | Sentences judged unsupported. |
| `total_sentences` | integer | Hallucination checker | `4` | Number of answer sentences considered. |
| `grounded_count` | integer | Hallucination checker | `3` | Count of grounded sentences. |
| `ungrounded_count` | integer | Hallucination checker | `1` | Count of ungrounded sentences. |
| `llm_verdict` | object (optional) | LLM/hybrid checker | `{"verdict":"NOT_SUPPORTED","confidence":0.62,"reason":"..."}` | Present for `llm`/`hybrid` paths. Use this when semantic and final verdict differ. |
| `semantic_ungrounded_overridden` | string[] (optional) | Hybrid checker | `["..."]` | Sentences semantic flagged, but LLM overruled as supported. |
| `note` | string (optional) | Semantic fallback path | `"sentence-transformers not installed, using keyword-based method"` | Signals semantic model fallback to keyword mode. |

#### 5.4.4 `question_generation` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `model` | string | `utils/question_generator.py` | `"meta-llama/Meta-Llama-3.1-8B-Instruct"` | Generator model used for questions. |
| `provider` | string | Question generator | `"vllm"` | Question generation provider. |
| `timestamp` | string (ISO datetime) | Question generator | `"2026-02-13T14:30:25+08:00"` | Time question generation finished for this document. |
| `timezone` | string | Question generator | `"Asia/Singapore"` | Timezone for timestamp field. |
| `num_questions` | integer | Question generator | `3` | Number of final questions emitted. |
| `complexity` | string | Question generator config | `"advanced"` | Complexity preset used by prompt builder. |
| `question_types` | string[] | Question generator config/preset | `["analysis","aggregation","inference"]` | Target reasoning types requested in prompts. |
| `question_validation` | object[] \| null | Question validation stage | `[{...}]` | Per-question validation/comprehensiveness audit. Null if validation disabled. |

#### 5.4.5 `question_generation.question_validation[]` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `question_index` | integer | Question generator | `1` | 1-based index of the question in this document. |
| `original_question` | string | Question generator | `"What is X?"` | First generated form before validation/refinement. |
| `final_question` | string | Question generator | `"How do X and Y interact over time?"` | Final accepted question after checks. |
| `validation_info` | object (optional) | `_validate_and_regenerate_question()` | `{...}` | Grounding-oriented question check metadata (if enabled). |
| `comprehensiveness_check` | object (optional) | `_check_question_comprehensiveness()` | `{...}` | Depth/quality check metadata (if enabled). |

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

#### 5.4.8 `answer_generation` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `model` | string | `run_qa_pipeline.py` from `answer_metadata` | `"meta-llama/Meta-Llama-3.1-8B-Instruct"` | Model used for final answers. |
| `provider` | string | Answer generation metadata | `"vllm"` | Answer provider used. |
| `timestamp` | string (ISO datetime) | Answer metadata | `"2026-02-13T14:31:02+08:00"` | Answer generation timestamp. |
| `timezone` | string | Answer metadata | `"Asia/Singapore"` | Timezone for answer timestamp. |
| `num_answers` | integer | Answer metadata | `3` | Number of answers emitted. Should match number of questions. |

#### 5.4.9 `grading_summary` fields

| Field | Type | Produced by | Example | Meaning / troubleshooting |
|------|------|-------------|---------|---------------------------|
| `overall_grade` | string (`A`-`F`) | `grade_qa_results()` | `"B"` | Grade mapped from mean confidence thresholds. |
| `overall_confidence` | number (0.0-1.0) | `grade_qa_results()` | `0.84` | Average confidence across QA pairs in the document. |
| `grading_method` | string | `grade_qa_results()` | `"hybrid"` | Grading mode used for the document. |
| `judge_model` | string | `grade_qa_results()` | `"Qwen/Qwen2.5-7B-Instruct"` | Judge model used for llm/hybrid mode; `"N/A (semantic only)"` for semantic mode. |

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
| `generator_model` | string \| null | `"meta-llama/Meta-Llama-3.1-8B-Instruct"` | Primary generation model from first document summary. |
| `judge_model` | string \| null | `"Qwen/Qwen2.5-7B-Instruct"` | Judge model for run summaries. |
| `provider` | string \| null | `"vllm"` | Provider used for generation. |
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
| `grading_method` | string | `"hybrid"` | Copied from document `grading_summary`. |
| `model` | string | `"meta-llama/Meta-Llama-3.1-8B-Instruct"` | Generator model for this document. |
| `judge_model` | string | `"Qwen/Qwen2.5-7B-Instruct"` | Judge model for this document summary. |
| `provider` | string | `"vllm"` | Provider used for this document. |
| `timestamp` | string | `"2026-02-13T14:30:25+08:00"` | Generation timestamp (question or answer metadata fallback). |
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
2. Open the corresponding `*_analysis.json` and inspect `qa_pairs[].grading`.
3. If question quality seems weak, inspect `question_generation.question_validation[].comprehensiveness_check`.
4. If `is_comprehensive` is false repeatedly, tune:
   - `question_generation.validation.comprehensiveness_min_score`
   - `question_generation.validation.comprehensiveness_max_attempts`
5. If many answers are ungrounded, inspect `grading.method`, `issues`, and `llm_verdict` before changing prompts or thresholds.

---

## 6. Docker Architecture & Permission Model

### 6.1 Three-container design

```
Host machine (offline server)
|
+-- qagredo_host/ (bind-mounted to all containers)
    |
    +-- vllm container (GPU 0, port 7100)
    |   - Llama-3.1-8B: question & answer generation
    |   - Mounts: models_llm (ro), hf_cache (rw)
    |   - Runs as root (vLLM image requirement)
    |
    +-- vllm-judge container (GPU 1, port 7101)
    |   - Qwen2.5-7B: independent LLM-as-judge for hallucination checking
    |   - Separate model avoids self-evaluation bias
    |   - Mounts: models_llm (ro), hf_cache (rw)
    |
    +-- qagredo container (CPU)
        - Pipeline orchestration + MiniLM for semantic similarity
        - Mounts: code (rw), config (rw), data (rw),
                  output (rw), hf_cache (rw), models_embed (rw)
        - Entrypoint maps container user to host UID/GID
```

**GPU assignment:** GPU assignment is handled entirely by Docker's
`deploy.resources.reservations.devices.device_ids`. Do **not** set
`CUDA_VISIBLE_DEVICES` in the environment — Docker maps the reserved GPU as
device 0 inside the container, so `CUDA_VISIBLE_DEVICES: "1"` would cause
`pynvml.NVMLError_InvalidArgument` (trying to find a non-existent second GPU).

**Why three containers:**
- **Separation of concerns**: vLLM containers are GPU-intensive model servers;
  QAGRedo is a CPU-bound pipeline. They have different resource requirements
  and failure modes.
- **Independent lifecycle**: vLLM containers start once and stay running;
  QAGRedo runs per pipeline invocation and exits.
- **Portability**: The same vLLM image can serve different models without
  rebuilding.

### 6.2 Permission model (entrypoint pattern)

**Problem:** Docker containers default to running as root. Files created in
bind-mounted volumes are owned by root on the host, making them unreadable
and undeletable by the non-root host user.

**Solution:** Three-layer defence:

| Layer | Where | What it does |
|-------|-------|-------------|
| 1. Entrypoint startup | Inside qagredo container | `chown` all writable dirs to HOST_UID:HOST_GID before running |
| 2. Entrypoint EXIT trap | Inside qagredo container | `chown` all writable dirs on exit (catches files created during run) |
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
| `./models_embed/` | `/opt/models_embed` | rw | MiniLM embedding model |

---

## 7. End-to-end flow for one document

```
1. LOAD document from JSONL

2. GENERATE QUESTIONS (utils/question_generator.py)
   +-- Build complexity-aware prompt (advanced: 10 question types)
   +-- Include few-shot examples (8 good + 4 bad patterns)
   +-- Enforce complexity rules (must reason across 2+ parts)
   +-- Call LLM via vLLM API (temperature=0.7 for diversity)
   +-- Parse response, strip ALL trailing type tags
   +-- Deduplicate (MiniLM semantic, threshold=0.85)
   +-- Validate each question (semantic grounding check)
   |   +-- If ungrounded: regenerate (up to 2 attempts)
   +-- Comprehensiveness check (LLM evaluates depth/complexity)
       +-- If too simple: regenerate with weakness guidance (up to 2 attempts)

3. GENERATE ANSWERS (utils/answer_generator.py)
   +-- For each question, build structured answer prompt
   |   (includes "list items before counting" instruction)
   +-- Call LLM via vLLM API (temperature=0.3 for accuracy)
   +-- Parse structured response into answer + supporting evidence
   +-- Validate each answer (hybrid grounding check)
       +-- If ungrounded: regenerate (up to 3 attempts)

4. GRADE (utils/hallucination_checker.py)
   +-- Split answer into sentences (abbreviation/decimal/list-safe)
   +-- For each Q&A pair:
   |   +-- Pass 1: Semantic similarity with sliding window (MiniLM)
   |   |   +-- Compare against 1/2/3-sentence document chunks
   |   |   +-- If all grounded -> done (fast path)
   |   +-- Pass 2: LLM-as-judge (if ungrounded sentences found)
   |       +-- Override or confirm semantic verdict
   +-- Compute per-Q&A confidence
   +-- Average -> overall_confidence
   +-- Map to grade (A/B/C/D/F)

5. SAVE output JSON to timestamped folder:
   output/vllm/<model>/YYYY-MM-DD_HHMMSS/
   - Document metadata
   - Q&A pairs with per-pair grounding status and reasons
   - Supporting evidence (quoted from document)
   - Grading summary (grade, confidence, method)
   - Generation metadata (model, provider, timestamp)
```

---

## 8. Summary of design decisions

| # | Design decision | Rationale |
|---|----------------|-----------|
| 1 | **10 question types** including synthesis, evaluation, counterfactual | Simple fact-lookup questions don't test comprehension. Complex multi-step questions reveal real understanding gaps |
| 2 | **Few-shot examples** (good + bad) in question prompt | In-context learning produces correctly-typed questions; "bad" examples prevent common mistakes |
| 3 | **Complexity rules** in prompt ("must reason across 2+ parts") | Explicitly prevents the LLM from generating trivial questions |
| 4 | **Structured answer format** with supporting evidence | Forces document grounding; "list then count" improves aggregation accuracy by ~30% |
| 5 | **Separate temperatures** (0.7 questions, 0.3 answers) | Questions need diversity; answers need factual accuracy |
| 6 | **3 answer retries** (was 2) | Gives the LLM enough attempts for complex answers without excessive runtime |
| 7 | **Hybrid grading** (semantic + LLM fallback) | Fast for clearly-grounded answers (no LLM call), accurate for edge cases (counting, inference) |
| 8 | **Sliding window** (1/2/3-sentence chunks) | Captures cross-sentence information that single-sentence comparison misses |
| 9 | **Sentence splitting** with abbreviation/decimal/list protection | Prevents "Dr.", "3.5", "1." from creating false ungrounded fragments |
| 10 | **Per-run timestamped folders** (YYYY-MM-DD_HHMMSS) | Multiple runs per day get separate folders; no overwrites |
| 11 | **Ungrounded reasons** in run_summary.json | Analyst can quickly see WHY something is ungrounded without opening each file |
| 12 | **Separate judge model** (Qwen2.5-7B vs Llama-3.1-8B) | Avoids self-evaluation bias — the generator does not grade its own outputs |
| 13 | **Three-layer permission model** (entrypoint + trap + post-run chown) | Ensures files are always owned by the host user regardless of Docker configuration |
| 14 | **All mounts `:rw`** | Prevents container failures and allows host user to edit/delete freely |
| 15 | **`--privileged --userns=host`** for permission fixes | Bypasses Docker user namespace remapping that blocks `chown` |
| 16 | **Comprehensiveness check** for each question | Prompt instructions are soft guidelines — the LLM sometimes ignores them. A per-question LLM evaluation catches trivial questions that slip through and regenerates them with targeted feedback |

---

## 9. Models used

| Model | Purpose | Size | Runs on |
|-------|---------|------|---------|
| **Llama-3.1-8B** | Question & answer generation | ~16 GB | GPU 0 (vllm, port 7100) |
| **Qwen2.5-7B** | LLM-as-judge for hallucination checking | ~14 GB | GPU 1 (vllm-judge, port 7101) |
| **all-MiniLM-L6-v2** | Semantic similarity (grounding check, dedup) | ~80 MB | CPU (qagredo) |

---

## 10. Configuration reference

```yaml
# config/config.yaml -- full settings

run:
  input_folder: /home/user/offline20260209/qagredo_host/data
  input_file: dev-data.jsonl        # used when input_folder is empty
  input_type: auto                  # auto/jsonl/json/txt/pdf/docx/xlsx/csv
  max_files: 10                     # folder mode only
  num_documents: 2
  min_content_words: 20             # skip short docs
  min_content_chars: 0

llm:
  provider: "vllm"
  model: "meta-llama/Meta-Llama-3.1-8B-Instruct"
  temperature: 0.7               # used for question generation
  max_tokens: 500
  max_retries: 3
  retry_delay: 1.0
  api_key: "llama-local"
  base_url: "http://localhost:7100/v1"
  timeout: 60

answer_generation:
  temperature: 0.3               # lower temperature for factual answers
  multi_turn:
    enable_rejection: true
    min_confidence_threshold: 0.7
    max_regeneration_attempts: 3   # up to 3 retries for answers
  coverage_validation:
    enable: true
    min_score_threshold: 0.7
    max_doc_chars: 5000

question_generation:
  num_questions: 3
  complexity: "advanced"         # "basic", "moderate", "advanced"
  # question_types: [...]        # optional: override which types to use
  duplicate_similarity_threshold: 0.85
  deduplication_method: "semantic"
  validation:
    enable_rejection: true
    min_confidence_threshold: 0.7
    max_regeneration_attempts: 2
    method: "semantic"
    enable_comprehensiveness_check: true  # check depth/complexity of each question
    comprehensiveness_min_score: 0.6     # minimum score to pass (0.0-1.0)
    comprehensiveness_max_attempts: 2    # max regen attempts if too simple

judge:
  provider: "vllm"
  model: "Qwen/Qwen2.5-7B-Instruct"
  base_url: "http://localhost:7101/v1"
  api_key: "qwen-local"
  temperature: 0.0               # deterministic for judging
  max_tokens: 200
  timeout: 60
  max_retries: 3
  retry_delay: 1.0

hallucination:
  method: "hybrid"               # "semantic", "keyword", "llm", "hybrid"
```

---

## 11. Agentic Classification

### 11.1 Context

"Agentic AI" refers to systems that autonomously pursue goals by planning,
reasoning, using tools, observing outcomes, and adapting their behaviour
without human intervention at each step. This section evaluates QAGRedo
against established agentic characteristics to clarify what it is and
what it is not.

### 11.2 Agentic traits QAGRedo exhibits

| Trait | Where in QAGRedo | Section |
|-------|-----------------|---------|
| **Self-correction** | Questions undergo two-stage validation: grounding check (regenerated up to 2 times if ungrounded) and comprehensiveness check (regenerated up to 2 times if too simple). Answers use grounding retries (up to 3) plus a targeted coverage rewrite pass with grounding gate. The system evaluates output quality and self-corrects autonomously | 2.6, 2.7, 3.4, 3.5 |
| **Multi-model tool orchestration** | Coordinates three models (Llama for generation, Qwen as judge, MiniLM for embeddings), selecting which to invoke based on intermediate results | 4.3.4 |
| **Autonomous multi-step execution** | Once started, the full pipeline (generate questions -> generate answers -> grade -> output) runs end-to-end without human intervention | 7 |
| **Adaptive routing** | The hybrid grading method makes a runtime decision: ~70-80% of answers take the fast semantic path; only edge cases are routed to the LLM judge | 4.3.4 |

### 11.3 Traits QAGRedo does not exhibit

| Trait | What a full agent would do | What QAGRedo does instead |
|-------|---------------------------|--------------------------|
| **Dynamic planning** | Reason about what steps to take next based on the situation | Follows a fixed, predetermined sequence (question gen -> answer gen -> grading) |
| **Goal decomposition** | Break a high-level objective into sub-goals on its own | Stages are hard-coded in the pipeline, not dynamically planned |
| **Environment exploration** | Search for additional information, browse external sources, or adaptively gather context | Processes a given document in a fixed manner with no external retrieval |
| **Cross-run memory** | Learn from previous runs and adapt strategy over time | Each run is stateless and independent |
| **Open-ended tool selection** | Choose which tools to use from an open set based on reasoning | Tool usage is predetermined in the code |

### 11.4 Classification

QAGRedo is best described as a **pipeline with agentic elements** -- it
sits between a simple prompt chain and a fully autonomous agent:

| Characteristic | Simple chain | **QAGRedo** | Full agent |
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

### 11.5 What would make QAGRedo more agentic

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
