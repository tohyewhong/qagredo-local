"""Output manager for organizing results by provider and model."""

from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional
import json


# ---------------------------------------------------------------------------
#  Run-level timestamp: captured once per pipeline run so every file from the
#  same run lands in the same folder (e.g. 2026-02-13_143025).
# ---------------------------------------------------------------------------
_run_timestamp: Optional[str] = None


def init_run_timestamp() -> str:
    """Call once at the start of a pipeline run to lock the folder timestamp."""
    global _run_timestamp
    _run_timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return _run_timestamp


def get_run_timestamp() -> str:
    """Return the current run timestamp, creating one if not yet initialised."""
    if _run_timestamp is None:
        return init_run_timestamp()
    return _run_timestamp


def get_output_path(
    provider: str,
    model: str,
    output_type: str = "results",
    base_dir: Optional[Path] = None,
    create_dirs: bool = True,
    use_timestamp: bool = True,
) -> Path:
    if base_dir is None:
        project_root = Path(__file__).parent.parent
        base_dir = project_root / "output"
    else:
        base_dir = Path(base_dir)

    provider = provider.lower().replace(" ", "-").replace("_", "-")
    model = model.lower().replace("/", "-").replace(" ", "-").replace("_", "-")

    if use_timestamp:
        run_ts = get_run_timestamp()
        output_path = base_dir / provider / model / run_ts
    else:
        output_path = base_dir / provider / model

    if create_dirs:
        output_path.mkdir(parents=True, exist_ok=True)

    filename = f"{output_type}.json"
    return output_path / filename


def get_timestamped_output_path(
    provider: str,
    model: str,
    output_type: str = "results",
    base_dir: Optional[Path] = None,
    create_dirs: bool = True,
) -> Path:
    if base_dir is None:
        project_root = Path(__file__).parent.parent
        base_dir = project_root / "output"
    else:
        base_dir = Path(base_dir)

    provider = provider.lower().replace(" ", "-").replace("_", "-")
    model = model.lower().replace("/", "-").replace(" ", "-").replace("_", "-")

    run_ts = get_run_timestamp()
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = base_dir / provider / model / run_ts

    if create_dirs:
        output_path.mkdir(parents=True, exist_ok=True)

    filename = f"{timestamp_str}_{output_type}.json"
    return output_path / filename


def save_results(
    data: Any,
    provider: str,
    model: str,
    output_type: str = "results",
    base_dir: Optional[Path] = None,
    use_timestamp: bool = False,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> Path:
    if use_timestamp:
        output_path = get_timestamped_output_path(
            provider=provider,
            model=model,
            output_type=output_type,
            base_dir=base_dir,
            create_dirs=True,
        )
    else:
        output_path = get_output_path(
            provider=provider,
            model=model,
            output_type=output_type,
            base_dir=base_dir,
            create_dirs=True,
            use_timestamp=True,
        )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
    return output_path


def load_results(
    provider: str,
    model: str,
    output_type: str = "results",
    base_dir: Optional[Path] = None,
    date: Optional[str] = None,
    file_path: Optional[Path] = None,
) -> Any:
    if file_path:
        path = Path(file_path)
    else:
        if base_dir is None:
            project_root = Path(__file__).parent.parent
            base_dir = project_root / "output"
        else:
            base_dir = Path(base_dir)

        provider = provider.lower().replace(" ", "-").replace("_", "-")
        model = model.lower().replace("/", "-").replace(" ", "-").replace("_", "-")

        model_dir = base_dir / provider / model

        if date is not None:
            # Exact folder name supplied (e.g. "2026-02-13_143025" or legacy "2026-02-13")
            path = model_dir / date / f"{output_type}.json"
        else:
            # No date given — find the most recent run folder
            if model_dir.exists():
                run_dirs = sorted(
                    [d for d in model_dir.iterdir() if d.is_dir()],
                    key=lambda d: d.name,
                    reverse=True,
                )
                if run_dirs:
                    path = run_dirs[0] / f"{output_type}.json"
                else:
                    path = model_dir / "results.json"  # will raise FileNotFoundError
            else:
                path = model_dir / "results.json"

    if not path.exists():
        raise FileNotFoundError(f"Results file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_available_results(
    base_dir: Optional[Path] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> list:
    if base_dir is None:
        project_root = Path(__file__).parent.parent
        base_dir = project_root / "output"
    else:
        base_dir = Path(base_dir)

    if not base_dir.exists():
        return []

    results = []
    for provider_dir in base_dir.iterdir():
        if not provider_dir.is_dir():
            continue

        provider_name = provider_dir.name
        provider_normalized = provider.lower().replace(" ", "-").replace("_", "-") if provider else None
        if provider_normalized and provider_name != provider_normalized:
            continue

        for model_dir in provider_dir.iterdir():
            if not model_dir.is_dir():
                continue

            model_name = model_dir.name
            model_normalized = model.lower().replace("/", "-").replace(" ", "-").replace("_", "-") if model else None
            if model_normalized and model_name != model_normalized:
                continue

            for date_dir in model_dir.iterdir():
                if not date_dir.is_dir():
                    continue

                date_str = date_dir.name
                for json_file in date_dir.glob("*.json"):
                    results.append(
                        {
                            "provider": provider_name,
                            "model": model_name,
                            "date": date_str,
                            "file": json_file.name,
                            "path": json_file,
                            "size": json_file.stat().st_size,
                            "modified": datetime.fromtimestamp(json_file.stat().st_mtime),
                        }
                    )

    return sorted(results, key=lambda x: (x["date"], x["modified"]), reverse=True)


def get_output_summary(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    results = list_available_results(base_dir=base_dir)
    summary: Dict[str, Any] = {"total_files": len(results), "providers": {}, "models": {}, "dates": {}}

    for result in results:
        provider = result["provider"]
        model = result["model"]
        date = result["date"]

        summary["providers"][provider] = summary["providers"].get(provider, 0) + 1
        model_key = f"{provider}/{model}"
        summary["models"][model_key] = summary["models"].get(model_key, 0) + 1
        summary["dates"][date] = summary["dates"].get(date, 0) + 1

    return summary

