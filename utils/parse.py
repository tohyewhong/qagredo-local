"""
Minimal article/document JSON parser.

Provides ``load_article_json`` which the conversion script
(scripts/conversion/convert_to_qagredo_jsonl.py) imports.

The function accepts a file path and returns a list of document dicts,
regardless of whether the source JSON is a single object, a list of
objects, or a nested structure with a top-level key wrapping an array
(e.g. {"articles": [...]}).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


# Common top-level keys that wrap an array of documents / articles.
_COLLECTION_KEYS = (
    "articles",
    "documents",
    "data",
    "items",
    "records",
    "results",
    "entries",
    "passages",
    "rows",
)


def load_article_json(file_path: str) -> List[Dict[str, Any]]:
    """Load a JSON file and return a flat list of document dicts.

    Handles three common shapes:
    1. A JSON **array** of objects  →  returned as-is (non-dict items dropped).
    2. A single JSON **object**     →  wrapped in a list.
       - If the object has exactly one key whose value is a list of dicts,
         or the key is a well-known collection name, the nested list is
         returned instead.
    3. Anything else                →  raises ``ValueError``.
    """
    path = Path(file_path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        # Check for a well-known wrapper key (e.g. {"articles": [...]})
        for key in _COLLECTION_KEYS:
            if key in data and isinstance(data[key], list):
                docs = [item for item in data[key] if isinstance(item, dict)]
                if docs:
                    return docs

        # Check for a single-key wrapper with a list value
        if len(data) == 1:
            only_value = next(iter(data.values()))
            if isinstance(only_value, list):
                docs = [item for item in only_value if isinstance(item, dict)]
                if docs:
                    return docs

        # Treat the entire object as one document
        return [data]

    raise ValueError(
        f"Unsupported JSON root type in {file_path}: {type(data).__name__}"
    )
