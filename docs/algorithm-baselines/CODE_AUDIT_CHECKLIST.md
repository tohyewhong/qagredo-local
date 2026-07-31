# Algorithm documentation — code audit checklist

Walk **every row** before creating a baseline. Record results in
`docs/algorithm-baselines/vN/code_audit.json`.

## Checklist

| # | Doc target | Code to read | Confirm |
|---|------------|--------------|---------|
| 1 | `ALGORITHM_REPORT.md` §1 Pipeline / input prep | `run_qa_pipeline.py` (`_prepare_jsonl_input_if_needed`, loader by extension), `scripts/conversion/convert_to_qag_jsonl.py` | `run.input_file` wiring; converter CLI vs YAML keys not passed; fast-path rules |
| 2 | `ALGORITHM_REPORT.md` §2 Question generation | `utils/question_generator.py`, `utils/duplicate_detector.py` | Prompt types, dedup, comprehensiveness, **answerability**, grounding validation, retries |
| 3 | `ALGORITHM_REPORT.md` §3 + §3.4 slot loop | `utils/answer_generator.py`, `run_qa_pipeline.py` (`_process_one_document`, `_pair_passes_grounding_gate`, `evaluate_question_answerability`, `_synthetic_unanswerable_slot_pair`, `build_qa_pairs`) | Answerability pre-check, retries, replacement questions, `answerability_strict`, gate thresholds |
| 4 | `ALGORITHM_REPORT.md` §4 Grading | `utils/hallucination_checker.py`, `run_qa_pipeline.py` (`_preflight_llm_judge`, `build_grading_summary_block`) | Strict LLM judge default; fail-fast; legacy semantic/hybrid status |
| 5 | `ALGORITHM_REPORT.md` §5 Output schema | `utils/output_manager.py`, `run_qa_pipeline.py` (`_snapshot_document_for_output`, `_minimal_qa_pairs_for_output`) | `*_analysis.json` fields, evidence/citation mapping, minimise paths |
| 6 | `ALGORITHM_REPORT.md` §7 End-to-end flow | `run_qa_pipeline.py` (`_process_one_document`), `utils/langgraph_pipeline.py` | **Runtime orchestrator is the slot loop in `run_qa_pipeline.py`**, not LangGraph; `run_document_graph()` exists but is unwired |
| 7 | `ALGORITHM_REPORT.md` §10 Configuration | `utils/config_manager.py`, `config/config.*.yaml` | Documented YAML keys exist; defaults match at least one profile |
| 8 | `HANDOVER.md` Code map | Same utils + `run.sh` | Paths and CLI flags in the table are accurate |
| 9 | `HANDOVER.md` Release checks | `tests/test_*.py` (slot, grounding, resume) | Described behavior matches test expectations |
| 10 | Diagram sources | Same code as rows 1–7 | `.dot`/`.puml`/`.svg` match current pipeline and grading entry points |

## Confirmation tests

Run before snapshot:

```bash
python3 scripts/verify_docs_links.py
python3 -m unittest discover -s tests -p 'test_*.py'
```

Minimum subset when full suite is too slow:

```bash
python3 -m unittest \
  tests.test_slot_pipeline_smoke \
  tests.test_reject_ungrounded_after_retries \
  tests.test_reject_insufficient_answers \
  tests.test_answerability_check \
  tests.test_comprehensiveness_strict \
  tests.test_strict_llm_judge \
  tests.test_grounding_why \
  tests.test_save_grounded_qa_filter \
  tests.test_backend_selection \
  tests.test_auto_convert_prepare
```

**Do not snapshot** if `verify_docs_links.py` fails or any confirmation test
fails. Fix docs or code, then retry.

## code_audit.json template

```json
{
  "audited_at": "2026-06-26T12:00:00Z",
  "git_commit": null,
  "checklist_rows_completed": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  "code_files_read": ["run_qa_pipeline.py", "utils/question_generator.py"],
  "doc_edits": [
    {
      "file": "docs/ALGORITHM_REPORT.md",
      "sections": ["§3.4"],
      "summary": "Aligned gate threshold with _pair_passes_grounding_gate"
    }
  ],
  "verify_docs_links": "pass",
  "tests_run": ["verify_docs_links", "unittest discover"],
  "tests_passed": true
}
```
