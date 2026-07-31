#!/usr/bin/env python3
"""Summarize QAG metrics for a subset of documents in a run folder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _as_dict(obj: Any) -> Dict[str, Any]:
    return obj if isinstance(obj, dict) else {}


def _as_list(obj: Any) -> list:
    return obj if isinstance(obj, list) else []


def _coerce_grounded(val: Any) -> Optional[bool]:
    if val is True or val is False:
        return val
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


def _parse_conf(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return None


def _letter_grade(conf: float) -> str:
    if conf >= 0.9:
        return "A"
    if conf >= 0.8:
        return "B"
    if conf >= 0.7:
        return "C"
    if conf >= 0.6:
        return "D"
    return "F"


def _load_doc_ids(path: Path) -> Set[str]:
    found: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        found.add(line.split(",")[0].strip())
    return found


def _doc_id_from_analysis(data: Dict[str, Any]) -> str:
    doc = _as_dict(data.get("document"))
    return str(doc.get("id") or doc.get("title") or "").strip()


def summarize_subset(
    run_dir: Path,
    doc_ids: Set[str],
    label: str,
) -> Dict[str, Any]:
    analysis_files = sorted(
        [
            p
            for p in run_dir.glob("*_analysis.json")
            if "_minimal_" not in p.name
        ],
        key=lambda p: p.name,
    )

    documents: List[Dict[str, Any]] = []
    missing_ids = set(doc_ids)

    for path in analysis_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        doc_id = _doc_id_from_analysis(data)
        if doc_id not in doc_ids:
            continue
        missing_ids.discard(doc_id)

        qa_pairs = _as_list(data.get("qa_pairs"))
        checks = _as_list(data.get("hallucination_checks"))
        grading_summary = _as_dict(data.get("grading_summary"))
        run_metrics = _as_dict(data.get("run_metrics"))
        quality_counters = _as_dict(run_metrics.get("quality_counters"))

        grounded = 0
        ungrounded = 0
        confidences: List[float] = []
        for qi, pair in enumerate(qa_pairs):
            if not isinstance(pair, dict):
                continue
            grading = _as_dict(pair.get("hallucination_check"))
            if not grading:
                grading = _as_dict(pair.get("grading"))
            if not grading and qi < len(checks):
                chk = checks[qi]
                if isinstance(chk, dict):
                    grading = _as_dict(chk.get("check_result"))
            is_grounded = _coerce_grounded(grading.get("is_grounded"))
            conf = _parse_conf(grading.get("confidence"))
            if is_grounded is True:
                grounded += 1
            elif is_grounded is False:
                ungrounded += 1
            if conf is not None:
                confidences.append(conf)

        avg_conf = (
            sum(confidences) / len(confidences) if confidences else None
        )
        ov_conf = _parse_conf(grading_summary.get("overall_confidence"))
        if ov_conf is None and avg_conf is not None:
            ov_conf = avg_conf
        og_raw = grading_summary.get("overall_grade")
        if og_raw is not None and str(og_raw).strip():
            overall_grade = str(og_raw).strip()
        elif ov_conf is not None:
            overall_grade = _letter_grade(ov_conf)
        else:
            overall_grade = "N/A"

        precheck_failures = int(
            quality_counters.get("answerability_precheck_failures") or 0
        )
        q_grounding_retries = int(
            quality_counters.get("question_grounding_retries") or 0
        )
        slot_outcomes = len(qa_pairs) + precheck_failures
        documents.append(
            {
                "document_id": doc_id,
                "num_qa_pairs": len(qa_pairs),
                "grounded": grounded,
                "ungrounded": ungrounded,
                "avg_confidence": avg_conf,
                "overall_grade": overall_grade,
                "answerability_precheck_failures": precheck_failures,
                "question_grounding_retries": q_grounding_retries,
                "answerability_rejection_rate": (
                    round(precheck_failures / slot_outcomes, 4)
                    if slot_outcomes
                    else 0.0
                ),
            }
        )

    total_qa = sum(d["num_qa_pairs"] for d in documents)
    total_grounded = sum(d["grounded"] for d in documents)
    total_ungrounded = sum(d["ungrounded"] for d in documents)
    total_precheck_failures = sum(
        d.get("answerability_precheck_failures", 0) for d in documents
    )
    total_q_grounding_retries = sum(
        d.get("question_grounding_retries", 0) for d in documents
    )
    qa_conf = [
        c
        for d in documents
        for c in [d.get("avg_confidence")]
        if c is not None
    ]
    avg_confidence = sum(qa_conf) / len(qa_conf) if qa_conf else None

    grade_counts: Dict[str, int] = {}
    for doc in documents:
        grade = str(doc.get("overall_grade") or "N/A")
        grade_counts[grade] = grade_counts.get(grade, 0) + 1

    grounded_rate = (
        total_grounded / total_qa if total_qa else 0.0
    )
    judge_rejection_rate = (
        total_ungrounded / total_qa if total_qa else 0.0
    )
    slot_outcomes_total = total_qa + total_precheck_failures
    answerability_rejection_rate = (
        total_precheck_failures / slot_outcomes_total
        if slot_outcomes_total
        else 0.0
    )
    question_replacement_rate = (
        total_q_grounding_retries / total_qa if total_qa else 0.0
    )

    return {
        "label": label,
        "run_dir": str(run_dir),
        "requested_documents": len(doc_ids),
        "matched_documents": len(documents),
        "missing_document_ids": sorted(missing_ids),
        "num_qa_pairs": total_qa,
        "grounded": total_grounded,
        "ungrounded": total_ungrounded,
        "grounded_rate": round(grounded_rate, 4),
        "judge_rejection_rate": round(judge_rejection_rate, 4),
        "answerability_precheck_failures": total_precheck_failures,
        "answerability_rejection_rate": round(
            answerability_rejection_rate, 4
        ),
        "question_grounding_retries": total_q_grounding_retries,
        "question_replacement_rate": round(question_replacement_rate, 4),
        "avg_confidence": (
            round(avg_confidence, 4) if avg_confidence is not None else None
        ),
        "grade_counts": grade_counts,
        "documents": documents,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize QAG metrics for selected document ids."
    )
    parser.add_argument("run_dir", type=Path, help="Run folder with analysis")
    parser.add_argument(
        "--doc-ids",
        type=Path,
        required=True,
        help="Text file with one document id per line",
    )
    parser.add_argument(
        "--label",
        default="subset",
        help="Label for this summary (base, sft, dpo)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON summary to this path",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    doc_ids = _load_doc_ids(args.doc_ids.expanduser().resolve())
    summary = summarize_subset(run_dir, doc_ids, args.label)

    text = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.out:
        out_path = args.out.expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
        print(f"[ok] wrote {out_path}")
    else:
        print(text)

    if summary["missing_document_ids"]:
        print(
            "[warn] missing analysis for "
            f"{len(summary['missing_document_ids'])} doc ids",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
