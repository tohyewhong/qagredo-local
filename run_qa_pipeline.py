"""Run question & answer generation sequentially using configurable settings."""

# CRITICAL: Set this BEFORE any imports to prevent Pydantic plugin loading issues
import os
os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from utils import (
    grade_qa_results,
    load_data_file,
    print_grading_report,
    generate_answers_from_results,
    generate_questions,
    save_results,
)
from utils.output_manager import init_run_timestamp
from utils.hallucination_checker import set_llm_config
from utils.config_manager import (
    build_effective_config,
)

sys.stdout.reconfigure(line_buffering=True)

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
    if "llama" in model_l or "meta-llama" in model_l:
        return "1"
    # Fallback: keep provider name to avoid collisions.
    return provider_l or "unknown"


def _get_selected_profile_id(config: Dict[str, Any]) -> str | None:
    run_cfg = config.get("run", {}) if isinstance(config.get("run", {}), dict) else {}
    profile = run_cfg.get("profile")
    if profile is None:
        return None
    profile_str = str(profile).strip()
    return profile_str or None


def build_qa_pairs(question_result: Dict[str, Any], qa_result: Dict[str, Any], grading: Dict[str, Any]) -> List[Dict[str, Any]]:
    grading_lookup = {}
    if grading:
        for check in grading.get("hallucination_checks", []):
            grading_lookup[check.get("question")] = check.get("check_result")

    pairs = []
    for question, answer in zip(question_result.get("questions", []), qa_result.get("answers", [])):
        pairs.append(
            {
                "question": question,
                "answer": answer,
                "grading": grading_lookup.get(question),
            }
        )
    return pairs


def run_pipeline(config: Dict[str, Any], settings: Dict[str, Any]) -> None:
    # Lock the run timestamp so all output files go into the same folder
    run_ts = init_run_timestamp()

    input_path = settings["input_file"]
    print("=" * 80)
    print("Configurable Q&A Pipeline")
    print("=" * 80)
    print()
    print(f"Input file     : {input_path}")
    print(
        "Provider/model : "
        f"{settings.get('provider') or config.get('llm', {}).get('provider', 'config default')} / "
        f"{settings.get('model') or config.get('llm', {}).get('model', 'config default')}"
    )
    print(f"Documents to run: {settings['num_documents']}")
    print(f"Run folder      : {run_ts}")
    print("=" * 80)
    print()

    # ---- hallucination check method ----
    halluc_method = config.get("hallucination", {}).get("method", "hybrid")
    if halluc_method in ("llm", "hybrid"):
        set_llm_config(config)
    print(f"Halluc. method : {halluc_method}")
    print()

    documents = load_data_file(input_path)
    if not documents:
        print("No documents found to process.")
        return

    documents = documents[: settings["num_documents"]]
    print(f"[OK] Loaded {len(documents)} documents\n")

    for idx, document in enumerate(documents, 1):
        doc_id = document.get("id", document.get("title", f"doc_{idx}"))
        safe_doc_id = str(doc_id).replace(" ", "_")

        print("=" * 80)
        print(f"Processing Document {idx}/{len(documents)}: {doc_id}")
        print("=" * 80)
        print()

        print("DOCUMENT CONTENT:")
        print("-" * 80)
        print(document.get("content", ""))
        print()

        print("Generating questions...")
        start_time = time.time()
        question_results = generate_questions([document], config=config)
        if not question_results:
            print(f"[WARN] No questions generated for {doc_id}; skipping document.\n")
            continue
        question_result = question_results[0]
        print(f"[OK] Questions ready in {time.time() - start_time:.1f} seconds\n")

        print("GENERATED QUESTIONS:")
        print("-" * 80)
        for q_idx, question in enumerate(question_result.get("questions", []), 1):
            print(f"{q_idx}. {question}")
        print()

        print("Generating answers...")
        start_time = time.time()
        qa_results = generate_answers_from_results([question_result], config=config)
        if not qa_results:
            print(f"[WARN] No answers generated for {doc_id}; skipping document.\n")
            continue
        qa_result = qa_results[0]
        print(f"[OK] Answers ready in {time.time() - start_time:.1f} seconds\n")

        print("QUESTION & ANSWER PAIRS:")
        print("-" * 80)
        for q_idx, (question, answer) in enumerate(
            zip(qa_result.get("questions", []), qa_result.get("answers", [])), 1
        ):
            print(f"\nQ{q_idx}. {question}")
            print(f"A{q_idx}. {answer}")
        print()

        print(f"Grading for Hallucination (method={halluc_method})...")
        analysis_info = None
        try:
            graded_results = grade_qa_results([qa_result], method=halluc_method)
            analysis_info = graded_results[0]
            print_grading_report(graded_results)
        except Exception as exc:
            print(f"[WARN] Could not grade {doc_id}: {exc}")

        suffix = f"{safe_doc_id}_doc{idx}"
        # Extract answer generation metadata (may be in answer_metadata or generation_metadata)
        answer_gen_metadata = qa_result.get("answer_metadata", {})
        if not answer_gen_metadata:
            # Fallback: extract answer fields from merged generation_metadata
            merged_meta = qa_result.get("generation_metadata", {})
            answer_gen_metadata = {
                "model": merged_meta.get("answer_model", merged_meta.get("model")),
                "provider": merged_meta.get("answer_provider", merged_meta.get("provider")),
                "timestamp": merged_meta.get("answer_timestamp", merged_meta.get("timestamp")),
                "timezone": merged_meta.get("answer_timezone", merged_meta.get("timezone", "Asia/Singapore")),
                "num_answers": merged_meta.get("num_answers", len(qa_result.get("answers", [])))
            }
        
        combined_result = {
            "document": {
                "id": doc_id,
                "title": document.get("title"),
                "source": document.get("source"),
                "type": document.get("type"),
                "content": document.get("content"),
            },
            "qa_pairs": build_qa_pairs(question_result, qa_result, analysis_info or {}),
            "question_generation": question_result.get("generation_metadata", {}),
            "answer_generation": answer_gen_metadata,
            "grading_summary": {
                "overall_grade": (analysis_info or {}).get("overall_grade"),
                "overall_confidence": (analysis_info or {}).get("overall_confidence"),
                "grading_method": (analysis_info or {}).get("grading_method"),
                "judge_model": (analysis_info or {}).get("judge_model"),
            },
        }

        # Determine provider and model from settings, metadata, or config (in that order)
        provider = (
            settings.get("provider") 
            or combined_result["question_generation"].get("provider") 
            or config.get("llm", {}).get("provider", "openai")
        )
        model = (
            settings.get("model") 
            or combined_result["question_generation"].get("model") 
            or config.get("llm", {}).get("model", "gpt-4")
        )
        
        print(f"[INFO] Saving results with provider: {provider}, model: {model}")

        # Optional output naming scheme (does not affect which provider/model is used for LLM calls).
        output_cfg = (config.get("output") or {}) if isinstance(config, dict) else {}
        selected_profile_id = _get_selected_profile_id(config)

        # If user selected a profile, default to routing outputs by that profile id.
        # This can be overridden by explicitly setting output.scheme.
        if "scheme" in output_cfg:
            output_scheme = str(output_cfg.get("scheme", "default")).lower()
        else:
            output_scheme = "profile" if selected_profile_id else "default"

        output_provider = provider
        if output_scheme in {"profile", "profiles", "profile_id", "profile-id"} and selected_profile_id:
            output_provider = selected_profile_id
        elif output_scheme in {"numeric", "numeric_profile", "numeric-profiles"}:
            output_provider = _infer_numeric_output_profile(provider=provider, model=model)
        # else: keep provider/model scheme (default)
        
        combined_path = save_results(
            combined_result,
            provider=output_provider,
            model=model,
            output_type=f"doc_{suffix}_analysis",
            use_timestamp=True,
        )
        print(f"[OK] Saved combined analysis to: {combined_path}\n")

    print("=" * 80)
    print("[OK] All documents processed!")
    print("=" * 80)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configurable Q&A pipeline runner")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to configuration YAML (default: config/config.yaml)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    effective_config = build_effective_config(args.config)
    run_cfg = effective_config.get("run", {}) or {}
    settings = {
        "input_file": run_cfg.get("input_file", "dev-data.jsonl"),
        "num_documents": int(run_cfg.get("num_documents", 2)),
        # Keep these for output naming / display (optional).
        "provider": run_cfg.get("provider"),
        "model": run_cfg.get("model"),
    }

    # Optional run-level overrides (if present) should override llm defaults.
    provider_override = settings.get("provider")
    model_override = settings.get("model")
    if provider_override or model_override:
        effective_config = build_effective_config(
            args.config,
            provider_override=provider_override,
            model_override=model_override,
        )
    run_pipeline(effective_config, settings)


if __name__ == "__main__":
    main()

