#!/usr/bin/env python3
"""Export per-document good/bad QA pair splits from analysis outputs."""

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
    _pair_passes_grounding_gate,
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


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _pair_row(pair: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "question": _as_text(pair.get("question")),
        "answer": _as_text(pair.get("answer")),
    }


def _dpo_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_pairs = data.get("dpo_pairs")
    if not isinstance(raw_pairs, list):
        return []
    rows: List[Dict[str, Any]] = []
    for pair in raw_pairs:
        if not isinstance(pair, dict):
            continue
        question = _as_text(pair.get("question"))
        chosen = _as_text(pair.get("chosen"))
        rejected = _as_text(pair.get("rejected"))
        if not question or not chosen or not rejected or chosen == rejected:
            continue
        rows.append(
            {
                "question": question,
                "chosen": chosen,
                "rejected": rejected,
            }
        )
    return rows


def _target_output_path(src: Path, mode: str) -> Path:
    suffix = "good" if mode == "good" else "bad"
    out_name = f"{src.stem}_minimal_{suffix}_pairs.json"
    return src.with_name(out_name)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Export per-document good/bad split files from existing "
            "*_analysis.json outputs."
        )
    )
    p.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Files or directories (directories are scanned recursively).",
    )
    p.add_argument(
        "--mode",
        choices=("good", "bad"),
        required=True,
        help=(
            "Export grounded gate-pass pairs (good) "
            "or gate-fail pairs (bad)."
        ),
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        default=0.7,
        help="Grounding gate confidence threshold (default: 0.7).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print targets only; do not write files.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing per-document output files.",
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
        text = src.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            print(f"[skip] not a JSON object: {src}", flush=True)
            continue
        document = data.get("document")
        qa_pairs = data.get("qa_pairs")
        if not isinstance(document, dict) or not isinstance(qa_pairs, list):
            print(
                f"[skip] {src}: missing/invalid document or qa_pairs",
                flush=True,
            )
            continue
        rows: List[Dict[str, Any]] = []
        raw_id = document.get("id", document.get("title", "unknown"))
        doc_id = _as_text(raw_id) or "unknown"
        minimal_doc = _minimal_document_for_output(document, doc_id)
        for pair in qa_pairs:
            if not isinstance(pair, dict):
                continue
            is_good = _pair_passes_grounding_gate(pair, args.min_confidence)
            if args.mode == "good" and not is_good:
                continue
            if args.mode == "bad" and is_good:
                continue
            rows.append(_pair_row(pair))

        out = _target_output_path(src, args.mode)
        if out.exists() and not args.force:
            print(f"[skip] exists (use --force): {out}", flush=True)
            continue
        if args.dry_run:
            print(
                f"[dry-run] would write {len(rows)} rows to {out}",
                flush=True,
            )
            continue
        payload = {"document": minimal_doc, "qa_pairs": rows}
        if args.mode == "good":
            payload["dpo_pairs"] = _dpo_rows(data)
        out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[ok] {out} ({len(rows)} rows)", flush=True)


if __name__ == "__main__":
    main()
