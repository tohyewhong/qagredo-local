"""Resolve document body from input records (English preferred over native)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Standard priority (first non-empty wins).
_TEXT_FIELDS = (
    "content",
    "text",
    "body",
    "document",
    "article",
    "passage",
)

_METADATA_FIELDS = frozenset(
    {"id", "title", "source", "type", "metadata"},
)

# Never treat as prose in generic fallback (avoids merged QA payloads).
_FALLBACK_SKIP = frozenset(
    {
        "native",
        "english",
        "questions",
        "answers",
        "supporting_evidence",
        "hallucination_checks",
        "qa_pairs",
        "grading",
        "grading_summary",
        "question_generation",
        "answer_generation",
        "run_metrics",
    },
)

_ARTICLE_KEYS = frozenset(
    {"article", "content", "text", "body", "passage"},
)


def _scalar_from_priority_fields(document: Dict[str, Any]) -> Optional[str]:
    for field in _TEXT_FIELDS:
        if field not in document or not document[field]:
            continue
        raw = document[field]
        if isinstance(raw, list):
            joined = " ".join(str(item) for item in raw)
            return joined if joined.strip() else None
        s = str(raw).strip()
        return s if s else None
    return None


def _text_from_top_level_english(document: Dict[str, Any]) -> Optional[str]:
    eng = document.get("english")
    if not isinstance(eng, dict) or not eng:
        return None
    article_text = eng.get("article", "")
    if isinstance(article_text, str) and article_text.strip():
        return article_text.strip()
    if isinstance(article_text, list):
        joined = " ".join(str(x) for x in article_text if x)
        return joined.strip() if joined.strip() else None
    return None


def _get_source_list(doc: Dict[str, Any]) -> Optional[List[Any]]:
    for key in ("source", "sources"):
        val = doc.get(key)
        if isinstance(val, list):
            return val
    return None


def _extract_source_articles_english_only(
    src_item: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Match convert script: English or flat ``article`` only; skip ``native``.
    """
    results: List[Dict[str, Any]] = []
    if not isinstance(src_item, dict):
        return results
    found_via_lang_key = False
    lang_data = src_item.get("english")
    if isinstance(lang_data, dict) and lang_data:
        article_text = lang_data.get("article", "")
        if isinstance(article_text, str) and article_text.strip():
            results.append(
                {
                    "language": "english",
                    "article": article_text.strip(),
                    "title": str(lang_data.get("title", "") or ""),
                    "source_date": lang_data.get("source_date"),
                }
            )
            found_via_lang_key = True
    if not found_via_lang_key:
        direct_article = src_item.get("article", "")
        if isinstance(direct_article, str) and direct_article.strip():
            results.append(
                {
                    "language": "unknown",
                    "article": direct_article.strip(),
                    "title": str(src_item.get("title", "") or ""),
                    "source_date": src_item.get("source_date"),
                }
            )
    return results


def _text_from_source_list(document: Dict[str, Any]) -> Optional[str]:
    source_list = _get_source_list(document)
    if not source_list:
        return None
    parts: List[str] = []
    for src_item in source_list:
        if not isinstance(src_item, dict):
            continue
        for art_info in _extract_source_articles_english_only(src_item):
            art = art_info.get("article")
            if art:
                parts.append(str(art))
    if not parts:
        return None
    return "\n\n".join(parts).strip()


def _deep_extract_non_native(obj: Any, *, _depth: int = 0) -> List[str]:
    """Collect text under article-like keys; skip any ``native`` subtree."""
    if _depth > 6:
        return []
    results: List[str] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "native":
                continue
            if key in _ARTICLE_KEYS and isinstance(val, str) and val.strip():
                results.append(val.strip())
            elif isinstance(val, (dict, list)):
                results.extend(
                    _deep_extract_non_native(val, _depth=_depth + 1)
                )
    elif isinstance(obj, list):
        for item in obj:
            results.extend(
                _deep_extract_non_native(item, _depth=_depth + 1)
            )
    return results


def _text_from_deep(document: Dict[str, Any]) -> Optional[str]:
    source_list = _get_source_list(document)
    if source_list:
        deep_texts = _deep_extract_non_native(source_list)
        if deep_texts:
            return "\n\n".join(deep_texts).strip()
    deep_texts = _deep_extract_non_native(document)
    if deep_texts:
        return "\n\n".join(deep_texts).strip()
    return None


def _text_from_fallback(document: Dict[str, Any]) -> Optional[str]:
    text_parts: List[str] = []
    for key, value in document.items():
        if key in _METADATA_FIELDS or key in _FALLBACK_SKIP:
            continue
        if key in ("source", "sources") and isinstance(value, list):
            continue
        if not value:
            continue
        text_parts.append(str(value))
    if not text_parts:
        return None
    joined = " ".join(text_parts).strip()
    return joined if joined else None


def extract_document_text(
    document: Dict[str, Any],
    *,
    strict: bool = True,
    allow_loose_resolution: bool = True,
) -> str:
    """
    Resolve body text for Q&A / snapshots (English preferred over native).

    Order: (1) standard text fields, (2) top-level ``english.article``,
    (3) English-only text from ``source`` / ``sources`` list items,
    (4) if *allow_loose_resolution*: deep extract skipping ``native``,
    then a generic fallback that omits ``native``, ``english``, and QA
    keys.

    When *allow_loose_resolution* is False (merged grading dicts), stop
    after (3) so ``questions`` / ``answers`` are never used as body text.

    Raises ``ValueError`` if *strict* and no text is found.
    """
    if not isinstance(document, dict):
        if strict:
            raise ValueError("Document must be a dict")
        return ""

    for step in (
        _scalar_from_priority_fields,
        _text_from_top_level_english,
        _text_from_source_list,
    ):
        text = step(document)
        if text:
            return text

    if allow_loose_resolution:
        text = _text_from_deep(document)
        if text:
            return text
        text = _text_from_fallback(document)
        if text:
            return text

    if strict:
        raise ValueError(
            "No text content found in document. Available keys: "
            f"{list(document.keys())}"
        )
    return ""
