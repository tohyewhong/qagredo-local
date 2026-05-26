"""Question generator using LLM to generate questions from documents."""

from .langchain_components import parse_questions as parse_questions_langchain
from .config_manager import (
    build_llm_config,
    validate_provider_for_offline_mode,
)
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Union, Dict, List, Any, Optional
from urllib.request import Request, urlopen

from .duplicate_detector import filter_duplicates_from_new_questions
from .ollama_urls import is_ollama_openai_base_url
from .document_text import extract_document_text
from .hallucination_checker import check_hallucination

# Optional custom CA bundle.
_project_root = Path(__file__).parent.parent
_cert_path = _project_root / "certbundle" / "certbundle.crt"
if _cert_path.exists() and _cert_path.is_file():
    os.environ.setdefault("SSL_CERT_FILE", str(_cert_path.resolve()))
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(_cert_path.resolve()))


SINGAPORE_TZ = timezone(timedelta(hours=8), name="Asia/Singapore")


def _load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    return build_llm_config(base_config_path=config_path)


def _extract_text_content(document: Dict[str, Any]) -> str:
    """Body text for questions; English preferred over native."""
    return extract_document_text(
        document,
        strict=True,
        allow_loose_resolution=True,
    )


# ---------------------------------------------------------------------------
#  Question type definitions (Bloom's Taxonomy inspired)
# ---------------------------------------------------------------------------

QUESTION_TYPES = {
    "analysis": {
        "label": "Analysis",
        "instruction": "Break down information into parts and examine relationships.",  # noqa: E501
        "example": "What are the separate factors that contributed to [event]?",  # noqa: E501
    },
    "aggregation": {
        "label": "Aggregation / Counting",
        "instruction": "Count, sum, or aggregate information scattered across different parts of the document.",  # noqa: E501
        "example": "How many [people/events/items] are mentioned in total across the document?",  # noqa: E501
    },
    "comparison": {
        "label": "Comparison",
        "instruction": "Compare or contrast two or more entities, events, or viewpoints in the document.",  # noqa: E501
        "example": "How does [A]'s role differ from [B]'s role?",
    },
    "inference": {
        "label": "Inference / Deduction",
        "instruction": "Draw conclusions or make logical inferences from facts stated in the document.",  # noqa: E501
        "example": "Based on the information provided, what can be inferred about [topic]?",  # noqa: E501
    },
    "causal": {
        "label": "Causal Reasoning",
        "instruction": "Identify cause-and-effect relationships between events or actions.",  # noqa: E501
        "example": "What was the likely consequence of [action] on [outcome]?",
    },
    "temporal": {
        "label": "Temporal / Sequence",
        "instruction": "Analyze the chronological order, timeline, or sequence of events.",  # noqa: E501
        "example": "What is the sequence of events that led to [outcome]?",
    },
    "multi_hop": {
        "label": "Multi-hop Reasoning",
        "instruction": "Connect information from multiple separate parts of the document to answer.",  # noqa: E501
        "example": "Given that [fact A] and [fact B], what does this imply about [topic]?",  # noqa: E501
    },
    "synthesis": {
        "label": "Synthesis",
        "instruction": "Combine multiple pieces of information from different parts of the document to form a comprehensive answer that no single sentence provides.",  # noqa: E501
        "example": "Drawing from the financial data, leadership changes, and market conditions described in the document, what overall picture emerges about [entity]'s trajectory?",  # noqa: E501
    },
    "evaluation": {
        "label": "Evaluation / Critical Assessment",
        "instruction": "Assess the strength, adequacy, or consistency of claims, evidence, or actions described in the document.",  # noqa: E501
        "example": "Based on the evidence presented, how well-supported is the claim that [assertion]?",  # noqa: E501
    },
    "counterfactual": {
        "label": "Counterfactual / Hypothetical",
        "instruction": "Reason about what would change if a stated fact, condition, or action were different, using only information in the document.",  # noqa: E501
        "example": "According to the document, what would likely have been different if [condition] had not occurred?",  # noqa: E501
    },
}

# Complexity presets: which question types to use and how many of each
COMPLEXITY_PRESETS = {
    "basic": {
        "description": "Simple factual and comprehension questions",
        "types": ["analysis"],
        "prompt_style": "basic",
    },
    "moderate": {
        "description": "Mix of analysis, comparison, and inference",
        "types": ["analysis", "comparison", "inference"],
        "prompt_style": "moderate",
    },
    "advanced": {
        "description": "Full range including aggregation, causal, temporal, multi-hop, synthesis, evaluation, and counterfactual",  # noqa: E501
        "types": [
            "analysis",
            "aggregation",
            "comparison",
            "inference",
            "causal",
            "temporal",
            "multi_hop",
            "synthesis",
            "evaluation",
            "counterfactual",
        ],
        "prompt_style": "advanced",
    },
}


def _use_langchain_features(config: Dict[str, Any]) -> bool:
    framework_cfg = (
        (config.get("framework") or {}) if isinstance(config, dict) else {}
    )
    return bool(framework_cfg.get("use_langchain", False))


def _create_question_prompt(
    text_content: str,
    num_questions: int = 3,
    complexity: str = "advanced",
    question_types: Optional[List[str]] = None,
) -> str:
    """
    Build the LLM prompt for question generation.

    Args:
        text_content: The document text.
        num_questions: How many questions to generate.
        complexity: One of "basic", "moderate", "advanced".
        question_types: Optional explicit list of types to use (overrides complexity preset).  # noqa: E501
    """
    preset = COMPLEXITY_PRESETS.get(complexity, COMPLEXITY_PRESETS["advanced"])

    if question_types:
        types_to_use = [t for t in question_types if t in QUESTION_TYPES]
        if not types_to_use:
            types_to_use = preset["types"]
    else:
        types_to_use = preset["types"]

    if preset["prompt_style"] == "basic":
        return _create_basic_prompt(text_content, num_questions)

    # Build type instruction block
    type_instructions: List[str] = []
    for i, qtype in enumerate(types_to_use, 1):
        info = QUESTION_TYPES[qtype]
        type_instructions.append(
            f"  {i}. **{info['label']}**: {info['instruction']}\n"
            f"     Example pattern: \"{info['example']}\""
        )
    types_block = "\n".join(type_instructions)

    # Distribute questions across types
    if num_questions <= len(types_to_use):
        distribution_note = (
            f"Generate exactly {num_questions} questions. "
            f"Each question should use a DIFFERENT type from the list above."
        )
    else:
        distribution_note = (
            f"Generate exactly {num_questions} questions. "
            f"Distribute them across the types above — try to cover as many types as possible. "  # noqa: E501
            f"Do NOT generate multiple questions of the same type unless you have covered all types."  # noqa: E501
        )

    # Build few-shot examples block
    few_shot_block = """
FEW-SHOT EXAMPLES (for reference — do NOT copy these; generate questions specific to the document):  # noqa: E501

Example document excerpt: "In 2024, Company A acquired Company B for $2M. In 2025, Company A also acquired Company C for $3M. The CEO stated the acquisitions were to expand market share. Company B had 50 employees while Company C had 120 employees. Analysts noted that Company A's stock price dropped 10% after the second acquisition."  # noqa: E501

  Good (aggregation): What is the total acquisition expenditure and combined employee count that Company A absorbed through both deals? (aggregation)  # noqa: E501
  Good (causal): How might the CEO's stated goal of expanding market share relate to the analysts' observation about the stock price decline after the second acquisition? (causal)  # noqa: E501
  Good (multi-hop): Considering that Company C had more than twice the employees of Company B but cost only 50% more, what does the per-employee acquisition cost suggest about the relative value of the two companies? (multi_hop)  # noqa: E501
  Good (synthesis): Drawing from the acquisition timeline, costs, workforce sizes, and market reaction, what overall pattern emerges about Company A's growth strategy and its reception? (synthesis)  # noqa: E501
  Good (evaluation): Based on the stock price decline and the CEO's stated rationale, how well does the evidence in the document support the claim that the acquisitions were strategically sound? (evaluation)  # noqa: E501
  Good (counterfactual): If Company A had not proceeded with the second acquisition of Company C, how would the total expenditure and workforce integration challenge have differed based on the information provided? (counterfactual)  # noqa: E501
  Good (comparison): In what ways do the two acquisitions differ in terms of cost, timing, scale (employees), and apparent market reaction? (comparison)  # noqa: E501
  Good (temporal): What is the chronological relationship between the two acquisitions and the stock price movement, and what does the sequence suggest? (temporal)  # noqa: E501

  Bad: What is Company A? (too simple — just locating a name)
  Bad: How much did Company B cost? (too simple — answer is a single number from one sentence)  # noqa: E501
  Bad: What will Company A acquire next? (speculative, not in the document)
  Bad: What is an acquisition? (asks for general knowledge, not document-specific)  # noqa: E501
"""

    return f"""You are an expert analyst creating COMPLEX questions strictly based on the document provided below.  # noqa: E501
Do not use outside knowledge, and do not invent any facts, names, numbers, or events that are not present in the document.  # noqa: E501

YOUR GOAL: Generate questions that require DEEP REASONING — not simple fact lookup.  # noqa: E501
Every question should require the reader to combine, analyze, compare, or reason across MULTIPLE pieces of information in the document.  # noqa: E501
A good question CANNOT be answered by copying a single sentence from the document.  # noqa: E501

QUESTION TYPES (use a diverse mix of these):
{types_block}
{few_shot_block}
COMPLEXITY REQUIREMENTS (STRICTLY FOLLOW):
1. Every question MUST require reasoning across at least 2 different parts of the document.  # noqa: E501
2. NEVER ask a question whose answer is a single fact found in one sentence (e.g. "What is X?" or "When did Y happen?").  # noqa: E501
3. Prefer questions that ask "how", "why", "what does X imply about Y", "how does X relate to Y", or "what overall pattern emerges".  # noqa: E501
4. For aggregation questions: require counting or combining information scattered across MULTIPLE paragraphs or sections.  # noqa: E501
5. For multi-hop questions: require connecting two or more separate facts to derive an answer that is NOT explicitly stated.  # noqa: E501
6. For causal questions: ask about cause-and-effect CHAINS, not just a single cause-effect pair.  # noqa: E501
7. For synthesis questions: require integrating 3+ separate facts into a coherent analysis.  # noqa: E501
8. For evaluation questions: ask whether the evidence in the document supports or contradicts a claim.  # noqa: E501
9. For counterfactual questions: ask what would change if a specific stated condition were different.  # noqa: E501

Document:
{text_content}

{distribution_note}
Output one question per line, without numbering or bullet points.
After each question, add a tag in parentheses indicating its type, e.g. (analysis), (aggregation), (causal), (synthesis), (evaluation), (counterfactual)."""  # noqa: E501


def _create_basic_prompt(text_content: str, num_questions: int = 3) -> str:
    """Original simple prompt for basic complexity."""
    return f"""You are creating questions strictly based on the document provided below.  # noqa: E501
Do not use outside knowledge, and do not invent any facts, names, numbers, or events that are not present in the document.  # noqa: E501

Based on the following document, generate {num_questions} questions that test understanding of the content.  # noqa: E501

Document:
{text_content}

Generate exactly {num_questions} questions, one per line, without numbering or bullet points."""  # noqa: E501


def _call_llm(prompt: str, config: Dict[str, Any]) -> str:
    llm_section = config.get("llm", {})
    provider = llm_section.get("provider", "ollama").lower()
    max_retries = llm_section.get("max_retries", 3)
    retry_delay = llm_section.get("retry_delay", 1.0)

    validate_provider_for_offline_mode(provider, config)

    if provider in ("vllm", "ollama"):
        return _call_vllm_llm(
            prompt,
            llm_section,
            max_retries,
            retry_delay,
            system_prompt=(
                "You generate questions using ONLY the provided document. "
                "Do not invent facts not present in the document."
            ),
        )
    if provider == "openai":
        return _call_openai_llm(
            prompt,
            llm_section,
            max_retries,
            retry_delay,
            system_prompt="You generate grounded questions.",
        )
    raise ValueError(
        f"Unsupported LLM provider: {provider}. "
        "Supported providers: ollama, vllm, openai"
    )


def _call_vllm_llm(
    prompt: str,
    llm_section: Dict[str, Any],
    max_retries: int,
    retry_delay: float,
    system_prompt: str,
) -> str:
    import openai

    api_key = llm_section.get("api_key")
    if api_key == "EMPTY" or not api_key:
        api_key = "not-required"

    base_url = llm_section.get("base_url", "http://localhost:11434/v1")
    model = llm_section.get("model", "meta-llama/Llama-2-7b-chat-hf")
    temperature = llm_section.get("temperature", 0.7)
    max_tokens = llm_section.get("max_tokens", 500)
    timeout = llm_section.get("timeout", 60)

    if is_ollama_openai_base_url(base_url):
        return _call_ollama_chat_native(
            base_url=base_url,
            model=model,
            prompt=prompt,
            max_retries=max_retries,
            retry_delay=retry_delay,
            timeout=timeout,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )

    from utils.openai_helpers import (
        openai_chat_extra_body,
        openai_message_text,
        qwen_no_thinking_system_suffix,
    )

    client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    system_prompt = system_prompt + qwen_no_thinking_system_suffix(model)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=openai_chat_extra_body(model),
            )
            if not response.choices:
                return ""
            return openai_message_text(
                response.choices[0].message,
                for_question=True,
            )
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise RuntimeError(
                f"LLM API call failed after {max_retries} attempts: {e}\n"
                f"Make sure the server is running at {base_url}"
            )


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
    system_prompt: str,
) -> str:
    # Ollama's OpenAI-compat endpoint for Qwen3.5 may return empty `content`
    # and put tokens in a separate reasoning field. Use native /api/chat with
    # think=false so answer text lands in message.content.
    root = base_url.split("/v1", 1)[0].rstrip("/")
    endpoint = f"{root}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
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
            return ""
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise RuntimeError(
                f"Ollama API call failed after {max_retries} attempts: {e}\n"
                f"Make sure Ollama is running at {endpoint}"
            )

    return ""


def _call_openai_llm(
    prompt: str,
    llm_section: Dict[str, Any],
    max_retries: int,
    retry_delay: float,
    system_prompt: str,
) -> str:
    import openai

    from utils.openai_helpers import (
        openai_chat_extra_body,
        openai_message_text,
        qwen_no_thinking_system_suffix,
    )

    api_key = llm_section.get("api_key")
    if not api_key:
        raise RuntimeError(
            "OpenAI API key is missing. Set OPENAI_API_KEY env var or llm.api_key in config."  # noqa: E501
        )

    base_url = llm_section.get("base_url")
    timeout = llm_section.get("timeout", 60)
    model = llm_section.get("model", "gpt-4o-mini")
    temperature = llm_section.get("temperature", 0.7)
    max_tokens = llm_section.get("max_tokens", 500)

    client_kwargs = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = openai.OpenAI(**client_kwargs)
    system_prompt = system_prompt + qwen_no_thinking_system_suffix(model)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=openai_chat_extra_body(model),
            )
            if not response.choices:
                return ""
            return openai_message_text(
                response.choices[0].message,
                for_question=True,
            )
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise RuntimeError(
                f"OpenAI API call failed after {max_retries} attempts: {e}"
            )


def _config_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _dedup_llm_section(config: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    qcfg = config.get("question_generation", {})
    dcfg = qcfg.get("dedup_llm", {})
    strict = _config_bool(dcfg.get("strict"), default=True)
    use_judge_model = _config_bool(dcfg.get("use_judge_model"), default=True)

    llm_base = dict(config.get("llm", {}))
    judge_base = dict(config.get("judge", {}))
    source = judge_base if use_judge_model and judge_base else llm_base

    llm_section = {
        "provider": source.get("provider", llm_base.get("provider", "ollama")),
        "base_url": source.get("base_url", llm_base.get("base_url", "")),
        "model": source.get("model", llm_base.get("model", "")),
        "api_key": source.get("api_key", llm_base.get("api_key", "")),
        "temperature": dcfg.get("temperature", 0.0),
        "max_tokens": dcfg.get("max_tokens", 80),
        "timeout": dcfg.get("timeout", source.get("timeout", 60)),
        "max_retries": dcfg.get("max_retries", source.get("max_retries", 3)),
        "retry_delay": dcfg.get("retry_delay", source.get("retry_delay", 1.0)),
    }
    return llm_section, strict


def _call_dedup_llm(prompt: str, config: Dict[str, Any]) -> tuple[str, bool]:
    llm_section, strict = _dedup_llm_section(config)
    provider = str(llm_section.get("provider", "ollama")).lower()
    max_retries = int(llm_section.get("max_retries", 3))
    retry_delay = float(llm_section.get("retry_delay", 1.0))
    validate_provider_for_offline_mode(provider, {"llm": llm_section})

    system_prompt = (
        "You are a strict duplicate-question judge. "
        "Return only compact JSON with key duplicate."
    )
    if provider in ("vllm", "ollama"):
        return (
            _call_vllm_llm(
                prompt,
                llm_section,
                max_retries,
                retry_delay,
                system_prompt=system_prompt,
            ),
            strict,
        )
    if provider == "openai":
        return (
            _call_openai_llm(
                prompt,
                llm_section,
                max_retries,
                retry_delay,
                system_prompt=system_prompt,
            ),
            strict,
        )
    raise ValueError(
        f"Unsupported dedup LLM provider: {provider}. "
        "Supported providers: ollama, vllm, openai"
    )


def _parse_llm_duplicate_verdict(response: str) -> Optional[bool]:
    text = (response or "").strip()
    if not text:
        return None

    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if "\n" in text:
                text = text.split("\n", 1)[1]
            text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("duplicate"), bool):
            return bool(parsed["duplicate"])
    except Exception:
        pass

    lowered = text.lower()
    if '"duplicate": true' in lowered or lowered == "true":
        return True
    if '"duplicate": false' in lowered or lowered == "false":
        return False
    return None


def _llm_duplicate_questions(
    existing_questions: List[str],
    new_questions: List[str],
    similarity_threshold: float,
    config: Dict[str, Any],
) -> List[str]:
    kept: List[str] = []
    baseline = [q for q in existing_questions if isinstance(q, str) and q.strip()]
    for candidate in new_questions:
        if not isinstance(candidate, str) or not candidate.strip():
            continue

        is_duplicate = False
        for prior in [*baseline, *kept]:
            prompt = f"""Decide whether Question B is a duplicate of Question A.

Treat as duplicate when they ask essentially the same thing even if phrased differently, target the same evidence, and would lead to materially equivalent answers.
Treat as non-duplicate when they ask for distinct reasoning, evidence, or output.
Use strict duplicate threshold = {similarity_threshold:.2f}.

Return EXACTLY this JSON and nothing else:
{{"duplicate": true}}
or
{{"duplicate": false}}

Question A: {prior}
Question B: {candidate}
"""
            response, strict = _call_dedup_llm(prompt, config)
            verdict = _parse_llm_duplicate_verdict(response)
            if verdict is None:
                if strict:
                    raise RuntimeError(
                        "LLM dedup strict mode requires valid JSON verdict. "
                        f"Got: {response!r}"
                    )
                continue
            if verdict:
                is_duplicate = True
                break

        if not is_duplicate:
            kept.append(candidate)
    return kept


def _parse_questions(response: str, num_questions: int = 3) -> List[str]:
    import re as _re

    lines = [
        line.strip() for line in (response or "").split("\n") if line.strip()
    ]
    questions: List[str] = []
    for line in lines:
        line = line.lstrip("0123456789.-) ")
        # Remove all trailing type tags like (analysis), (aggregation), etc.
        # Handle multiple tags: "Question? (analysis) (comparison)" →
        # "Question?"
        line = _re.sub(r"(\s*\([a-z_]+\))+\s*$", "", line).strip()
        if line:
            questions.append(line)
    return (
        questions[:num_questions]
        if len(questions) >= num_questions
        else questions
    )


def _parse_questions_with_framework(
    response: str, num_questions: int, config: Dict[str, Any]
) -> List[str]:
    from utils.minimal_text import (
        _clean_question_candidate,
        sanitize_llm_question_response,
    )

    cleaned = sanitize_llm_question_response(
        response,
        max_items=num_questions,
    )
    if cleaned:
        return cleaned
    if _use_langchain_features(config):
        legacy = parse_questions_langchain(response)[:num_questions]
    else:
        legacy = _parse_questions(response, num_questions=num_questions)
    out: List[str] = []
    for q in legacy:
        c = _clean_question_candidate(q)
        if c and c not in out:
            out.append(c)
    return out[:num_questions]


def _validate_and_regenerate_question(
    question: str,
    document_content: str,
    config: Dict[str, Any],
    min_confidence: float = 0.7,
    max_attempts: int = 2,
) -> tuple[str, Dict[str, Any]]:
    validation_info = {
        "confidence": 0.0,
        "attempts": 0,
        "was_regenerated": False,
        "is_grounded": False,
        "issues": [],
    }

    # Final default is strict LLM judge behavior; use llm validation unless
    # explicitly overridden in question_generation.validation.method.
    qval_method = (
        (config.get("question_generation") or {})
        .get("validation", {})
        .get("method", "llm")
    )

    check_result = check_hallucination(
        answer=question,
        document_content=document_content,
        question=question,
        method=qval_method,
    )
    confidence = check_result.get("confidence", 0.0)
    is_grounded = check_result.get("is_grounded", False)
    validation_info.update(
        {
            "confidence": confidence,
            "is_grounded": is_grounded,
            "attempts": 1,
            "issues": check_result.get("issues", []),
        }
    )

    if is_grounded and confidence >= min_confidence:
        return question, validation_info

    current_question = question
    for attempt in range(1, max_attempts + 1):
        validation_info["attempts"] = attempt + 1
        validation_info["was_regenerated"] = True

        regeneration_prompt = f"""Document:
{document_content}

Previous Question (REJECTED):
{current_question}

Generate a NEW question grounded ONLY in the document. Provide only the question."""  # noqa: E501

        regenerated = _call_llm(regeneration_prompt, config).strip()
        if regenerated:
            from utils.minimal_text import sanitize_llm_question_response

            cleaned = sanitize_llm_question_response(regenerated, max_items=1)
            current_question = cleaned[0] if cleaned else regenerated
        if current_question and not current_question.endswith("?"):
            current_question += "?"

        check_result = check_hallucination(
            answer=current_question,
            document_content=document_content,
            question=current_question,
            method=qval_method,
        )
        confidence = check_result.get("confidence", 0.0)
        is_grounded = check_result.get("is_grounded", False)
        validation_info.update(
            {
                "confidence": confidence,
                "is_grounded": is_grounded,
                "issues": check_result.get("issues", []),
            }
        )

        if is_grounded and confidence >= min_confidence:
            return current_question, validation_info

    return current_question, validation_info


def _parse_comprehensiveness_result(response: str) -> Dict[str, Any]:
    """Parse the LLM's comprehensiveness evaluation JSON response."""
    import json as _json
    import re as _re

    defaults = {
        "is_comprehensive": False,
        "score": 0.5,
        "reason": "",
        "weakness": "",
    }

    # Try direct JSON parse
    try:
        data = _json.loads(response)
        return {
            "is_comprehensive": bool(data.get("is_comprehensive", False)),
            "score": min(max(float(data.get("score", 0.5)), 0.0), 1.0),
            "reason": str(data.get("reason", "")),
            "weakness": str(data.get("weakness", "")),
        }
    except (ValueError, _json.JSONDecodeError):
        pass

    # Try extracting JSON object from surrounding text
    json_match = _re.search(r"\{[^{}]*\}", response, _re.DOTALL)
    if json_match:
        try:
            data = _json.loads(json_match.group())
            return {
                "is_comprehensive": bool(data.get("is_comprehensive", False)),
                "score": min(max(float(data.get("score", 0.5)), 0.0), 1.0),
                "reason": str(data.get("reason", "")),
                "weakness": str(data.get("weakness", "")),
            }
        except (ValueError, _json.JSONDecodeError):
            pass

    # Fallback: regex extraction from raw text
    result = dict(defaults)
    response_lower = response.lower()

    if (
        '"is_comprehensive": true' in response_lower
        or '"is_comprehensive":true' in response_lower
    ):
        result["is_comprehensive"] = True
        result["score"] = 0.7
    elif (
        '"is_comprehensive": false' in response_lower
        or '"is_comprehensive":false' in response_lower
    ):
        result["is_comprehensive"] = False
        result["score"] = 0.3

    score_match = _re.search(r'"score"\s*:\s*([\d.]+)', response)
    if score_match:
        try:
            result["score"] = min(max(float(score_match.group(1)), 0.0), 1.0)
        except ValueError:
            pass

    reason_match = _re.search(r'"reason"\s*:\s*"([^"]*)"', response)
    if reason_match:
        result["reason"] = reason_match.group(1)

    weakness_match = _re.search(r'"weakness"\s*:\s*"([^"]*)"', response)
    if weakness_match:
        result["weakness"] = weakness_match.group(1)

    return result


def comprehensiveness_passed(
    comp_info: Dict[str, Any], min_score: float
) -> bool:
    """True when the question passed the comprehensiveness threshold."""
    return bool(comp_info.get("is_comprehensive")) and float(
        comp_info.get("score", 0.0)
    ) >= min_score


def _check_question_comprehensiveness(
    question: str,
    document_content: str,
    config: Dict[str, Any],
    min_score: float = 0.6,
    max_attempts: int = 2,
) -> tuple[str, Dict[str, Any]]:
    """Check if a question is comprehensive and regenerate if not.

    A comprehensive question requires multi-step reasoning, is self-contained,
    and cannot be answered by copying a single sentence from the document.
    """
    comp_info: Dict[str, Any] = {
        "score": 0.0,
        "is_comprehensive": False,
        "accepted": False,
        "attempts": 0,
        "was_regenerated": False,
        "reason": "",
    }

    max_doc_chars = 6000
    doc_text = document_content[:max_doc_chars]
    if len(document_content) > max_doc_chars:
        doc_text += "\n... [document truncated] ..."

    current_question = question

    for attempt in range(max_attempts + 1):
        comp_info["attempts"] = attempt + 1

        eval_prompt = f"""You are evaluating whether a question about a document is COMPREHENSIVE.  # noqa: E501

A comprehensive question:
1. Requires reasoning across MULTIPLE parts of the document (not a single-sentence lookup)  # noqa: E501
2. Is self-contained and clearly worded
3. Requires analysis, inference, comparison, synthesis, or multi-step reasoning
4. Can be fully answered using only the document content
5. Is NOT a simple "What is X?" or "When did Y happen?" factual lookup

DOCUMENT:
{doc_text}

QUESTION:
{current_question}

Evaluate this question and respond with EXACTLY this JSON format (no other text):  # noqa: E501
{{"is_comprehensive": true or false, "score": 0.0 to 1.0, "reason": "brief explanation", "weakness": "what could be improved if not comprehensive"}}"""  # noqa: E501

        try:
            response = _call_llm(eval_prompt, config).strip()
            parsed = _parse_comprehensiveness_result(response)
        except Exception:
            comp_info[
                "reason"
            ] = "comprehensiveness check failed — keeping question as-is"
            comp_info["score"] = 0.5
            comp_info["accepted"] = comprehensiveness_passed(
                comp_info, min_score
            )
            return current_question, comp_info

        comp_info["score"] = parsed["score"]
        comp_info["is_comprehensive"] = parsed["is_comprehensive"]
        comp_info["reason"] = parsed["reason"]

        if parsed["is_comprehensive"] and parsed["score"] >= min_score:
            comp_info["accepted"] = True
            return current_question, comp_info

        # Regenerate if we still have attempts left
        if attempt < max_attempts:
            comp_info["was_regenerated"] = True
            weakness = parsed.get("weakness") or "not comprehensive enough"

            regen_prompt = f"""Document:
{doc_text}

Previous Question (NOT COMPREHENSIVE ENOUGH):
{current_question}

Weakness: {weakness}

Generate a NEW, more comprehensive question that:
- Requires reasoning across multiple parts of the document
- Cannot be answered by copying a single sentence
- Requires analysis, comparison, inference, or synthesis
- Is self-contained and clearly worded

Provide ONLY the new question, nothing else."""

            try:
                regenerated = _call_llm(regen_prompt, config).strip()
                if regenerated:
                    from utils.minimal_text import sanitize_llm_question_response

                    cleaned = sanitize_llm_question_response(
                        regenerated,
                        max_items=1,
                    )
                    current_question = (
                        cleaned[0] if cleaned else regenerated
                    )
                    if not current_question.endswith("?"):
                        current_question += "?"
            except Exception:
                pass

    comp_info["accepted"] = comprehensiveness_passed(comp_info, min_score)
    return current_question, comp_info


def generate_questions(
    documents: Union[List[Dict[str, Any]], Dict[str, Any], List[Any]],
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if config is None:
        config = _load_config(config_path)

    if isinstance(documents, dict):
        doc_list = [documents]
    elif isinstance(documents, list):
        doc_list = documents
    else:
        raise ValueError(
            f"Invalid documents format. Expected dict or list, got {type(documents)}"  # noqa: E501
        )

    qgen_config = config.get("question_generation", {})
    num_questions = qgen_config.get("num_questions", 3)
    complexity = qgen_config.get("complexity", "advanced")
    question_types = qgen_config.get("question_types", None)

    results: List[Dict[str, Any]] = []
    for idx, doc in enumerate(doc_list, 1):
        if not isinstance(doc, dict):
            continue
        try:
            text_content = _extract_text_content(doc)
            if not text_content.strip():
                continue

            max_generation_attempts = 5
            all_questions: List[str] = []
            generation_attempts = 0

            while (
                len(all_questions) < num_questions
                and generation_attempts < max_generation_attempts
            ):
                generation_attempts += 1
                questions_needed = num_questions - len(all_questions)
                prompt = _create_question_prompt(
                    text_content,
                    questions_needed + 2,
                    complexity=complexity,
                    question_types=question_types,
                )
                response = _call_llm(prompt, config)
                new_questions = _parse_questions_with_framework(
                    response,
                    questions_needed + 2,
                    config,
                )

                qgen_config = config.get("question_generation", {})
                similarity_threshold = qgen_config.get(
                    "duplicate_similarity_threshold", 0.85
                )
                dedup_method = str(
                    qgen_config.get("deduplication_method", "llm")
                ).lower()
                if dedup_method in ("semantic", "embedding"):
                    dedup_method = "jaccard"

                if dedup_method == "llm":
                    unique_new = _llm_duplicate_questions(
                        all_questions,
                        new_questions,
                        similarity_threshold,
                        config,
                    )
                else:
                    unique_new = filter_duplicates_from_new_questions(
                        all_questions,
                        new_questions,
                        similarity_threshold,
                        method=dedup_method,
                    )
                all_questions.extend(unique_new)
                if len(all_questions) >= num_questions:
                    break

            questions = all_questions[:num_questions]

            validation_config = config.get("question_generation", {}).get(
                "validation", {}
            )
            enable_validation = validation_config.get("enable_rejection", True)
            enable_comp_check = validation_config.get(
                "enable_comprehensiveness_check", True
            )
            question_validation_details = []

            if enable_validation or enable_comp_check:
                min_confidence = validation_config.get(
                    "min_confidence_threshold", 0.7
                )
                max_regeneration_attempts = validation_config.get(
                    "max_regeneration_attempts", 2
                )
                comp_min_score = validation_config.get(
                    "comprehensiveness_min_score", 0.6
                )
                comp_max_attempts = validation_config.get(
                    "comprehensiveness_max_attempts", 2
                )
                comp_strict = validation_config.get(
                    "comprehensiveness_strict", False
                )

                validated_questions = []
                for q_idx, question in enumerate(questions, 1):
                    detail: Dict[str, Any] = {
                        "question_index": q_idx,
                        "original_question": question,
                    }
                    final_question = question

                    if enable_validation:
                        (
                            final_question,
                            validation_info,
                        ) = _validate_and_regenerate_question(
                            question=final_question,
                            document_content=text_content,
                            config=config,
                            min_confidence=min_confidence,
                            max_attempts=max_regeneration_attempts,
                        )
                        detail["validation_info"] = validation_info

                    if enable_comp_check:
                        (
                            final_question,
                            comp_info,
                        ) = _check_question_comprehensiveness(
                            question=final_question,
                            document_content=text_content,
                            config=config,
                            min_score=comp_min_score,
                            max_attempts=comp_max_attempts,
                        )
                        detail["comprehensiveness_check"] = comp_info

                    from utils.minimal_text import _clean_question_candidate

                    cleaned_final = _clean_question_candidate(final_question)
                    if cleaned_final:
                        final_question = cleaned_final
                    detail["final_question"] = final_question

                    rejected = False
                    if (
                        comp_strict
                        and enable_comp_check
                        and isinstance(detail.get("comprehensiveness_check"), dict)
                        and not comprehensiveness_passed(
                            detail["comprehensiveness_check"],
                            comp_min_score,
                        )
                    ):
                        rejected = True
                        detail["accepted"] = False
                        detail["rejection_reason"] = (
                            "comprehensiveness_check_failed"
                        )
                    else:
                        detail["accepted"] = True

                    question_validation_details.append(detail)
                    if not rejected:
                        validated_questions.append(final_question)

                questions = validated_questions

            results.append(
                {
                    **doc,
                    "questions": questions,
                    "generation_metadata": {
                        "model": config["llm"].get("model", "unknown"),
                        "provider": config["llm"].get("provider", "unknown"),
                        "timestamp": datetime.now(SINGAPORE_TZ).isoformat(),
                        "timezone": "Asia/Singapore",
                        "num_questions": len(questions),
                        "complexity": complexity,
                        "question_types": question_types
                        or COMPLEXITY_PRESETS.get(complexity, {}).get(
                            "types", []
                        ),
                        "question_validation": question_validation_details
                        if (enable_validation or enable_comp_check)
                        else None,
                    },
                }
            )
        except Exception as e:
            print(f"Error processing document: {e}", flush=True)
            continue

    return results
