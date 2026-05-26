"""Tests for resume / skip-existing output helpers."""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class ResumeSkipOutputsTest(unittest.TestCase):
    def setUp(self) -> None:
        import utils.output_manager as om

        self.om = importlib.reload(om)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "output"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_find_latest_run_directory(self) -> None:
        prov, mdl = self.om.normalize_provider_model_segments(
            "vllm", "Qwen/Qwen3.5-9B"
        )
        pm = self.base / prov / mdl
        (pm / "2026-05-20_100000").mkdir(parents=True)
        (pm / "2026-05-21_100000").mkdir(parents=True)
        latest = self.om.find_latest_run_directory(
            "vllm", "Qwen/Qwen3.5-9B", base_dir=self.base
        )
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.name, "2026-05-21_100000")

    def test_analysis_output_exists_exact(self) -> None:
        run_dir = self.base / "ollama" / "qwen3-5-9b" / "2026-05-26_120000"
        run_dir.mkdir(parents=True)
        (run_dir / "doc_a_analysis.json").write_text("{}", encoding="utf-8")
        self.assertTrue(
            self.om.analysis_output_exists(run_dir, "doc_a_analysis")
        )
        self.assertFalse(
            self.om.analysis_output_exists(run_dir, "doc_b_analysis")
        )

    def test_expected_stem_matches_save_logic(self) -> None:
        import run_qa_pipeline as rqp

        doc = {"id": "my_doc", "title": "My Doc"}
        run_cfg = {"output_analysis_stem": "document_id"}
        stem = rqp._expected_analysis_output_stem(
            doc, 1, 3, run_cfg, "/data/batch.jsonl"
        )
        self.assertEqual(stem, "my_doc_0001_analysis")

    def test_resolve_resume_run_dir_by_segment(self) -> None:
        pm = self.base / "vllm" / "m"
        run_dir = pm / "custom_run"
        run_dir.mkdir(parents=True)
        got = self.om.resolve_resume_run_directory(
            "vllm", "m", "custom_run", base_dir=self.base
        )
        self.assertEqual(got, run_dir)


if __name__ == "__main__":
    unittest.main()
