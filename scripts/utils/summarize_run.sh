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
      echo "  bash scripts/utils/summarize_run.sh output/vllm/meta-llama-meta-llama-3.1-8b-instruct/2026-02-11/"
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

    doc_info = data.get("document", {})
    qa_pairs = data.get("qa_pairs", [])
    grading_summary = data.get("grading_summary", {})
    q_gen = data.get("question_generation", {})
    a_gen = data.get("answer_generation", {})

    # Per-QA stats
    grounded_count = 0
    ungrounded_count = 0
    confidences = []
    qa_details = []
    for pair in qa_pairs:
        grading = pair.get("grading", {})
        question = pair.get("question", "")
        answer = pair.get("answer", "")
        is_grounded = grading.get("is_grounded")
        conf = grading.get("confidence")
        issues = grading.get("issues", [])
        method = grading.get("method", "")
        ungrounded_sentences = grading.get("ungrounded_sentences", [])

        if is_grounded is True:
            grounded_count += 1
        elif is_grounded is False:
            ungrounded_count += 1
        if conf is not None:
            confidences.append(conf)

        # Build per-QA detail entry
        qa_entry = {
            "question": question,
            "answer": answer,
            "is_grounded": is_grounded,
            "confidence": conf,
            "method": method,
        }
        # Include reasons for ungrounded answers
        if not is_grounded:
            if issues:
                qa_entry["issues"] = issues
            if ungrounded_sentences:
                qa_entry["ungrounded_sentences"] = ungrounded_sentences
            # Include LLM verdict if available (from hybrid/llm method)
            llm_verdict = grading.get("llm_verdict", {})
            if llm_verdict:
                qa_entry["llm_verdict"] = llm_verdict

        qa_details.append(qa_entry)

    avg_conf = sum(confidences) / len(confidences) if confidences else None

    documents.append({
        "file": f.name,
        "document_id": doc_info.get("id", "unknown"),
        "title": doc_info.get("title", "untitled"),
        "num_qa_pairs": len(qa_pairs),
        "grounded": grounded_count,
        "ungrounded": ungrounded_count,
        "avg_confidence": avg_conf,
        "overall_grade": grading_summary.get("overall_grade", "N/A"),
        "overall_confidence": grading_summary.get("overall_confidence"),
        "grading_method": grading_summary.get("grading_method", "N/A"),
        "model": q_gen.get("model", a_gen.get("model", "unknown")),
        "judge_model": grading_summary.get("judge_model", "unknown"),
        "provider": q_gen.get("provider", a_gen.get("provider", "unknown")),
        "timestamp": q_gen.get("timestamp", a_gen.get("timestamp", "")),
        "qa_details": qa_details,
    })

# ---- compute run-level stats ----
total_docs = len(documents)
total_qa = sum(d["num_qa_pairs"] for d in documents)
total_grounded = sum(d["grounded"] for d in documents)
total_ungrounded = sum(d["ungrounded"] for d in documents)
all_confidences = [d["overall_confidence"] for d in documents if d["overall_confidence"] is not None]
avg_overall_conf = sum(all_confidences) / len(all_confidences) if all_confidences else None

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
    title = d["title"][:24] if d["title"] else "untitled"
    doc_id = d["document_id"][:19] if d["document_id"] else "unknown"
    conf_str = f"{d['overall_confidence']:.2f}" if d["overall_confidence"] is not None else "N/A"
    grounded_str = f"{d['grounded']}/{d['num_qa_pairs']}"
    print(f"  {i:<4} {doc_id:<20} {title:<25} {d['num_qa_pairs']:>4} {grounded_str:>9} {conf_str:>6} {d['overall_grade']:>6}")

print()
print(THIN)
print("  OVERALL STATISTICS")
print(THIN)
print(f"  Total documents     : {total_docs}")
print(f"  Total Q&A pairs     : {total_qa}")
print(f"  Avg QAs per document: {total_qa / total_docs:.1f}" if total_docs else "  Avg QAs per document: N/A")
print(f"  Grounded answers    : {total_grounded}/{total_qa} ({100 * total_grounded / total_qa:.0f}%)" if total_qa else "  Grounded answers    : N/A")
print(f"  Ungrounded answers  : {total_ungrounded}/{total_qa} ({100 * total_ungrounded / total_qa:.0f}%)" if total_qa else "  Ungrounded answers  : N/A")
print(f"  Avg confidence      : {avg_overall_conf:.2f}" if avg_overall_conf is not None else "  Avg confidence      : N/A")
print(f"  Grade distribution  : {', '.join(f'{g}: {c}' for g, c in sorted(grade_counts.items()))}")
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
            for issue in qa.get("issues", []):
                highlight["reasons"].append(issue)
            llm = qa.get("llm_verdict", {})
            if llm and llm.get("reason"):
                highlight["reasons"].append(f"LLM verdict ({llm.get('verdict', '?')}): {llm['reason']}")
            ungrounded_highlights.append(highlight)

if has_ungrounded:
    print()
    print(SEP)
    print("  UNGROUNDED ANSWERS — WHY?")
    print(SEP)
    for idx, h in enumerate(ungrounded_highlights, 1):
        conf_str = f"{h['confidence']:.2f}" if h["confidence"] is not None else "N/A"
        print(f"\n  [{idx}] Document: {h['document']} — {h['title']}")
        print(f"      Question : {h['question'][:120]}")
        print(f"      Answer   : {h['answer'][:200]}")
        print(f"      Confidence: {conf_str}")
        if h["reasons"]:
            print("      Reasons:")
            for r in h["reasons"]:
                print(f"        - {r[:200]}")
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
