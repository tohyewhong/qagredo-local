#!/usr/bin/env python3
"""Write a markdown comparison report from adapter eval summaries."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _load_summary(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(rate: float) -> str:
    return f"{100.0 * rate:.1f}%"


def _delta(new_val: float, base_val: float, *, higher_is_better: bool) -> str:
    diff = new_val - base_val
    if abs(diff) < 1e-9:
        return "0"
    sign = "+" if diff > 0 else ""
    arrow = ""
    if higher_is_better:
        arrow = " (better)" if diff > 0 else " (worse)"
    else:
        arrow = " (better)" if diff < 0 else " (worse)"
    return f"{sign}{diff:.4f}{arrow}"


def build_report(
    summaries: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    source_run: str,
) -> str:
    by_label = {s["label"]: s for s in summaries}
    base = by_label.get("base")
    if base is None:
        raise ValueError("base summary is required")

    lines: List[str] = []
    lines.append("# QAG Adapter Evaluation Report")
    lines.append("")
    lines.append(
        f"Generated: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    lines.append("")
    lines.append("## Test set selection")
    lines.append("")
    lines.append(
        manifest.get(
            "selection_method",
            "Holdout documents from lora_sft_eval.jsonl.",
        )
    )
    lines.append("")
    lines.append(f"- Source training run: `{source_run}`")
    lines.append(
        f"- Holdout documents: **{manifest.get('holdout_documents', '?')}** "
        f"(from **{manifest.get('eval_rows', '?')}** eval QA rows)"
    )
    protocol = manifest.get("protocol", "")
    if protocol == "fair_fresh_pipeline_all_models":
        lines.append(
            "- **Fair protocol:** base, SFT, and DPO each ran a **fresh** "
            "pipeline pass on the same document ids (same judge and config; "
            "only generator weights differ)."
        )
    else:
        lines.append(
            "- Base metrics: filtered from the original 500-doc pipeline run "
            "(same documents, pre-tuning generator)."
        )
        lines.append(
            "- SFT/DPO metrics: fresh pipeline runs on the same document ids "
            "with LoRA loaded in vLLM (new questions each run; judge "
            "unchanged)."
        )
    lines.append("")
    lines.append("## Headline metrics")
    lines.append("")
    lines.append(
        "| Model | Docs | QA pairs | Grounded | Ungrounded | "
        "Grounded rate | Judge rejection | Avg confidence |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    order = ["base", "sft", "dpo"]
    for label in order:
        row = by_label.get(label)
        if not row:
            continue
        rej = row.get("judge_rejection_rate")
        if rej is None and row.get("num_qa_pairs"):
            rej = row["ungrounded"] / row["num_qa_pairs"]
        rej_disp = _pct(rej) if rej is not None else "N/A"
        lines.append(
            f"| {label.upper()} | {row['matched_documents']} | "
            f"{row['num_qa_pairs']} | {row['grounded']} | "
            f"{row['ungrounded']} | {_pct(row['grounded_rate'])} | "
            f"{rej_disp} | {row.get('avg_confidence', 'N/A')} |"
        )

    lines.append("")
    lines.append("## Rejection rates")
    lines.append("")
    lines.append(
        "Judge **rejection rate** = ungrounded final QA pairs / all final "
        "QA pairs (lower is better). **Answerability rejection rate** = "
        "slots that failed the answerability pre-check before answer "
        "generation, divided by final QA pairs plus those failures "
        "(lower is better). **Question replacement rate** = replacement "
        "question generations after a failed grounding gate, per final "
        "QA pair (lower is better)."
    )
    lines.append("")
    lines.append(
        "| Model | Judge rejection | Answerability rejection | "
        "Precheck failures | Question replacements | "
        "Replacement rate |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for label in order:
        row = by_label.get(label)
        if not row:
            continue
        judge_rej = row.get("judge_rejection_rate")
        if judge_rej is None and row.get("num_qa_pairs"):
            judge_rej = row["ungrounded"] / row["num_qa_pairs"]
        ans_rej = row.get("answerability_rejection_rate")
        precheck = row.get("answerability_precheck_failures", 0)
        q_retries = row.get("question_grounding_retries", 0)
        repl_rate = row.get("question_replacement_rate")
        judge_disp = _pct(judge_rej) if judge_rej is not None else "N/A"
        ans_disp = _pct(ans_rej) if ans_rej is not None else "N/A"
        repl_disp = _pct(repl_rate) if repl_rate is not None else "N/A"
        lines.append(
            f"| {label.upper()} | {judge_disp} | {ans_disp} | "
            f"{precheck} | {q_retries} | {repl_disp} |"
        )

    lines.append("")
    lines.append("## vs base (rejection rates)")
    lines.append("")
    base_judge_rej = base.get("judge_rejection_rate")
    if base_judge_rej is None and base.get("num_qa_pairs"):
        base_judge_rej = base["ungrounded"] / base["num_qa_pairs"]
    base_ans_rej = base.get("answerability_rejection_rate")
    base_repl = base.get("question_replacement_rate")
    for label in ("sft", "dpo"):
        row = by_label.get(label)
        if not row:
            continue
        lines.append(f"### {label.upper()}")
        lines.append("")
        judge_rej = row.get("judge_rejection_rate")
        if judge_rej is None and row.get("num_qa_pairs"):
            judge_rej = row["ungrounded"] / row["num_qa_pairs"]
        if judge_rej is not None and base_judge_rej is not None:
            judge_delta = _delta(
                judge_rej,
                base_judge_rej,
                higher_is_better=False,
            )
            lines.append(
                f"- Judge rejection rate: {_pct(judge_rej)} "
                f"({judge_delta} vs base; lower is better)"
            )
        ans_rej = row.get("answerability_rejection_rate")
        if ans_rej is not None and base_ans_rej is not None:
            ans_delta = _delta(
                ans_rej,
                base_ans_rej,
                higher_is_better=False,
            )
            lines.append(
                f"- Answerability rejection rate: {_pct(ans_rej)} "
                f"({ans_delta} vs base; lower is better)"
            )
        precheck_delta = (
            row.get("answerability_precheck_failures", 0)
            - base.get("answerability_precheck_failures", 0)
        )
        lines.append(
            f"- Answerability pre-check failures: "
            f"{row.get('answerability_precheck_failures', 0)} "
            f"({precheck_delta:+d} vs base)"
        )
        q_delta = (
            row.get("question_grounding_retries", 0)
            - base.get("question_grounding_retries", 0)
        )
        lines.append(
            f"- Question grounding retries: "
            f"{row.get('question_grounding_retries', 0)} "
            f"({q_delta:+d} vs base)"
        )
        repl_rate = row.get("question_replacement_rate")
        if repl_rate is not None and base_repl is not None:
            repl_delta = _delta(
                repl_rate,
                base_repl,
                higher_is_better=False,
            )
            lines.append(
                f"- Question replacement rate: {_pct(repl_rate)} "
                f"({repl_delta} vs base; lower is better)"
            )
        lines.append("")

    lines.append("## vs base (grounded rate)")
    lines.append("")
    for label in ("sft", "dpo"):
        row = by_label.get(label)
        if not row:
            continue
        lines.append(f"### {label.upper()}")
        lines.append("")
        rate_delta = _delta(
            row["grounded_rate"],
            base["grounded_rate"],
            higher_is_better=True,
        )
        lines.append(
            f"- Grounded rate: {_pct(row['grounded_rate'])} "
            f"({rate_delta} vs base)"
        )
        missing = len(row.get("missing_document_ids") or [])
        req = row.get("requested_documents", 0)
        matched = row.get("matched_documents", 0)
        if req and matched < req:
            lines.append(
                f"- Coverage: {matched}/{req} docs scored "
                f"({missing} missing analysis files)"
            )
        lines.append(
            f"- Ungrounded count: {row['ungrounded']} "
            f"({row['ungrounded'] - base['ungrounded']:+d} vs base)"
        )
        if (
            row.get("avg_confidence") is not None
            and base.get("avg_confidence")
        ):
            conf_delta = _delta(
                row["avg_confidence"],
                base["avg_confidence"],
                higher_is_better=True,
            )
            lines.append(
                f"- Avg confidence: {row['avg_confidence']} "
                f"({conf_delta} vs base)"
            )
        lines.append("")

    lines.append("## Run directories")
    lines.append("")
    for label in order:
        row = by_label.get(label)
        if row:
            lines.append(f"- **{label.upper()}**: `{row['run_dir']}`")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "Improvement is indicated by **lower rejection rates** (judge, "
        "answerability, question replacement) and a **higher grounded rate** "
        "on this held-out document set, with the same judge and pipeline "
        "settings."
    )
    lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write adapter eval comparison report."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="eval_holdout/manifest.json",
    )
    parser.add_argument(
        "--summary",
        action="append",
        dest="summaries",
        required=True,
        help="Summary JSON (repeat for base, sft, dpo)",
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        required=True,
        help="Markdown report output path",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Optional combined JSON output",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    manifest = _load_summary(args.manifest)
    summaries = [_load_summary(Path(p)) for p in args.summaries]
    report = build_report(
        summaries,
        manifest,
        str(manifest.get("source_run_dir", "")),
    )

    out_md = args.out_md.expanduser().resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(report + "\n", encoding="utf-8")
    print(f"[ok] report -> {out_md}")

    if args.out_json:
        payload = {
            "manifest": manifest,
            "summaries": summaries,
        }
        out_json = args.out_json.expanduser().resolve()
        out_json.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[ok] combined json -> {out_json}")


if __name__ == "__main__":
    main()
