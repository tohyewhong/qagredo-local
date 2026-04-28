#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# summarize_run.sh  --  Summarize all analysis JSON files from a pipeline run
# ============================================================================
#
# Reads all *_analysis.json files in the output directory and produces a
# concise summary: per-document stats + overall run statistics.
#
# Usage:
#   bash scripts/utils/summarize_run.sh                          # latest run in ./output/
#   bash scripts/utils/summarize_run.sh /path/to/output/date/    # specific date folder
#   bash scripts/utils/summarize_run.sh --latest                 # auto-find latest date folder
#   bash scripts/utils/summarize_run.sh --all                    # all dates combined
#
# Output:
#   Prints summary to terminal, and optionally saves to a JSON file.
#   Use --json to save: bash scripts/utils/summarize_run.sh --json
# ============================================================================

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR=""
SAVE_JSON=0
MODE="auto"  # auto, latest, all, path

for arg in "$@"; do
  case "$arg" in
    --json)    SAVE_JSON=1 ;;
    --latest)  MODE="latest" ;;
    --all)     MODE="all" ;;
    -h|--help)
      echo "Usage: bash scripts/utils/summarize_run.sh [OPTIONS] [OUTPUT_DIR]"
      echo ""
      echo "Options:"
      echo "  --latest    Auto-find the latest date folder in output/"
      echo "  --all       Summarize all dates combined"
      echo "  --json      Also save summary as JSON file"
      echo "  -h, --help  Show this help"
      echo ""
      echo "Examples:"
      echo "  bash scripts/utils/summarize_run.sh"
      echo "  bash scripts/utils/summarize_run.sh output/ollama/qwen3.5-9b/2026-02-11/"
      echo "  bash scripts/utils/summarize_run.sh --latest --json"
      exit 0
      ;;
    *)
      if [[ -d "$arg" ]]; then
        OUTPUT_DIR="$arg"
        MODE="path"
      else
        echo "Unknown argument or directory not found: $arg" >&2
        exit 2
      fi
      ;;
  esac
done

# ---- find the output directory ----
if [[ "$MODE" != "path" ]]; then
  BASE_OUTPUT="$HOST_DIR/output"
  if [[ ! -d "$BASE_OUTPUT" ]]; then
    echo "[ERROR] Output directory not found: $BASE_OUTPUT" >&2
    exit 1
  fi
fi

python3 - "$MODE" "$OUTPUT_DIR" "$HOST_DIR" "$SAVE_JSON" <<'PYTHON_SCRIPT'
import json
import sys
import os
from pathlib import Path
from datetime import datetime

mode = sys.argv[1]
output_dir_arg = sys.argv[2]
host_dir = sys.argv[3]
save_json = sys.argv[4] == "1"

base_output = Path(host_dir) / "output"


def _as_dict(obj):
    """JSON null or wrong type must not become .get on None."""
    return obj if isinstance(obj, dict) else {}


def _as_list(obj):
    return obj if isinstance(obj, list) else []


def _str_field(val, default):
    """JSON null must not reach f-string :>N / :.2f (None.__format__ errors)."""
    return default if val is None else val


def _first_non_null_str(*vals, default="unknown"):
    for v in vals:
        if v is not None:
            return v
    return default


def _coerce_grounded(val):
    """Normalize is_grounded for JSON bool / string / missing."""
    if val is True or val is False:
        return val
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


def _parse_conf(val):
    """Parse confidence for display; JSON may use str or null."""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return None


def _safe_float(val, default=0.0):
    if val is None:
        return default
    if isinstance(val, bool):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0):
    if val is None:
        return default
    if isinstance(val, bool):
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _letter_grade(conf: float) -> str:
    """Same thresholds as utils/hallucination_checker grade_qa_results."""
    if conf >= 0.9:
        return "A"
    if conf >= 0.8:
        return "B"
    if conf >= 0.7:
        return "C"
    if conf >= 0.6:
        return "D"
    return "F"

# ---- find analysis files ----
def find_analysis_files(search_dir):
    """Find all *_analysis.json files recursively."""
    p = Path(search_dir)
    if not p.exists():
        return []
    return sorted(p.rglob("*_analysis.json"), key=lambda f: f.name)

if mode == "path":
    search_dir = Path(output_dir_arg)
    analysis_files = find_analysis_files(search_dir)
elif mode == "all":
    analysis_files = find_analysis_files(base_output)
else:
    # auto or latest: find the most recent run folder
    # Supports both new format (YYYY-MM-DD_HHMMSS) and legacy (YYYY-MM-DD)
    import re
    run_dir_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{6})?$")
    run_dirs = []
    for p in base_output.rglob("*"):
        if p.is_dir() and run_dir_pattern.match(p.name):
            run_dirs.append(p)
    if not run_dirs:
        print("[ERROR] No run folders found in", base_output)
        sys.exit(1)
    latest_dir = max(run_dirs, key=lambda d: d.name)
    search_dir = latest_dir
    analysis_files = find_analysis_files(search_dir)

if not analysis_files:
    print("[WARN] No *_analysis.json files found.")
    sys.exit(0)

# ---- parse all files ----
documents = []
for f in analysis_files:
    try:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] Skipping {f.name}: {e}")
        continue

    if not isinstance(data, dict):
        print(f"[WARN] Skipping {f.name}: root JSON must be an object")
        continue

    doc_info = _as_dict(data.get("document"))
    qa_pairs = _as_list(data.get("qa_pairs"))
    root_hallucination_checks = _as_list(data.get("hallucination_checks"))
    grading_summary = _as_dict(data.get("grading_summary"))
    q_gen = _as_dict(data.get("question_generation"))
    a_gen = _as_dict(data.get("answer_generation"))
    run_metrics = _as_dict(data.get("run_metrics"))
    timings = _as_dict(run_metrics.get("timings_seconds"))
    quality_counters = _as_dict(run_metrics.get("quality_counters"))

    # Per-QA stats
    grounded_count = 0
    ungrounded_count = 0
    confidences = []
    qa_details = []
    for qi, pair in enumerate(qa_pairs):
        if not isinstance(pair, dict):
            continue
        grading = _as_dict(pair.get("hallucination_check"))
        if not grading:
            grading = _as_dict(pair.get("grading"))
        if not grading and qi < len(root_hallucination_checks):
            chk = root_hallucination_checks[qi]
            if isinstance(chk, dict):
                grading = _as_dict(chk.get("check_result"))
        question = pair.get("question") or ""
        answer = pair.get("answer") or ""
        is_grounded = _coerce_grounded(grading.get("is_grounded"))
        conf = _parse_conf(grading.get("confidence"))
        issues = _as_list(grading.get("issues"))
        method = grading.get("method") or ""
        ungrounded_sentences = _as_list(
            grading.get("ungrounded_sentences")
        )

        if is_grounded is True:
            grounded_count += 1
        elif is_grounded is False:
            ungrounded_count += 1
        if conf is not None:
            confidences.append(float(conf))

        # Build per-QA detail entry
        qa_entry = {
            "question": question,
            "answer": answer,
            "is_grounded": is_grounded,
            "confidence": conf,
            "method": method,
        }
        # Include reasons only when explicitly ungrounded
        if is_grounded is False:
            if issues:
                qa_entry["issues"] = issues
            if ungrounded_sentences:
                qa_entry["ungrounded_sentences"] = ungrounded_sentences
            # Include LLM verdict if available (from hybrid/llm method)
            llm_verdict = _as_dict(grading.get("llm_verdict"))
            if llm_verdict:
                qa_entry["llm_verdict"] = llm_verdict

        qa_details.append(qa_entry)

    avg_conf = sum(confidences) / len(confidences) if confidences else None

    # Doc-level grade/conf come from grading_summary; if missing (e.g. older
    # runs or failed persist), fall back to mean per-QA confidence + letter.
    ov_conf = _parse_conf(grading_summary.get("overall_confidence"))
    if ov_conf is None and avg_conf is not None:
        ov_conf = float(avg_conf)
    og_raw = grading_summary.get("overall_grade")
    if og_raw is not None and str(og_raw).strip() != "":
        overall_grade = str(og_raw).strip()
    elif ov_conf is not None:
        overall_grade = _letter_grade(ov_conf)
    else:
        overall_grade = "N/A"

    documents.append({
        "file": f.name,
        "document_id": _str_field(doc_info.get("id"), "unknown"),
        "title": _str_field(doc_info.get("title"), "untitled"),
        "num_qa_pairs": len(qa_pairs),
        "grounded": grounded_count,
        "ungrounded": ungrounded_count,
        "avg_confidence": avg_conf,
        "overall_grade": overall_grade,
        "overall_confidence": ov_conf,
        "grading_method": _str_field(
            grading_summary.get("grading_method"), "N/A"
        ),
        "model": _first_non_null_str(
            q_gen.get("model"), a_gen.get("model")
        ),
        "judge_model": _str_field(
            grading_summary.get("judge_model"), "unknown"
        ),
        "provider": _first_non_null_str(
            q_gen.get("provider"), a_gen.get("provider")
        ),
        "timestamp": _first_non_null_str(
            q_gen.get("timestamp"), a_gen.get("timestamp"), default=""
        ),
        "qa_details": qa_details,
        "timings_seconds": {
            "question_generation": _safe_float(
                timings.get("question_generation"), 0.0
            ),
            "answer_generation": _safe_float(
                timings.get("answer_generation"), 0.0
            ),
            "grading": _safe_float(timings.get("grading"), 0.0),
        },
        "quality_counters": {
            "question_grounding_retries": _safe_int(
                quality_counters.get("question_grounding_retries"), 0
            ),
            "question_comprehensiveness_retries": _safe_int(
                quality_counters.get(
                    "question_comprehensiveness_retries"
                ),
                0,
            ),
            "answer_grounding_retries": _safe_int(
                quality_counters.get("answer_grounding_retries"), 0
            ),
            "coverage_rewrites": _safe_int(
                quality_counters.get("coverage_rewrites"), 0
            ),
        },
    })

# ---- compute run-level stats ----
total_docs = len(documents)
total_qa = sum(d["num_qa_pairs"] for d in documents)
total_grounded = sum(d["grounded"] for d in documents)
total_ungrounded = sum(d["ungrounded"] for d in documents)
all_confidences = [d["overall_confidence"] for d in documents if d["overall_confidence"] is not None]
avg_overall_conf = sum(all_confidences) / len(all_confidences) if all_confidences else None

total_qgen_secs = sum((d.get("timings_seconds") or {}).get("question_generation", 0.0) for d in documents)
total_agen_secs = sum((d.get("timings_seconds") or {}).get("answer_generation", 0.0) for d in documents)
total_grade_secs = sum((d.get("timings_seconds") or {}).get("grading", 0.0) for d in documents)
avg_qgen_secs = (total_qgen_secs / total_docs) if total_docs else 0.0
avg_agen_secs = (total_agen_secs / total_docs) if total_docs else 0.0
avg_grade_secs = (total_grade_secs / total_docs) if total_docs else 0.0

total_q_ground_retries = sum((d.get("quality_counters") or {}).get("question_grounding_retries", 0) for d in documents)
total_q_comp_retries = sum((d.get("quality_counters") or {}).get("question_comprehensiveness_retries", 0) for d in documents)
total_a_ground_retries = sum((d.get("quality_counters") or {}).get("answer_grounding_retries", 0) for d in documents)
total_coverage_rewrites = sum((d.get("quality_counters") or {}).get("coverage_rewrites", 0) for d in documents)

grade_counts = {}
for d in documents:
    g = d["overall_grade"]
    grade_counts[g] = grade_counts.get(g, 0) + 1

# ---- print summary ----
SEP = "=" * 80
THIN = "-" * 80

print(SEP)
print("  QAGRedo Run Summary")
print(SEP)
if documents:
    print(f"  Generator: {documents[0]['model']}")
    print(f"  Judge    : {documents[0].get('judge_model', 'unknown')}")
    print(f"  Provider : {documents[0]['provider']}")
if mode == "path":
    print(f"  Directory: {output_dir_arg}")
elif mode != "all":
    print(f"  Directory: {search_dir}")
else:
    print(f"  Directory: {base_output} (all dates)")
print(SEP)
print()

# Per-document table
print(f"  {'#':<4} {'Doc ID':<20} {'Title':<25} {'QAs':>4} {'Grounded':>9} {'Conf':>6} {'Grade':>6}")
print(f"  {THIN}")

for i, d in enumerate(documents, 1):
    title_s = str(d["title"] or "untitled")
    title = title_s[:24] if title_s else "untitled"
    doc_id_s = str(d["document_id"] or "unknown")
    doc_id = doc_id_s[:19] if doc_id_s else "unknown"
    oc = d["overall_confidence"]
    conf_str = (
        f"{float(oc):.2f}"
        if oc is not None and isinstance(oc, (int, float))
        else "N/A"
    )
    grounded_str = f"{d['grounded']}/{d['num_qa_pairs']}"
    gr_disp = str(d["overall_grade"] if d["overall_grade"] is not None else "N/A")
    print(
        f"  {i:<4} {doc_id:<20} {title:<25} {d['num_qa_pairs']:>4} "
        f"{grounded_str:>9} {conf_str:>6} {gr_disp:>6}"
    )

print()
print(THIN)
print("  OVERALL STATISTICS")
print(THIN)
print(f"  Total documents     : {total_docs}")
print(f"  Total Q&A pairs     : {total_qa}")
print(f"  Avg QAs per document: {total_qa / total_docs:.1f}" if total_docs else "  Avg QAs per document: N/A")
print(f"  Grounded answers    : {total_grounded}/{total_qa} ({100 * total_grounded / total_qa:.0f}%)" if total_qa else "  Grounded answers    : N/A")
print(f"  Ungrounded answers  : {total_ungrounded}/{total_qa} ({100 * total_ungrounded / total_qa:.0f}%)" if total_qa else "  Ungrounded answers  : N/A")
_aoc = avg_overall_conf
print(
    f"  Avg confidence      : {float(_aoc):.2f}"
    if _aoc is not None and isinstance(_aoc, (int, float))
    else "  Avg confidence      : N/A"
)
print(f"  Grade distribution  : {', '.join(f'{g}: {c}' for g, c in sorted(grade_counts.items()))}")
print(THIN)
print("  PIPELINE TIMINGS")
print(THIN)
print(f"  Q generation total  : {total_qgen_secs:.2f}s")
print(f"  A generation total  : {total_agen_secs:.2f}s")
print(f"  Grading total       : {total_grade_secs:.2f}s")
print(f"  Avg Q gen / doc     : {avg_qgen_secs:.2f}s")
print(f"  Avg A gen / doc     : {avg_agen_secs:.2f}s")
print(f"  Avg grading / doc   : {avg_grade_secs:.2f}s")
print(THIN)
print("  QUALITY COUNTERS")
print(THIN)
print(f"  Q grounding retries : {total_q_ground_retries}")
print(f"  Q comp retries      : {total_q_comp_retries}")
print(f"  A grounding retries : {total_a_ground_retries}")
print(f"  Coverage rewrites   : {total_coverage_rewrites}")
print(SEP)

# ---- Ungrounded highlights (text) ----
ungrounded_highlights = []
has_ungrounded = False
for d in documents:
    for qa in d.get("qa_details", []):
        if qa.get("is_grounded") is False:
            has_ungrounded = True
            highlight = {
                "document": d["document_id"],
                "title": d["title"],
                "question": qa["question"],
                "answer": qa["answer"],
                "confidence": qa.get("confidence"),
                "reasons": [],
            }
            # Collect reasons
            for issue in _as_list(qa.get("issues")):
                highlight["reasons"].append(issue)
            llm = _as_dict(qa.get("llm_verdict"))
            if llm and llm.get("reason"):
                highlight["reasons"].append(f"LLM verdict ({llm.get('verdict', '?')}): {llm['reason']}")
            ungrounded_highlights.append(highlight)

if has_ungrounded:
    print()
    print(SEP)
    print("  UNGROUNDED ANSWERS — WHY?")
    print(SEP)
    for idx, h in enumerate(ungrounded_highlights, 1):
        hc = h["confidence"]
        conf_str = (
            f"{float(hc):.2f}"
            if hc is not None and isinstance(hc, (int, float))
            else "N/A"
        )
        doc_h = str(h["document"] or "")
        title_h = str(h["title"] or "")
        qtxt = str(h["question"] or "")
        atxt = str(h["answer"] or "")
        print(f"\n  [{idx}] Document: {doc_h} — {title_h}")
        print(f"      Question : {qtxt[:120]}")
        print(f"      Answer   : {atxt[:200]}")
        print(f"      Confidence: {conf_str}")
        if h["reasons"]:
            print("      Reasons:")
            for r in h["reasons"]:
                rs = r if isinstance(r, str) else str(r)
                print(f"        - {rs[:200]}")
        else:
            print("      Reasons: (none recorded)")
    print()
    print(SEP)

# ---- optionally save JSON ----
if save_json:
    summary = {
        "generated_at": datetime.now().isoformat(),
        "total_documents": total_docs,
        "total_qa_pairs": total_qa,
        "grounded_answers": total_grounded,
        "ungrounded_answers": total_ungrounded,
        "avg_confidence": avg_overall_conf,
        "grade_distribution": grade_counts,
        "generator_model": documents[0]["model"] if documents else None,
        "judge_model": documents[0].get("judge_model", "unknown") if documents else None,
        "provider": documents[0]["provider"] if documents else None,
        "run_metrics": {
            "timings_seconds": {
                "question_generation_total": round(total_qgen_secs, 3),
                "answer_generation_total": round(total_agen_secs, 3),
                "grading_total": round(total_grade_secs, 3),
                "question_generation_avg_per_doc": round(avg_qgen_secs, 3),
                "answer_generation_avg_per_doc": round(avg_agen_secs, 3),
                "grading_avg_per_doc": round(avg_grade_secs, 3),
            },
            "quality_counters": {
                "question_grounding_retries": total_q_ground_retries,
                "question_comprehensiveness_retries": total_q_comp_retries,
                "answer_grounding_retries": total_a_ground_retries,
                "coverage_rewrites": total_coverage_rewrites,
            },
        },
        "ungrounded_highlights": ungrounded_highlights,
        "documents": documents,
    }
    # Try to save next to the analysis files; fall back to current directory
    # if the output directory is not writable (e.g. root-owned from Docker).
    if mode == "path":
        out_path = Path(output_dir_arg) / "run_summary.json"
    elif mode != "all":
        out_path = search_dir / "run_summary.json"
    else:
        out_path = base_output / "run_summary.json"

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)
        print(f"\n  Summary saved to: {out_path}")
    except PermissionError:
        # Fall back to current working directory
        fallback_path = Path(host_dir) / "run_summary.json"
        try:
            with open(fallback_path, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)
            print(f"\n  (Permission denied on {out_path.parent}/)")
            print(f"  Summary saved to: {fallback_path}")
        except PermissionError:
            # Last resort: print JSON to stdout
            print(f"\n  (Permission denied — printing JSON to stdout instead)")
            print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

PYTHON_SCRIPT
