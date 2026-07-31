#!/usr/bin/env python3
"""Build a document holdout list from lora_sft_eval.jsonl in a QAG run."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_qa_pipeline import _minimal_document_for_output  # noqa: E402

DOC_USER_RE = re.compile(r"Document:\n(.*)\n\nQuestion:", re.DOTALL)


def _doc_from_user_message(content: str) -> str:
    match = DOC_USER_RE.match(content or "")
    return match.group(1).strip() if match else ""


def _load_eval_rows(path: Path) -> List[dict]:
    rows: List[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _build_content_to_id(run_dir: Path) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for path in sorted(run_dir.glob("*_analysis.json")):
        if "_minimal_" in path.name:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        document = data.get("document")
        if not isinstance(document, dict):
            continue
        doc_id = str(document.get("id") or document.get("title") or "").strip()
        if not doc_id:
            continue
        minimal = _minimal_document_for_output(document, doc_id)
        content = str(minimal.get("content") or "").strip()
        if content:
            mapping[content] = doc_id
    return mapping


def prepare_holdout(run_dir: Path, out_dir: Path | None = None) -> Path:
    run_dir = run_dir.expanduser().resolve()
    eval_path = run_dir / "lora_sft_eval.jsonl"
    if not eval_path.is_file():
        raise FileNotFoundError(
            f"Missing {eval_path}. Run: bash run.sh --minimise {run_dir}"
        )

    holdout_dir = (
        (out_dir or (run_dir / "eval_holdout")).expanduser().resolve()
    )
    holdout_dir.mkdir(parents=True, exist_ok=True)

    content_to_id = _build_content_to_id(run_dir)
    eval_rows = _load_eval_rows(eval_path)

    doc_ids: Set[str] = set()
    unmatched = 0
    for row in eval_rows:
        messages = row.get("messages") or []
        user_content = ""
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                user_content = str(msg.get("content") or "")
                break
        doc_text = _doc_from_user_message(user_content)
        doc_id = content_to_id.get(doc_text)
        if doc_id:
            doc_ids.add(doc_id)
        else:
            unmatched += 1

    ids_path = holdout_dir / "doc_ids.txt"
    ids_path.write_text(
        "\n".join(sorted(doc_ids)) + ("\n" if doc_ids else ""),
        encoding="utf-8",
    )

    manifest = {
        "source_run_dir": str(run_dir),
        "eval_jsonl": str(eval_path),
        "eval_rows": len(eval_rows),
        "holdout_documents": len(doc_ids),
        "unmatched_eval_rows": unmatched,
        "doc_ids_file": str(ids_path),
        "selection_method": (
            "Documents mapped from lora_sft_eval.jsonl (10% QA holdout, "
            "seed=42 export split). Used for adapter A/B evaluation."
        ),
    }
    manifest_path = holdout_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"[ok] holdout docs={len(doc_ids)} eval_rows={len(eval_rows)}")
    print(f"[ok] doc ids -> {ids_path}")
    print(f"[ok] manifest -> {manifest_path}")
    if unmatched:
        print(
            f"[warn] {unmatched} eval rows could not be mapped to doc ids",
            file=sys.stderr,
        )
    return holdout_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare eval holdout doc id list from lora_sft_eval.jsonl."
        ),
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="QAG run directory containing lora_sft_eval.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Holdout output directory (default: RUN_DIR/eval_holdout)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    prepare_holdout(args.run_dir, args.output_dir)


if __name__ == "__main__":
    main()
