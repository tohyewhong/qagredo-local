"""Configurable Q&A pipeline runner (sequential generation)."""

# CRITICAL: These must run BEFORE any imports
import os

os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")
# HF_HOME replaces deprecated TRANSFORMERS_CACHE
os.environ.pop("TRANSFORMERS_CACHE", None)

import argparse  # noqa: E402
from copy import deepcopy  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

from utils import (  # noqa: E402
    grade_qa_results,
    load_data_file,
    print_grading_report,
    generate_answers,
    generate_questions,
    save_results,
)
from utils.question_generator import _call_llm, _extract_text_content  # noqa: E402
from utils.output_manager import (  # noqa: E402
    _safe_output_filename_stem,
    analysis_json_path,
    analysis_output_exists,
    init_run_timestamp,
    input_file_output_segment,
    resolve_resume_run_directory,
)
from utils.hallucination_checker import (  # noqa: E402
    apply_grounding_why_when_no_citations,
    set_llm_config,
)
from utils.config_manager import (  # noqa: E402
    build_effective_config,
    default_config_path,
)
from utils.data_loader import (  # noqa: E402
    resolve_data_file_path,
    resolve_data_folder_path,
)

sys.stdout.reconfigure(line_buffering=True)

_convert_mod: Any = None


def _get_convert_to_jsonl_module() -> Any:
    global _convert_mod
    if _convert_mod is None:
        cpath = (
            Path(__file__).resolve().parent
            / "scripts"
            / "conversion"
            / "convert_to_qagredo_jsonl.py"
        )
        spec = importlib.util.spec_from_file_location(
            "qagredo_convert_to_jsonl",
            cpath,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load converter module: {cpath}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _convert_mod = mod
    return _convert_mod


def _config_bool(val: Any) -> bool:
    """YAML-friendly bool (string true/false and common synonyms)."""
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("0", "false", "no", "off", ""):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return False


def _converter_type_override(run_cfg: Dict[str, Any]) -> Optional[str]:
    it = str(run_cfg.get("input_type") or "").strip().lower()
    if it in ("", "auto"):
        return None
    return it


def _converter_semantic_options(config: Dict[str, Any]) -> Tuple[bool, int]:
    run_raw = config.get("run")
    run_block = run_raw if isinstance(run_raw, dict) else {}
    sem = run_block.get("semantic_normalization")
    sem_en = bool(isinstance(sem, dict) and sem.get("enable"))
    sem_chars = 5000
    if isinstance(sem, dict):
        try:
            sem_chars = int(sem.get("max_content_chars") or 5000)
        except (TypeError, ValueError):
            sem_chars = 5000
    return sem_en, sem_chars


def _prepare_jsonl_input_if_needed(
    input_file: str,
    run_cfg: Dict[str, Any],
    config: Dict[str, Any],
) -> str:
    """Return path to .json / .jsonl, optionally converting via converter."""
    resolved = resolve_data_file_path(input_file)
    sfx = resolved.suffix.lower()
    if sfx in (".json", ".jsonl"):
        return input_file

    if not _config_bool(run_cfg.get("auto_convert")):
        raise ValueError(
            f"run.input_file is {sfx!r}; pipeline loads .json / .jsonl only. "
            "Convert first (scripts/conversion/convert_to_qagredo_jsonl.py), "
            "point run.input_file at the .jsonl, or set run.auto_convert: "
            "true to write <stem>.jsonl beside the source and run on that."
        )

    mod = _get_convert_to_jsonl_module()
    ext_key = sfx.lstrip(".")
    supported = getattr(mod, "SUPPORTED_INPUT_TYPES", ())
    if ext_key not in supported:
        raise ValueError(
            f"auto_convert: unsupported source extension {sfx!r}. "
            f"Converter accepts: {supported}"
        )

    override = _converter_type_override(run_cfg)
    sem_en, sem_chars = _converter_semantic_options(config)

    out_path = resolved.with_suffix(".jsonl")
    rc = mod.convert_to_qagredo_jsonl(
        str(resolved),
        str(out_path),
        input_type=override,
        semantic_normalize=sem_en,
        semantic_max_content_chars=sem_chars,
    )
    if rc != 0:
        raise RuntimeError(
            f"convert_to_qagredo_jsonl exited with {rc} for {resolved}"
        )
    print(
        "[INFO] run.auto_convert: wrote JSONL "
        f"{out_path} from {resolved}"
    )
    return str(out_path)


def _record_from_txt_path(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stem = path.stem
    safe = _safe_output_filename_stem(stem)
    src = str(path.resolve())
    return {
        "id": safe,
        "title": stem,
        "content": text,
        "text": text,
        "source": src,
        "type": "text_document",
    }


def _merge_input_folder_to_jsonl(
    run_cfg: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Merge files under run.input_folder into one JSONL.

    Returns (absolute path to merged JSONL, folder basename for output
    directory naming).
    """
    folder_s = str(run_cfg.get("input_folder") or "").strip()
    resolved_dir = resolve_data_folder_path(folder_s)
    glob_raw = str(run_cfg.get("input_glob") or "*.txt").strip() or "*.txt"
    patterns = [p.strip() for p in glob_raw.split(",") if p.strip()]
    seen: set[Path] = set()
    paths: list[Path] = []
    for pat in patterns:
        for p in resolved_dir.glob(pat):
            if p.is_file() and p not in seen:
                seen.add(p)
                paths.append(p)
    paths.sort(key=lambda p: p.name)
    mf = run_cfg.get("max_files")
    mf_i = 0
    if mf is not None:
        try:
            mf_i = int(mf)
        except (TypeError, ValueError):
            mf_i = 0
    if mf_i > 0:
        paths = paths[:mf_i]

    if not paths:
        raise ValueError(
            f"No files matched run.input_glob={glob_raw!r} under "
            f"{resolved_dir}"
        )

    records: List[Dict[str, Any]] = []
    mod: Any = None
    override = _converter_type_override(run_cfg)
    sem_en, sem_chars = _converter_semantic_options(config)
    auto_cv = _config_bool(run_cfg.get("auto_convert"))

    for p in paths:
        ext = p.suffix.lower()
        if ext == ".txt":
            records.append(_record_from_txt_path(p))
            continue
        if ext in (".json", ".jsonl") and not auto_cv:
            raise ValueError(
                "Folder batch needs run.auto_convert: true for .json/.jsonl "
                "per file, or set run.input_file to one JSON/JSONL."
            )
        if not auto_cv:
            print(
                f"[WARN] Skipping {p.name} in folder batch (need "
                f"run.auto_convert: true for non-.txt files)."
            )
            continue
        if mod is None:
            mod = _get_convert_to_jsonl_module()
        supported = getattr(mod, "SUPPORTED_INPUT_TYPES", ())
        ek = ext.lstrip(".")
        if ek not in supported:
            print(f"[WARN] Skipping unsupported type in folder: {p.name}")
            continue
        with tempfile.TemporaryDirectory(prefix="qagredo_fconv_") as td:
            out_p = Path(td) / f"{_safe_output_filename_stem(p.stem)}.jsonl"
            rc = mod.convert_to_qagredo_jsonl(
                str(p.resolve()),
                str(out_p),
                input_type=override,
                semantic_normalize=sem_en,
                semantic_max_content_chars=sem_chars,
            )
            if rc != 0:
                raise RuntimeError(
                    f"convert_to_qagredo_jsonl exited with {rc} for {p}"
                )
            raw_lines = out_p.read_text(encoding="utf-8").splitlines()
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    if not records:
        raise ValueError(
            f"No ingestible documents under {resolved_dir} "
            f"(glob={glob_pat!r})."
        )

    root = Path(__file__).resolve().parent
    cache_root = root / "data" / ".qagredo_batch"
    cache_root.mkdir(parents=True, exist_ok=True)
    safe_dir = _safe_output_filename_stem(resolved_dir.name)
    out_jsonl = cache_root / f"{safe_dir}.jsonl"
    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(
        "[INFO] run.input_folder: merged "
        f"{len(records)} document(s) → {out_jsonl}"
    )
    return str(out_jsonl.resolve()), resolved_dir.name


def _infer_numeric_output_profile(provider: str, model: str) -> str:
    """
    Map runs into simple numeric buckets for output folder naming.

    Example scheme:
      - 1: Llama (served via vLLM)
      - 3: OpenAI
    """
    provider_l = (provider or "").lower()
    model_l = (model or "").lower()

    if provider_l == "openai":
        return "3"
    if provider_l == "ollama":
        return "ollama"
    if "llama" in model_l or "meta-llama" in model_l:
        return "1"
    # Fallback: keep provider name to avoid collisions.
    return provider_l or "unknown"


def _normalize_document_list(
    loaded: Any,
    input_path: str,
) -> List[Dict[str, Any]]:
    if isinstance(loaded, dict):
        return [loaded]
    if isinstance(loaded, list):
        out: List[Dict[str, Any]] = []
        for i, item in enumerate(loaded):
            if not isinstance(item, dict):
                raise TypeError(
                    f"{input_path}: item {i} is {type(item).__name__}, "
                    "expected object"
                )
            out.append(item)
        return out
    raise TypeError(
        f"{input_path}: expected list or dict, got {type(loaded).__name__}"
    )


def _expected_analysis_output_stem(
    document: Dict[str, Any],
    idx: int,
    total: int,
    run_cfg: Dict[str, Any],
    input_path: str,
) -> str:
    """Mirror per-document save naming (``*_analysis.json`` stem)."""
    doc_id = document.get("id", document.get("title", f"doc_{idx}"))
    stem_mode = str(
        run_cfg.get("output_analysis_stem") or "document_id"
    ).strip().lower()
    multi = total > 1
    if stem_mode in ("input_file", "input", "file", "filename"):
        base = _safe_output_filename_stem(Path(input_path).stem)
        if multi:
            return f"{base}_{idx:04d}_analysis"
        return f"{base}_analysis"
    base = _safe_output_filename_stem(str(doc_id))
    if multi:
        return f"{base}_{idx:04d}_analysis"
    return f"{base}_analysis"


def _resolve_output_provider_model(
    config: Dict[str, Any],
    settings: Dict[str, Any],
) -> Tuple[str, str]:
    llm_cfg = config.get("llm", {}) or {}
    provider = settings.get("provider") or llm_cfg.get("provider", "openai")
    model = settings.get("model") or llm_cfg.get("model", "gpt-4")
    output_cfg = (
        (config.get("output") or {})
        if isinstance(config, dict)
        else {}
    )
    selected_profile_id = _get_selected_profile_id(config)
    if "scheme" in output_cfg:
        output_scheme = str(output_cfg.get("scheme", "default")).lower()
    else:
        output_scheme = "profile" if selected_profile_id else "default"
    output_provider = provider
    profile_schemes = {
        "profile",
        "profiles",
        "profile_id",
        "profile-id",
    }
    numeric_schemes = {"numeric", "numeric_profile", "numeric-profiles"}
    if output_scheme in profile_schemes and selected_profile_id:
        output_provider = selected_profile_id
    elif output_scheme in numeric_schemes:
        output_provider = _infer_numeric_output_profile(
            provider=str(provider),
            model=str(model),
        )
    return str(output_provider), str(model)


def _pipeline_resume_options(
    config: Dict[str, Any],
    settings: Dict[str, Any],
    run_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    resume_mode = _config_bool(settings.get("resume")) or _config_bool(
        run_cfg.get("resume")
    )
    skip_existing = (
        resume_mode
        or _config_bool(settings.get("skip_existing_outputs"))
        or _config_bool(run_cfg.get("skip_existing_outputs"))
    )
    resume_run_dir = settings.get("resume_run_dir")
    if resume_run_dir is None:
        resume_run_dir = run_cfg.get("resume_run_dir")
    return {
        "resume_mode": resume_mode,
        "skip_existing": skip_existing,
        "resume_run_dir": resume_run_dir,
    }


def _get_selected_profile_id(config: Dict[str, Any]) -> str | None:
    run_raw = config.get("run", {})
    run_cfg = run_raw if isinstance(run_raw, dict) else {}
    profile = run_cfg.get("profile")
    if profile is None:
        return None
    profile_str = str(profile).strip()
    return profile_str or None


def _strip_evidence_line_prefixes(piece: str) -> str:
    """Remove leading list markers so fragments match document text."""
    p = piece.strip()
    for _ in range(4):
        nxt = re.sub(r"^[\-\*•]\s*", "", p)
        nxt = re.sub(r"^\d+[\.)]\s*", "", nxt)
        if nxt == p:
            break
        p = nxt
    return p.strip()


def _split_evidence_fragments(evidence_text: str) -> List[str]:
    """
    Split model supporting_evidence into candidate verbatim quotes.

    Strips bullet and numeric list prefixes (e.g. ``2.``), then drops
    duplicate fragments while preserving first-seen order so repeated
    model lines do not multiply citation_spans / citation_notes.
    """
    if not evidence_text or not str(evidence_text).strip():
        return []
    parts: List[str] = []
    seen: set[str] = set()
    for block in re.split(r"[\n\r]+", str(evidence_text).strip()):
        b = block.strip()
        if not b:
            continue
        for piece in re.split(r"\s*;\s*", b):
            p = _strip_evidence_line_prefixes(piece)
            if len(p) < 3:
                continue
            key = re.sub(r"\s+", " ", p)
            if key in seen:
                continue
            seen.add(key)
            parts.append(p)
    return parts


def _find_quote_span(content: str, quote: str) -> Tuple[int, int] | None:
    """Return (start, end) char offsets of quote in content, or None."""
    q = quote.strip()
    if len(q) < 2 or not content:
        return None
    pos = content.find(q)
    if pos >= 0:
        return pos, pos + len(q)
    tokens = [t for t in re.split(r"\s+", q) if t]
    if len(tokens) < 2:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    m = re.search(pattern, content, re.DOTALL)
    if m:
        return m.start(), m.end()
    return None


def _evidence_to_citation_spans(
    document_content: str,
    evidence_text: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Map supporting_evidence text to document character spans.

    Unmatched fragments (paraphrases, etc.) are returned for audit.
    """
    spans: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    content = document_content or ""
    for frag in _split_evidence_fragments(evidence_text):
        found = _find_quote_span(content, frag)
        if found is None:
            unmatched.append(frag)
            continue
        start, end = found
        spans.append(
            {
                "start": start,
                "end": end,
                "text": content[start:end],
            }
        )
    return spans, unmatched


def _citation_alignment_state(
    evidence_text: str,
    spans: List[Any],
    notes: List[Any],
) -> str:
    """
    Classify supporting_evidence vs resolved citation_spans / citation_notes.

    Returns:
        skipped — no non-empty supporting_evidence (grader-only grounding).
        ok — evidence present and every fragment mapped to a span (no notes).
        failed_notes — at least one evidence fragment not found verbatim.
        failed_no_span — evidence text present but zero spans (paraphrase-only,
            unparseable fragments, or nothing matched).
    """
    ev_raw = str(evidence_text or "").strip()
    if not ev_raw:
        return "skipped"
    n_spans = len(spans) if isinstance(spans, list) else 0
    n_notes = len(notes) if isinstance(notes, list) else 0
    if n_notes > 0:
        return "failed_notes"
    if n_spans == 0:
        return "failed_no_span"
    return "ok"


def _qa_pair_hallucination_check(
    pair: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Resolve per-slot grading payload (current and legacy key names)."""
    hc = pair.get("hallucination_check")
    if isinstance(hc, dict):
        return hc
    legacy = pair.get("grading")
    return legacy if isinstance(legacy, dict) else None


def _pick_question_validation_detail(
    metadata: Dict[str, Any],
    slot_idx: int,
    current_question: str,
) -> Optional[Dict[str, Any]]:
    """
    Select a validation detail for the current slot question.

    Priority:
      1) Match by final_question text.
      2) Match by question_index (1-based slot index).
      3) If there is exactly one detail (replacement generation), use it.
    """
    raw = metadata.get("question_validation")
    if not isinstance(raw, list) or not raw:
        return None
    details = [d for d in raw if isinstance(d, dict)]
    if not details:
        return None

    for d in details:
        if str(d.get("final_question") or "") == str(current_question):
            return d

    target_idx = slot_idx + 1
    for d in details:
        if d.get("question_index") == target_idx:
            return d

    if len(details) == 1:
        return details[0]
    return None


def _apply_citation_alignment_to_pair(
    pair: Dict[str, Any],
    evidence_text: str,
    halluc_cfg: Dict[str, Any],
) -> None:
    """
    Refine grading using citation resolution (optional).

    hallucination.citation_alignment:
        off (default) — no change.
        annotate — set citation_alignment + append issues when misaligned.
        strict — same as annotate and force is_grounded False if misaligned.
    """
    mode = str(
        (halluc_cfg or {}).get("citation_alignment") or "off"
    ).strip().lower()
    if mode in ("", "off", "false", "0", "no"):
        return
    spans = pair.get("citation_spans")
    notes = pair.get("citation_notes")
    if not isinstance(spans, list):
        spans = []
    if not isinstance(notes, list):
        notes = []
    state = _citation_alignment_state(evidence_text, spans, notes)
    chk = _qa_pair_hallucination_check(pair)
    if not isinstance(chk, dict):
        return
    pair["hallucination_check"] = chk
    grading = chk
    grading["citation_alignment"] = state
    if state in ("skipped", "ok"):
        return
    if mode not in ("annotate", "strict", "warn"):
        return
    detail = (
        "Citation alignment: supporting_evidence could not be located "
        "verbatim in document.content "
        f"({state})."
    )
    if state == "failed_notes":
        detail += f" Unmatched fragments: {len(notes)}."
    issues = grading.get("issues")
    if not isinstance(issues, list):
        issues = []
    else:
        issues = list(issues)
    if detail not in issues:
        issues.append(detail)
    grading["issues"] = issues
    if mode == "strict":
        grading["is_grounded"] = False
        print(
            "[INFO] citation_alignment strict: marking answer not grounded "
            f"({state})."
        )


def _document_plain_text(doc: Dict[str, Any]) -> str:
    """Resolve body text like question generation (content, text, body)."""
    try:
        return _extract_text_content(doc)
    except ValueError:
        return ""


def _count_words(text: str) -> int:
    """Word count for run.min_content_words (whitespace-separated tokens)."""
    return len([part for part in (text or "").split() if part.strip()])


def _document_content_metrics(document: Dict[str, Any]) -> Tuple[int, int]:
    """Return (word_count, char_count) for plain document body."""
    body = _document_plain_text(document).strip()
    return _count_words(body), len(body)


def _document_skip_reason_for_min_length(
    document: Dict[str, Any],
    run_cfg: Dict[str, Any],
) -> Optional[str]:
    """
    If run.min_content_words / min_content_chars are set, return a skip reason
    when the document is too short; otherwise None (process document).
    """
    try:
        min_words = int(run_cfg.get("min_content_words") or 0)
    except (TypeError, ValueError):
        min_words = 0
    try:
        min_chars = int(run_cfg.get("min_content_chars") or 0)
    except (TypeError, ValueError):
        min_chars = 0
    if min_words <= 0 and min_chars <= 0:
        return None
    words, chars = _document_content_metrics(document)
    if min_words > 0 and words < min_words:
        return (
            f"word count {words} is below run.min_content_words ({min_words})"
        )
    if min_chars > 0 and chars < min_chars:
        return (
            f"character count {chars} is below "
            f"run.min_content_chars ({min_chars})"
        )
    return None


def _snapshot_document_for_output(
    document: Dict[str, Any],
    doc_id: Any,
) -> Dict[str, Any]:
    """
    Persist id/title/source/type/content using common input aliases so outputs
    are not null when the JSONL used ``text`` or ``sources`` instead.
    """
    if not isinstance(document, dict):
        document = {}
    src = document.get("source")
    if src is None:
        src = document.get("sources")
    typ = document.get("type")
    if typ is None:
        typ = document.get("doc_type")
    meta = document.get("metadata")
    if typ is None and isinstance(meta, dict):
        typ = meta.get("type")
    body = _document_plain_text(document)
    title = document.get("title")
    if title is None and isinstance(meta, dict):
        title = meta.get("title")
    content_out = body if body else None
    return {
        "id": doc_id,
        "title": title,
        "source": src,
        "type": typ,
        "content": content_out,
    }


def _minimal_document_for_output(
    document: Dict[str, Any],
    doc_id: Any,
) -> Dict[str, Any]:
    """
    For run.minimal_qa_output: document blob is only {"content": ...}.
    Plain text matches _snapshot_document_for_output (same extraction).
    """
    snap = _snapshot_document_for_output(document, doc_id)
    raw = snap.get("content")
    text = "" if raw is None else str(raw)
    return {"content": text}


def build_qa_pairs(
    question_result: Dict[str, Any],
    qa_result: Dict[str, Any],
    grading: Dict[str, Any],
    document: Dict[str, Any],
    doc_index: int,
) -> List[Dict[str, Any]]:
    # grade_qa_results builds hallucination_checks in answer order; match by
    # index. Question-string keys fail when text differs slightly between
    # question_result and graded payload.
    checks: List[Any] = []
    grading_lookup: Dict[Any, Any] = {}
    if isinstance(grading, dict):
        raw_checks = grading.get("hallucination_checks")
        if isinstance(raw_checks, list):
            checks = raw_checks
            for check in checks:
                if isinstance(check, dict):
                    qk = check.get("question")
                    grading_lookup[qk] = check.get("check_result")

    raw_id = document.get("id", document.get("title", f"doc_{doc_index}"))
    source_doc_id = str(raw_id)
    title_val = document.get("title")
    if title_val is not None and str(title_val).strip():
        source_title = str(title_val)
    else:
        source_title = source_doc_id

    evidence_list = qa_result.get("supporting_evidence")
    if not isinstance(evidence_list, list):
        evidence_list = []

    content = _document_plain_text(document)
    pairs = []
    qr_q = question_result.get("questions")
    if not isinstance(qr_q, list):
        qr_q = []
    qa_q = qa_result.get("questions")
    if not isinstance(qa_q, list):
        qa_q = []
    qa_a = qa_result.get("answers")
    if not isinstance(qa_a, list):
        qa_a = []
    # grade_qa_results zips qa_result questions/answers — match that order.
    if qa_q and qa_a and len(qa_q) == len(qa_a):
        qlist = qa_q
        alist = qa_a
    else:
        if qa_q and qa_a and len(qa_q) != len(qa_a):
            print(
                "[WARN] qa_result questions/answers length mismatch; "
                "pairing question_result questions with answers."
            )
        qlist = qr_q
        alist = qa_a
    from utils.minimal_text import (
        plain_text_for_minimal_output,
        sanitize_llm_answer_response,
        sanitize_llm_question_response,
    )

    for i, (question, answer) in enumerate(zip(qlist, alist)):
        q_str = str(question) if question is not None else ""
        a_str = str(answer) if answer is not None else ""
        qs = sanitize_llm_question_response(q_str, max_items=1)
        if qs:
            q_str = qs[0]
        else:
            q_clean = plain_text_for_minimal_output(q_str, field="question")
            if q_clean:
                q_str = q_clean
        ans, _ = sanitize_llm_answer_response(a_str)
        if ans:
            a_str = ans
        else:
            a_clean = plain_text_for_minimal_output(a_str, field="answer")
            if a_clean:
                a_str = a_clean
        question, answer = q_str, a_str
        ev = evidence_list[i] if i < len(evidence_list) else ""
        spans, notes = _evidence_to_citation_spans(content, ev)
        if not spans and str(ev).strip():
            print(
                f"[WARN] No citation_spans resolved for Q{i + 1} "
                f"(evidence may be paraphrased)."
            )
        check_result = None
        if i < len(checks):
            entry = checks[i]
            if isinstance(entry, dict):
                check_result = entry.get("check_result")
        if check_result is None:
            check_result = grading_lookup.get(question)
        pairs.append(
            {
                "question": question,
                "answer": answer,
                "hallucination_check": check_result,
                "citation_spans": spans,
                "citation_notes": notes,
                "source_doc_id": source_doc_id,
                "source_title": source_title,
            }
        )
    return pairs


def _merge_pair_grading_from_checks(
    pairs: List[Dict[str, Any]],
    analysis_info: Optional[Dict[str, Any]],
) -> None:
    """
    Attach check_result by index so qa_pairs[].hallucination_check is set when
    grading produced hallucination_checks (handles any Q-list mismatch).
    """
    if not pairs or not analysis_info:
        return
    raw = analysis_info.get("hallucination_checks")
    if not isinstance(raw, list) or not raw:
        return
    n_pairs = len(pairs)
    n_chk = len(raw)
    if n_pairs != n_chk:
        print(
            f"[WARN] qa_pairs ({n_pairs}) vs hallucination_checks ({n_chk}) "
            "— merging grading by index up to the shorter length."
        )
    for i, pair in enumerate(pairs):
        if i >= len(raw):
            break
        entry = raw[i]
        if not isinstance(entry, dict):
            continue
        cr = entry.get("check_result")
        if cr is not None:
            pair["hallucination_check"] = cr


def _comprehensiveness_strict_enabled(config: Dict[str, Any]) -> bool:
    """When true, failed comprehensiveness slots are omitted (no answers)."""
    qcfg = config.get("question_generation")
    if not isinstance(qcfg, dict):
        return False
    val = qcfg.get("validation")
    if not isinstance(val, dict):
        return False
    return bool(val.get("comprehensiveness_strict", False))


def _slot_questions_for_pipeline(
    seed_questions: List[str],
    base_q_count: int,
    *,
    comprehensiveness_strict: bool,
) -> List[str]:
    """Questions to run through the answer/grade slot loop."""
    if comprehensiveness_strict:
        return list(seed_questions)
    if not seed_questions:
        return []
    return [
        seed_questions[i] if i < len(seed_questions) else seed_questions[-1]
        for i in range(base_q_count)
    ]


def _answer_is_insufficient(answer: Any) -> bool:
    """True when the model refused with the insufficient-info phrase."""
    return "insufficient information" in str(answer or "").strip().lower()


def _pair_passes_grounding_gate(
    pair: Dict[str, Any], min_confidence: float
) -> bool:
    """Match multi-round retry logic: grounded and confidence >= threshold."""
    # Treat explicit "Insufficient information" answers as a hard failure so
    # that the pipeline regenerates a new question for this slot instead of
    # accepting an unanswerable one.
    if _answer_is_insufficient(pair.get("answer")):
        return False

    grading = _qa_pair_hallucination_check(pair)
    if not isinstance(grading, dict):
        return False
    if grading.get("is_grounded") is not True:
        return False
    conf_val = grading.get("confidence")
    try:
        conf = float(conf_val) if conf_val is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    return conf >= min_confidence


def _slot_answer_validation_rejected(qa_result: Dict[str, Any]) -> bool:
    """True when generate_answers discarded text after failed retries."""
    meta = qa_result.get("generation_metadata")
    if not isinstance(meta, dict):
        return False
    checks = meta.get("answer_quality_checks")
    if not isinstance(checks, list) or not checks:
        return False
    first = checks[0]
    if not isinstance(first, dict):
        return False
    val = first.get("validation")
    return isinstance(val, dict) and val.get("accepted") is False


def _grading_from_answer_validation(
    qa_result: Dict[str, Any],
    question: str,
    halluc_method: str,
) -> Optional[Dict[str, Any]]:
    """Reuse answer-level judge result; skip duplicate grade_qa_results."""
    meta = qa_result.get("generation_metadata")
    if not isinstance(meta, dict):
        return None
    checks = meta.get("answer_quality_checks")
    if not isinstance(checks, list) or not checks:
        return None
    first = checks[0]
    if not isinstance(first, dict):
        return None
    val = first.get("validation")
    if not isinstance(val, dict) or val.get("accepted") is not False:
        return None
    answers = qa_result.get("answers")
    answer = ""
    if isinstance(answers, list) and answers:
        answer = str(answers[0] or "")
    try:
        conf = float(val.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    check_result: Dict[str, Any] = {
        "is_grounded": val.get("is_grounded", False),
        "confidence": conf,
        "issues": list(val.get("issues") or []),
        "method": halluc_method,
    }
    if conf >= 0.9:
        grade = "A"
    elif conf >= 0.8:
        grade = "B"
    elif conf >= 0.7:
        grade = "C"
    elif conf >= 0.6:
        grade = "D"
    else:
        grade = "F"
    return {
        "hallucination_checks": [
            {
                "question": question,
                "answer": answer,
                "check_result": check_result,
            }
        ],
        "overall_confidence": round(conf, 3),
        "overall_grade": grade,
        "grading_method": halluc_method,
    }


def _filter_pairs_and_validation_by_grounding_gate(
    pairs: List[Dict[str, Any]],
    validation: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Keep slots that pass the gate; validation entries follow pair index."""
    kept_pairs: List[Dict[str, Any]] = []
    kept_val: List[Dict[str, Any]] = []
    for i, p in enumerate(pairs):
        if not _pair_passes_grounding_gate(p, min_confidence):
            continue
        kept_pairs.append(p)
        if i < len(validation):
            kept_val.append(validation[i])
    return kept_pairs, kept_val


def _minimal_qa_pairs_for_output(
    qa_pairs_out: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    Each list item is only question + answer strings (FT JSON).
    """
    out: List[Dict[str, str]] = []
    for p in qa_pairs_out:
        if not isinstance(p, dict):
            continue
        if "question" not in p and "answer" not in p:
            continue
        q = p.get("question")
        a = p.get("answer")
        from utils.minimal_text import plain_text_for_minimal_output

        out.append(
            {
                "question": plain_text_for_minimal_output(
                    "" if q is None else str(q),
                    field="question",
                ),
                "answer": plain_text_for_minimal_output(
                    "" if a is None else str(a),
                    field="answer",
                ),
            }
        )
    return out


def _letter_grade_from_float(overall_confidence: float) -> str:
    """Match utils/hallucination_checker.grade_qa_results letter cutoffs."""
    if overall_confidence >= 0.9:
        return "A"
    if overall_confidence >= 0.8:
        return "B"
    if overall_confidence >= 0.7:
        return "C"
    if overall_confidence >= 0.6:
        return "D"
    return "F"


def _aggregate_from_checks(
    checks: List[Any],
    *,
    grounded_only: bool = True,
) -> Optional[Tuple[str, float]]:
    confs: List[float] = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        cr = c.get("check_result")
        if not isinstance(cr, dict):
            continue
        if grounded_only and cr.get("is_grounded") is not True:
            continue
        try:
            confs.append(float(cr.get("confidence") or 0.0))
        except (TypeError, ValueError):
            continue
    if not confs:
        return None
    oc = sum(confs) / len(confs)
    return _letter_grade_from_float(oc), round(oc, 3)


def _aggregate_from_pairs(
    pairs: List[Dict[str, Any]],
    *,
    grounded_only: bool = True,
) -> Optional[Tuple[str, float]]:
    confs: List[float] = []
    for p in pairs:
        g = _qa_pair_hallucination_check(p)
        if not isinstance(g, dict):
            continue
        if grounded_only and g.get("is_grounded") is not True:
            continue
        c = g.get("confidence")
        if c is None and grounded_only:
            continue
        try:
            confs.append(float(c) if c is not None else 0.0)
        except (TypeError, ValueError):
            continue
    if not confs:
        return None
    oc = sum(confs) / len(confs)
    return _letter_grade_from_float(oc), round(oc, 3)


def build_grading_summary_block(
    analysis_info: Optional[Dict[str, Any]],
    qa_pairs: List[Dict[str, Any]],
    default_method: str,
    *,
    aggregate_grounded_only: bool = True,
) -> Dict[str, Any]:
    """
    Fill grading_summary even when grade_qa_results failed or omitted fields,
    using hallucination_checks or per-pair grading.

    If aggregate_grounded_only is False, mean confidence includes failed
    pairs (used when saving the full final round after max replacement
    rounds).
    """
    ai = analysis_info or {}
    og = ai.get("overall_grade")
    oc = ai.get("overall_confidence")
    gm = ai.get("grading_method")
    jm = ai.get("judge_model")
    if og is not None and oc is not None:
        return {
            "overall_grade": og,
            "overall_confidence": oc,
            "grading_method": gm or default_method,
            "judge_model": jm,
        }
    checks = ai.get("hallucination_checks")
    if isinstance(checks, list) and checks:
        agg = _aggregate_from_checks(
            checks, grounded_only=aggregate_grounded_only
        )
        if agg:
            letter, conf = agg
            return {
                "overall_grade": letter,
                "overall_confidence": conf,
                "grading_method": gm or default_method,
                "judge_model": jm,
            }
    agg2 = _aggregate_from_pairs(
        qa_pairs, grounded_only=aggregate_grounded_only
    )
    if agg2:
        letter, conf = agg2
        return {
            "overall_grade": letter,
            "overall_confidence": conf,
            "grading_method": "average_of_each_qa_pair",
            "judge_model": (
                "N/A (overall score is the average of each QA pair)"
            ),
        }
    return {
        "overall_grade": None,
        "overall_confidence": None,
        "grading_method": gm or default_method,
        "judge_model": jm,
    }


def _is_valid_judge_verdict(verdict: Any) -> bool:
    if not isinstance(verdict, dict):
        return False
    value = str(verdict.get("verdict", "")).strip().upper()
    if value not in ("SUPPORTED", "NOT_SUPPORTED"):
        return False
    try:
        conf = float(verdict.get("confidence", 0.0))
    except (TypeError, ValueError):
        return False
    return 0.0 <= conf <= 1.0


def _preflight_llm_judge(config: Dict[str, Any]) -> None:
    """
    Validate judge endpoint/model before processing input documents.

    Strict mode requires a parseable judge verdict. This catches endpoint,
    model-name, and parser issues early so the run fails fast.
    """
    probe = {
        "id": "__judge_preflight__",
        "title": "judge_preflight",
        "content": (
            "Alpha appears in the source text. Beta is not mentioned "
            "in the source text."
        ),
        "questions": ["Does the source mention alpha?"],
        "answers": ["The source mentions alpha."],
    }
    graded = grade_qa_results([probe], method="llm")
    if not graded:
        raise RuntimeError(
            "Judge preflight failed: no grading result returned."
        )
    checks = graded[0].get("hallucination_checks")
    if not isinstance(checks, list) or not checks:
        raise RuntimeError(
            "Judge preflight failed: hallucination checks missing."
        )
    first = checks[0] if isinstance(checks[0], dict) else {}
    check_result = first.get("check_result")
    if not isinstance(check_result, dict):
        raise RuntimeError(
            "Judge preflight failed: check_result missing from grading output."
        )
    verdict = check_result.get("llm_verdict")
    if not _is_valid_judge_verdict(verdict):
        detail = (
            verdict
            if isinstance(verdict, dict)
            else {"verdict": "UNKNOWN", "reason": "no llm_verdict present"}
        )
        raise RuntimeError(
            "Judge preflight failed: invalid llm_verdict "
            f"payload ({detail})."
        )


def _probe_reply_is_ready(reply: Any) -> bool:
    """Accept plain READY or Qwen-style replies whose last line is READY."""
    text = str(reply or "").strip()
    if text.upper() == "READY":
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return bool(lines) and lines[-1].upper() == "READY"


def _preflight_llm_generator(config: Dict[str, Any]) -> None:
    """
    Validate generator endpoint/model before processing input documents.

    This keeps first-question failures (often surfaced as Ollama 500s under
    model cold-start or GPU pressure) in a clear preflight stage.
    """
    probe_prompt = "Reply with exactly: READY"
    probe_reply = _call_llm(probe_prompt, config)
    if not _probe_reply_is_ready(probe_reply):
        raise RuntimeError(
            "Generator preflight failed: unexpected model response "
            f"({probe_reply!r})."
        )


def run_pipeline(config: Dict[str, Any], settings: Dict[str, Any]) -> None:
    input_path = settings["input_file"]
    run_cfg = config.get("run") if isinstance(config.get("run"), dict) else {}
    output_provider, output_model = _resolve_output_provider_model(
        config, settings
    )
    resume_opts = _pipeline_resume_options(config, settings, run_cfg)
    skip_check_dir = None
    if resume_opts["skip_existing"]:
        skip_check_dir = resolve_resume_run_directory(
            output_provider,
            output_model,
            resume_opts["resume_run_dir"],
        )
        if skip_check_dir is None:
            print(
                "[INFO] skip_existing_outputs: no prior run folder found "
                "under output/ — nothing to skip yet.\n"
            )

    ofn = str(run_cfg.get("output_folder") or "input_basename").strip().lower()
    batch_lbl = settings.get("input_label_for_output")
    if resume_opts["resume_mode"] and skip_check_dir is not None:
        run_ts = init_run_timestamp(skip_check_dir.name)
        print(
            f"[INFO] Resume: reusing run folder {skip_check_dir}\n"
        )
    elif ofn in ("timestamp", "time", "ts", "dated"):
        if resume_opts["resume_mode"]:
            print(
                "[WARN] --resume: no prior run folder found; "
                "starting a new timestamped run.\n"
            )
        run_ts = init_run_timestamp(None)
    else:
        if batch_lbl:
            run_ts = init_run_timestamp(
                _safe_output_filename_stem(str(batch_lbl))
            )
        else:
            run_ts = init_run_timestamp(input_file_output_segment(input_path))

    print("=" * 80)
    print("Configurable Q&A Pipeline")
    print("=" * 80)
    print()
    print(f"Input file     : {input_path}")
    cfg_in_type = str(run_cfg.get("input_type") or "").strip().lower()
    suffix = Path(input_path).suffix.lower()
    if (
        cfg_in_type
        and cfg_in_type != "auto"
        and suffix in (".json", ".jsonl")
        and cfg_in_type not in ("json", "jsonl")
    ):
        print(
            "[INFO] run.input_type is ignored for a direct .json/.jsonl path; "
            "the extension selects the parser. "
            "(YAML run.input_type applies when run.auto_convert prepares "
            "pdf/txt/pptx/etc.)"
        )
    llm_cfg = config.get("llm", {}) or {}
    prov = settings.get("provider") or llm_cfg.get(
        "provider", "config default"
    )
    mdl = settings.get("model") or llm_cfg.get("model", "config default")
    print(f"Provider/model : {prov} / {mdl}")
    nd = settings["num_documents"]
    nd_disp = str(nd) if isinstance(nd, int) and nd > 0 else "all"
    print(f"Documents to run: {nd_disp}")
    print(f"Run folder      : {run_ts}")
    if resume_opts["skip_existing"]:
        if skip_check_dir is not None:
            print(f"Skip existing   : yes (check {skip_check_dir})")
        else:
            print("Skip existing   : yes (no prior run folder yet)")
    if resume_opts["resume_mode"]:
        print("Resume mode     : yes (append to run folder when it exists)")
    print("=" * 80)
    print()

    # ---- hallucination check method ----
    halluc_cfg = (
        config.get("hallucination")
        if isinstance(config.get("hallucination"), dict)
        else {}
    )
    halluc_method = str(halluc_cfg.get("method") or "hybrid").strip().lower()
    judge_required = _config_bool(halluc_cfg.get("judge_required"))
    allow_raw = halluc_cfg.get("allow_semantic_fallback")
    if allow_raw is None:
        allow_semantic_fallback = halluc_method == "hybrid"
    else:
        allow_semantic_fallback = _config_bool(allow_raw)
    if judge_required:
        allow_semantic_fallback = False
    if halluc_method in ("llm", "hybrid"):
        set_llm_config(config)
    print("Generator preflight: checking LLM generator availability...")
    _preflight_llm_generator(config)
    print("[OK] Generator preflight passed.\n")
    if halluc_method == "llm" and judge_required:
        print("Judge preflight: checking LLM judge availability...")
        _preflight_llm_judge(config)
        print("[OK] Judge preflight passed.\n")
    print(f"Halluc. method : {halluc_method}")
    print()

    raw_docs = load_data_file(input_path)
    documents = _normalize_document_list(raw_docs, input_path)
    if not documents:
        print("No documents found to process.")
        return

    if isinstance(nd, int) and nd > 0:
        documents = documents[:nd]
    print(f"[OK] Loaded {len(documents)} documents\n")

    save_grounded_only = bool(
        run_cfg.get("save_grounded_qa_pairs_only", False)
    )
    reject_insufficient = bool(
        run_cfg.get("reject_insufficient_answers", False)
    )
    if save_grounded_only:
        print(
            "[INFO] run.save_grounded_qa_pairs_only is true - "
            "saving only grounded Q&A rows.\n"
        )
    if reject_insufficient:
        print(
            "[INFO] run.reject_insufficient_answers is true - "
            "slots whose final answer is 'Insufficient information in the "
            "document.' are omitted from qa_pairs.\n"
        )

    try:
        min_content_words = int(run_cfg.get("min_content_words") or 0)
    except (TypeError, ValueError):
        min_content_words = 0
    try:
        min_content_chars = int(run_cfg.get("min_content_chars") or 0)
    except (TypeError, ValueError):
        min_content_chars = 0
    if min_content_words > 0 or min_content_chars > 0:
        parts = []
        if min_content_words > 0:
            parts.append(f"min_content_words={min_content_words}")
        if min_content_chars > 0:
            parts.append(f"min_content_chars={min_content_chars}")
        print(
            "[INFO] Document length filter active "
            f"({', '.join(parts)}) — shorter documents are skipped.\n"
        )

    minimal_qa_output = bool(run_cfg.get("minimal_qa_output", False))
    if minimal_qa_output:
        print(
            "[INFO] run.minimal_qa_output is true - "
            "saved JSON will contain document.content plus "
            "qa_pairs (question + answer per row only).\n"
        )

    skipped_short = 0
    skipped_existing = 0
    total_docs = len(documents)
    for idx, document in enumerate(documents, 1):
        doc_id = document.get("id", document.get("title", f"doc_{idx}"))

        if resume_opts["skip_existing"] and skip_check_dir is not None:
            out_stem = _expected_analysis_output_stem(
                document,
                idx,
                total_docs,
                run_cfg,
                input_path,
            )
            if analysis_output_exists(skip_check_dir, out_stem):
                skipped_existing += 1
                existing = analysis_json_path(skip_check_dir, out_stem)
                print("=" * 80)
                print(
                    f"Skipping Document {idx}/{total_docs}: {doc_id} "
                    "(analysis already exists)"
                )
                print("=" * 80)
                print(f"[INFO] Existing output: {existing}\n")
                continue

        skip_reason = _document_skip_reason_for_min_length(document, run_cfg)
        if skip_reason:
            skipped_short += 1
            print("=" * 80)
            print(f"Skipping Document {idx}/{len(documents)}: {doc_id}")
            print("=" * 80)
            print(f"[WARN] {skip_reason}\n")
            continue

        print("=" * 80)
        print(f"Processing Document {idx}/{len(documents)}: {doc_id}")
        print("=" * 80)
        print()

        print("DOCUMENT CONTENT:")
        print("-" * 80)
        print(_document_plain_text(document) or "(no extractable text)")
        print()

        qcfg = (config.get("question_generation") or {})
        base_q_count = int(qcfg.get("num_questions", 3) or 3)
        mt_cfg = (config.get("answer_generation") or {}).get("multi_turn", {})
        max_q_rounds = int(mt_cfg.get("max_question_regeneration_rounds", 3))
        min_conf = float(mt_cfg.get("min_confidence_threshold", 0.7) or 0.7)

        total_qtime = 0.0
        total_atime = 0.0
        total_gtime = 0.0
        final_pairs: List[Dict[str, Any]] = []
        base_question_metadata: Dict[str, Any] = {}
        last_answer_metadata: Dict[str, Any] = {}
        slot_question_validation: List[Dict[str, Any]] = []

        print(
            f"Generating initial question set (target={base_q_count})..."
        )
        base_config = deepcopy(config)
        base_qcfg = (
            base_config.get("question_generation")
            if isinstance(base_config.get("question_generation"), dict)
            else {}
        )
        base_qcfg["num_questions"] = base_q_count
        base_config["question_generation"] = base_qcfg

        start_time = time.time()
        base_question_results = generate_questions(
            [document], config=base_config
        )
        qtime = time.time() - start_time
        total_qtime += qtime
        if not base_question_results:
            print(
                f"[WARN] No questions generated for {doc_id}; "
                "skipping document.\n"
            )
            continue

        base_question_result = base_question_results[0]
        seed_questions = list(base_question_result.get("questions", []))
        if not seed_questions:
            print(
                f"[WARN] Empty initial questions for {doc_id}; "
                "skipping document.\n"
            )
            continue
        base_question_metadata = dict(
            base_question_result.get("generation_metadata", {})
        )
        comp_strict = _comprehensiveness_strict_enabled(config)
        slot_questions = _slot_questions_for_pipeline(
            seed_questions,
            base_q_count,
            comprehensiveness_strict=comp_strict,
        )
        if comp_strict and len(slot_questions) < base_q_count:
            dropped = base_q_count - len(slot_questions)
            print(
                f"[INFO] Comprehensiveness strict: {dropped} question(s) "
                "rejected; "
                f"{len(slot_questions)} slot(s) will receive answers."
            )
        print(f"[OK] Questions ready in {qtime:.1f} seconds\n")

        slot_total = len(slot_questions)
        for slot_idx, current_question in enumerate(slot_questions):
            slot_pair: Optional[Dict[str, Any]] = None
            current_question_metadata = dict(base_question_metadata)

            print(
                f"[INFO] Slot {slot_idx + 1}/{slot_total}: "
                "answer trials for current question."
            )
            for replace_idx in range(max_q_rounds + 1):
                question_result = {
                    **document,
                    "questions": [current_question],
                    "generation_metadata": current_question_metadata,
                }

                start_time = time.time()
                qa_result = generate_answers(
                    questions=[current_question],
                    document=document,
                    config=config,
                )
                atime = time.time() - start_time
                total_atime += atime
                last_answer_metadata = dict(
                    qa_result.get("generation_metadata", {})
                )

                print(
                    f"[OK] Slot {slot_idx + 1} answer ready in "
                    f"{atime:.1f} seconds"
                )

                analysis_info = None
                gtime = 0.0
                if _slot_answer_validation_rejected(qa_result):
                    analysis_info = _grading_from_answer_validation(
                        qa_result,
                        str(current_question or ""),
                        halluc_method,
                    )
                try:
                    if analysis_info is None:
                        t_grade = time.time()
                        # generate_answers() omits document body; grading
                        # needs content/text for semantic + hybrid judges.
                        grading_payload = {**document, **qa_result}
                        graded_results = grade_qa_results(
                            [grading_payload], method=halluc_method
                        )
                        if not graded_results:
                            raise RuntimeError(
                                "grade_qa_results returned no results"
                            )
                        analysis_info = graded_results[0]
                        gtime = time.time() - t_grade
                except Exception as exc:
                    print(
                        f"[WARN] Could not grade {doc_id} "
                        f"({halluc_method}): {exc}"
                    )
                    if not allow_semantic_fallback:
                        raise RuntimeError(
                            "Grading failed and semantic fallback is disabled "
                            f"(method={halluc_method}): {exc}"
                        ) from exc
                    if halluc_method in ("hybrid", "llm"):
                        try:
                            t_grade = time.time()
                            grading_payload = {**document, **qa_result}
                            graded_results = grade_qa_results(
                                [grading_payload], method="keyword"
                            )
                            if graded_results:
                                analysis_info = graded_results[0]
                                gtime = time.time() - t_grade
                        except Exception as exc2:
                            print(
                                "[WARN] Keyword fallback grading failed: "
                                f"{exc2}"
                            )
                total_gtime += gtime

                pair_list = build_qa_pairs(
                    question_result,
                    qa_result,
                    analysis_info or {},
                    document,
                    idx,
                )
                _merge_pair_grading_from_checks(pair_list, analysis_info)
                ev_for_alignment = ""
                raw_ev_list = qa_result.get("supporting_evidence")
                if isinstance(raw_ev_list, list) and raw_ev_list:
                    ev_for_alignment = raw_ev_list[0]
                if pair_list:
                    slot_pair = pair_list[0]
                    _apply_citation_alignment_to_pair(
                        slot_pair,
                        ev_for_alignment,
                        halluc_cfg,
                    )
                    gw_mode = str(
                        halluc_cfg.get(
                            "grounding_explanation_when_no_citations",
                            "off",
                        )
                        or "off"
                    )
                    apply_grounding_why_when_no_citations(
                        slot_pair,
                        _document_plain_text(document),
                        str(current_question or ""),
                        gw_mode,
                    )
                else:
                    slot_pair = {
                        "question": current_question,
                        "answer": "(Answer generation failed)",
                        "hallucination_check": None,
                        "citation_spans": [],
                        "citation_notes": [],
                        "source_doc_id": str(doc_id),
                        "source_title": str(document.get("title") or doc_id),
                    }

                if _pair_passes_grounding_gate(slot_pair, min_conf):
                    print(
                        f"[OK] Slot {slot_idx + 1}: passed grounding gate."
                    )
                    break

                if replace_idx >= max_q_rounds:
                    print(
                        f"[WARN] Slot {slot_idx + 1}: max question "
                        "replacements reached; slot omitted from qa_pairs "
                        "if still ungrounded."
                    )
                    break

                print(
                    f"[INFO] Slot {slot_idx + 1}: failed gate; generating "
                    "replacement question."
                )
                replace_config = deepcopy(config)
                replace_qcfg = (
                    replace_config.get("question_generation")
                    if isinstance(
                        replace_config.get("question_generation"), dict
                    )
                    else {}
                )
                replace_qcfg["num_questions"] = 1
                replace_config["question_generation"] = replace_qcfg
                t_q = time.time()
                repl_results = generate_questions(
                    [document], config=replace_config
                )
                qtime = time.time() - t_q
                total_qtime += qtime
                if not repl_results:
                    print(
                        f"[WARN] Slot {slot_idx + 1}: replacement question "
                        "generation failed."
                    )
                    break
                repl_questions = repl_results[0].get("questions", [])
                if not repl_questions:
                    print(
                        f"[WARN] Slot {slot_idx + 1}: replacement question "
                        "empty."
                    )
                    break
                current_question = repl_questions[0]
                current_question_metadata = dict(
                    repl_results[0].get("generation_metadata", {})
                )

            if slot_pair is not None:
                picked = _pick_question_validation_detail(
                    current_question_metadata,
                    slot_idx,
                    current_question,
                )
                if isinstance(picked, dict):
                    detail = dict(picked)
                    detail["question_index"] = slot_idx + 1
                    detail["final_question"] = current_question
                    if (
                        reject_insufficient
                        and _answer_is_insufficient(slot_pair.get("answer"))
                    ):
                        detail["accepted"] = False
                        detail["rejection_reason"] = (
                            "insufficient_information_answer"
                        )
                    slot_question_validation.append(detail)

                if (
                    reject_insufficient
                    and _answer_is_insufficient(slot_pair.get("answer"))
                ):
                    print(
                        f"[INFO] Slot {slot_idx + 1}: "
                        "insufficient-information answer rejected; "
                        "kept in qa_pairs (will be tagged bad in "
                        "minimise-bad)."
                    )
                    final_pairs.append(slot_pair)
                elif _pair_passes_grounding_gate(slot_pair, min_conf):
                    final_pairs.append(slot_pair)
                else:
                    print(
                        f"[INFO] Slot {slot_idx + 1}: failed grounding "
                        "gate; kept in qa_pairs (for minimise-bad)."
                    )
                    final_pairs.append(slot_pair)

        if not final_pairs:
            print(
                f"[WARN] No QA pairs produced for {doc_id}; "
                "skipping document.\n"
            )
            continue

        trim_limit = len(slot_questions) if comp_strict else base_q_count
        trimmed_pairs = list(final_pairs[:trim_limit])
        if save_grounded_only:
            qa_pairs_out, val_f = (
                _filter_pairs_and_validation_by_grounding_gate(
                    trimmed_pairs,
                    slot_question_validation,
                    min_conf,
                )
            )
            slot_question_validation = val_f
            if not qa_pairs_out:
                print(
                    f"[WARN] save_grounded_qa_pairs_only: no grounded "
                    f"pairs for {doc_id}; skipping save.\n"
                )
                continue
        else:
            qa_pairs_out = trimmed_pairs
            if not comp_strict:
                while len(qa_pairs_out) < base_q_count:
                    qa_pairs_out.append(
                        {
                            "question": "(No question)",
                            "answer": "(No answer)",
                            "hallucination_check": None,
                            "citation_spans": [],
                            "citation_notes": [],
                            "source_doc_id": str(doc_id),
                            "source_title": str(
                                document.get("title") or doc_id
                            ),
                        }
                    )

        # Saved JSON name (no redundant doc_*doc* doubling from old scheme).
        stem_mode = str(
            run_cfg.get("output_analysis_stem") or "document_id"
        ).strip().lower()
        multi = len(documents) > 1
        if stem_mode in ("input_file", "input", "file", "filename"):
            base = _safe_output_filename_stem(Path(input_path).stem)
            if multi:
                out_stem = f"{base}_{idx:04d}_analysis"
            else:
                out_stem = f"{base}_analysis"
        else:
            base = _safe_output_filename_stem(str(doc_id))
            if multi:
                out_stem = f"{base}_{idx:04d}_analysis"
            else:
                out_stem = f"{base}_analysis"
        # Metadata: answer_metadata or merged generation_metadata
        mm = last_answer_metadata
        answer_gen_metadata = {
            "model": mm.get("answer_model", mm.get("model")),
            "provider": mm.get("answer_provider", mm.get("provider")),
            "timestamp": mm.get("answer_timestamp", mm.get("timestamp")),
            "timezone": mm.get(
                "answer_timezone",
                mm.get("timezone", "Asia/Singapore"),
            ),
            "num_answers": len(qa_pairs_out),
        }

        grading_summary = build_grading_summary_block(
            {
                "grading_method": halluc_method,
                "judge_model": None,
                "overall_grade": None,
                "overall_confidence": None,
            },
            qa_pairs_out,
            halluc_method,
            aggregate_grounded_only=False,
        )

        question_metadata = dict(base_question_metadata)
        question_metadata["num_questions"] = len(qa_pairs_out)
        if slot_question_validation:
            question_metadata["question_validation"] = (
                slot_question_validation
            )

        if minimal_qa_output:
            combined_result = {
                "document": _minimal_document_for_output(
                    document, doc_id
                ),
                "qa_pairs": _minimal_qa_pairs_for_output(qa_pairs_out),
            }
        else:
            combined_result = {
                "document": _snapshot_document_for_output(
                    document, doc_id
                ),
                "qa_pairs": qa_pairs_out,
                "question_generation": question_metadata,
                "answer_generation": answer_gen_metadata,
                "grading_summary": grading_summary,
                "run_metrics": {
                    "timings_seconds": {
                        "question_generation": round(total_qtime, 3),
                        "answer_generation": round(total_atime, 3),
                        "grading": round(total_gtime, 3),
                    },
                    "quality_counters": {},
                },
            }

        # Provider/model: settings, then question metadata, then config
        provider = (
            settings.get("provider")
            or question_metadata.get("provider")
            or config.get("llm", {}).get("provider", "openai")
        )
        model = (
            settings.get("model")
            or question_metadata.get("model")
            or config.get("llm", {}).get("model", "gpt-4")
        )

        print(
            f"[INFO] Saving results with provider: {provider}, "
            f"model: {model}"
        )

        combined_path = save_results(
            combined_result,
            provider=output_provider,
            model=model,
            output_type=out_stem,
            use_timestamp=True,
        )
        print(f"[OK] Saved combined analysis to: {combined_path}\n")

    print("=" * 80)
    if skipped_short:
        print(
            f"[INFO] Skipped {skipped_short} document(s) below "
            "run.min_content_words / min_content_chars."
        )
    if skipped_existing:
        print(
            f"[INFO] Skipped {skipped_existing} document(s) with existing "
            "*_analysis.json in the resume check folder."
        )
    print("[OK] All documents processed!")
    print("=" * 80)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configurable Q&A pipeline runner"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to configuration YAML "
            "(default: config/config.<profile>.yaml from QAGREDO_PROFILE; "
            "defaults to ollama when unset)"
        ),
    )
    parser.add_argument(
        "--input-file",
        default=None,
        metavar="PATH",
        help=(
            "Override run.input_file (and skip run.input_folder): "
            ".json / .jsonl under repo or data/"
        ),
    )
    parser.add_argument(
        "--num-documents",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Override run.num_documents (max records to process; "
            "0 = all loaded)."
        ),
    )
    parser.add_argument(
        "--minimal-qa-output",
        action="store_true",
        help=(
            "Set run.minimal_qa_output true: saved JSON is only "
            "document.content plus qa_pairs (question/answer per row)."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Reuse the latest run folder (or run.resume_run_dir) and skip "
            "documents that already have *_analysis.json there."
        ),
    )
    parser.add_argument(
        "--skip-existing-outputs",
        action="store_true",
        help=(
            "Skip documents whose *_analysis.json already exists in the "
            "latest run folder (or run.resume_run_dir). Writes new outputs "
            "to a new run folder unless --resume is also set."
        ),
    )
    parser.add_argument(
        "--resume-run-dir",
        default=None,
        metavar="DIR",
        help=(
            "Run folder for --resume / skip checks: path, folder name under "
            "output/<provider>/<model>/, or 'latest' (default when omitted)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = (
        args.config if args.config is not None else default_config_path()
    )
    effective_config = build_effective_config(config_path)
    run_cfg = effective_config.get("run", {}) or {}
    num_docs = args.num_documents
    if num_docs is None:
        raw_num_docs = run_cfg.get("num_documents", 2)
        try:
            num_docs = int(raw_num_docs)
        except (TypeError, ValueError):
            print(
                "[WARN] run.num_documents is invalid "
                f"({raw_num_docs!r}); defaulting to 2."
            )
            num_docs = 2
    cli_in = args.input_file
    settings: Dict[str, Any] = {
        "input_file": "",
        "num_documents": num_docs,
        "provider": run_cfg.get("provider"),
        "model": run_cfg.get("model"),
        "input_label_for_output": None,
    }

    if cli_in is not None and str(cli_in).strip():
        settings["input_file"] = _prepare_jsonl_input_if_needed(
            str(cli_in).strip(),
            run_cfg,
            effective_config,
        )
    else:
        raw_cfg = str(run_cfg.get("input_file") or "").strip()
        folder = str(run_cfg.get("input_folder") or "").strip()
        if raw_cfg:
            settings["input_file"] = _prepare_jsonl_input_if_needed(
                raw_cfg,
                run_cfg,
                effective_config,
            )
        elif folder:
            merged, label = _merge_input_folder_to_jsonl(
                run_cfg,
                effective_config,
            )
            settings["input_file"] = merged
            settings["input_label_for_output"] = label
        else:
            settings["input_file"] = _prepare_jsonl_input_if_needed(
                "dev-data.jsonl",
                run_cfg,
                effective_config,
            )

    # Optional run-level overrides (if present) should override llm defaults.
    provider_override = settings.get("provider")
    model_override = settings.get("model")
    if provider_override or model_override:
        effective_config = build_effective_config(
            config_path,
            provider_override=provider_override,
            model_override=model_override,
        )
    if args.minimal_qa_output:
        run_block = effective_config.get("run")
        if not isinstance(run_block, dict):
            effective_config["run"] = {"minimal_qa_output": True}
        else:
            run_block["minimal_qa_output"] = True
    if args.resume:
        settings["resume"] = True
    if args.skip_existing_outputs:
        settings["skip_existing_outputs"] = True
    if args.resume_run_dir is not None and str(args.resume_run_dir).strip():
        settings["resume_run_dir"] = str(args.resume_run_dir).strip()
    run_pipeline(effective_config, settings)


if __name__ == "__main__":
    main()
