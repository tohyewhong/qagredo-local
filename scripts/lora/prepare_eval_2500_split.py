#!/usr/bin/env python3
"""Build train/holdout doc id lists for the 2500-doc adapter eval campaign."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from run_qa_pipeline import (  # noqa: E402
    _document_filter_id,
    _merge_input_folder_to_jsonl,
)
from utils.data_loader import load_data_file  # noqa: E402


def _load_config(config_path: Path) -> Dict[str, Any]:
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _ids_from_run(run_dir: Path) -> Set[str]:
    found: Set[str] = set()
    for path in run_dir.glob("*_analysis.json"):
        if "_minimal_" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        doc = data.get("document")
        if isinstance(doc, dict):
            doc_id = _document_filter_id(doc)
            if doc_id:
                found.add(doc_id)
    return found


def _load_env_file(host_dir: Path) -> None:
    env_path = host_dir / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _corpus_ids(config_path: Path, host_dir: Path) -> List[str]:
    _load_env_file(host_dir)
    cfg = _load_config(config_path)
    run_cfg = cfg.get("run") if isinstance(cfg.get("run"), dict) else {}
    run_cfg = dict(run_cfg)
    folder = str(run_cfg.get("input_folder") or "").strip()
    if folder in ("", "."):
        data_dir = os.environ.get("QAG_DATA_DIR") or os.environ.get("DATA_DIR")
        if data_dir:
            run_cfg["input_folder"] = data_dir
    merged_path, _label = _merge_input_folder_to_jsonl(run_cfg, cfg)
    loaded = load_data_file(merged_path)
    if isinstance(loaded, dict):
        docs = [loaded]
    elif isinstance(loaded, list):
        docs = [d for d in loaded if isinstance(d, dict)]
    else:
        raise ValueError(f"Unexpected corpus format from {merged_path}")
    ids: List[str] = []
    seen: Set[str] = set()
    for doc in docs:
        doc_id = _document_filter_id(doc)
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        ids.append(doc_id)
    ids.sort()
    return ids


def prepare_split(
    *,
    anchor_run: Path,
    eval_dir: Path,
    exclude_run: Path,
    total: int,
    holdout: int,
    seed: int,
    config_path: Path,
    host_dir: Path,
) -> Dict[str, Any]:
    if holdout >= total:
        raise ValueError("holdout must be smaller than total")
    train_n = total - holdout

    pool = _corpus_ids(config_path, host_dir)
    excluded = _ids_from_run(exclude_run)
    available = [doc_id for doc_id in pool if doc_id not in excluded]
    if len(available) < total:
        raise ValueError(
            f"Need {total} docs but only {len(available)} after excluding "
            f"{len(excluded)} from {exclude_run}"
        )

    rng = random.Random(seed)
    picked = list(available)
    rng.shuffle(picked)
    selected = picked[:total]
    holdout_ids = sorted(selected[:holdout])
    train_ids = sorted(selected[holdout:])

    eval_dir.mkdir(parents=True, exist_ok=True)
    train_path = eval_dir / "doc_ids_2000_train.txt"
    holdout_path = eval_dir / "doc_ids_500_holdout.txt"
    fair_path = eval_dir / "doc_ids.txt"

    train_path.write_text(
        "\n".join(train_ids) + ("\n" if train_ids else ""),
        encoding="utf-8",
    )
    holdout_path.write_text(
        "\n".join(holdout_ids) + ("\n" if holdout_ids else ""),
        encoding="utf-8",
    )
    fair_path.write_text(
        "\n".join(holdout_ids) + ("\n" if holdout_ids else ""),
        encoding="utf-8",
    )

    manifest = {
        "campaign": "eval_2500",
        "anchor_run_dir": str(anchor_run.resolve()),
        "eval_dir": str(eval_dir.resolve()),
        "corpus_documents": len(pool),
        "excluded_documents": len(excluded),
        "excluded_run_dir": str(exclude_run.resolve()),
        "selected_documents": total,
        "train_documents": train_n,
        "holdout_documents": holdout,
        "seed": seed,
        "selection_method": (
            f"seed={seed} shuffle on corpus minus exclude_run; "
            f"first {holdout} = holdout, next {train_n} = train"
        ),
        "train_doc_ids_file": str(train_path),
        "holdout_doc_ids_file": str(holdout_path),
        "fair_eval_doc_ids_file": str(fair_path),
    }
    manifest_path = eval_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare 2500-doc train/holdout id lists.",
    )
    parser.add_argument(
        "--anchor-run",
        type=Path,
        default=ROOT / "output/vllm/qwen-qwen3.5-9b/2026-07-17_095536",
        help="Anchor run directory (eval_2500/ lives here)",
    )
    parser.add_argument(
        "--exclude-run",
        type=Path,
        default=None,
        help="Exclude doc ids from this run (default: anchor-run)",
    )
    parser.add_argument(
        "--eval-dir",
        type=Path,
        default=None,
        help="Output dir (default: ANCHOR/eval_2500)",
    )
    parser.add_argument(
        "--total",
        type=int,
        default=2500,
    )
    parser.add_argument(
        "--holdout",
        type=int,
        default=500,
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/config.vllm.yaml",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    anchor = args.anchor_run.expanduser().resolve()
    exclude = (
        args.exclude_run.expanduser().resolve()
        if args.exclude_run
        else anchor
    )
    eval_dir = (
        args.eval_dir.expanduser().resolve()
        if args.eval_dir
        else anchor / "eval_2500"
    )
    manifest = prepare_split(
        anchor_run=anchor,
        eval_dir=eval_dir,
        exclude_run=exclude,
        total=args.total,
        holdout=args.holdout,
        seed=args.seed,
        config_path=args.config.expanduser().resolve(),
        host_dir=ROOT,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
