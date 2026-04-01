#!/usr/bin/env python3
"""
Split a JSONL file into one text file per record.

Each output `.txt` file contains:
- record["content"] if present and non-empty
- otherwise record["text"] if present and non-empty
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, Tuple


def _iter_jsonl_records(input_path: Path) -> Iterable[Tuple[int, dict]]:
    with input_path.open("r", encoding="utf-8") as handle:
        for line_num, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at line {line_num} in {input_path}: {exc.msg}"
                ) from exc

            if not isinstance(parsed, dict):
                raise ValueError(
                    f"Expected JSON object at line {line_num} in {input_path}, "
                    f"got {type(parsed).__name__}."
                )
            yield line_num, parsed


def _extract_text(record: dict) -> str:
    for key in ("content", "text"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def split_jsonl_to_txt(input_path: Path, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for _, record in _iter_jsonl_records(input_path):
        text = _extract_text(record)
        if not text:
            raise ValueError(
                "Encountered record missing non-empty `content` and `text` fields."
            )

        written += 1
        output_path = output_dir / f"{written:05d}.txt"
        output_path.write_text(text, encoding="utf-8")

    if written == 0:
        raise ValueError(f"No JSON records found in {input_path}.")
    return written


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split JSONL file into separate txt files."
    )
    parser.add_argument(
        "--input",
        default="/home/tyewhong/qagredo/data/train-data.jsonl",
        help="Path to source JSONL file.",
    )
    parser.add_argument(
        "--output-dir",
        default="/home/tyewhong/qagredo/data/train-data_txt",
        help="Directory to store generated .txt files.",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not input_path.is_file():
        raise ValueError(f"Input path is not a file: {input_path}")

    count = split_jsonl_to_txt(input_path=input_path, output_dir=output_dir)
    print(f"Wrote {count} text files to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
