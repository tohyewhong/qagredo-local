"""Data loading utilities for JSON and JSONL files."""

import json
from pathlib import Path
from typing import Union, Dict, List, Any


def _resolve_file_path(file_path: str) -> Path:
    path = Path(file_path)

    if path.is_absolute() and path.exists():
        return path

    project_root = Path(__file__).parent.parent
    data_folder = project_root / "data"

    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append(data_folder / path)
        candidates.append(project_root / path)
        if "data" not in str(path):
            candidates.append(project_root / "data" / path)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    raise FileNotFoundError(
        f"File not found: {file_path}\n"
        f"Tried locations:\n" + "\n".join(f"  - {c}" for c in candidates)
    )


def load_data_file(file_path: str) -> Union[List[Dict[str, Any]], Dict[str, Any], List[Any], Any]:
    resolved_path = _resolve_file_path(file_path)
    file_ext = resolved_path.suffix.lower()

    if file_ext == ".jsonl":
        return _load_jsonl(resolved_path)
    if file_ext == ".json":
        return _load_json(resolved_path)
    raise ValueError(
        f"Unsupported file format: {file_ext}. Supported formats: .json, .jsonl"
    )


def _load_jsonl(file_path: Path) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise json.JSONDecodeError(
                    f"Invalid JSON on line {line_num} of {file_path}: {e.msg}",
                    e.doc,
                    e.pos,
                )
            result.append(obj)
    return result


def _load_json(file_path: Path) -> Union[Dict[str, Any], List[Any], Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

