#!/usr/bin/env python3
"""Rename each *.txt in a folder to <prefix>_<random>.txt (keeps extension)."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rename .txt files in DIR to PREFIX_<12 hex chars>.txt "
            "(safe one-pass; unique names)."
        )
    )
    parser.add_argument(
        "--dir",
        type=Path,
        required=True,
        help=(
            "Directory of .txt files (e.g. WSL /mnt/c/.../Downloads/txt)."
        ),
    )
    parser.add_argument(
        "--prefix",
        default="window",
        help="Filename prefix (default: window).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned renames only.",
    )
    args = parser.parse_args()
    root = args.dir.expanduser().resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    files = sorted(p for p in root.glob("*.txt") if p.is_file())
    if not files:
        print(f"No .txt files in {root}")
        return 0

    used: set[str] = set()
    for p in files:
        while True:
            name = f"{args.prefix}_{uuid.uuid4().hex[:12]}.txt"
            if name not in used and not (root / name).exists():
                used.add(name)
                break
        target = root / name
        if args.dry_run:
            print(f"{p.name} -> {name}")
        else:
            p.rename(target)
            print(f"{p.name} -> {name}")
    action = "Would rename" if args.dry_run else "Renamed"
    print(f"{action} {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
