"""Strip model reasoning from text used in minimal Q/A export."""

from __future__ import annotations

import re
from typing import Literal

FieldKind = Literal["question", "answer"]


def _is_plausible_answer(text: str) -> bool:
    s = (text or "").strip()
    if not s or s.lower() in ("string", "null", "none", "n/a"):
        return False
    if len(s) < 25:
        return False
    if s[-1] not in ".?!":
        return False
    return True


def looks_like_thinking_blob(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if re.search(r"(?is)^thinking\s+process\s*:", s):
        return True
    if re.search(r"(?m)^\s*\d+\.\s+\*\*Analyze\s+the\s+Request", s):
        return True
    if "**Analyze the Request:**" in s and len(s) > 400:
        return True
    return False


def _strip_markdown_inline(text: str) -> str:
    line = text.strip()
    line = re.sub(r"^\*+\s*", "", line)
    line = re.sub(r"\s*\*+\s*$", "", line)
    line = re.sub(r"^\*\s+", "", line)
    return line.strip()


def _remove_think_xml_blocks(text: str) -> str:
    s = text
    for tag in ("think", "redacted_reasoning"):
        open_t, close_t = f"<{tag}>", f"</{tag}>"
        s = re.sub(
            re.escape(open_t) + r".*?" + re.escape(close_t),
            "",
            s,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return s.strip()


def _extract_draft_lines(text: str) -> list[str]:
    drafts: list[str] = []
    for m in re.finditer(
        r"(?im)(?:\*+\s*)*Draft\s*\d+\s*(?:\*+\s*)*:?\s*(.+)$",
        text,
    ):
        cand = _strip_markdown_inline(m.group(1))
        cand = re.sub(r"^(?:\*+\s*)+", "", cand).strip()
        if cand and cand not in ("?", "*?", "*") and len(cand) > 10:
            drafts.append(cand)
    return drafts


def _question_lines_from_text(text: str) -> list[str]:
    out: list[str] = []
    skip_fragments = (
        "thinking process",
        "analyze the request",
        "meta-prompt",
        "does the",
        "is it ",
        "wait,",
        "this looks like",
        "drafting potential",
        "drafting the question",
    )
    for line in text.splitlines():
        line = _strip_markdown_inline(line)
        if not line.endswith("?"):
            continue
        low = line.lower()
        if any(frag in low for frag in skip_fragments):
            continue
        if len(line) < 20:
            continue
        line = re.sub(
            r"(?i)^(?:\*+\s*)*draft\s*\d+\s*(?:\*+\s*)*:?\s*",
            "",
            line,
        ).strip()
        if len(line) >= 20:
            out.append(line)
    return out


def extract_deliverable_from_thinking(
    text: str,
    *,
    field: FieldKind,
) -> str:
    """Pull a question or answer out of a Qwen-style thinking dump."""
    s = (text or "").strip()
    if not s:
        return ""

    drafts = _extract_draft_lines(s)

    if field == "question":
        q_lines = _question_lines_from_text(s)
        if q_lines:
            return q_lines[-1]
        if drafts:
            return drafts[-1]
        return ""

    # answer
    for pat in (
        r"(?im)^\s*(?:\*\*)?final answer(?:\*\*)?\s*:\s*(.+)$",
        r"(?im)^\s*(?:\*\*)?answer(?:\*\*)?\s*:\s*(.+)$",
    ):
        matches = list(re.finditer(pat, s))
        if matches:
            body = matches[-1].group(1).strip()
            body = re.split(
                r"(?is)\n\s*supporting evidence\s*:",
                body,
                maxsplit=1,
            )[0].strip()
            if body and not looks_like_thinking_blob(body):
                return body

    if drafts:
        cand = drafts[-1]
        if field == "answer" and not _is_plausible_answer(cand):
            return ""
        if not looks_like_thinking_blob(cand):
            return cand

    return ""


def plain_text_for_minimal_output(
    text: str,
    *,
    field: FieldKind = "answer",
) -> str:
    """
    Return user-facing question or answer only (no Thinking Process / think XML).
    """
    from utils.langchain_components import parse_structured_answer

    raw = (text or "").strip()
    s = _remove_think_xml_blocks(raw)
    if not s:
        return ""

    was_thinking = looks_like_thinking_blob(raw) or looks_like_thinking_blob(s)

    if was_thinking:
        extracted = extract_deliverable_from_thinking(s, field=field)
        if extracted:
            return extracted
        return ""

    for pat in (
        r"(?im)^\s*(?:\*\*)?final answer(?:\*\*)?\s*:\s*",
        r"(?im)^\s*(?:\*\*)?answer(?:\*\*)?\s*:\s*",
    ):
        parts = re.split(pat, s)
        if len(parts) > 1:
            s = parts[-1].strip()
    s = re.sub(r"^\*+\s*", "", s).strip()

    parsed = parse_structured_answer(s)
    body = (parsed.get("answer") or "").strip()
    if body and not looks_like_thinking_blob(body):
        s = body

    s = re.split(r"(?is)\n\s*supporting evidence\s*:", s, maxsplit=1)[0].strip()

    if was_thinking and looks_like_thinking_blob(s):
        return extract_deliverable_from_thinking(s, field=field)

    if field == "question":
        q_lines = _question_lines_from_text(s)
        if q_lines:
            return q_lines[-1]
        if was_thinking:
            return s if s.endswith("?") and len(s) >= 20 else ""
        return s

    if field == "answer":
        if s.strip().lower() in ("string", "null", "none", "n/a"):
            return ""
        if was_thinking and not _is_plausible_answer(s):
            return ""

    return s


_META_QUESTION_LINE = re.compile(
    r"(?i)^(?:thinking\s+process|analyze\s+the\s+request|drafting|"
    r"provide\s+only|instructions|document:|question:|\*?\s*input:)"
)


def _clean_question_candidate(line: str) -> str:
    q = plain_text_for_minimal_output(line, field="question")
    if not q or looks_like_thinking_blob(q):
        return ""
    if len(q) < 15:
        return ""
    if "?" not in q:
        return ""
    return q


def sanitize_llm_question_response(raw: str, *, max_items: int = 10) -> list[str]:
    """Parse generator LLM output into clean question strings only."""
    text = _remove_think_xml_blocks((raw or "").strip())
    if not text:
        return []

    if looks_like_thinking_blob(text):
        one = extract_deliverable_from_thinking(text, field="question")
        if one:
            cleaned = _clean_question_candidate(one)
            return [cleaned] if cleaned else []

    questions: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^[\d\.\)\-\*]+\s*", "", line).strip()
        if _META_QUESTION_LINE.search(line):
            continue
        if "Thinking Process" in line or "**Analyze the Request" in line:
            continue
        cleaned = _clean_question_candidate(line)
        if cleaned and cleaned not in questions:
            questions.append(cleaned)
        if len(questions) >= max_items:
            break

    if not questions:
        one = extract_deliverable_from_thinking(raw, field="question")
        if one:
            cleaned = _clean_question_candidate(one)
            if cleaned:
                questions.append(cleaned)

    return questions[:max_items]


def sanitize_llm_answer_response(raw: str) -> tuple[str, str]:
    """Parse answer LLM output into (answer, supporting_evidence)."""
    from utils.langchain_components import parse_structured_answer

    text = _remove_think_xml_blocks((raw or "").strip())
    if not text:
        return "", ""

    parsed = parse_structured_answer(text)
    evidence = (parsed.get("supporting_evidence") or "").strip()
    answer = plain_text_for_minimal_output(
        (parsed.get("answer") or "").strip() or text,
        field="answer",
    )

    if looks_like_thinking_blob(raw) and not _is_plausible_answer(answer):
        answer = extract_deliverable_from_thinking(text, field="answer")

    if answer.strip().lower() in ("string", "null", "none", "n/a"):
        answer = ""

    return answer.strip(), evidence
