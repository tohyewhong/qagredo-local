"""Tests for configurable parallel document processing."""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")

from utils.config_manager import build_effective_config  # noqa: E402


def _fake_generate_questions(documents, config=None, config_path=None):
    cfg = config or {}
    qg = cfg.get("question_generation") or {}
    n = int(qg.get("num_questions", 3) or 3)
    doc = documents[0] if documents else {}
    doc_id = doc.get("id", "doc")
    time.sleep(0.05)
    qs = [f"{doc_id} question {i + 1}?" for i in range(n)]
    return [
        {
            **doc,
            "questions": qs,
            "generation_metadata": {
                "model": "mock-q",
                "provider": "mock",
                "num_questions": n,
            },
        }
    ]


def _fake_generate_answers(questions, document, config=None, config_path=None):
    ql = questions if isinstance(questions, list) else [questions]
    doc_id = document.get("id", "doc")
    time.sleep(0.05)
    return {
        "questions": ql,
        "answers": [f"Answer for {doc_id}: {q}" for q in ql],
        "supporting_evidence": [""] * len(ql),
        "document_id": doc_id,
        "generation_metadata": {
            "model": "mock-a",
            "provider": "mock",
            "num_questions": len(ql),
            "num_answers": len(ql),
            "timestamp": "mock",
            "timezone": "Asia/Singapore",
        },
    }


def _fake_grade_qa_results(qa_results, method="llm"):
    out = []
    for res in qa_results:
        checks = []
        confs = []
        for q, a in zip(res.get("questions") or [], res.get("answers") or []):
            cr = {
                "is_grounded": True,
                "confidence": 0.92,
                "issues": [],
            }
            checks.append(
                {"question": q, "answer": a, "check_result": cr}
            )
            confs.append(0.92)
        oc = sum(confs) / len(confs) if confs else 0.0
        out.append(
            {
                **res,
                "hallucination_checks": checks,
                "overall_grade": "A",
                "overall_confidence": round(oc, 3),
                "grading_method": method,
                "judge_model": "mock",
            }
        )
    return out


class ResolveParallelDocumentsTest(unittest.TestCase):
    def test_defaults_to_one(self) -> None:
        import run_qa_pipeline as rqp

        self.assertEqual(
            rqp._resolve_parallel_documents({}, {}),
            1,
        )

    def test_reads_settings_then_config(self) -> None:
        import run_qa_pipeline as rqp

        self.assertEqual(
            rqp._resolve_parallel_documents(
                {"parallel_documents": 3},
                {"parallel_documents": 1},
            ),
            3,
        )
        self.assertEqual(
            rqp._resolve_parallel_documents(
                {},
                {"parallel_documents": 2},
            ),
            2,
        )

    def test_invalid_values_floor_to_one(self) -> None:
        import run_qa_pipeline as rqp

        self.assertEqual(
            rqp._resolve_parallel_documents(
                {"parallel_documents": "bad"},
                {},
            ),
            1,
        )
        self.assertEqual(
            rqp._resolve_parallel_documents(
                {"parallel_documents": 0},
                {},
            ),
            1,
        )


class ParallelDocumentPipelineTest(unittest.TestCase):
    def _write_jsonl(self, rows: int) -> str:
        fd, path = tempfile.mkstemp(suffix=".jsonl", text=True)
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            for i in range(rows):
                fh.write(
                    '{"id": "doc_%d", "title": "Doc %d", '
                    '"content": "Alpha beta gamma delta epsilon zeta."}\n'
                    % (i + 1, i + 1)
                )
        return path

    def _run_pipeline(self, path: str, parallel: int) -> list:
        cfg_path = _ROOT / "config" / "config.ollama.yaml"
        if not cfg_path.is_file():
            self.skipTest("config/config.ollama.yaml not found")
        config = build_effective_config(cfg_path)
        run_block = config.setdefault("run", {})
        if isinstance(run_block, dict):
            run_block["min_content_words"] = 0
            run_block["min_content_chars"] = 0
        qg = config.setdefault("question_generation", {})
        if isinstance(qg, dict):
            qg["num_questions"] = 1

        saved = []
        save_lock = threading.Lock()

        def capture_save(combined_result, **kwargs):
            with save_lock:
                saved.append(combined_result)
            return Path(f"/tmp/mock_qag_{len(saved)}.json")

        settings = {
            "input_file": path,
            "num_documents": 0,
            "parallel_documents": parallel,
            "provider": None,
            "model": None,
        }
        with patch(
            "run_qa_pipeline.generate_questions",
            _fake_generate_questions,
        ), patch(
            "run_qa_pipeline.generate_answers",
            _fake_generate_answers,
        ), patch(
            "run_qa_pipeline.grade_qa_results",
            _fake_grade_qa_results,
        ), patch(
            "run_qa_pipeline.save_results",
            capture_save,
        ), patch(
            "run_qa_pipeline.init_run_timestamp",
            return_value="test_run",
        ), patch(
            "run_qa_pipeline._preflight_llm_generator",
        ), patch(
            "run_qa_pipeline._preflight_llm_judge",
        ):
            import run_qa_pipeline as rqp

            rqp.run_pipeline(config, settings)
        return saved

    def test_parallel_two_documents_saves_both(self) -> None:
        path = self._write_jsonl(2)
        try:
            saved = self._run_pipeline(path, parallel=2)
        finally:
            os.unlink(path)

        self.assertEqual(len(saved), 2)
        ids = {
            item.get("document", {}).get("id")
            for item in saved
        }
        self.assertEqual(ids, {"doc_1", "doc_2"})
        for item in saved:
            self.assertEqual(len(item.get("qa_pairs") or []), 1)

    def test_serial_one_document_unchanged(self) -> None:
        path = self._write_jsonl(1)
        try:
            saved = self._run_pipeline(path, parallel=1)
        finally:
            os.unlink(path)

        self.assertEqual(len(saved), 1)
        pairs = saved[0].get("qa_pairs") or []
        self.assertEqual(len(pairs), 1)
        self.assertIn("hallucination_check", pairs[0])


if __name__ == "__main__":
    unittest.main()
