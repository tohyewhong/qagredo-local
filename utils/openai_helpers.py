"""OpenAI-compatible client helpers (Qwen3.x / vLLM)."""

from __future__ import annotations

from typing import Any, Dict

from utils.minimal_text import (
    extract_deliverable_from_thinking,
    looks_like_thinking_blob,
)


def _is_qwen3_vllm_model(model: str) -> bool:
    """True for Qwen3.x base names and QAG merged/LoRA served names."""
    m = (model or "").lower()
    if "qwen3" in m:
        return True
    if m.startswith("qag-"):
        return True
    return False


def qwen_no_thinking_system_suffix(model: str) -> str:
    """Extra system instruction for Qwen3 models on vLLM."""
    if _is_qwen3_vllm_model(model):
        return (
            " Do not output Thinking Process, chain-of-thought, or "
            "reasoning. Output only the final questions or answers requested."
        )
    return ""


def openai_chat_extra_body(model: str) -> Dict[str, Any]:
    """Disable Qwen3 thinking in vLLM so content is the final reply."""
    if _is_qwen3_vllm_model(model):
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


def openai_message_text(
    message: Any,
    *,
    for_question: bool = False,
) -> str:
    """
    Prefer message.content; never return raw reasoning as the user-visible text.
    """
    content = (getattr(message, "content", None) or "").strip()
    field = "question" if for_question else "answer"

    if content and not looks_like_thinking_blob(content):
        from utils.minimal_text import (
            sanitize_llm_answer_response,
            sanitize_llm_question_response,
        )

        if for_question:
            qs = sanitize_llm_question_response(content, max_items=1)
            return qs[0] if qs else content
        ans, _ = sanitize_llm_answer_response(content)
        return ans if ans else content

    if content and looks_like_thinking_blob(content):
        extracted = extract_deliverable_from_thinking(
            content,
            field=field,
        )
        if extracted:
            return extracted
        return ""

    reasoning = getattr(message, "reasoning_content", None)
    if isinstance(reasoning, str) and reasoning.strip():
        # Do not surface reasoning chains as Q/A; only use if we can extract.
        extracted = extract_deliverable_from_thinking(
            reasoning.strip(),
            field=field,
        )
        if extracted:
            return extracted

    return content
