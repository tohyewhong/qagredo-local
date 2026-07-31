#!/usr/bin/env python3
"""Verify image href/src in docs/*.md and docs/*.html exist."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MISSING: list[str] = []

MD_IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HTML_SRC = re.compile(r'src="([^"]+)"')


def check(base: Path, ref: str, src: Path) -> None:
    ref = ref.split("?")[0].split("#")[0].strip()
    if not ref or ref.startswith(("http://", "https://", "data:")):
        return
    if ref.startswith("/"):
        target = ROOT / ref.lstrip("/")
    else:
        target = (base.parent / ref).resolve()
    if not target.is_file():
        MISSING.append(f"{src.relative_to(ROOT)} -> {ref}")


for md in DOCS.rglob("*.md"):
    if "algorithm-baselines" in md.parts:
        continue
    text = md.read_text(encoding="utf-8", errors="replace")
    for m in MD_IMG.finditer(text):
        check(md, m.group(1), md)

for html in DOCS.rglob("*.html"):
    if "algorithm-baselines" in html.parts:
        continue
    text = html.read_text(encoding="utf-8", errors="replace")
    for m in HTML_SRC.finditer(text):
        check(html, m.group(1), html)

if MISSING:
    print("Missing doc image references:")
    for line in MISSING:
        print(f"  {line}")
    sys.exit(1)
print("OK: all checked doc image references exist.")
