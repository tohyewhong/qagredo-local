"""Answer generator using LLM to generate answers from questions and documents."""

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Union, Dict, List, Any, Optional

from .hallucination_checker import check_hallucination


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


def _get_answer_temperature(config: Dict[str, Any]) -> float:
    """
    Get the temperature for answer generation.

    Uses answer_generation.temperature if set, otherwise falls back to
    llm.temperature with a lower default (0.3 vs 0.7) since answers
    should be more deterministic and factual than questions.
    """
    answer_temp = (config.get("answer_generation") or {}).get("temperature")
    if answer_temp is not None:
        try:
            return float(answer_temp)
        except (TypeError, ValueError):
            pass
    # Default to a lower temperature for answers to reduce hallucination
    return float((config.get("llm") or {}).get("temperature", 0.3))


def _create_answer_prompt(question: str, document_content: str) -> str:
    return f"""Document:
{document_content}

Question: {question}

Instructions:
1. Answer using ONLY information found in the document above.
2. If the answer requires counting or aggregating, list the items first, then state the total.
3. After your answer, provide a "Supporting evidence" section quoting the key phrases from the document that support your answer.
4. If the document does not contain sufficient information, say "Insufficient information in the document."

Format your response as:
Answer: [your answer]
Supporting evidence: [relevant quotes from document]"""


def _validate_and_regenerate_answer(
    answer: str,
    question: str,
    document_content: str,
    config: Dict[str, Any],
    min_confidence: float = 0.7,
    max_attempts: int = 3,
) -> tuple[str, Dict[str, Any]]:
    validation_info = {"confidence": 0.0, "attempts": 0, "was_regenerated": False, "issues": []}

    # Use hallucination method from config for answer validation (default: hybrid)
    halluc_method = (config.get("hallucination") or {}).get("method", "hybrid")

    check_result = check_hallucination(
        answer=answer,
        document_content=document_content,
        question=question,
        method=halluc_method,
    )
    confidence = check_result.get("confidence", 0.0)
    is_grounded = check_result.get("is_grounded", False)
    validation_info.update(
        {"confidence": confidence, "is_grounded": is_grounded, "attempts": 1, "issues": check_result.get("issues", [])}
    )

    if is_grounded and confidence >= min_confidence:
        return answer, validation_info

    current_answer = answer
    for attempt in range(1, max_attempts + 1):
        validation_info["attempts"] = attempt + 1
        validation_info["was_regenerated"] = True

        regeneration_prompt = f"""Document:
{document_content}

Question: {question}

Previous Answer (REJECTED):
{current_answer}

Generate a NEW answer using ONLY the document. Provide only the answer."""
        current_answer = _call_llm(regeneration_prompt, config)

        check_result = check_hallucination(
            answer=current_answer,
            document_content=document_content,
            question=question,
            method=halluc_method,
        )
        confidence = check_result.get("confidence", 0.0)
        is_grounded = check_result.get("is_grounded", False)
        validation_info.update(
            {"confidence": confidence, "is_grounded": is_grounded, "issues": check_result.get("issues", [])}
        )

        if is_grounded and confidence >= min_confidence:
            return current_answer, validation_info

    return current_answer, validation_info


def _parse_structured_answer(raw_answer: str) -> tuple:
    """
    Parse a structured LLM response into (answer, evidence).

    Expected format:
        Answer: [the answer text]
        Supporting evidence: [quotes from document]

    If the LLM doesn't follow the format, returns the full text as the answer
    with empty evidence (graceful fallback).
    """
    import re as _re

    answer = raw_answer.strip()
    evidence = ""

    # Try to extract "Answer:" and "Supporting evidence:" sections
    answer_match = _re.search(
        r"(?:^|\n)\s*Answer\s*:\s*(.*?)(?=\n\s*Supporting evidence\s*:|$)",
        answer,
        _re.DOTALL | _re.IGNORECASE,
    )
    evidence_match = _re.search(
        r"(?:^|\n)\s*Supporting evidence\s*:\s*(.*)",
        answer,
        _re.DOTALL | _re.IGNORECASE,
    )

    if answer_match:
        answer = answer_match.group(1).strip()
    if evidence_match:
        evidence = evidence_match.group(1).strip()

    # If no structured format detected, use entire text as answer
    if not answer_match and not evidence_match:
        answer = raw_answer.strip()

    return answer, evidence


def _parse_coverage_result(response: str) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "is_covered": True,
        "coverage_score": 1.0,
        "reason": "",
        "missing_points": [],
    }

    text = (response or "").strip()
    if not text:
        return defaults

    # Try direct JSON parse first.
    try:
        parsed = json.loads(text)
        missing = parsed.get("missing_points", [])
        if not isinstance(missing, list):
            missing = []
        return {
            "is_covered": bool(parsed.get("is_covered", True)),
            "coverage_score": min(max(float(parsed.get("coverage_score", 1.0)), 0.0), 1.0),
            "reason": str(parsed.get("reason", "")),
            "missing_points": [str(item) for item in missing if str(item).strip()],
        }
    except (ValueError, TypeError, json.JSONDecodeError):
        pass

    # Try extracting JSON object from surrounding text.
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            missing = parsed.get("missing_points", [])
            if not isinstance(missing, list):
                missing = []
            return {
                "is_covered": bool(parsed.get("is_covered", True)),
                "coverage_score": min(max(float(parsed.get("coverage_score", 1.0)), 0.0), 1.0),
                "reason": str(parsed.get("reason", "")),
                "missing_points": [str(item) for item in missing if str(item).strip()],
            }
        except (ValueError, TypeError, json.JSONDecodeError):
            pass

    # Conservative fallback if parser fails.
    lowered = text.lower()
    is_covered = not any(token in lowered for token in ["not covered", "missing", "incomplete", "partially"])
    return {
        "is_covered": is_covered,
        "coverage_score": 0.8 if is_covered else 0.4,
        "reason": text[:200],
        "missing_points": [],
    }


def _check_question_coverage(
    answer: str,
    question: str,
    document_content: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    max_doc_chars = int((config.get("answer_generation") or {}).get("coverage_validation", {}).get("max_doc_chars", 5000))
    doc_text = document_content[:max_doc_chars]
    if len(document_content) > max_doc_chars:
        doc_text += "\n... [document truncated] ..."

    prompt = f"""You are a QA evaluator checking if an answer FULLY addresses a question.

DOCUMENT:
{doc_text}

QUESTION:
{question}

ANSWER:
{answer}

Evaluate whether the answer addresses all parts of the question while staying grounded in the document.
Respond with EXACTLY this JSON (no extra text):
{{"is_covered": true or false, "coverage_score": 0.0 to 1.0, "reason": "brief explanation", "missing_points": ["point 1", "point 2"]}}"""

    try:
        response = _call_llm(prompt, config)
        return _parse_coverage_result(response)
    except Exception:
        # Do not block answer generation if the coverage judge fails.
        return {
            "is_covered": True,
            "coverage_score": 1.0,
            "reason": "coverage check failed — skipped",
            "missing_points": [],
        }


def _rewrite_for_question_coverage(
    answer: str,
    question: str,
    document_content: str,
    missing_points: List[str],
    config: Dict[str, Any],
) -> tuple[str, str]:
    missing = "\n".join(f"- {point}" for point in missing_points) if missing_points else "- Address all implied sub-parts of the question."
    prompt = f"""Document:
{document_content}

Question:
{question}

Previous answer (did not fully address the question):
{answer}

Missing points to address:
{missing}

Rewrite the answer so it fully addresses the question using ONLY the document.
If information is missing, state: "Insufficient information in the document."

Format:
Answer: [revised answer]
Supporting evidence: [quotes from the document supporting the revised answer]"""

    revised_raw = _call_llm(prompt, config)
    return _parse_structured_answer(revised_raw)


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

    base_url = config["llm"].get("base_url", "http://localhost:7100/v1")
    model = config["llm"].get("model", "meta-llama/Llama-2-7b-chat-hf")
    temperature = _get_answer_temperature(config)
    max_tokens = config["llm"].get("max_tokens", 500)
    timeout = config["llm"].get("timeout", 60)

    client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Answer using ONLY the given document."},
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
            raise RuntimeError(f"vLLM API call failed after {max_retries} attempts: {e}\nMake sure vLLM server is running at {base_url}")


def _call_openai_llm(prompt: str, config: Dict[str, Any], max_retries: int, retry_delay: float) -> str:
    import openai

    api_key = config["llm"].get("api_key")
    if not api_key:
        raise RuntimeError("OpenAI API key is missing. Set OPENAI_API_KEY env var or llm.api_key in config.")

    base_url = config["llm"].get("base_url")
    timeout = config["llm"].get("timeout", 60)
    model = config["llm"].get("model", "gpt-4o-mini")
    temperature = _get_answer_temperature(config)
    max_tokens = config["llm"].get("max_tokens", 500)

    client_kwargs = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = openai.OpenAI(**client_kwargs)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
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


def generate_answers(
    questions: Union[List[str], str],
    document: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    if config is None:
        config = _load_config(config_path)

    if isinstance(questions, str):
        question_list = [questions]
    elif isinstance(questions, list):
        question_list = questions
    else:
        raise ValueError(f"Invalid questions format. Expected str or list, got {type(questions)}")

    document_content = _extract_text_content(document)
    answers: List[str] = []
    evidence_list: List[str] = []

    for q_idx, question in enumerate(question_list, 1):
        if not isinstance(question, str) or not question.strip():
            answers.append("(Invalid question)")
            evidence_list.append("")
            continue

        try:
            prompt = _create_answer_prompt(question, document_content)
            raw_answer = _call_llm(prompt, config)

            # Parse structured response: separate answer from supporting evidence
            answer, evidence = _parse_structured_answer(raw_answer)

            answer_cfg = config.get("answer_generation", {}).get("multi_turn", {})
            if answer_cfg.get("enable_rejection", True):
                min_conf = answer_cfg.get("min_confidence_threshold", 0.7)
                max_attempts = answer_cfg.get("max_regeneration_attempts", 3)
                validated_answer, _ = _validate_and_regenerate_answer(
                    answer=answer,
                    question=question,
                    document_content=document_content,
                    config=config,
                    min_confidence=min_conf,
                    max_attempts=max_attempts,
                )
                if validated_answer != answer:
                    # Regenerated answers are plain text and may no longer match prior evidence.
                    evidence = ""
                answer = validated_answer

            coverage_cfg = config.get("answer_generation", {}).get("coverage_validation", {})
            if coverage_cfg.get("enable", True):
                min_cov = coverage_cfg.get("min_score_threshold", 0.7)
                coverage_result = _check_question_coverage(
                    answer=answer,
                    question=question,
                    document_content=document_content,
                    config=config,
                )
                needs_rewrite = (not coverage_result.get("is_covered", True)) or (
                    float(coverage_result.get("coverage_score", 1.0)) < float(min_cov)
                )

                if needs_rewrite:
                    revised_answer, revised_evidence = _rewrite_for_question_coverage(
                        answer=answer,
                        question=question,
                        document_content=document_content,
                        missing_points=coverage_result.get("missing_points", []),
                        config=config,
                    )
                    halluc_method = (config.get("hallucination") or {}).get("method", "hybrid")
                    revised_check = check_hallucination(
                        answer=revised_answer,
                        document_content=document_content,
                        question=question,
                        method=halluc_method,
                    )
                    min_conf = answer_cfg.get("min_confidence_threshold", 0.7)
                    if revised_check.get("is_grounded", False) and revised_check.get("confidence", 0.0) >= min_conf:
                        answer = revised_answer
                        evidence = revised_evidence
        except Exception as exc:
            print(f"  [WARN] Answer generation failed for Q{q_idx}: {exc}")
            answer = "(Answer generation failed)"
            evidence = ""

        answers.append(answer)
        evidence_list.append(evidence)

    return {
        "questions": question_list,
        "answers": answers,
        "supporting_evidence": evidence_list,
        "document_id": document.get("id"),
        "document_title": document.get("title"),
        "generation_metadata": {
            "model": config["llm"].get("model", "unknown"),
            "provider": config["llm"].get("provider", "unknown"),
            "timestamp": datetime.now(SINGAPORE_TZ).isoformat(),
            "timezone": "Asia/Singapore",
            "num_questions": len(question_list),
            "num_answers": len(answers),
        },
    }


def generate_answers_from_results(
    question_results: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(question_results, list):
        raise ValueError(f"Invalid question_results format. Expected list, got {type(question_results)}")

    if config is None:
        config = _load_config(config_path)

    results: List[Dict[str, Any]] = []
    for idx, result in enumerate(question_results, 1):
        if not isinstance(result, dict):
            continue
        questions = result.get("questions", []) or []
        if not questions:
            continue

        document = {k: v for k, v in result.items() if k not in ["questions", "generation_metadata"]}
        answer_result = generate_answers(questions=questions, document=document, config=config)

        question_metadata = (result.get("generation_metadata") or {}).copy()
        answer_metadata = answer_result["generation_metadata"]

        question_metadata["answer_model"] = answer_metadata["model"]
        question_metadata["answer_provider"] = answer_metadata["provider"]
        question_metadata["answer_timestamp"] = answer_metadata["timestamp"]
        question_metadata["answer_timezone"] = answer_metadata.get("timezone", "Asia/Singapore")
        question_metadata["num_answers"] = answer_metadata["num_answers"]

        results.append(
            {
                **{k: v for k, v in result.items() if k not in ["generation_metadata"]},
                "answers": answer_result["answers"],
                "supporting_evidence": answer_result.get("supporting_evidence", []),
                "generation_metadata": question_metadata,
                "answer_metadata": {
                    "model": answer_metadata["model"],
                    "provider": answer_metadata["provider"],
                    "timestamp": answer_metadata["timestamp"],
                    "timezone": answer_metadata.get("timezone", "Asia/Singapore"),
                    "num_answers": answer_metadata["num_answers"],
                },
            }
        )

    return results

