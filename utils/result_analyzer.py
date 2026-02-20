"""Utilities to evaluate quality of generated Q&A documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional


DEFAULT_THRESHOLDS = {
    "min_questions": 3,
    "attention_confidence": 0.5,
    "review_confidence": 0.75,
    "low_confidence": 0.65,
    "short_answer_chars": 60,
}


@dataclass
class QAQuality:
    question_index: int
    confidence: Optional[float]
    is_grounded: Optional[bool]
    answer_length: int
    status: str
    notes: List[str] = field(default_factory=list)


def _evaluate_pair(pair: Dict[str, Any], idx: int, thresholds: Dict[str, float]) -> QAQuality:
    grading = pair.get("grading") or {}
    confidence = grading.get("confidence")
    is_grounded = grading.get("is_grounded")
    answer = pair.get("answer") or ""

    notes: List[str] = []
    status = "ok"

    if confidence is None:
        notes.append("missing confidence")
        status = "warn"
    elif confidence < thresholds["low_confidence"]:
        notes.append(f"confidence {confidence:.2f} below {thresholds['low_confidence']:.2f}")
        status = "warn"

    if is_grounded is False:
        notes.append("grading flagged as ungrounded")
        status = "fail"

    if len(answer.strip()) < thresholds["short_answer_chars"]:
        notes.append("answer very short")
        status = "warn" if status == "ok" else status

    return QAQuality(
        question_index=idx,
        confidence=confidence,
        is_grounded=is_grounded,
        answer_length=len(answer.strip()),
        status=status,
        notes=notes,
    )


def evaluate_document_quality(
    document: Dict[str, Any],
    *,
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    qa_pairs = document.get("qa_pairs") or []

    per_pair = [_evaluate_pair(pair, idx + 1, cfg) for idx, pair in enumerate(qa_pairs)]

    overall_conf = document.get("grading_summary", {}).get("overall_confidence")
    confidences = [pair.confidence for pair in per_pair if pair.confidence is not None]
    if overall_conf is None and confidences:
        overall_conf = mean(confidences)

    warnings: List[str] = []
    if len(qa_pairs) < cfg["min_questions"]:
        warnings.append(f"Only {len(qa_pairs)} question(s); expected ≥ {cfg['min_questions']}")

    low_conf_pairs = [pair for pair in per_pair if pair.status in {"warn", "fail"}]
    warnings.extend(
        f"Q{pair.question_index}: {', '.join(pair.notes)}"
        for pair in low_conf_pairs
        if pair.notes
    )

    if low_conf_pairs and confidences:
        overall_conf = mean(confidences)

    quality_band = "excellent"
    if not qa_pairs:
        quality_band = "needs_attention"
        warnings.append("No Q&A pairs available")
    elif overall_conf is not None and overall_conf < cfg["attention_confidence"]:
        quality_band = "needs_attention"
    elif low_conf_pairs and overall_conf is not None and overall_conf < cfg["review_confidence"]:
        quality_band = "needs_attention"
    elif warnings or (overall_conf is not None and overall_conf < cfg["review_confidence"]):
        quality_band = "review"

    document_id = (
        document.get("document", {}).get("id")
        or document.get("document_id")
        or "unknown"
    )

    return {
        "document_id": document_id,
        "num_questions": len(qa_pairs),
        "overall_confidence": overall_conf,
        "quality_band": quality_band,
        "warnings": warnings,
        "pair_details": [
            {
                "question": qa_pairs[idx].get("question"),
                "status": pair.status,
                "confidence": pair.confidence,
                "notes": pair.notes,
            }
            for idx, pair in enumerate(per_pair)
        ],
    }


def summarize_documents(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = {"excellent": 0, "review": 0, "needs_attention": 0}
    for doc in documents:
        band = doc.get("quality_band", "review")
        totals[band] = totals.get(band, 0) + 1
    return {"total_documents": len(documents), "quality_breakdown": totals}


__all__ = [
    "evaluate_document_quality",
    "summarize_documents",
    "DEFAULT_THRESHOLDS",
]

