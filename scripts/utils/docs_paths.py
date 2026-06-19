"""Repo-relative paths for documentation and PPTX builders."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
ASSET_DIR = DOCS_DIR / "assets"
