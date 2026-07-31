"""LangChain prompt/parsing helpers used by QAG.

QAG currently enforces LangChain at runtime via framework checks in
`run_qa_pipeline.py`. The helper functions still keep internal parsing
fallbacks for robustness against malformed model outputs.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any, Dict, List, Tuple


@functools.lru_cache(maxsize=1)
def _langchain_probe() -> Tuple[bool, str]:
    try:
        import langchain_core  # noqa: F401
    except BaseException as e:
        return False, f"{type(e).__name__}: {e}"
    return True, ""


def is_langchain_available() -> bool:
    return _langchain_probe()[0]


def langchain_import_error() -> str:
    """Empty if OK; otherwise the exception from importing langchain_core (for diagnostics)."""
    ok, err = _langchain_probe()
    return "" if ok else err


def build_answer_prompt(question: str, document_content: str, *, structured: bool = False) -> str:
    """Build answer prompt using LangChain templating."""
    if not is_langchain_available():
        return _legacy_answer_prompt(question, document_content, structured=structured)

    from langchain_core.prompts import PromptTemplate

    if structured:
        template = PromptTemplate.from_template(
            """Document:
{document_content}

Question: {question}

Instructions:
1. Answer using ONLY information found in the document above.
2. If counting or aggregating, list items first, then state the total.
3. Return STRICT JSON with this schema:
   {{
     "answer": "string",
     "supporting_evidence": "string"
   }}
4. If insufficient information exists, set "answer" to:
   "Insufficient information in the document."
"""
        )
    else:
        template = PromptTemplate.from_template(
            """Document:
{document_content}

Question: {question}

Instructions:
1. Answer using ONLY information found in the document above.
2. If the answer requires counting or aggregating, list the items first, then state the total.
3. After your answer, provide a "Supporting evidence" section quoting key phrases from the document.
4. If the document does not contain sufficient information, say "Insufficient information in the document."

Format your response as:
Answer: [your answer]
Supporting evidence: [relevant quotes from document]"""
        )
    return template.format(question=question, document_content=document_content)


def parse_structured_answer(raw_text: str) -> Dict[str, str]:
    """Parse answer text into structured fields using LangChain-first strategy."""
    text = (raw_text or "").strip()
    if not text:
        return {"answer": "", "supporting_evidence": ""}

    # 1) Strict JSON parse.
    try:
        parsed = json.loads(text)
        return {
            "answer": str(parsed.get("answer", "")).strip(),
            "supporting_evidence": str(parsed.get("supporting_evidence", "")).strip(),
        }
    except Exception:
        pass

    # 2) Extract embedded JSON object.
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return {
                "answer": str(parsed.get("answer", "")).strip(),
                "supporting_evidence": str(parsed.get("supporting_evidence", "")).strip(),
            }
        except Exception:
            pass

    # 3) LangChain parser (if installed) over relaxed schema fields.
    if is_langchain_available():
        try:
            from pydantic import BaseModel, Field

            class AnswerSchema(BaseModel):
                answer: str = Field(default="")
                supporting_evidence: str = Field(default="")

            # We avoid binding to a specific LangChain parser class to keep
            # compatibility broad; validate via Pydantic if we can detect keys.
            pseudo = {
                "answer": _extract_answer_section(text),
                "supporting_evidence": _extract_evidence_section(text),
            }
            obj = AnswerSchema.model_validate(pseudo)
            return {
                "answer": obj.answer.strip(),
                "supporting_evidence": obj.supporting_evidence.strip(),
            }
        except Exception:
            pass

    # 4) Legacy fallback.
    return {
        "answer": _extract_answer_section(text),
        "supporting_evidence": _extract_evidence_section(text),
    }


def parse_questions(raw_text: str) -> List[str]:
    """Parse newline questions and strip optional trailing type tags."""
    if not raw_text:
        return []

    questions: List[str] = []
    for line in raw_text.splitlines():
        q = line.strip()
        if not q:
            continue
        # Remove one or many trailing type tags: "(analysis) (comparison)".
        q = re.sub(r"(?:\s*\([a-zA-Z0-9_\- /]+\)\s*)+$", "", q).strip()
        if q:
            questions.append(q)
    return questions


def _legacy_answer_prompt(question: str, document_content: str, *, structured: bool) -> str:
    if structured:
        return (
            "Document:\n"
            f"{document_content}\n\n"
            f"Question: {question}\n\n"
            "Return STRICT JSON:\n"
            "{\n"
            '  "answer": "string",\n'
            '  "supporting_evidence": "string"\n'
            "}\n"
            "Use only document facts. If insufficient information, set answer to:\n"
            '"Insufficient information in the document."'
        )
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


def _extract_answer_section(text: str) -> str:
    m = re.search(
        r"(?:^|\n)\s*Answer\s*:\s*(.*?)(?=\n\s*Supporting evidence\s*:|$)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return text.strip()


def _extract_evidence_section(text: str) -> str:
    m = re.search(
        r"(?:^|\n)\s*Supporting evidence\s*:\s*(.*)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""
