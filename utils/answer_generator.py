"""Answer generator using LLM to generate answers from questions and documents."""  # noqa: E501

from .langchain_components import build_answer_prompt, parse_structured_answer
from .config_manager import (
    build_llm_config,
    validate_provider_for_offline_mode,
)
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Union, Dict, List, Any, Optional
from urllib.request import Request, urlopen

from .document_text import extract_document_text
from .ollama_urls import is_ollama_openai_base_url
from .hallucination_checker import check_hallucination


_project_root = Path(__file__).parent.parent
_cert_path = _project_root / "certbundle" / "certbundle.crt"
if _cert_path.exists() and _cert_path.is_file():
    os.environ.setdefault("SSL_CERT_FILE", str(_cert_path.resolve()))
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(_cert_path.resolve()))


SINGAPORE_TZ = timezone(timedelta(hours=8), name="Asia/Singapore")


def _load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    return build_llm_config(base_config_path=config_path)


def _extract_text_content(document: Dict[str, Any]) -> str:
    """Body text for answers; English preferred over native."""
    return extract_document_text(
        document,
        strict=True,
        allow_loose_resolution=True,
    )


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


def _use_langchain_features(config: Dict[str, Any]) -> bool:
    framework_cfg = (
        (config.get("framework") or {}) if isinstance(config, dict) else {}
    )
    return bool(framework_cfg.get("use_langchain", False))


def _create_answer_prompt(
    question: str, document_content: str, config: Dict[str, Any]
) -> str:
    framework_cfg = (
        (config.get("framework") or {}) if isinstance(config, dict) else {}
    )
    structured_json = bool(
        (framework_cfg.get("langchain") or {}).get(
            "structured_json_output", False
        )
    )
    if _use_langchain_features(config):
        return build_answer_prompt(
            question, document_content, structured=structured_json
        )
    return build_answer_prompt(question, document_content, structured=False)


def _validate_and_regenerate_answer(
    answer: str,
    question: str,
    document_content: str,
    config: Dict[str, Any],
    min_confidence: float = 0.7,
    max_attempts: int = 3,
) -> tuple[str, Dict[str, Any]]:
    validation_info = {
        "confidence": 0.0,
        "attempts": 0,
        "was_regenerated": False,
        "issues": [],
    }

    # Use hallucination method from config for answer validation (default: llm)
    halluc_method = (config.get("hallucination") or {}).get("method", "llm")

    check_result = check_hallucination(
        answer=answer,
        document_content=document_content,
        question=question,
        method=halluc_method,
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
        return answer, validation_info

    current_answer = answer
    for attempt in range(2, max_attempts + 1):
        validation_info["attempts"] = attempt
        validation_info["was_regenerated"] = True

        regeneration_prompt = f"""Document:
{document_content}

Question: {question}

Previous Answer (REJECTED):
{current_answer}

Generate a NEW answer using ONLY the document. Provide only the answer."""
        raw = _call_llm(regeneration_prompt, config)
        from utils.minimal_text import sanitize_llm_answer_response

        current_answer, _ = sanitize_llm_answer_response(raw)
        if not current_answer.strip():
            current_answer = raw.strip()

        check_result = check_hallucination(
            answer=current_answer,
            document_content=document_content,
            question=question,
            method=halluc_method,
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
    parsed = parse_structured_answer(raw_answer)
    return parsed.get("answer", ""), parsed.get("supporting_evidence", "")


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
            "coverage_score": min(
                max(float(parsed.get("coverage_score", 1.0)), 0.0), 1.0
            ),
            "reason": str(parsed.get("reason", "")),
            "missing_points": [
                str(item) for item in missing if str(item).strip()
            ],
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
                "coverage_score": min(
                    max(float(parsed.get("coverage_score", 1.0)), 0.0), 1.0
                ),
                "reason": str(parsed.get("reason", "")),
                "missing_points": [
                    str(item) for item in missing if str(item).strip()
                ],
            }
        except (ValueError, TypeError, json.JSONDecodeError):
            pass

    # Conservative fallback if parser fails.
    lowered = text.lower()
    is_covered = not any(
        token in lowered
        for token in ["not covered", "missing", "incomplete", "partially"]
    )
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
    max_doc_chars = int(
        (config.get("answer_generation") or {})
        .get("coverage_validation", {})
        .get("max_doc_chars", 5000)
    )
    doc_text = document_content[:max_doc_chars]
    if len(document_content) > max_doc_chars:
        doc_text += "\n... [document truncated] ..."

    prompt = f"""You are a QA evaluator checking if an answer FULLY addresses a question.  # noqa: E501

DOCUMENT:
{doc_text}

QUESTION:
{question}

ANSWER:
{answer}

Evaluate whether the answer addresses all parts of the question while staying grounded in the document.  # noqa: E501
Respond with EXACTLY this JSON (no extra text):
{{"is_covered": true or false, "coverage_score": 0.0 to 1.0, "reason": "brief explanation", "missing_points": ["point 1", "point 2"]}}"""  # noqa: E501

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
    missing = (
        "\n".join(f"- {point}" for point in missing_points)
        if missing_points
        else "- Address all implied sub-parts of the question."
    )
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
Supporting evidence: [quotes from the document supporting the revised answer]"""  # noqa: E501

    revised_raw = _call_llm(prompt, config)
    from utils.minimal_text import sanitize_llm_answer_response

    return sanitize_llm_answer_response(revised_raw)


def _call_llm(prompt: str, config: Dict[str, Any]) -> str:
    provider = config["llm"].get("provider", "ollama").lower()
    max_retries = config["llm"].get("max_retries", 3)
    retry_delay = config["llm"].get("retry_delay", 1.0)

    validate_provider_for_offline_mode(provider, config)

    if provider in ("vllm", "ollama"):
        return _call_vllm_llm(prompt, config, max_retries, retry_delay)
    if provider == "openai":
        return _call_openai_llm(prompt, config, max_retries, retry_delay)
    raise ValueError(
        f"Unsupported LLM provider: {provider}. "
        "Supported providers: ollama, vllm, openai"
    )


def _call_vllm_llm(
    prompt: str, config: Dict[str, Any], max_retries: int, retry_delay: float
) -> str:
    import openai

    api_key = config["llm"].get("api_key")
    if api_key == "EMPTY" or not api_key:
        api_key = "not-required"

    base_url = config["llm"].get("base_url", "http://localhost:11434/v1")
    model = config["llm"].get("model", "meta-llama/Llama-2-7b-chat-hf")
    temperature = _get_answer_temperature(config)
    max_tokens = config["llm"].get("max_tokens", 500)
    timeout = config["llm"].get("timeout", 60)

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
            system_prompt="Answer using ONLY the given document.",
        )

    from utils.openai_helpers import (
        openai_chat_extra_body,
        openai_message_text,
        qwen_no_thinking_system_suffix,
    )

    client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    system = (
        "Answer using ONLY the given document."
        + qwen_no_thinking_system_suffix(model)
    )

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": system,
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
                for_question=False,
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
                f"Ollama API call failed after {max_retries} attempts: {e}"
            )
    return ""


def _call_openai_llm(
    prompt: str, config: Dict[str, Any], max_retries: int, retry_delay: float
) -> str:
    import openai

    from utils.openai_helpers import (
        openai_chat_extra_body,
        openai_message_text,
        qwen_no_thinking_system_suffix,
    )

    api_key = config["llm"].get("api_key")
    if not api_key:
        raise RuntimeError(
            "OpenAI API key is missing. Set OPENAI_API_KEY env var or llm.api_key in config."  # noqa: E501
        )

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
            system = (
                "Answer using ONLY the given document."
                + qwen_no_thinking_system_suffix(model)
            )
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
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
                for_question=False,
            )
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise RuntimeError(
                f"OpenAI API call failed after {max_retries} attempts: {e}"
            )


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
        raise ValueError(
            f"Invalid questions format. Expected str or list, got {type(questions)}"  # noqa: E501
        )

    document_content = _extract_text_content(document)
    answers: List[str] = []
    evidence_list: List[str] = []
    answer_quality_checks: List[Dict[str, Any]] = []

    for q_idx, question in enumerate(question_list, 1):
        quality_info: Dict[str, Any] = {
            "question_index": q_idx,
            "question": question,
            "validation": None,
            "coverage": None,
            "coverage_rewrite_applied": False,
        }
        if not isinstance(question, str) or not question.strip():
            answers.append("(Invalid question)")
            evidence_list.append("")
            quality_info["validation"] = {
                "is_grounded": False,
                "confidence": 0.0,
                "attempts": 0,
                "was_regenerated": False,
                "issues": ["Invalid question"],
            }
            answer_quality_checks.append(quality_info)
            continue

        try:
            prompt = _create_answer_prompt(question, document_content, config)
            raw_answer = _call_llm(prompt, config)

            from utils.minimal_text import sanitize_llm_answer_response

            answer, evidence = sanitize_llm_answer_response(raw_answer)
            if not answer.strip():
                answer = "Insufficient information in the document."

            answer_cfg = config.get("answer_generation", {}).get(
                "multi_turn", {}
            )
            if answer_cfg.get("enable_rejection", True):
                min_conf = answer_cfg.get("min_confidence_threshold", 0.7)
                max_attempts = int(
                    answer_cfg.get("max_answer_attempts", 0) or 0
                )
                if max_attempts <= 0:
                    regen_attempts = int(
                        answer_cfg.get("max_regeneration_attempts", 2) or 2
                    )
                    max_attempts = regen_attempts + 1
                if max_attempts < 1:
                    max_attempts = 1
                (
                    validated_answer,
                    validation_info,
                ) = _validate_and_regenerate_answer(
                    answer=answer,
                    question=question,
                    document_content=document_content,
                    config=config,
                    min_confidence=min_conf,
                    max_attempts=max_attempts,
                )
                quality_info["validation"] = validation_info
                if validated_answer != answer:
                    # Regenerated answers are plain text and may no longer
                    # match prior evidence.
                    evidence = ""
                answer = validated_answer
            else:
                quality_info["validation"] = {
                    "is_grounded": None,
                    "confidence": None,
                    "attempts": 0,
                    "was_regenerated": False,
                    "issues": [],
                }

            coverage_cfg = config.get("answer_generation", {}).get(
                "coverage_validation", {}
            )
            if coverage_cfg.get("enable", True):
                min_cov = coverage_cfg.get("min_score_threshold", 0.7)
                coverage_result = _check_question_coverage(
                    answer=answer,
                    question=question,
                    document_content=document_content,
                    config=config,
                )
                quality_info["coverage"] = coverage_result
                needs_rewrite = (
                    not coverage_result.get("is_covered", True)
                ) or (
                    float(coverage_result.get("coverage_score", 1.0))
                    < float(min_cov)
                )

                if needs_rewrite:
                    quality_info["coverage_rewrite_applied"] = True
                    (
                        revised_answer,
                        revised_evidence,
                    ) = _rewrite_for_question_coverage(
                        answer=answer,
                        question=question,
                        document_content=document_content,
                        missing_points=coverage_result.get(
                            "missing_points", []
                        ),
                        config=config,
                    )
                    halluc_method = (config.get("hallucination") or {}).get(
                        "method", "hybrid"
                    )
                    revised_check = check_hallucination(
                        answer=revised_answer,
                        document_content=document_content,
                        question=question,
                        method=halluc_method,
                    )
                    min_conf = answer_cfg.get("min_confidence_threshold", 0.7)
                    if (
                        revised_check.get("is_grounded", False)
                        and revised_check.get("confidence", 0.0) >= min_conf
                    ):
                        answer = revised_answer
                        evidence = revised_evidence
                    quality_info["coverage_rewrite_grounding"] = {
                        "is_grounded": revised_check.get("is_grounded"),
                        "confidence": revised_check.get("confidence"),
                    }
            else:
                quality_info["coverage"] = {
                    "is_covered": None,
                    "coverage_score": None,
                    "reason": "coverage validation disabled",
                    "missing_points": [],
                }
        except Exception as exc:
            print(f"  [WARN] Answer generation failed for Q{q_idx}: {exc}")
            answer = "(Answer generation failed)"
            evidence = ""
            quality_info["error"] = str(exc)

        answers.append(answer)
        evidence_list.append(evidence)
        answer_quality_checks.append(quality_info)

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
            "answer_quality_checks": answer_quality_checks,
        },
    }


def generate_answers_from_results(
    question_results: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(question_results, list):
        raise ValueError(
            f"Invalid question_results format. Expected list, got {type(question_results)}"  # noqa: E501
        )

    if config is None:
        config = _load_config(config_path)

    results: List[Dict[str, Any]] = []
    for idx, result in enumerate(question_results, 1):
        if not isinstance(result, dict):
            continue
        questions = result.get("questions", []) or []
        if not questions:
            continue

        document = {
            k: v
            for k, v in result.items()
            if k not in ["questions", "generation_metadata"]
        }
        answer_result = generate_answers(
            questions=questions, document=document, config=config
        )

        question_metadata = (result.get("generation_metadata") or {}).copy()
        answer_metadata = answer_result["generation_metadata"]

        question_metadata["answer_model"] = answer_metadata["model"]
        question_metadata["answer_provider"] = answer_metadata["provider"]
        question_metadata["answer_timestamp"] = answer_metadata["timestamp"]
        question_metadata["answer_timezone"] = answer_metadata.get(
            "timezone", "Asia/Singapore"
        )
        question_metadata["num_answers"] = answer_metadata["num_answers"]

        results.append(
            {
                **{
                    k: v
                    for k, v in result.items()
                    if k not in ["generation_metadata"]
                },
                "answers": answer_result["answers"],
                "supporting_evidence": answer_result.get(
                    "supporting_evidence", []
                ),
                "generation_metadata": question_metadata,
                "answer_metadata": {
                    "model": answer_metadata["model"],
                    "provider": answer_metadata["provider"],
                    "timestamp": answer_metadata["timestamp"],
                    "timezone": answer_metadata.get(
                        "timezone", "Asia/Singapore"
                    ),
                    "num_answers": answer_metadata["num_answers"],
                },
            }
        )

    return results
