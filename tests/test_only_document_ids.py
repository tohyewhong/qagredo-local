"""Tests for re-running only selected document ids."""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class OnlyDocumentIdsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rqp = importlib.reload(
            importlib.import_module("run_qa_pipeline")
        )

    def test_resolve_only_document_ids_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ids_path = Path(tmp) / "ids.txt"
            ids_path.write_text(
                "# comment\nfoo\nbar\n",
                encoding="utf-8",
            )
            got = self.rqp._resolve_only_document_ids(
                {"only_document_ids_file": str(ids_path)},
                {},
            )
            self.assertEqual(got, {"foo", "bar"})

    def test_precheck_skips_existing_unless_reprocess_id(self) -> None:
        import utils.output_manager as om

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "doc_a_0001_analysis.json").write_text(
                "{}", encoding="utf-8"
            )
            doc = {"id": "doc_a", "title": "Doc A", "content": "x" * 300}
            resume_opts = {"skip_existing": True}
            run_cfg = {"output_analysis_stem": "document_id"}
            kind = self.rqp._document_precheck_skip_kind(
                idx=1,
                document=doc,
                total_docs=1,
                run_cfg=run_cfg,
                input_path="/data/batch.jsonl",
                resume_opts=resume_opts,
                skip_check_dir=run_dir,
            )
            self.assertEqual(kind, "skipped_existing")
            kind2 = self.rqp._document_precheck_skip_kind(
                idx=1,
                document=doc,
                total_docs=1,
                run_cfg=run_cfg,
                input_path="/data/batch.jsonl",
                resume_opts=resume_opts,
                skip_check_dir=run_dir,
                reprocess_document_ids={"doc_a"},
            )
            self.assertIsNone(kind2)

    def test_pair_failure_reason_from_judge_issues(self) -> None:
        pair = {
            "answer": "wrong value",
            "hallucination_check": {
                "is_grounded": False,
                "confidence": 0.0,
                "issues": ["LLM judge: document does not provide X."],
            },
        }
        reason = self.rqp._pair_failure_reason(pair)
        self.assertIn("document does not provide", reason)

    def test_empty_answer_fails_grounding_gate(self) -> None:
        pair = {
            "answer": "",
            "hallucination_check": {
                "is_grounded": True,
                "confidence": 1.0,
            },
        }
        self.assertFalse(self.rqp._pair_passes_grounding_gate(pair, 0.7))


if __name__ == "__main__":
    unittest.main()
