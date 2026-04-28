#!/usr/bin/env python3
"""Write minimal analysis JSON next to full pipeline outputs (no LLM rerun).

Reads files like ``.../foo_0001_analysis.json`` and writes
``.../foo_0001_analysis_minimal.json`` with only ``document.content`` and
``qa_pairs`` rows ``{question, answer}``, matching ``run.minimal_qa_output``.

Example::

  python3 scripts/utils/export_analysis_minimal.py \\
    /path/to/run_folder \\
    /path/to/single_doc_analysis.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_qa_pipeline import (  # noqa: E402
    _minimal_document_for_output,
    _minimal_qa_pairs_for_output,
)


def _is_source_analysis_path(path: Path) -> bool:
    name = path.name
    if name.endswith("_analysis_minimal.json"):
        return False
    return name.endswith("_analysis.json")


def _iter_analysis_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        if _is_source_analysis_path(path):
            yield path
        return
    if path.is_dir():
        for child in sorted(path.rglob("*_analysis.json")):
            if _is_source_analysis_path(child):
                yield child
        return
    raise SystemExit(f"Not a file or directory: {path}")


def _minimal_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    document = data.get("document")
    if not isinstance(document, dict):
        raise ValueError("missing or invalid 'document' object")
    qa_pairs = data.get("qa_pairs")
    if not isinstance(qa_pairs, list):
        raise ValueError("missing or invalid 'qa_pairs' list")
    raw_id = document.get("id", document.get("title", "unknown"))
    doc_id = str(raw_id) if raw_id is not None else "unknown"
    return {
        "document": _minimal_document_for_output(document, doc_id),
        "qa_pairs": _minimal_qa_pairs_for_output(qa_pairs),
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Export minimal analysis JSON (*_analysis_minimal.json) from "
            "existing *_analysis.json outputs."
        )
    )
    p.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Files or directories (directories are scanned recursively).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print targets only; do not write files.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing *_analysis_minimal.json files.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    seen: List[Path] = []
    for raw in args.paths:
        path = raw.expanduser().resolve()
        seen.extend(_iter_analysis_files(path))

    if not seen:
        print("No matching *_analysis.json inputs.", flush=True)
        return

    for src in seen:
        out = src.with_name(f"{src.stem}_minimal.json")
        if out.is_file() and not args.force:
            print(f"[skip] exists (use --force): {out}", flush=True)
            continue
        text = src.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            print(f"[skip] not a JSON object: {src}", flush=True)
            continue
        try:
            minimal = _minimal_payload(data)
        except ValueError as exc:
            print(f"[skip] {src}: {exc}", flush=True)
            continue
        payload = json.dumps(minimal, indent=2, ensure_ascii=False) + "\n"
        if args.dry_run:
            print(f"[dry-run] would write {out}", flush=True)
            continue
        out.write_text(payload, encoding="utf-8")
        print(f"[ok] {out}", flush=True)


if __name__ == "__main__":
    main()
