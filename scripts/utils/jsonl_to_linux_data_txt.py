#!/usr/bin/env python3
"""
Split a QAG-style JSONL file into one .txt per record.

Output filenames: linux_Data_<12-char-hex>.txt
  (hex = first 12 chars of MD5 over record id + content, stable per row)

Example:
  python3 scripts/utils/jsonl_to_linux_data_txt.py \\
    /home/tyewhong/khangzhie-data/pathfinder/txt/datatrain-data.jsonl \\
    /home/tyewhong/khangzhie-data/pathfinder/txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _stem_for_record(record: dict, line_no: int) -> str:
    rid = str(record.get("id") or f"line_{line_no}")
    body = str(record.get("content") or record.get("text") or "").strip()
    digest = hashlib.md5(f"{rid}\n{body}".encode("utf-8")).hexdigest()[:12]
    return f"linux_Data_{digest}"


def _body_for_record(record: dict) -> str:
    return str(record.get("content") or record.get("text") or "").strip()


def convert(jsonl_path: Path, out_dir: Path, *, dry_run: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    seen_stems: dict[str, int] = {}
    written = 0
    skipped_empty = 0
    collisions = 0

    with jsonl_path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            body = _body_for_record(record)
            if not body:
                skipped_empty += 1
                continue
            stem = _stem_for_record(record, line_no)
            if stem in seen_stems:
                collisions += 1
                stem = f"{stem}_{seen_stems[stem]}"
                seen_stems[stem] = 1
            else:
                seen_stems[stem] = 1
            out_path = out_dir / f"{stem}.txt"
            if not dry_run:
                out_path.write_text(body + "\n", encoding="utf-8")
            written += 1

    return {
        "written": written,
        "skipped_empty": skipped_empty,
        "collisions": collisions,
        "out_dir": str(out_dir),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("jsonl", type=Path, help="Input .jsonl file")
    p.add_argument(
        "out_dir",
        type=Path,
        nargs="?",
        default=None,
        help="Output directory (default: same folder as JSONL)",
    )
    p.add_argument("--dry-run", action="store_true", help="Count only; do not write")
    args = p.parse_args()
    jsonl_path = args.jsonl.expanduser().resolve()
    if not jsonl_path.is_file():
        print(f"Not found: {jsonl_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = (
        args.out_dir.expanduser().resolve()
        if args.out_dir
        else jsonl_path.parent
    )
    stats = convert(jsonl_path, out_dir, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
