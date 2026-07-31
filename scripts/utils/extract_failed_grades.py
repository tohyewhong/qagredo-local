#!/usr/bin/env python3
"""Extract D/F (and other low-grade) rows from run_summary.json."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set


def _pair_grade(conf: float) -> str:
    if conf >= 0.9:
        return "A"
    if conf >= 0.8:
        return "B"
    if conf >= 0.7:
        return "C"
    if conf >= 0.6:
        return "D"
    return "F"


def _iter_failed_qa(
    doc: Dict[str, Any],
) -> Iterable[Dict[str, Any]]:
    for slot_idx, qa in enumerate(doc.get("qa_details") or [], 1):
        if not isinstance(qa, dict):
            continue
        grounded = qa.get("is_grounded", True)
        conf = float(qa.get("confidence") or 0.0)
        if grounded is False or conf < 0.7:
            issues = qa.get("issues") or []
            yield {
                "document_id": doc.get("document_id", ""),
                "doc_overall_grade": doc.get("overall_grade", ""),
                "doc_overall_confidence": doc.get("overall_confidence", ""),
                "slot": slot_idx,
                "pair_grade": _pair_grade(conf),
                "is_grounded": grounded,
                "confidence": conf,
                "question": (qa.get("question") or "").strip(),
                "answer": (qa.get("answer") or "").strip(),
                "judge_issues": " | ".join(str(i) for i in issues),
            }


def extract_rows(
    summary: Dict[str, Any],
    *,
    doc_grades: Set[str],
    include_ungrounded_only: bool,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for doc in summary.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        og = str(doc.get("overall_grade") or "").strip().upper()
        if doc_grades and og not in doc_grades:
            if not include_ungrounded_only:
                continue
        if include_ungrounded_only or (doc_grades and og in doc_grades):
            for row in _iter_failed_qa(doc):
                rows.append(row)
        elif not doc_grades:
            for row in _iter_failed_qa(doc):
                rows.append(row)
    return rows


def write_outputs(
    rows: List[Dict[str, Any]],
    csv_path: Path,
    ids_path: Path,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "document_id",
        "doc_overall_grade",
        "doc_overall_confidence",
        "slot",
        "pair_grade",
        "is_grounded",
        "confidence",
        "question",
        "answer",
        "judge_issues",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    doc_ids = sorted({r["document_id"] for r in rows if r["document_id"]})
    ids_path.write_text(
        "\n".join(doc_ids) + ("\n" if doc_ids else ""),
        encoding="utf-8",
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract failed/low-grade Q&A rows from run_summary.json"
    )
    parser.add_argument(
        "run_summary",
        type=Path,
        help="Path to run_summary.json",
    )
    parser.add_argument(
        "--grades",
        default="D,F",
        help="Comma-separated document overall grades (default: D,F)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Output CSV path (default: beside summary)",
    )
    parser.add_argument(
        "--ids",
        type=Path,
        default=None,
        help="Output document-id list for re-run (default: beside summary)",
    )
    args = parser.parse_args(argv)

    summary_path = args.run_summary.resolve()
    if not summary_path.is_file():
        print(f"[ERROR] Not found: {summary_path}", file=sys.stderr)
        return 1

    grades = {g.strip().upper() for g in args.grades.split(",") if g.strip()}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = extract_rows(
        summary,
        doc_grades=grades,
        include_ungrounded_only=False,
    )

    stem = summary_path.parent / "failed_grades_DF"
    csv_path = args.csv or Path(f"{stem}.csv")
    ids_path = args.ids or Path(f"{stem}_document_ids.txt")

    write_outputs(rows, csv_path, ids_path)

    doc_ids = sorted({r["document_id"] for r in rows if r["document_id"]})
    print(f"Extracted {len(rows)} failed slots from {len(doc_ids)} documents")
    print(f"CSV : {csv_path}")
    print(f"IDs : {ids_path}")
    print()
    print("Re-run failed docs (overwrite in resume folder):")
    print(
        "  bash run.sh --pipeline-only --fast --resume "
        f"--only-document-ids-file {ids_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
