#!/usr/bin/env python3
"""Optional smoke test: keyword-based hallucination checks on N numbered txt files.

Legacy filename kept for scripts; ``method='semantic'`` in config now maps to
keyword overlap (embedding grading was removed).

Not required for production. For real Q&A runs, use ``run_qa_pipeline.py``.

Usage (from repo root):
  python3 scripts/utils/smoke_semantic_five_docs.py
  python3 scripts/utils/smoke_semantic_five_docs.py --count 10
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test keyword (legacy 'semantic' alias) grading on data/txt/."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        metavar="N",
        help="How many consecutive files to test (default: 5).",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=1,
        metavar="I",
        help="First file index, e.g. 1 -> 00001.txt (default: 1).",
    )
    args = parser.parse_args()
    if args.count < 1:
        print("ERROR: --count must be >= 1", file=sys.stderr)
        return 1
    if args.start < 1:
        print("ERROR: --start must be >= 1", file=sys.stderr)
        return 1

    from utils.hallucination_checker import check_hallucination

    txt_dir = PROJECT_ROOT / "data" / "txt"
    end = args.start + args.count
    files = [txt_dir / f"{i:05d}.txt" for i in range(args.start, end)]
    missing = [f for f in files if not f.is_file()]
    if missing:
        print("ERROR: Missing expected file(s):", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        return 1

    print(f"Keyword grading smoke (config method 'semantic' → keyword) — {args.count} file(s)")
    print("-" * 72)

    all_ok = True
    tested = 0
    for path in files:
        document = path.read_text(encoding="utf-8", errors="replace").strip()
        if not document:
            print(f"{path.name}: SKIP (empty file)")
            continue
        tested += 1
        answer = document[: min(400, len(document))]
        result = check_hallucination(
            answer=answer,
            document_content=document,
            question="Summarize the opening of this document.",
            method="semantic",
        )
        method = result.get("method", "")
        conf = result.get("confidence")
        ok = method == "keyword"
        all_ok = all_ok and ok
        status = "OK" if ok else "FAIL"
        print(f"{path.name}  method={method!r}  confidence={conf}  [{status}]")

    print("-" * 72)
    if tested == 0:
        print("FAIL: No non-empty documents tested.", file=sys.stderr)
        return 1
    if all_ok:
        print(f"PASS: All {tested} used method 'keyword' (legacy semantic alias).")
        return 0
    print("FAIL: Expected method 'keyword' for legacy semantic path.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
