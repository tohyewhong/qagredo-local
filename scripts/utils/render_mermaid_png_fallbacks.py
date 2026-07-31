#!/usr/bin/env python3
"""Render ```mermaid blocks to PNG and insert embeds for offline preview.

Skips blocks that already have a ![...](*.png) within the next few lines.
Does not modify files under docs/algorithm-baselines/ (frozen snapshots).

Usage:
  python3 scripts/utils/render_mermaid_png_fallbacks.py
  python3 scripts/utils/render_mermaid_png_fallbacks.py --check
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MERMAID_FENCE = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)
PNG_AFTER = re.compile(r"!\[[^\]]*\]\([^)]+\.png\)")
SKIP_PREFIX = "docs/algorithm-baselines/"

MD_GLOBS = (
    "docs/*.md",
    "docs/architecture/*.md",
    "docs/algorithm-baselines/README.md",
    "config/README.md",
    "README.md",
)


def _md_files() -> list[Path]:
    paths: list[Path] = []
    for pattern in MD_GLOBS:
        paths.extend(REPO_ROOT.glob(pattern))
    out: list[Path] = []
    for path in sorted(set(paths)):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(SKIP_PREFIX):
            continue
        out.append(path)
    return out


def _has_png_fallback(content: str, end: int) -> bool:
    snippet = content[end : end + 400]
    lines = snippet.split("\n")[:6]
    return any(PNG_AFTER.search(line) for line in lines)


def _mmdc_bin() -> str:
    mmdc = shutil.which("mmdc")
    if mmdc:
        return mmdc
    npx = shutil.which("npx")
    if npx:
        return f"{npx} -y @mermaid-js/mermaid-cli"
    return ""


def _render_png(mermaid_src: str, png_path: Path, mmdc: str) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".mmd",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(mermaid_src)
        tmp_path = Path(tmp.name)
    try:
        cmd = f"{mmdc} -i {tmp_path} -o {png_path} -b white"
        subprocess.run(
            cmd,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    if not png_path.is_file():
        raise RuntimeError(f"mmdc did not create {png_path}")


def _alt_text(md_path: Path, index: int) -> str:
    stem = md_path.stem.replace("_", " ")
    return f"{stem} flowchart {index}"


def _png_name(md_path: Path, index: int) -> str:
    return f"{md_path.stem}_flow_{index:02d}.png"


def process_file(md_path: Path, mmdc: str, dry_run: bool) -> tuple[int, int]:
    content = md_path.read_text(encoding="utf-8")
    inserts: list[tuple[int, str]] = []
    rendered = 0
    skipped = 0
    index = 0

    for match in MERMAID_FENCE.finditer(content):
        index += 1
        if _has_png_fallback(content, match.end()):
            skipped += 1
            continue

        png_name = _png_name(md_path, index)
        png_path = md_path.parent / png_name
        png_rel = png_name
        if md_path.parent != REPO_ROOT / "docs" and md_path.name == "README.md":
            if md_path == REPO_ROOT / "README.md":
                png_path = REPO_ROOT / "docs" / png_name
                png_rel = f"docs/{png_name}"

        mermaid_src = match.group(1).strip() + "\n"
        if not dry_run:
            _render_png(mermaid_src, png_path, mmdc)
        alt = _alt_text(md_path, index)
        embed = f"\n\n![{alt}]({png_rel})\n"
        inserts.append((match.end(), embed))
        rendered += 1

    if inserts and not dry_run:
        new_content = content
        for pos, embed in reversed(inserts):
            new_content = new_content[:pos] + embed + new_content[pos:]
        md_path.write_text(new_content, encoding="utf-8")

    return rendered, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render mermaid blocks to PNG fallbacks in Markdown.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report missing PNG fallbacks without writing.",
    )
    args = parser.parse_args()
    mmdc = _mmdc_bin()
    if not mmdc and not args.check:
        print("ERROR: mmdc or npx not found.", file=sys.stderr)
        return 1

    total_render = 0
    total_skip = 0
    missing = 0

    for md_path in _md_files():
        rel = md_path.relative_to(REPO_ROOT)
        if args.check:
            content = md_path.read_text(encoding="utf-8")
            idx = 0
            file_missing = 0
            for match in MERMAID_FENCE.finditer(content):
                idx += 1
                if not _has_png_fallback(content, match.end()):
                    file_missing += 1
            if file_missing:
                print(f"{rel}: {file_missing} mermaid block(s) without PNG")
                missing += file_missing
            continue

        rendered, skipped = process_file(md_path, mmdc, dry_run=False)
        if rendered:
            print(f"{rel}: rendered {rendered}, skipped {skipped}")
        total_render += rendered
        total_skip += skipped

    if args.check:
        if missing:
            print(f"Total missing PNG fallbacks: {missing}")
            return 1
        print("All mermaid blocks have PNG fallbacks.")
        return 0

    print(f"Done. Rendered {total_render}, already had PNG: {total_skip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
