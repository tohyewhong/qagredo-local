"""CLI tool to surface quality issues in generated Q&A outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils import list_available_results
from utils.result_analyzer import (
    DEFAULT_THRESHOLDS,
    evaluate_document_quality,
    summarize_documents,
)


def _filter_analysis_files(
    base_dir: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    date: Optional[str],
    limit: Optional[int],
) -> List[Path]:
    results = list_available_results(base_dir=Path(base_dir) if base_dir else None,
                                     provider=provider,
                                     model=model)
    filtered: List[Path] = []
    for entry in results:
        if date and entry["date"] != date:
            continue
        if not entry["file"].startswith("doc_") or not entry["file"].endswith("_analysis.json"):
            continue
        filtered.append(entry["path"])
        if limit and len(filtered) >= limit:
            break
    return filtered


def _load_document(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _print_summary(reports: List[Dict[str, Any]], verbose: bool) -> None:
    aggregate = summarize_documents(reports)
    print("=" * 80)
    print("Quality Summary")
    print("=" * 80)
    print(f"Documents analyzed : {aggregate['total_documents']}")
    for band, count in aggregate["quality_breakdown"].items():
        print(f"{band.title():<17}: {count}")
    print("=" * 80)
    for report in reports:
        print(
            f"[{report['quality_band'].upper():>8}] "
            f"{report['document_id']}  "
            f"questions={report['num_questions']}  "
            f"confidence={report['overall_confidence'] or 'n/a'}"
        )
        if report["warnings"]:
            print("  - Issues:")
            for warning in report["warnings"]:
                print(f"    • {warning}")
        if verbose:
            for detail in report["pair_details"]:
                notes = ", ".join(detail["notes"]) if detail["notes"] else ""
                print(
                    f"    Q: {detail['question']}\n"
                    f"       status={detail['status']} confidence={detail['confidence']} {notes}"
                )
        print()


def _write_markdown(reports: List[Dict[str, Any]], summary_path: Path) -> None:
    aggregate = summarize_documents(reports)
    lines = [
        "# Q&A Quality Summary",
        "",
        f"- **Documents analyzed**: {aggregate['total_documents']}",
        *(f"- **{band.title()}**: {count}" for band, count in aggregate["quality_breakdown"].items()),
        "",
        "## Per-Document Details",
        "",
    ]
    for report in reports:
        lines.append(f"### {report['document_id']} ({report['quality_band']})")
        lines.append(
            f"- Questions: {report['num_questions']}\n"
            f"- Overall Confidence: {report['overall_confidence'] or 'n/a'}"
        )
        if report["warnings"]:
            lines.append("- Issues:")
            lines.extend(f"  - {warning}" for warning in report["warnings"])
        lines.append("")
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Markdown summary written to {summary_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze quality of Q&A pipeline outputs.")
    parser.add_argument("--base-dir", type=str, default=None, help="Base output directory (default: ./output)")
    parser.add_argument("--provider", type=str, default=None, help="Filter by provider (openai, anthropic, etc.)")
    parser.add_argument("--model", type=str, default=None, help="Filter by model name")
    parser.add_argument("--date", type=str, default=None, help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of documents analyzed")
    parser.add_argument("--summary-file", type=Path, default=None, help="Optional Markdown summary output path")
    parser.add_argument("--verbose", action="store_true", help="Include per-question details")
    parser.add_argument("--min-questions", type=int, default=None, help="Override minimum questions threshold")
    parser.add_argument("--low-confidence", type=float, default=None, help="Override low confidence threshold")
    parser.add_argument("--review-confidence", type=float, default=None, help="Override review band threshold")
    parser.add_argument("--attention-confidence", type=float, default=None, help="Override needs-attention threshold")

    args = parser.parse_args()

    files = _filter_analysis_files(
        args.base_dir,
        args.provider,
        args.model,
        args.date,
        args.limit,
    )

    if not files:
        print("No document analysis files found matching the filters.")
        return

    custom_thresholds = DEFAULT_THRESHOLDS.copy()
    overrides = {
        "min_questions": args.min_questions,
        "low_confidence": args.low_confidence,
        "review_confidence": args.review_confidence,
        "attention_confidence": args.attention_confidence,
    }
    for key, value in overrides.items():
        if value is not None:
            custom_thresholds[key] = value

    reports: List[Dict[str, Any]] = []
    for path in files:
        document = _load_document(path)
        report = evaluate_document_quality(document, thresholds=custom_thresholds)
        report["path"] = str(path)
        reports.append(report)

    _print_summary(reports, args.verbose)

    if args.summary_file:
        _write_markdown(reports, args.summary_file)


if __name__ == "__main__":
    main()

