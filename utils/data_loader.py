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


def resolve_data_file_path(file_path: str) -> Path:
    """Resolve path the same way ``load_data_file`` does (repo-relative OK)."""
    return _resolve_file_path(file_path)


def resolve_data_folder_path(folder_path: str) -> Path:
    """
    Resolve a directory under the repo or ``data/`` (same search order as
    files, but require an existing directory).
    """
    path = Path(folder_path)

    if path.is_absolute() and path.is_dir():
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
        if candidate.is_dir():
            return candidate

    raise FileNotFoundError(
        f"Directory not found: {folder_path}\n"
        f"Tried locations:\n" + "\n".join(f"  - {c}" for c in candidates)
    )


def load_data_file(
    file_path: str,
) -> Union[List[Dict[str, Any]], Dict[str, Any], List[Any], Any]:
    resolved_path = _resolve_file_path(file_path)
    file_ext = resolved_path.suffix.lower()

    if file_ext == ".jsonl":
        return _load_jsonl(resolved_path)
    if file_ext == ".json":
        return _load_json(resolved_path)
    raise ValueError(
        f"Unsupported file format: {file_ext}. Supported formats: .json, .jsonl"  # noqa: E501
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
