"""Question generator using LLM to generate questions from documents."""

import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Union, Dict, List, Any, Optional

from .duplicate_detector import filter_duplicates_from_new_questions
from .hallucination_checker import check_hallucination

# Optional custom CA bundle.
_project_root = Path(__file__).parent.parent
_cert_path = _project_root / "certbundle" / "certbundle.crt"
if _cert_path.exists() and _cert_path.is_file():
    os.environ.setdefault("SSL_CERT_FILE", str(_cert_path.resolve()))
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(_cert_path.resolve()))

from .config_manager import build_llm_config, validate_provider_for_offline_mode


SINGAPORE_TZ = timezone(timedelta(hours=8), name="Asia/Singapore")


def _load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    return build_llm_config(base_config_path=config_path)


def _extract_text_content(document: Dict[str, Any]) -> str:
    text_fields = ["content", "text", "body", "document", "article", "passage"]
    for field in text_fields:
        if field in document and document[field]:
            content = document[field]
            if isinstance(content, list):
                return " ".join(str(item) for item in content)
            return str(content)

    metadata_fields = ["id", "title", "source", "type", "metadata"]
    text_parts = []
    for key, value in document.items():
        if key not in metadata_fields and value:
            text_parts.append(str(value))
    if text_parts:
        return " ".join(text_parts)
    raise ValueError(f"No text content found in document. Available keys: {list(document.keys())}")


# ---------------------------------------------------------------------------
#  Question type definitions (Bloom's Taxonomy inspired)
# ---------------------------------------------------------------------------

QUESTION_TYPES = {
    "analysis": {
        "label": "Analysis",
        "instruction": "Break down information into parts and examine relationships.",
        "example": "What are the separate factors that contributed to [event]?",
    },
    "aggregation": {
        "label": "Aggregation / Counting",
        "instruction": "Count, sum, or aggregate information scattered across different parts of the document.",
        "example": "How many [people/events/items] are mentioned in total across the document?",
    },
    "comparison": {
        "label": "Comparison",
        "instruction": "Compare or contrast two or more entities, events, or viewpoints in the document.",
        "example": "How does [A]'s role differ from [B]'s role?",
    },
    "inference": {
        "label": "Inference / Deduction",
        "instruction": "Draw conclusions or make logical inferences from facts stated in the document.",
        "example": "Based on the information provided, what can be inferred about [topic]?",
    },
    "causal": {
        "label": "Causal Reasoning",
        "instruction": "Identify cause-and-effect relationships between events or actions.",
        "example": "What was the likely consequence of [action] on [outcome]?",
    },
    "temporal": {
        "label": "Temporal / Sequence",
        "instruction": "Analyze the chronological order, timeline, or sequence of events.",
        "example": "What is the sequence of events that led to [outcome]?",
    },
    "multi_hop": {
        "label": "Multi-hop Reasoning",
        "instruction": "Connect information from multiple separate parts of the document to answer.",
        "example": "Given that [fact A] and [fact B], what does this imply about [topic]?",
    },
    "synthesis": {
        "label": "Synthesis",
        "instruction": "Combine multiple pieces of information from different parts of the document to form a comprehensive answer that no single sentence provides.",
        "example": "Drawing from the financial data, leadership changes, and market conditions described in the document, what overall picture emerges about [entity]'s trajectory?",
    },
    "evaluation": {
        "label": "Evaluation / Critical Assessment",
        "instruction": "Assess the strength, adequacy, or consistency of claims, evidence, or actions described in the document.",
        "example": "Based on the evidence presented, how well-supported is the claim that [assertion]?",
    },
    "counterfactual": {
        "label": "Counterfactual / Hypothetical",
        "instruction": "Reason about what would change if a stated fact, condition, or action were different, using only information in the document.",
        "example": "According to the document, what would likely have been different if [condition] had not occurred?",
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
        "description": "Full range including aggregation, causal, temporal, multi-hop, synthesis, evaluation, and counterfactual",
        "types": [
            "analysis", "aggregation", "comparison", "inference",
            "causal", "temporal", "multi_hop",
            "synthesis", "evaluation", "counterfactual",
        ],
        "prompt_style": "advanced",
    },
}


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
        question_types: Optional explicit list of types to use (overrides complexity preset).
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
            f"Distribute them across the types above — try to cover as many types as possible. "
            f"Do NOT generate multiple questions of the same type unless you have covered all types."
        )

    # Build few-shot examples block
    few_shot_block = """
FEW-SHOT EXAMPLES (for reference — do NOT copy these; generate questions specific to the document):

Example document excerpt: "In 2024, Company A acquired Company B for $2M. In 2025, Company A also acquired Company C for $3M. The CEO stated the acquisitions were to expand market share. Company B had 50 employees while Company C had 120 employees. Analysts noted that Company A's stock price dropped 10% after the second acquisition."

  Good (aggregation): What is the total acquisition expenditure and combined employee count that Company A absorbed through both deals? (aggregation)
  Good (causal): How might the CEO's stated goal of expanding market share relate to the analysts' observation about the stock price decline after the second acquisition? (causal)
  Good (multi-hop): Considering that Company C had more than twice the employees of Company B but cost only 50% more, what does the per-employee acquisition cost suggest about the relative value of the two companies? (multi_hop)
  Good (synthesis): Drawing from the acquisition timeline, costs, workforce sizes, and market reaction, what overall pattern emerges about Company A's growth strategy and its reception? (synthesis)
  Good (evaluation): Based on the stock price decline and the CEO's stated rationale, how well does the evidence in the document support the claim that the acquisitions were strategically sound? (evaluation)
  Good (counterfactual): If Company A had not proceeded with the second acquisition of Company C, how would the total expenditure and workforce integration challenge have differed based on the information provided? (counterfactual)
  Good (comparison): In what ways do the two acquisitions differ in terms of cost, timing, scale (employees), and apparent market reaction? (comparison)
  Good (temporal): What is the chronological relationship between the two acquisitions and the stock price movement, and what does the sequence suggest? (temporal)

  Bad: What is Company A? (too simple — just locating a name)
  Bad: How much did Company B cost? (too simple — answer is a single number from one sentence)
  Bad: What will Company A acquire next? (speculative, not in the document)
  Bad: What is an acquisition? (asks for general knowledge, not document-specific)
"""

    return f"""You are an expert analyst creating COMPLEX questions strictly based on the document provided below.
Do not use outside knowledge, and do not invent any facts, names, numbers, or events that are not present in the document.

YOUR GOAL: Generate questions that require DEEP REASONING — not simple fact lookup.
Every question should require the reader to combine, analyze, compare, or reason across MULTIPLE pieces of information in the document.
A good question CANNOT be answered by copying a single sentence from the document.

QUESTION TYPES (use a diverse mix of these):
{types_block}
{few_shot_block}
COMPLEXITY REQUIREMENTS (STRICTLY FOLLOW):
1. Every question MUST require reasoning across at least 2 different parts of the document.
2. NEVER ask a question whose answer is a single fact found in one sentence (e.g. "What is X?" or "When did Y happen?").
3. Prefer questions that ask "how", "why", "what does X imply about Y", "how does X relate to Y", or "what overall pattern emerges".
4. For aggregation questions: require counting or combining information scattered across MULTIPLE paragraphs or sections.
5. For multi-hop questions: require connecting two or more separate facts to derive an answer that is NOT explicitly stated.
6. For causal questions: ask about cause-and-effect CHAINS, not just a single cause-effect pair.
7. For synthesis questions: require integrating 3+ separate facts into a coherent analysis.
8. For evaluation questions: ask whether the evidence in the document supports or contradicts a claim.
9. For counterfactual questions: ask what would change if a specific stated condition were different.

Document:
{text_content}

{distribution_note}
Output one question per line, without numbering or bullet points.
After each question, add a tag in parentheses indicating its type, e.g. (analysis), (aggregation), (causal), (synthesis), (evaluation), (counterfactual)."""


def _create_basic_prompt(text_content: str, num_questions: int = 3) -> str:
    """Original simple prompt for basic complexity."""
    return f"""You are creating questions strictly based on the document provided below.
Do not use outside knowledge, and do not invent any facts, names, numbers, or events that are not present in the document.

Based on the following document, generate {num_questions} questions that test understanding of the content.

Document:
{text_content}

Generate exactly {num_questions} questions, one per line, without numbering or bullet points."""


def _call_llm(prompt: str, config: Dict[str, Any]) -> str:
    provider = config["llm"].get("provider", "vllm").lower()
    max_retries = config["llm"].get("max_retries", 3)
    retry_delay = config["llm"].get("retry_delay", 1.0)

    validate_provider_for_offline_mode(provider, config)

    if provider == "vllm":
        return _call_vllm_llm(prompt, config, max_retries, retry_delay)
    if provider == "openai":
        return _call_openai_llm(prompt, config, max_retries, retry_delay)
    raise ValueError(f"Unsupported LLM provider: {provider}. Supported providers: vllm, openai")


def _call_vllm_llm(prompt: str, config: Dict[str, Any], max_retries: int, retry_delay: float) -> str:
    import openai

    api_key = config["llm"].get("api_key")
    if api_key == "EMPTY" or not api_key:
        api_key = "not-required"

    base_url = config["llm"].get("base_url", "http://localhost:8100/v1")
    model = config["llm"].get("model", "meta-llama/Llama-2-7b-chat-hf")
    temperature = config["llm"].get("temperature", 0.7)
    max_tokens = config["llm"].get("max_tokens", 500)
    timeout = config["llm"].get("timeout", 60)

    client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You generate questions using ONLY the provided document. "
                            "Do not invent facts not present in the document."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content if response.choices else None
            return (content or "").strip()
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise RuntimeError(
                f"vLLM API call failed after {max_retries} attempts: {e}\n"
                f"Make sure vLLM server is running at {base_url}"
            )


def _call_openai_llm(prompt: str, config: Dict[str, Any], max_retries: int, retry_delay: float) -> str:
    import openai

    api_key = config["llm"].get("api_key")
    if not api_key:
        raise RuntimeError("OpenAI API key is missing. Set OPENAI_API_KEY env var or llm.api_key in config.")

    base_url = config["llm"].get("base_url")
    timeout = config["llm"].get("timeout", 60)
    model = config["llm"].get("model", "gpt-4o-mini")
    temperature = config["llm"].get("temperature", 0.7)
    max_tokens = config["llm"].get("max_tokens", 500)

    client_kwargs = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = openai.OpenAI(**client_kwargs)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You generate grounded questions."},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content if response.choices else None
            return (content or "").strip()
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise RuntimeError(f"OpenAI API call failed after {max_retries} attempts: {e}")


def _parse_questions(response: str, num_questions: int = 3) -> List[str]:
    import re as _re
    lines = [line.strip() for line in (response or "").split("\n") if line.strip()]
    questions: List[str] = []
    for line in lines:
        line = line.lstrip("0123456789.-) ")
        # Remove all trailing type tags like (analysis), (aggregation), etc.
        # Handle multiple tags: "Question? (analysis) (comparison)" → "Question?"
        line = _re.sub(r'(\s*\([a-z_]+\))+\s*$', '', line).strip()
        if line:
            questions.append(line)
    return questions[:num_questions] if len(questions) >= num_questions else questions


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

    # For question validation, use semantic unless explicitly set to hybrid/llm
    # to avoid excessive LLM calls during generation (final grading uses hybrid)
    qval_method = (config.get("question_generation") or {}).get("validation", {}).get(
        "method", "semantic"
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
        {"confidence": confidence, "is_grounded": is_grounded, "attempts": 1, "issues": check_result.get("issues", [])}
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

Generate a NEW question grounded ONLY in the document. Provide only the question."""

        regenerated = _call_llm(regeneration_prompt, config).strip()
        # Keep previous question if regeneration returned empty
        if regenerated:
            current_question = regenerated
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
            {"confidence": confidence, "is_grounded": is_grounded, "issues": check_result.get("issues", [])}
        )

        if is_grounded and confidence >= min_confidence:
            return current_question, validation_info

    return current_question, validation_info


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
        raise ValueError(f"Invalid documents format. Expected dict or list, got {type(documents)}")

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

            while len(all_questions) < num_questions and generation_attempts < max_generation_attempts:
                generation_attempts += 1
                questions_needed = num_questions - len(all_questions)
                prompt = _create_question_prompt(
                    text_content,
                    questions_needed + 2,
                    complexity=complexity,
                    question_types=question_types,
                )
                response = _call_llm(prompt, config)
                new_questions = _parse_questions(response, questions_needed + 2)

                qgen_config = config.get("question_generation", {})
                similarity_threshold = qgen_config.get("duplicate_similarity_threshold", 0.85)
                dedup_method = qgen_config.get("deduplication_method", "semantic")

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

            validation_config = config.get("question_generation", {}).get("validation", {})
            enable_validation = validation_config.get("enable_rejection", True)
            question_validation_details = []
            if enable_validation:
                min_confidence = validation_config.get("min_confidence_threshold", 0.7)
                max_regeneration_attempts = validation_config.get("max_regeneration_attempts", 2)
                validated_questions = []
                for q_idx, question in enumerate(questions, 1):
                    final_question, validation_info = _validate_and_regenerate_question(
                        question=question,
                        document_content=text_content,
                        config=config,
                        min_confidence=min_confidence,
                        max_attempts=max_regeneration_attempts,
                    )
                    validated_questions.append(final_question)
                    question_validation_details.append(
                        {
                            "question_index": q_idx,
                            "original_question": question,
                            "final_question": final_question,
                            "validation_info": validation_info,
                        }
                    )
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
                        "question_types": question_types or COMPLEXITY_PRESETS.get(complexity, {}).get("types", []),
                        "question_validation": question_validation_details if enable_validation else None,
                    },
                }
            )
        except Exception as e:
            print(f"Error processing document: {e}", flush=True)
            continue

    return results

