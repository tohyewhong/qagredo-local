"""Hallucination checker to verify answers are grounded in source documents.

Methods:
  - "keyword"   : key-phrase substring matching (fast, free)
  - "semantic"  : legacy alias → same as ``keyword`` (embedding path removed)
  - "llm"       : LLM-as-judge via Ollama / vLLM / OpenAI API
  - "hybrid"    : legacy alias → LLM-as-judge only (former embedding pass removed)
"""

from typing import Dict, List, Any, Optional
import re
import os
import time
import json
from urllib.request import Request, urlopen

from .config_manager import validate_provider_for_offline_mode
from .document_text import extract_document_text


# ---------------------------------------------------------------------------
#  LLM connection config (set by the pipeline before grading)
# ---------------------------------------------------------------------------
_llm_config: Dict[str, Any] = {}
_judge_config: Dict[str, Any] = {}


def _hallucination_policy() -> Dict[str, Any]:
    """Read hallucination policy flags from the active pipeline config."""
    if not isinstance(_llm_config, dict):
        return {}
    raw = _llm_config.get("hallucination")
    return raw if isinstance(raw, dict) else {}


def _config_bool(value: Any, default: bool = False) -> bool:
    """YAML-friendly bool parser used by strict judge policy flags."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    sval = str(value).strip().lower()
    if sval in ("1", "true", "yes", "on"):
        return True
    if sval in ("0", "false", "no", "off", ""):
        return False
    return default


def _strict_llm_judge_required() -> bool:
    """Whether llm judging must succeed without permissive fallbacks."""
    policy = _hallucination_policy()
    judge_required = _config_bool(policy.get("judge_required"), default=False)
    strict_verdict = _config_bool(
        policy.get("judge_strict_verdict"), default=False
    )
    return judge_required or strict_verdict


def _call_ollama_chat_native(
    *,
    base_url: str,
    model: str,
    prompt: str,
    max_retries: int,
    retry_delay: float,
    timeout: float,
    temperature: float,
    max_tokens: int,
) -> str:
    root = base_url.split("/v1", 1)[0].rstrip("/")
    endpoint = f"{root}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    for attempt in range(max_retries):
        try:
            req = Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            msg = body.get("message", {}) if isinstance(body, dict) else {}
            content = str(msg.get("content", "")).strip()
            if content:
                return content
            return str(msg.get("thinking", "")).strip()
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            return ""
    return ""


def set_llm_config(config: Dict[str, Any]) -> None:
    """Store the full pipeline config so the checker can call the LLM.

    The judge uses a SEPARATE model from the generator to avoid self-evaluation
    bias.  If config contains a ``judge`` section, that is used for
    ``_call_llm_judge()``.  Otherwise, falls back to the main ``llm`` section.
    """
    global _llm_config, _judge_config
    _llm_config = config

    # Build judge-specific config: prefer config["judge"], fall back to
    # config["llm"]
    judge_section = config.get("judge", {})
    llm_section = config.get("llm", {})

    provider = (
        judge_section.get("provider")
        or llm_section.get("provider")
        or "ollama"
    )
    provider = str(provider).lower()

    # Environment-variable overrides are scoped to the selected judge provider.
    # This avoids an Ollama judge accidentally reading VLLM_JUDGE_* values.
    prefix = provider.upper()
    judge_base_url = os.getenv(f"{prefix}_JUDGE_BASE_URL", "").strip()
    judge_model = os.getenv(f"{prefix}_JUDGE_MODEL", "").strip()
    judge_api_key = os.getenv(f"{prefix}_JUDGE_API_KEY", "").strip()

    _judge_config = {
        "provider": provider,
        "base_url": judge_base_url
        or judge_section.get("base_url")
        or llm_section.get("base_url", "http://localhost:11434/v1"),
        "model": judge_model
        or judge_section.get("model")
        or llm_section.get("model", "Qwen/Qwen2.5-7B-Instruct"),
        "api_key": judge_api_key
        or judge_section.get("api_key")
        or llm_section.get("api_key", "qwen-local"),
        "timeout": judge_section.get(
            "timeout", llm_section.get("timeout", 60)
        ),
        "max_retries": judge_section.get(
            "max_retries", llm_section.get("max_retries", 3)
        ),
        "retry_delay": judge_section.get(
            "retry_delay", llm_section.get("retry_delay", 1.0)
        ),
    }


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
                    "Potential hallucination: "
                    f"'{sentence[:100]}...' - key phrases not found in document"
                )

    total_sentences = len(grounded_sentences) + len(ungrounded_sentences)
    confidence = (
        (len(grounded_sentences) / total_sentences) if total_sentences else 0.0
    )

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


def _legacy_semantic_as_keyword(
    answer: str,
    document_content: str,
    question: Optional[str] = None,
) -> Dict[str, Any]:
    """Former ``semantic`` mode used embeddings; map to keyword overlap."""
    res = _check_keyword_based(answer, document_content, question)
    res["method"] = "keyword"
    note = (
        "hallucination.method 'semantic' maps to keyword overlap "
        "(embedding grading removed)."
    )
    issues = list(res.get("issues") or [])
    issues.append(note)
    res["issues"] = issues
    return res


def check_hallucination(
    answer: str,
    document_content: str,
    question: Optional[str] = None,
    method: str = "llm",
) -> Dict[str, Any]:
    if method in ("keyword", "both"):
        return _check_keyword_based(answer, document_content, question)
    if method == "semantic":
        return _legacy_semantic_as_keyword(
            answer, document_content, question
        )
    if method == "llm":
        return _check_llm_based(answer, document_content, question)
    if method == "hybrid":
        return _check_hybrid(answer, document_content, question)
    raise ValueError(
        f"Unknown method: {method}. Use 'keyword', 'semantic', 'llm', or "
        "'hybrid'"
    )


# ---------------------------------------------------------------------------
#  LLM-as-judge verification
# ---------------------------------------------------------------------------

_LLM_JUDGE_PROMPT = """You are a grounding verifier. Your job is to determine whether an answer is fully supported by the given document.  # noqa: E501

DOCUMENT:
{document}

QUESTION:
{question}

ANSWER:
{answer}

Instructions:
1. Check if EVERY claim in the answer is supported by the document.
2. Pay special attention to:
   - Numbers, counts, and aggregations (e.g. "3 men" — verify by counting in the document)  # noqa: E501
   - Inferences and conclusions drawn from multiple parts of the document
   - Negations and qualifiers
3. Respond with EXACTLY this JSON format (no other text):

{{"verdict": "SUPPORTED" or "NOT_SUPPORTED", "confidence": 0.0 to 1.0, "reason": "brief explanation"}}  # noqa: E501

If the answer correctly aggregates, counts, or infers from the document, it IS supported.  # noqa: E501
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
    if not _judge_config and not _llm_config:
        raise RuntimeError(
            "LLM config not set. Call set_llm_config() before using method='llm' or 'hybrid'."  # noqa: E501
        )

    # Use dedicated judge config (often a second Ollama model)
    jcfg = _judge_config if _judge_config else _llm_config.get("llm", {})
    provider = str(jcfg.get("provider", "ollama")).lower()
    validate_provider_for_offline_mode(provider, {"llm": jcfg})
    api_key = jcfg.get("api_key", "not-required")
    if api_key == "EMPTY" or not api_key:
        api_key = "not-required"
    base_url = jcfg.get("base_url", "http://localhost:11434/v1")
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

    if provider == "ollama":
        reply = _call_ollama_chat_native(
            base_url=base_url,
            model=model,
            prompt=prompt,
            max_retries=max_retries,
            retry_delay=retry_delay,
            timeout=timeout,
            temperature=0.0,
            max_tokens=200,
        )
        return _parse_llm_verdict(reply)

    try:
        import openai
    except ImportError:
        raise RuntimeError(
            "openai package required for LLM-based hallucination checking. "
            "Install with: pip install openai"
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
            content = (
                response.choices[0].message.content
                if response.choices
                else None
            )
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

    return {
        "verdict": "UNKNOWN",
        "confidence": 0.5,
        "reason": "LLM call exhausted retries",
    }


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


_GROUNDING_EXPLAIN_PROMPT = """You are assisting with audit documentation.

DOCUMENT (excerpt):
{document}

QUESTION:
{question}

ANSWER:
{answer}

The answer has already been marked as supported by an automated checker, but
there are no verbatim citation spans. In 1-2 short English sentences, explain
how the answer is supported by the document (which facts or phrases align).
Do not use JSON or bullet points. Max 80 words."""


def explain_grounding_brief(
    answer: str,
    document_content: str,
    question: str = "",
) -> str:
    """
    One extra judge-LLM call: short prose why the answer fits the document.

    Used when grounding_explanation_when_no_citations is 'always' and
    llm_verdict.reason is missing. Returns empty string on failure.
    """
    if not _judge_config and not _llm_config:
        return ""

    jcfg = _judge_config if _judge_config else _llm_config.get("llm", {})
    provider = str(jcfg.get("provider", "ollama")).lower()
    api_key = jcfg.get("api_key", "not-required")
    if api_key == "EMPTY" or not api_key:
        api_key = "not-required"
    base_url = jcfg.get("base_url", "http://localhost:11434/v1")
    model = jcfg.get("model", "Qwen/Qwen2.5-7B-Instruct")
    timeout = jcfg.get("timeout", 60)
    max_retries = int(jcfg.get("max_retries", 3))
    retry_delay = float(jcfg.get("retry_delay", 1.0))

    max_doc_chars = int(os.getenv("HALLUC_MAX_DOC_CHARS", "6000"))
    doc = document_content or ""
    doc_text = doc[:max_doc_chars]
    if len(doc) > max_doc_chars:
        doc_text += "\n... [document truncated] ..."

    prompt = _GROUNDING_EXPLAIN_PROMPT.format(
        document=doc_text,
        question=question or "(no question provided)",
        answer=answer or "",
    )
    max_out = 300
    if provider == "ollama":
        text = _call_ollama_chat_native(
            base_url=base_url,
            model=model,
            prompt=prompt,
            max_retries=max_retries,
            retry_delay=retry_delay,
            timeout=timeout,
            temperature=0.0,
            max_tokens=200,
        )
        if len(text) > max_out:
            text = text[: max_out - 3] + "..."
        return text

    try:
        import openai
    except ImportError:
        return ""

    client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
            )
            content = (
                response.choices[0].message.content
                if response.choices
                else None
            )
            text = (content or "").strip()
            if len(text) > max_out:
                text = text[: max_out - 3] + "..."
            return text
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            return ""
    return ""


def apply_grounding_why_when_no_citations(
    pair: Dict[str, Any],
    document_content: str,
    question: str,
    mode: str,
) -> None:
    """
    If is_grounded and citations are empty, set
    hallucination_check.grounding_why.

    mode: off | reuse_llm_only | always
    """
    raw_mode = str(mode or "off").strip().lower()
    if raw_mode in ("", "off", "false", "0", "no"):
        return
    grading = pair.get("hallucination_check")
    if not isinstance(grading, dict):
        grading = pair.get("grading")
    if not isinstance(grading, dict):
        return
    pair["hallucination_check"] = grading
    if grading.get("is_grounded") is not True:
        return

    spans = pair.get("citation_spans")
    notes = pair.get("citation_notes")
    if not isinstance(spans, list):
        spans = []
    if not isinstance(notes, list):
        notes = []
    if spans or notes:
        return

    if str(grading.get("grounding_why") or "").strip():
        return

    def _from_llm_verdict() -> bool:
        lv = grading.get("llm_verdict")
        if not isinstance(lv, dict):
            return False
        r = str(lv.get("reason") or "").strip()
        if not r:
            return False
        grading["grounding_why"] = r[:300]
        return True

    if raw_mode == "reuse_llm_only":
        _from_llm_verdict()
        return

    if raw_mode == "always":
        if _from_llm_verdict():
            return
        ans = pair.get("answer")
        brief = explain_grounding_brief(
            str(ans or ""),
            document_content,
            question or "",
        )
        if brief:
            grading["grounding_why"] = brief
    # Unknown modes: no-op


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
    strict_required = _strict_llm_judge_required()
    raw_verdict = str(verdict.get("verdict", "")).upper()
    if strict_required and raw_verdict not in ("SUPPORTED", "NOT_SUPPORTED"):
        reason = str(verdict.get("reason", "")).strip() or "unknown verdict"
        raise RuntimeError(
            "Strict LLM judge mode requires a valid verdict. "
            f"Got {raw_verdict or 'EMPTY'} ({reason})."
        )

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

    is_grounded = is_supported and confidence >= 0.7

    return {
        "is_grounded": is_grounded,
        "confidence": round(confidence, 3),
        "issues": issues,
        "grounded_sentences": grounded,
        "ungrounded_sentences": ungrounded,
        "method": "llm",
        "llm_verdict": verdict,
    }


# ---------------------------------------------------------------------------
#  Hybrid (legacy): formerly embedding + LLM; now LLM judge only.
# ---------------------------------------------------------------------------


def _check_hybrid(
    answer: str,
    document_content: str,
    question: Optional[str] = None,
) -> Dict[str, Any]:
    out = _check_llm_based(answer, document_content, question)
    if isinstance(out, dict) and out.get("method") == "llm":
        out["method"] = "hybrid"
    return out


def _document_text_for_grading(payload: Dict[str, Any]) -> str:
    """Resolve document body from a merged QA/grading dict.

    Same resolution order as ``extract_document_text`` (priority fields,
    ``english.article``, English-only ``source`` list), but **no** deep scan
    or generic fallback so ``questions`` / ``answers`` never become body
    text.
    """
    return extract_document_text(
        payload,
        strict=False,
        allow_loose_resolution=False,
    )


def grade_qa_results(
    qa_results: List[Dict[str, Any]],
    method: str = "llm",
) -> List[Dict[str, Any]]:
    graded_results: List[Dict[str, Any]] = []

    for result in qa_results:
        document_content = _document_text_for_grading(result)
        questions = result.get("questions") or []
        answers = result.get("answers") or []

        hallucination_checks = []
        grounded_confidences: List[float] = []

        for question, answer in zip(questions, answers):
            try:
                check_result = check_hallucination(
                    answer=answer,
                    document_content=document_content,
                    question=question,
                    method=method,
                )
            except Exception as exc:
                if method == "llm" and _strict_llm_judge_required():
                    raise RuntimeError(
                        "Strict LLM judge mode failed while grading: "
                        f"{exc}"
                    ) from exc
                check_result = {
                    "is_grounded": False,
                    "confidence": 0.0,
                    "issues": [f"Hallucination check failed: {exc}"],
                    "grounded_sentences": [],
                    "ungrounded_sentences": _split_into_sentences(
                        answer or ""
                    ),
                    "method": f"{method} (failed)",
                    "llm_verdict": {
                        "verdict": "UNKNOWN",
                        "confidence": 0.0,
                        "reason": str(exc),
                    },
                }
            hallucination_checks.append(
                {
                    "question": question,
                    "answer": answer,
                    "check_result": check_result,
                }
            )
            if check_result.get("is_grounded") is True:
                try:
                    grounded_confidences.append(
                        float(check_result.get("confidence", 0.0))
                    )
                except (TypeError, ValueError):
                    grounded_confidences.append(0.0)

        if grounded_confidences:
            overall_confidence = sum(grounded_confidences) / len(
                grounded_confidences
            )
        else:
            overall_confidence = 0.0

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
                "judge_model": _judge_config.get("model", "unknown")
                if method in ("llm", "hybrid")
                else "N/A (keyword / legacy semantic)",
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
    _ABBREVS = r"(?:Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|vs|etc|inc|ltd|corp|dept|approx|est|govt|intl|natl|assn|assoc|vol|no|fig|ref|pp|ed|rev|gen|sgt|cpl|pvt|lt|col|capt|maj|brig|adm|cmdr)"  # noqa: E501
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

    # Split on sentence-ending punctuation followed by whitespace or
    # end-of-string
    parts = re.split(r"(?<=[.!?])\s+", protected)

    # Also split on newlines (paragraphs are sentence boundaries)
    expanded: List[str] = []
    for part in parts:
        expanded.extend(part.split("\n"))

    # Restore protected tokens and clean up
    sentences: List[str] = []
    for s in expanded:
        s = s.replace("<DOT>", ".").replace("<ELLIPSIS>", "...").strip()
        # Keep short numeric answers (e.g. "7", "3.5", "10%") so they are
        # not misclassified as empty answers in downstream checks.
        is_short_numeric = bool(
            re.fullmatch(r"[$€£]?\s*[-+]?\d+(?:[.,]\d+)?(?:%|/\d+)?", s)
        )
        # Otherwise skip very short fragments that are often noise.
        if s and (len(s) > 2 or is_short_numeric):
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
    These are auto-grounded because penalising them would unfairly lower confidence.  # noqa: E501
    """
    generic_patterns = [
        r"^the document\b",
        r"^according to the (document|text|article|report)",
        r"^as (stated|mentioned|described|noted|indicated) in the (document|text|article)",  # noqa: E501
        r"^the document (states|mentions|describes|discusses|says|indicates|notes)",  # noqa: E501
        r"^based on the (document|text|article|information provided)",
        # Only treat "this/it is" as generic when followed by
        # document-reference context
        r"^this (is a|refers to|means|suggests that|indicates)",
        r"^it (refers to|means|should be noted|is (important|worth noting|clear|evident))",  # noqa: E501
    ]

    sentence_lower = sentence.lower().strip()
    return any(
        re.match(pattern, sentence_lower) for pattern in generic_patterns
    )


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
        qa_rows = result.get("qa_pairs", [])
        if checks:
            iterable = checks
            use_qa_pairs = False
        else:
            iterable = qa_rows
            use_qa_pairs = True
        for j, check in enumerate(iterable, 1):
            if use_qa_pairs:
                question = check.get("question", "N/A")
                check_result = check.get("hallucination_check")
                if not isinstance(check_result, dict):
                    check_result = check.get("grading") or {}
            else:
                question = check.get("question", "N/A")
                check_result = check.get("check_result", {})
            is_grounded = check_result.get("is_grounded", False)
            conf = check_result.get("confidence", 0.0)
            issues = check_result.get("issues", [])

            status = (
                "[OK] GROUNDED"
                if is_grounded
                else "[WARN] POTENTIAL HALLUCINATION"
            )
            print(f"  Q{j}. {question[:80]}...")
            print(f"     Status: {status} (Confidence: {conf:.1%})")

            if issues:
                print("     Issues:")
                for issue in issues[:3]:
                    print(f"       - {issue[:100]}...")
            print()

        print("-" * 80)
        print()
