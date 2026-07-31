#!/usr/bin/env python3
"""Export LoRA-ready JSONL from QAG analysis or minimal good/bad pair files."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_qa_pipeline import (  # noqa: E402
    _minimal_document_for_output,
    _minimal_qa_pairs_for_output,
    _pair_passes_grounding_gate,
)
from utils.minimal_text import plain_text_for_minimal_output  # noqa: E402

DEFAULT_SYSTEM_PROMPT = (
    "Answer using only the document below. "
    "Do not use outside knowledge."
)

SFT_OUT_NAME = "lora_sft.jsonl"
SFT_EVAL_OUT_NAME = "lora_sft_eval.jsonl"
DPO_OUT_NAME = "lora_dpo.jsonl"
DATASET_INFO_NAME = "lora_dataset_info.json"


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _clean_qa_text(text: str, field: str) -> str:
    return plain_text_for_minimal_output(text, field=field)


def _user_prompt(document_content: str, question: str) -> str:
    doc = document_content.strip()
    q = question.strip()
    return f"Document:\n{doc}\n\nQuestion: {q}"


def _sharegpt_record(
    document_content: str,
    question: str,
    answer: str,
    system_prompt: str,
) -> Dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": _user_prompt(document_content, question),
            },
            {"role": "assistant", "content": answer},
        ],
    }


def _alpaca_record(
    document_content: str,
    question: str,
    answer: str,
    system_prompt: str,
) -> Dict[str, Any]:
    return {
        "instruction": system_prompt,
        "input": _user_prompt(document_content, question),
        "output": answer,
    }


def _dpo_record(
    document_content: str,
    question: str,
    chosen: str,
    rejected: str,
    system_prompt: str,
) -> Dict[str, Any]:
    return {
        "system": system_prompt,
        "prompt": _user_prompt(document_content, question),
        "chosen": chosen,
        "rejected": rejected,
    }


def _iter_good_pair_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        name = path.name
        if name.endswith("_minimal_good_pairs.json"):
            yield path
            return
        if name.endswith("_analysis.json") and "_minimal_" not in name:
            yield path
            return
        raise SystemExit(
            f"Expected *_analysis.json or *_minimal_good_pairs.json: {path}"
        )
    if path.is_dir():
        seen: set[Path] = set()
        for child in sorted(path.rglob("*_minimal_good_pairs.json")):
            seen.add(child)
            yield child
        for child in sorted(path.rglob("*_analysis.json")):
            if "_minimal_" in child.name:
                continue
            good = child.with_name(
                f"{child.stem}_minimal_good_pairs.json"
            )
            if good in seen:
                continue
            yield child
        return
    raise SystemExit(f"Not a file or directory: {path}")


def _bad_pair_path(src: Path) -> Path:
    if src.name.endswith("_minimal_good_pairs.json"):
        return src.with_name(
            src.name.replace(
                "_minimal_good_pairs.json",
                "_minimal_bad_pairs.json",
            )
        )
    return src.with_name(f"{src.stem}_minimal_bad_pairs.json")


def _load_document_content(document: Dict[str, Any], doc_id: str) -> str:
    minimal = _minimal_document_for_output(document, doc_id)
    return _as_text(minimal.get("content"))


def _pairs_from_analysis(
    data: Dict[str, Any],
    min_confidence: float,
    mode: str,
) -> List[Dict[str, str]]:
    qa_pairs = data.get("qa_pairs")
    if not isinstance(qa_pairs, list):
        return []
    rows: List[Dict[str, str]] = []
    for pair in qa_pairs:
        if not isinstance(pair, dict):
            continue
        if mode == "good":
            if not _pair_passes_grounding_gate(pair, min_confidence):
                continue
        elif mode == "bad":
            if _pair_passes_grounding_gate(pair, min_confidence):
                continue
        else:
            raise ValueError(f"unknown mode: {mode}")
        cleaned = _minimal_qa_pairs_for_output([pair])
        if cleaned:
            rows.append(cleaned[0])
    return rows


def _pairs_from_minimal_payload(data: Dict[str, Any]) -> List[Dict[str, str]]:
    qa_pairs = data.get("qa_pairs")
    if not isinstance(qa_pairs, list):
        return []
    rows: List[Dict[str, str]] = []
    for pair in qa_pairs:
        if not isinstance(pair, dict):
            continue
        q = _clean_qa_text(_as_text(pair.get("question")), "question")
        a = _clean_qa_text(_as_text(pair.get("answer")), "answer")
        if q and a:
            rows.append({"question": q, "answer": a})
    return rows


def _load_good_pairs(
    src: Path,
    min_confidence: float,
) -> Tuple[str, List[Dict[str, str]]]:
    text = src.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        return "", []
    document = data.get("document")
    if not isinstance(document, dict):
        return "", []
    raw_id = document.get("id", document.get("title", "unknown"))
    doc_id = _as_text(raw_id) or "unknown"
    content = _load_document_content(document, doc_id)
    if src.name.endswith("_minimal_good_pairs.json"):
        pairs = _pairs_from_minimal_payload(data)
    else:
        pairs = _pairs_from_analysis(data, min_confidence, "good")
    return content, pairs


def _load_bad_pairs(
    src: Path,
    min_confidence: float,
) -> Tuple[str, List[Dict[str, str]]]:
    bad_path = _bad_pair_path(src)
    if bad_path.exists():
        text = bad_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            document = data.get("document")
            if isinstance(document, dict):
                raw_id = document.get("id", document.get("title", "unknown"))
                doc_id = _as_text(raw_id) or "unknown"
                content = _load_document_content(document, doc_id)
                return content, _pairs_from_minimal_payload(data)
    if src.name.endswith("_analysis.json"):
        text = src.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            document = data.get("document")
            if isinstance(document, dict):
                raw_id = document.get("id", document.get("title", "unknown"))
                doc_id = _as_text(raw_id) or "unknown"
                content = _load_document_content(document, doc_id)
                pairs = _pairs_from_analysis(data, min_confidence, "bad")
                return content, pairs
    return "", []


def _dpo_pairs_from_payload(
    data: Dict[str, Any],
) -> List[Dict[str, str]]:
    raw_pairs = data.get("dpo_pairs")
    if not isinstance(raw_pairs, list):
        return []
    rows: List[Dict[str, str]] = []
    for pair in raw_pairs:
        if not isinstance(pair, dict):
            continue
        question = _clean_qa_text(
            _as_text(pair.get("question")),
            "question",
        )
        chosen = _clean_qa_text(
            _as_text(pair.get("chosen")),
            "answer",
        )
        rejected = _clean_qa_text(
            _as_text(pair.get("rejected")),
            "answer",
        )
        if not question or not chosen or not rejected or chosen == rejected:
            continue
        rows.append(
            {
                "question": question,
                "chosen": chosen,
                "rejected": rejected,
            }
        )
    return rows


def _load_captured_dpo_pairs(src: Path) -> List[Dict[str, str]]:
    candidates = [src]
    suffix = "_minimal_good_pairs.json"
    if src.name.endswith(suffix):
        analysis_name = f"{src.name[:-len(suffix)]}.json"
        candidates.append(src.with_name(analysis_name))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        rows = _dpo_pairs_from_payload(data)
        if rows:
            return rows
    return []


def _build_sft_record(
    fmt: str,
    document_content: str,
    question: str,
    answer: str,
    system_prompt: str,
) -> Dict[str, Any]:
    if fmt == "sharegpt":
        return _sharegpt_record(
            document_content, question, answer, system_prompt
        )
    if fmt == "alpaca":
        return _alpaca_record(
            document_content, question, answer, system_prompt
        )
    raise ValueError(f"unsupported format: {fmt}")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(
                json.dumps(row, ensure_ascii=False) + "\n"
            )


def _split_train_eval(
    rows: List[Dict[str, Any]],
    eval_fraction: float,
    seed: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if eval_fraction <= 0 or len(rows) < 2:
        return rows, []
    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    eval_n = max(1, int(len(shuffled) * eval_fraction))
    eval_n = min(eval_n, len(shuffled) - 1)
    return shuffled[eval_n:], shuffled[:eval_n]


def _write_dataset_info(
    out_dir: Path,
    fmt: str,
    has_eval: bool,
    has_dpo: bool,
) -> None:
    if fmt == "sharegpt":
        sft_entry: Dict[str, Any] = {
            "file_name": SFT_OUT_NAME,
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
        }
    else:
        sft_entry = {
            "file_name": SFT_OUT_NAME,
            "columns": {
                "prompt": "instruction",
                "query": "input",
                "response": "output",
            },
        }
    payload: Dict[str, Any] = {"qag_lora_sft": sft_entry}
    if has_eval:
        key = "qag_lora_sft_eval"
        payload[key] = dict(sft_entry)
        payload[key]["file_name"] = SFT_EVAL_OUT_NAME
    if has_dpo:
        payload["qag_lora_dpo"] = {
            "file_name": DPO_OUT_NAME,
            "ranking": True,
            "columns": {
                "prompt": "prompt",
                "chosen": "chosen",
                "rejected": "rejected",
                "system": "system",
            },
        }
    path = out_dir / DATASET_INFO_NAME
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Export LoRA SFT (and optional DPO) JSONL from QAG run outputs."
        )
    )
    p.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Run directory or analysis / minimal_good_pairs file.",
    )
    p.add_argument(
        "--format",
        choices=("sharegpt", "alpaca"),
        default="sharegpt",
        help="SFT JSONL schema (default: sharegpt for LLaMA-Factory).",
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        default=0.7,
        help="Grounding threshold when reading full *_analysis.json.",
    )
    p.add_argument(
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
        help="System / instruction text prepended to each training row.",
    )
    p.add_argument(
        "--eval-fraction",
        type=float,
        default=0.1,
        help="Hold out this fraction for lora_sft_eval.jsonl (0=disable).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Shuffle seed for train/eval split.",
    )
    p.add_argument(
        "--include-dpo",
        action="store_true",
        help="Also write lora_dpo.jsonl when good+bad share a question.",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for JSONL outputs (default: run folder).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not write files.",
    )
    return p.parse_args()


def _resolve_output_dir(paths: List[Path], explicit: Optional[Path]) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    first = paths[0].expanduser().resolve()
    if first.is_dir():
        return first
    return first.parent


def main() -> None:
    args = _parse_args()
    sources: List[Path] = []
    for raw in args.paths:
        sources.extend(_iter_good_pair_files(raw.expanduser().resolve()))
    if not sources:
        print("No matching analysis or minimal_good_pairs inputs.", flush=True)
        return

    out_dir = _resolve_output_dir(
        [p.expanduser().resolve() for p in args.paths],
        args.output_dir,
    )
    system_prompt = _as_text(args.system_prompt) or DEFAULT_SYSTEM_PROMPT

    sft_rows: List[Dict[str, Any]] = []
    dpo_rows: List[Dict[str, Any]] = []

    for src in sources:
        content, good_pairs = _load_good_pairs(src, args.min_confidence)
        if not content or not good_pairs:
            continue
        for pair in good_pairs:
            q = pair["question"]
            a = pair["answer"]
            sft_rows.append(
                _build_sft_record(
                    args.format,
                    content,
                    q,
                    a,
                    system_prompt,
                )
            )
        if not args.include_dpo:
            continue
        captured_pairs = _load_captured_dpo_pairs(src)
        if captured_pairs:
            for pair in captured_pairs:
                dpo_rows.append(
                    _dpo_record(
                        content,
                        pair["question"],
                        pair["chosen"],
                        pair["rejected"],
                        system_prompt,
                    )
                )
            continue
        _, bad_pairs = _load_bad_pairs(src, args.min_confidence)
        if not bad_pairs:
            continue
        bad_by_q = {_as_text(p["question"]): p["answer"] for p in bad_pairs}
        for pair in good_pairs:
            q = _as_text(pair["question"])
            rejected = bad_by_q.get(q)
            if not rejected:
                continue
            dpo_rows.append(
                _dpo_record(
                    content,
                    q,
                    pair["answer"],
                    rejected,
                    system_prompt,
                )
            )

    train_rows, eval_rows = _split_train_eval(
        sft_rows,
        args.eval_fraction,
        args.seed,
    )

    if args.dry_run:
        print(
            f"[dry-run] sft={len(train_rows)} eval={len(eval_rows)} "
            f"dpo={len(dpo_rows)} -> {out_dir}",
            flush=True,
        )
        return

    if not train_rows:
        print("[warn] no SFT rows produced.", flush=True)
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / SFT_OUT_NAME, train_rows)
    print(
        f"[ok] {out_dir / SFT_OUT_NAME} ({len(train_rows)} rows)",
        flush=True,
    )
    if eval_rows:
        _write_jsonl(out_dir / SFT_EVAL_OUT_NAME, eval_rows)
        print(
            f"[ok] {out_dir / SFT_EVAL_OUT_NAME} ({len(eval_rows)} rows)",
            flush=True,
        )
    if dpo_rows:
        _write_jsonl(out_dir / DPO_OUT_NAME, dpo_rows)
        print(
            f"[ok] {out_dir / DPO_OUT_NAME} ({len(dpo_rows)} rows)",
            flush=True,
        )
    elif args.include_dpo:
        print(
            "[warn] no DPO rows produced; a rejected answer attempt must "
            "be followed by an accepted retry for the same question. "
            f"{DPO_OUT_NAME} was not written.",
            flush=True,
        )
    _write_dataset_info(
        out_dir,
        args.format,
        bool(eval_rows),
        bool(dpo_rows),
    )
    print(f"[ok] {out_dir / DATASET_INFO_NAME}", flush=True)


if __name__ == "__main__":
    main()
