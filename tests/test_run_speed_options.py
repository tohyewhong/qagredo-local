"""Tests for pipeline speed / resume workflow options."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")


class BuildDocumentWorkQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        import run_qa_pipeline as rqp

        self.rqp = rqp
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run"
        self.run_dir.mkdir()
        (self.run_dir / "doc_a_0001_analysis.json").write_text(
            "{}", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_prefilter_drops_existing_and_short(self) -> None:
        docs = [
            {"id": "doc_a", "content": "x " * 300},
            {"id": "doc_b", "content": "short"},
            {"id": "doc_c", "content": "y " * 300},
        ]
        run_cfg = {
            "output_analysis_stem": "document_id",
            "min_content_words": 250,
            "min_content_chars": 0,
        }
        items, short_n, exist_n, before_n = (
            self.rqp._build_document_work_queue(
                docs,
                total_docs=3,
                run_cfg=run_cfg,
                input_path="/data/batch.jsonl",
                resume_opts={
                    "skip_existing": True,
                    "resume_mode": True,
                },
                skip_check_dir=self.run_dir,
                prefilter_skips=True,
                quiet_skips=True,
                start_at_document=1,
            )
        )
        self.assertEqual(exist_n, 1)
        self.assertEqual(short_n, 1)
        self.assertEqual(before_n, 0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][1]["id"], "doc_c")

    def test_start_at_document_slices_before_workers(self) -> None:
        docs = [
            {"id": "d1", "content": "z " * 300},
            {"id": "d2", "content": "z " * 300},
        ]
        run_cfg = {
            "output_analysis_stem": "document_id",
            "min_content_words": 0,
            "min_content_chars": 0,
        }
        items, _, _, before_n = self.rqp._build_document_work_queue(
            docs,
            total_docs=2,
            run_cfg=run_cfg,
            input_path="/data/batch.jsonl",
            resume_opts={"skip_existing": False, "resume_mode": False},
            skip_check_dir=None,
            prefilter_skips=False,
            quiet_skips=False,
            start_at_document=2,
        )
        self.assertEqual(before_n, 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][1]["id"], "d2")


class ResolveSpeedSettingsTest(unittest.TestCase):
    def test_resolve_bool_setting_cli_over_config(self) -> None:
        import run_qa_pipeline as rqp

        self.assertTrue(
            rqp._resolve_bool_setting(
                {"skip_preflight": True},
                {"skip_preflight": False},
                "skip_preflight",
            )
        )
        self.assertTrue(
            rqp._resolve_bool_setting(
                {},
                {},
                "prefilter_skips",
                default=True,
            )
        )

    def test_start_at_document_minimum_one(self) -> None:
        import run_qa_pipeline as rqp

        self.assertEqual(
            rqp._resolve_start_at_document({"start_at_document": 0}, {}),
            1,
        )
        self.assertEqual(
            rqp._resolve_start_at_document({"start_at_document": 3080}, {}),
            3080,
        )


class DocumentLogPrefixTest(unittest.TestCase):
    def test_prefix_includes_index_and_id(self) -> None:
        import run_qa_pipeline as rqp

        pfx = rqp._document_log_prefix(
            4780, 19953, "cyrus_auditioned_disney_3"
        )
        self.assertIn("4780/19953", pfx)
        self.assertIn("cyrus_auditioned_disney_3", pfx)


if __name__ == "__main__":
    unittest.main()
