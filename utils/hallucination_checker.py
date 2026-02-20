"""Hallucination checker to verify answers are grounded in source documents.

Methods:
  - "semantic"  : sentence-level cosine similarity via MiniLM (fast, free)
  - "keyword"   : key-phrase substring matching (fast, free)
  - "llm"       : LLM-as-judge via vLLM/OpenAI API (accurate, uses GPU)
  - "hybrid"    : semantic first, LLM fallback for low-confidence sentences
                  (best balance of speed and accuracy)
"""

from typing import Dict, List, Any, Optional
import re
import os
import time


# ---------------------------------------------------------------------------
#  LLM connection config (set by the pipeline before grading)
# ---------------------------------------------------------------------------
_llm_config: Dict[str, Any] = {}
_judge_config: Dict[str, Any] = {}


def set_llm_config(config: Dict[str, Any]) -> None:
    """Store the full pipeline config so the checker can call the LLM.

    The judge uses a SEPARATE model from the generator to avoid self-evaluation
    bias.  If config contains a ``judge`` section, that is used for
    ``_call_llm_judge()``.  Otherwise, falls back to the main ``llm`` section.
    """
    global _llm_config, _judge_config
    _llm_config = config

    # Build judge-specific config: prefer config["judge"], fall back to config["llm"]
    judge_section = config.get("judge", {})
    llm_section = config.get("llm", {})

    # Environment-variable overrides (set by docker-compose)
    judge_base_url = os.getenv("VLLM_JUDGE_BASE_URL", "").strip()
    judge_model = os.getenv("VLLM_JUDGE_MODEL", "").strip()
    judge_api_key = os.getenv("VLLM_JUDGE_API_KEY", "").strip()

    _judge_config = {
        "base_url": judge_base_url or judge_section.get("base_url") or llm_section.get("base_url", "http://localhost:8101/v1"),
        "model": judge_model or judge_section.get("model") or llm_section.get("model", "Qwen/Qwen2.5-7B-Instruct"),
        "api_key": judge_api_key or judge_section.get("api_key") or llm_section.get("api_key", "qwen-local"),
        "timeout": judge_section.get("timeout", llm_section.get("timeout", 60)),
        "max_retries": judge_section.get("max_retries", llm_section.get("max_retries", 3)),
        "retry_delay": judge_section.get("retry_delay", llm_section.get("retry_delay", 1.0)),
    }


def check_hallucination(
    answer: str,
    document_content: str,
    question: Optional[str] = None,
    method: str = "semantic",
) -> Dict[str, Any]:
    if method in ("keyword", "both"):
        return _check_keyword_based(answer, document_content, question)
    if method == "semantic":
        return _check_semantic_based(answer, document_content, question)
    if method == "llm":
        return _check_llm_based(answer, document_content, question)
    if method == "hybrid":
        return _check_hybrid(answer, document_content, question)
    raise ValueError(f"Unknown method: {method}. Use 'keyword', 'semantic', 'llm', or 'hybrid'")


def _check_keyword_based(
    answer: str,
    document_content: str,
    question: Optional[str] = None,
) -> Dict[str, Any]:
    issues: List[str] = []
    grounded_sentences: List[str] = []
    ungrounded_sentences: List[str] = []

    answer_lower = (answer or "").lower()
    doc_lower = (document_content or "").lower()

    sentences = _split_into_sentences(answer or "")

    for sentence in sentences:
        if not sentence.strip():
            continue
        sentence_lower = sentence.lower()

        if any(
            phrase in sentence_lower
            for phrase in [
                "not in the document",
                "not found in the document",
                "not mentioned in the document",
                "not stated in the document",
                "not provided in the document",
                "not explicitly stated",
                "not explicitly mentioned",
            ]
        ):
            grounded_sentences.append(sentence)
            continue

        key_phrases = _extract_key_phrases(sentence)
        found_phrases = 0
        for phrase in key_phrases:
            if len(phrase) > 3 and phrase.lower() in doc_lower:
                found_phrases += 1

        if found_phrases > 0 or len(key_phrases) == 0:
            grounded_sentences.append(sentence)
        else:
            if _is_generic_statement(sentence):
                grounded_sentences.append(sentence)
            else:
                ungrounded_sentences.append(sentence)
                issues.append(
                    f"Potential hallucination: '{sentence[:100]}...' - key phrases not found in document"
                )

    total_sentences = len(grounded_sentences) + len(ungrounded_sentences)
    confidence = (len(grounded_sentences) / total_sentences) if total_sentences else 0.0

    if any(
        phrase in answer_lower
        for phrase in [
            "i don't know",
            "i cannot",
            "i'm not sure",
            "i cannot determine",
            "cannot be determined",
            "not enough information",
        ]
    ):
        confidence = min(confidence + 0.2, 1.0)

    is_grounded = confidence >= 0.7 and len(ungrounded_sentences) == 0

    return {
        "is_grounded": is_grounded,
        "confidence": round(confidence, 3),
        "issues": issues,
        "grounded_sentences": grounded_sentences,
        "ungrounded_sentences": ungrounded_sentences,
        "method": "keyword",
        "total_sentences": total_sentences,
        "grounded_count": len(grounded_sentences),
        "ungrounded_count": len(ungrounded_sentences),
    }


def _check_semantic_based(
    answer: str,
    document_content: str,
    question: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        result = _check_keyword_based(answer, document_content, question)
        result["method"] = "keyword (semantic unavailable)"
        result["note"] = "sentence-transformers not installed, using keyword-based method"
        return result

    try:
        offline_mode = os.getenv("OFFLINE_MODE", "").lower() in ("1", "true", "yes", "on")
        if offline_mode:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        else:
            os.environ.setdefault("HF_HUB_OFFLINE", "0")

        local_model_path = os.getenv("SENTENCE_TRANSFORMERS_MODEL_PATH", "").strip()
        if offline_mode and local_model_path and os.path.isdir(local_model_path):
            model = SentenceTransformer(local_model_path, device="cpu")
        else:
            model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    except Exception as e:
        result = _check_keyword_based(answer, document_content, question)
        result["method"] = "keyword (semantic unavailable)"
        result["note"] = f"Could not load semantic model: {e}. Using keyword-based fallback."
        return result

    issues: List[str] = []
    grounded_sentences: List[str] = []
    ungrounded_sentences: List[str] = []

    answer_sentences = _split_into_sentences(answer or "")
    doc_sentences = _split_into_sentences(document_content or "")

    if not answer_sentences:
        return {
            "is_grounded": False,
            "confidence": 0.0,
            "issues": ["Answer is empty"],
            "grounded_sentences": [],
            "ungrounded_sentences": [],
            "method": "semantic",
        }

    # Build sliding-window chunks from document sentences.
    # A window of 3 consecutive sentences captures cross-sentence context
    # (e.g. "John was arrested. Peter was arrested." → "John was arrested. Peter was arrested.")
    # which single-sentence comparison would miss.
    WINDOW_SIZE = 3
    doc_chunks: List[str] = list(doc_sentences)  # individual sentences
    for w in range(2, WINDOW_SIZE + 1):
        for j in range(len(doc_sentences) - w + 1):
            doc_chunks.append(" ".join(doc_sentences[j : j + w]))

    answer_embeddings = model.encode(answer_sentences)
    doc_chunk_embeddings = model.encode(doc_chunks)

    threshold = 0.5

    for i, answer_sentence in enumerate(answer_sentences):
        if not answer_sentence.strip():
            continue

        similarities = cosine_similarity([answer_embeddings[i]], doc_chunk_embeddings)[0]
        max_similarity = float(np.max(similarities))

        if max_similarity >= threshold:
            grounded_sentences.append(answer_sentence)
        else:
            if _is_generic_statement(answer_sentence):
                grounded_sentences.append(answer_sentence)
            else:
                ungrounded_sentences.append(answer_sentence)
                issues.append(f"Low similarity ({max_similarity:.2f}): '{answer_sentence[:100]}...'")

    total_sentences = len(grounded_sentences) + len(ungrounded_sentences)
    confidence = (len(grounded_sentences) / total_sentences) if total_sentences else 0.0
    is_grounded = confidence >= 0.7 and len(ungrounded_sentences) == 0

    return {
        "is_grounded": is_grounded,
        "confidence": round(confidence, 3),
        "issues": issues,
        "grounded_sentences": grounded_sentences,
        "ungrounded_sentences": ungrounded_sentences,
        "method": "semantic",
        "total_sentences": total_sentences,
        "grounded_count": len(grounded_sentences),
        "ungrounded_count": len(ungrounded_sentences),
    }


# ---------------------------------------------------------------------------
#  LLM-as-judge verification
# ---------------------------------------------------------------------------

_LLM_JUDGE_PROMPT = """You are a grounding verifier. Your job is to determine whether an answer is fully supported by the given document.

DOCUMENT:
{document}

QUESTION:
{question}

ANSWER:
{answer}

Instructions:
1. Check if EVERY claim in the answer is supported by the document.
2. Pay special attention to:
   - Numbers, counts, and aggregations (e.g. "3 men" — verify by counting in the document)
   - Inferences and conclusions drawn from multiple parts of the document
   - Negations and qualifiers
3. Respond with EXACTLY this JSON format (no other text):

{{"verdict": "SUPPORTED" or "NOT_SUPPORTED", "confidence": 0.0 to 1.0, "reason": "brief explanation"}}

If the answer correctly aggregates, counts, or infers from the document, it IS supported.
If the answer adds information not in the document, it is NOT supported."""


def _call_llm_judge(
    answer: str,
    document_content: str,
    question: str,
) -> Dict[str, Any]:
    """Call a SEPARATE judge LLM to verify answer grounding.

    Uses the judge config (Qwen by default) rather than the generator config
    (Llama) to avoid self-evaluation bias — the model that generated the answer
    should NOT be the same model that judges it.
    """
    try:
        import openai
    except ImportError:
        raise RuntimeError(
            "openai package required for LLM-based hallucination checking. "
            "Install with: pip install openai"
        )

    if not _judge_config and not _llm_config:
        raise RuntimeError(
            "LLM config not set. Call set_llm_config() before using method='llm' or 'hybrid'."
        )

    # Use dedicated judge config (Qwen on port 8101)
    jcfg = _judge_config if _judge_config else _llm_config.get("llm", {})
    api_key = jcfg.get("api_key", "not-required")
    if api_key == "EMPTY" or not api_key:
        api_key = "not-required"
    base_url = jcfg.get("base_url", "http://localhost:8101/v1")
    model = jcfg.get("model", "Qwen/Qwen2.5-7B-Instruct")
    timeout = jcfg.get("timeout", 60)
    max_retries = jcfg.get("max_retries", 3)
    retry_delay = jcfg.get("retry_delay", 1.0)

    # Truncate document to avoid exceeding context window
    max_doc_chars = int(os.getenv("HALLUC_MAX_DOC_CHARS", "6000"))
    document_content = document_content or ""
    doc_text = document_content[:max_doc_chars]
    if len(document_content) > max_doc_chars:
        doc_text += "\n... [document truncated] ..."

    prompt = _LLM_JUDGE_PROMPT.format(
        document=doc_text,
        question=question or "(no question provided)",
        answer=answer or "",
    )

    client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,  # deterministic for judging
                max_tokens=200,
            )
            content = response.choices[0].message.content if response.choices else None
            reply = (content or "").strip()
            return _parse_llm_verdict(reply)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            return {
                "verdict": "UNKNOWN",
                "confidence": 0.5,
                "reason": f"LLM call failed: {e}",
            }

    return {"verdict": "UNKNOWN", "confidence": 0.5, "reason": "LLM call exhausted retries"}


def _parse_llm_verdict(reply: str) -> Dict[str, Any]:
    """Parse the LLM's JSON response, tolerating minor formatting issues."""
    import json

    # Try direct JSON parse
    try:
        data = json.loads(reply)
        return {
            "verdict": data.get("verdict", "UNKNOWN").upper(),
            "confidence": float(data.get("confidence", 0.5)),
            "reason": data.get("reason", ""),
        }
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: extract from text
    verdict = "UNKNOWN"
    confidence = 0.5
    reason = reply[:200]

    reply_upper = reply.upper()
    if "NOT_SUPPORTED" in reply_upper or "NOT SUPPORTED" in reply_upper:
        verdict = "NOT_SUPPORTED"
        confidence = 0.3
    elif "SUPPORTED" in reply_upper:
        verdict = "SUPPORTED"
        confidence = 0.8

    # Try to extract confidence number
    conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', reply)
    if conf_match:
        try:
            confidence = min(max(float(conf_match.group(1)), 0.0), 1.0)
        except ValueError:
            pass

    return {"verdict": verdict, "confidence": confidence, "reason": reason}


def _check_llm_based(
    answer: str,
    document_content: str,
    question: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Use the LLM to judge whether the entire answer is grounded in the document.

    This handles aggregation, counting, inference, and multi-hop reasoning
    that sentence-level semantic similarity cannot detect.
    """
    if not answer or not answer.strip():
        return {
            "is_grounded": False,
            "confidence": 0.0,
            "issues": ["Answer is empty"],
            "grounded_sentences": [],
            "ungrounded_sentences": [],
            "method": "llm",
        }

    verdict = _call_llm_judge(answer, document_content, question or "")

    is_supported = verdict["verdict"] == "SUPPORTED"
    confidence = verdict["confidence"]
    reason = verdict.get("reason", "")

    sentences = _split_into_sentences(answer)
    if is_supported:
        grounded = sentences
        ungrounded: List[str] = []
        issues: List[str] = []
    else:
        grounded = []
        ungrounded = sentences
        issues = [f"LLM judge: {reason}"]

    total = len(grounded) + len(ungrounded)
    is_grounded = is_supported and confidence >= 0.7

    return {
        "is_grounded": is_grounded,
        "confidence": round(confidence, 3),
        "issues": issues,
        "grounded_sentences": grounded,
        "ungrounded_sentences": ungrounded,
        "method": "llm",
        "total_sentences": total,
        "grounded_count": len(grounded),
        "ungrounded_count": len(ungrounded),
        "llm_verdict": verdict,
    }


# ---------------------------------------------------------------------------
#  Hybrid: semantic first, LLM fallback for low-confidence sentences
# ---------------------------------------------------------------------------

def _check_hybrid(
    answer: str,
    document_content: str,
    question: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Two-pass verification:
      1. Semantic similarity (fast) — classifies each sentence
      2. LLM-as-judge (accurate) — re-checks sentences that semantic
         marked as ungrounded, since they may involve counting,
         aggregation, or inference that embedding similarity misses.

    This gives the speed of semantic for clearly-grounded sentences,
    and the accuracy of LLM for ambiguous ones.
    """
    # --- Pass 1: semantic check ---
    semantic_result = _check_semantic_based(answer, document_content, question)

    ungrounded = semantic_result.get("ungrounded_sentences", [])
    if not ungrounded:
        # All sentences passed semantic check — no need for LLM
        semantic_result["method"] = "hybrid (semantic only — all passed)"
        return semantic_result

    # --- Pass 2: LLM re-check for ungrounded sentences ---
    # Send the FULL answer + document to the LLM for holistic judgment,
    # since the issue may be aggregation/inference across sentences.
    try:
        llm_verdict = _call_llm_judge(answer, document_content, question or "")
    except Exception as e:
        # LLM unavailable — fall back to semantic-only result
        semantic_result["method"] = "hybrid (LLM unavailable — semantic only)"
        semantic_result["issues"].append(f"LLM fallback failed: {e}")
        return semantic_result

    llm_supported = llm_verdict["verdict"] == "SUPPORTED"
    llm_confidence = llm_verdict["confidence"]

    if llm_supported and llm_confidence >= 0.7:
        # LLM says the answer IS supported — override semantic's ungrounded verdict
        all_sentences = semantic_result.get("grounded_sentences", []) + ungrounded
        total = len(all_sentences)

        return {
            "is_grounded": True,
            "confidence": round(llm_confidence, 3),
            "issues": [],
            "grounded_sentences": all_sentences,
            "ungrounded_sentences": [],
            "method": "hybrid (semantic + LLM override)",
            "total_sentences": total,
            "grounded_count": total,
            "ungrounded_count": 0,
            "llm_verdict": llm_verdict,
            "semantic_ungrounded_overridden": ungrounded,
        }
    else:
        # LLM also says NOT supported — keep semantic's verdict but add LLM detail
        reason = llm_verdict.get("reason", "")
        # Use the lower confidence between semantic and LLM
        combined_conf = min(semantic_result["confidence"], llm_confidence)

        result = dict(semantic_result)
        result["method"] = "hybrid (semantic + LLM confirmed)"
        result["confidence"] = round(combined_conf, 3)
        result["is_grounded"] = combined_conf >= 0.7 and len(ungrounded) == 0
        result["llm_verdict"] = llm_verdict
        if reason:
            result["issues"].append(f"LLM confirms: {reason}")
        return result


def grade_qa_results(
    qa_results: List[Dict[str, Any]],
    method: str = "semantic",
) -> List[Dict[str, Any]]:
    graded_results: List[Dict[str, Any]] = []

    for result in qa_results:
        document_content = (result.get("content") or result.get("text") or result.get("body") or "")
        questions = result.get("questions") or []
        answers = result.get("answers") or []

        hallucination_checks = []
        total_confidence = 0.0
        total_checks = 0

        for question, answer in zip(questions, answers):
            check_result = check_hallucination(
                answer=answer,
                document_content=document_content,
                question=question,
                method=method,
            )
            hallucination_checks.append({"question": question, "answer": answer, "check_result": check_result})
            total_confidence += check_result.get("confidence", 0.0)
            total_checks += 1

        overall_confidence = (total_confidence / total_checks) if total_checks else 0.0

        if overall_confidence >= 0.9:
            overall_grade = "A"
        elif overall_confidence >= 0.8:
            overall_grade = "B"
        elif overall_confidence >= 0.7:
            overall_grade = "C"
        elif overall_confidence >= 0.6:
            overall_grade = "D"
        else:
            overall_grade = "F"

        graded_results.append(
            {
                **result,
                "hallucination_checks": hallucination_checks,
                "overall_grade": overall_grade,
                "overall_confidence": round(overall_confidence, 3),
                "grading_method": method,
                "judge_model": _judge_config.get("model", "unknown") if method in ("llm", "hybrid") else "N/A (semantic only)",
            }
        )

    return graded_results


def _split_into_sentences(text: str) -> List[str]:
    """
    Split text into sentences with proper handling of:
    - Abbreviations (Dr., Mr., Mrs., St., vs., etc.)
    - Decimal numbers (3.5, 0.7, $1.2M)
    - Numbered list items (1. First item  2. Second item)
    - Ellipsis (...)
    - Multi-line text (newlines treated as potential sentence boundaries)
    """
    if not text or not text.strip():
        return []

    # Protect abbreviations from being split
    _ABBREVS = r"(?:Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|vs|etc|inc|ltd|corp|dept|approx|est|govt|intl|natl|assn|assoc|vol|no|fig|ref|pp|ed|rev|gen|sgt|cpl|pvt|lt|col|capt|maj|brig|adm|cmdr)"
    protected = text
    # Protect abbreviation periods: "Dr." → "Dr<DOT>"
    protected = re.sub(
        rf"\b({_ABBREVS})\.\s",
        r"\1<DOT> ",
        protected,
        flags=re.IGNORECASE,
    )
    # Protect numbered list items: "1. " → "1<DOT> ", "12. " → "12<DOT> "
    protected = re.sub(r"(?:^|\n)\s*(\d{1,3})\.\s", r" \1<DOT> ", protected)
    # Protect decimal numbers: "3.5" → "3<DOT>5"
    protected = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", protected)
    # Protect ellipsis: "..." → "<ELLIPSIS>"
    protected = re.sub(r"\.{3,}", "<ELLIPSIS>", protected)

    # Split on sentence-ending punctuation followed by whitespace or end-of-string
    parts = re.split(r"(?<=[.!?])\s+", protected)

    # Also split on newlines (paragraphs are sentence boundaries)
    expanded: List[str] = []
    for part in parts:
        expanded.extend(part.split("\n"))

    # Restore protected tokens and clean up
    sentences: List[str] = []
    for s in expanded:
        s = s.replace("<DOT>", ".").replace("<ELLIPSIS>", "...").strip()
        # Skip fragments that are too short to be meaningful sentences
        # (e.g. standalone numbers "1", "2", single letters)
        if s and len(s) > 2:
            sentences.append(s)
    return sentences


def _extract_key_phrases(sentence: str, min_length: int = 4) -> List[str]:
    stop_words = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "can",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "they",
        "them",
        "their",
    }

    words = re.findall(r"\b\w+\b", sentence.lower())
    phrases: List[str] = []

    for i in range(len(words) - 1):
        if words[i] not in stop_words and words[i + 1] not in stop_words:
            phrase = f"{words[i]} {words[i+1]}"
            if len(phrase) >= min_length:
                phrases.append(phrase)

        if i < len(words) - 2:
            if (
                words[i] not in stop_words
                and words[i + 1] not in stop_words
                and words[i + 2] not in stop_words
            ):
                phrase = f"{words[i]} {words[i+1]} {words[i+2]}"
                if len(phrase) >= min_length:
                    phrases.append(phrase)

    return phrases


def _is_generic_statement(sentence: str) -> bool:
    """
    Detect meta-statements about the document that carry no factual claims.
    These are auto-grounded because penalising them would unfairly lower confidence.
    """
    generic_patterns = [
        r"^the document\b",
        r"^according to the (document|text|article|report)",
        r"^as (stated|mentioned|described|noted|indicated) in the (document|text|article)",
        r"^the document (states|mentions|describes|discusses|says|indicates|notes)",
        r"^based on the (document|text|article|information provided)",
        # Only treat "this/it is" as generic when followed by document-reference context
        r"^this (is a|refers to|means|suggests that|indicates)",
        r"^it (refers to|means|should be noted|is (important|worth noting|clear|evident))",
    ]

    sentence_lower = sentence.lower().strip()
    return any(re.match(pattern, sentence_lower) for pattern in generic_patterns)


def print_grading_report(graded_results: List[Dict[str, Any]]) -> None:
    print("=" * 80)
    print("HALLUCINATION GRADING REPORT")
    print("=" * 80)
    print()

    for i, result in enumerate(graded_results, 1):
        title = result.get("title", result.get("id", f"Document {i}"))
        grade = result.get("overall_grade", "N/A")
        confidence = result.get("overall_confidence", 0.0)
        method = result.get("grading_method", "unknown")

        print(f"Document {i}: {title}")
        print(f"  Overall Grade: {grade} (Confidence: {confidence:.1%})")
        print(f"  Method: {method}")
        print()

        checks = result.get("hallucination_checks", [])
        for j, check in enumerate(checks, 1):
            question = check.get("question", "N/A")
            check_result = check.get("check_result", {})
            is_grounded = check_result.get("is_grounded", False)
            conf = check_result.get("confidence", 0.0)
            issues = check_result.get("issues", [])

            status = "[OK] GROUNDED" if is_grounded else "[WARN] POTENTIAL HALLUCINATION"
            print(f"  Q{j}. {question[:80]}...")
            print(f"     Status: {status} (Confidence: {conf:.1%})")

            if issues:
                print("     Issues:")
                for issue in issues[:3]:
                    print(f"       - {issue[:100]}...")
            print()

        print("-" * 80)
        print()

