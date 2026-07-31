#!/usr/bin/env python3
"""
Rename .txt files to short logical slugs (max 3 words by default).

Uses titles from datatrain-data.jsonl and/or rename_manifest.csv; otherwise
the first line of each .txt. Writes rename_manifest.csv (old_name, new_name, title).

Examples:
  # First pass (linux_Data_*.txt still present):
  python3 scripts/utils/rename_pathfinder_txt.py DIR --jsonl DIR/datatrain-data.jsonl

  # Re-slug already-renamed files (use manifest for titles):
  python3 scripts/utils/rename_pathfinder_txt.py DIR --all-txt --from-manifest \\
    --max-words 2 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

SKIP_NAMES = frozenset({"datatrain-data.jsonl", "rename_manifest.csv"})

STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "as",
        "by",
        "with",
        "from",
        "that",
        "this",
        "it",
        "its",
    }
)


def _record_hash(record: dict, line_no: int) -> str:
    rid = str(record.get("id") or f"line_{line_no}")
    body = str(record.get("content") or record.get("text") or "").strip()
    return hashlib.md5(f"{rid}\n{body}".encode("utf-8")).hexdigest()[:12]


def _load_hash_to_title(jsonl_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with jsonl_path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            h = _record_hash(record, line_no)
            title = str(record.get("title") or "").strip()
            out[h] = title
    return out


def _slug_from_title(title: str, max_words: int = 3) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", title.lower())
    picked = [w for w in words if len(w) > 1 and w not in STOP_WORDS]
    if not picked:
        picked = [w for w in words if len(w) > 1]
    if not picked:
        picked = words
    slug = "_".join(picked[:max_words])
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "document"


def _slug_from_content_preview(text: str, max_words: int = 3) -> str:
    first_line = text.strip().split("\n", 1)[0]
    return _slug_from_title(first_line, max_words=max_words)


def _load_manifest_titles(manifest_path: Path) -> dict[str, str]:
    """Map current filename -> title from a prior rename_manifest.csv."""
    out: dict[str, str] = {}
    with manifest_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            name = str(row.get("new_name") or "").strip()
            title = str(row.get("title") or "").strip()
            if name and title:
                out[name] = title
    return out


def _list_txt_files(txt_dir: Path, *, all_txt: bool) -> list[Path]:
    if all_txt:
        return sorted(
            p
            for p in txt_dir.glob("*.txt")
            if p.is_file() and p.name not in SKIP_NAMES
        )
    return sorted(
        p for p in txt_dir.glob("linux_Data_*.txt") if p.is_file()
    )


def _unique_name(base: str, used: dict[str, int]) -> str:
    if base not in used:
        used[base] = 1
        return base
    used[base] += 1
    return f"{base}_{used[base]}"


def rename_txt_dir(
    txt_dir: Path,
    *,
    jsonl_path: Path | None,
    manifest_titles: dict[str, str] | None,
    all_txt: bool,
    max_words: int,
    dry_run: bool,
    manifest_path: Path | None,
) -> dict:
    hash_to_title = _load_hash_to_title(jsonl_path) if jsonl_path else {}
    manifest_titles = manifest_titles or {}

    txt_files = _list_txt_files(txt_dir, all_txt=all_txt)
    used_slugs: dict[str, int] = {}
    plan: list[tuple[Path, Path, str]] = []

    for path in txt_files:
        title = manifest_titles.get(path.name, "")
        if not title and path.name.startswith("linux_Data_"):
            h = path.stem.replace("linux_Data_", "", 1)
            title = hash_to_title.get(h, "")
        if not title:
            title = path.read_text(encoding="utf-8", errors="replace")[:500]
        slug_base = _slug_from_title(title, max_words=max_words)
        slug = _unique_name(slug_base, used_slugs)
        new_path = path.with_name(f"{slug}.txt")
        plan.append((path, new_path, title))

    if manifest_path and not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("w", newline="", encoding="utf-8") as mf:
            w = csv.writer(mf)
            w.writerow(["old_name", "new_name", "title"])
            for old, new, title in plan:
                w.writerow([old.name, new.name, title[:500]])

    renamed = 0
    todo = [(old, new) for old, new, _ in plan if old != new]
    if dry_run:
        renamed = len(todo)
    elif todo:
        temps: list[tuple[Path, Path, Path]] = []
        for i, (old, new) in enumerate(todo):
            tmp = old.with_name(f".qag_rename_{i}.tmp")
            old.rename(tmp)
            temps.append((tmp, new, old))
        for tmp, new, _old in temps:
            if new.exists():
                new.unlink()
            tmp.rename(new)
        renamed = len(todo)

    return {
        "txt_files": len(txt_files),
        "renamed": renamed,
        "dry_run": dry_run,
        "manifest": str(manifest_path) if manifest_path else None,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("txt_dir", type=Path, help="Directory containing .txt files")
    p.add_argument(
        "--all-txt",
        action="store_true",
        help="Rename every *.txt (not only linux_Data_*.txt)",
    )
    p.add_argument(
        "--from-manifest",
        action="store_true",
        help="Load titles from <txt_dir>/rename_manifest.csv (re-slug pass)",
    )
    p.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="datatrain-data.jsonl for titles (recommended)",
    )
    p.add_argument(
        "--max-words",
        type=int,
        default=3,
        help="Max words in each filename (default 3 = less than 4)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Write CSV mapping (default: <txt_dir>/rename_manifest.csv)",
    )
    args = p.parse_args()
    txt_dir = args.txt_dir.expanduser().resolve()
    jsonl = args.jsonl.expanduser().resolve() if args.jsonl else None
    if jsonl and not jsonl.is_file():
        print(f"JSONL not found: {jsonl}", file=sys.stderr)
        sys.exit(1)
    manifest = (
        args.manifest.expanduser().resolve()
        if args.manifest
        else txt_dir / "rename_manifest.csv"
    )
    manifest_titles = None
    if args.from_manifest:
        if not manifest.is_file():
            print(f"Manifest not found: {manifest}", file=sys.stderr)
            sys.exit(1)
        manifest_titles = _load_manifest_titles(manifest)
    stats = rename_txt_dir(
        txt_dir,
        jsonl_path=jsonl,
        manifest_titles=manifest_titles,
        all_txt=args.all_txt,
        max_words=max(1, min(args.max_words, 3)),
        dry_run=args.dry_run,
        manifest_path=None if args.dry_run else manifest,
    )
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
