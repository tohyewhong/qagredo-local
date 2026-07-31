"""
Smoke test: slot-based pipeline saves num_questions QA pairs (mocked).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Repo root on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")

from utils.config_manager import build_effective_config  # noqa: E402


def _fake_generate_questions(documents, config=None, config_path=None):
    """Return N questions per num_questions in config."""
    cfg = config or {}
    qg = cfg.get("question_generation") or {}
    n = int(qg.get("num_questions", 3) or 3)
    doc = documents[0] if documents else {}
    qs = [f"Mock question {i + 1}?" for i in range(n)]
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
    return {
        "questions": ql,
        "answers": [f"Mock answer for {q}" for q in ql],
        "supporting_evidence": [""] * len(ql),
        "document_id": document.get("id"),
        "generation_metadata": {
            "model": "mock-a",
            "provider": "mock",
            "num_questions": len(ql),
            "num_answers": len(ql),
            "timestamp": "mock",
            "timezone": "Asia/Singapore",
        },
    }


def _fake_answerability_check(*_args, **_kwargs):
    return True, {"is_answerable": True, "score": 1.0}


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


class SlotPipelineSmokeTest(unittest.TestCase):
    def setUp(self):
        self._saved = None

    def _capture_save(self, combined_result, **kwargs):
        self._saved = combined_result
        return Path("/tmp/mock_qag_output.json")

    def test_saves_three_pairs_when_num_questions_three(self):
        cfg_path = _ROOT / "config" / "config.ollama.yaml"
        if not cfg_path.is_file():
            self.skipTest("config/config.ollama.yaml not found")
        config = build_effective_config(cfg_path)
        run_block = config.setdefault("run", {})
        if isinstance(run_block, dict):
            run_block["min_content_words"] = 0
            run_block["min_content_chars"] = 0

        fd, path = tempfile.mkstemp(suffix=".jsonl", text=True)
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    '{"id": "t1", "title": "T", '
                    '"content": "Alpha beta gamma delta epsilon."}\n'
                )
            settings = {
                "input_file": path,
                "num_documents": 1,
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
                "run_qa_pipeline.evaluate_question_answerability",
                _fake_answerability_check,
            ), patch(
                "run_qa_pipeline.save_results",
                self._capture_save,
            ), patch(
                "run_qa_pipeline.init_run_timestamp",
                return_value="test_run",
            ), patch(
                "run_qa_pipeline.print_grading_report"
            ), patch(
                "run_qa_pipeline._preflight_llm_judge"
            ), patch(
                "run_qa_pipeline._preflight_llm_generator"
            ):
                from run_qa_pipeline import run_pipeline  # noqa: E402

                run_pipeline(config, settings)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        self.assertIsNotNone(self._saved)
        pairs = self._saved.get("qa_pairs") or []
        self.assertEqual(
            len(pairs),
            3,
            msg=f"Expected 3 qa_pairs, got {len(pairs)}",
        )
        self.assertNotIn("hallucination_checks", self._saved)
        self.assertTrue(
            all("hallucination_check" in p for p in pairs),
            msg="Each qa_pair should include hallucination_check",
        )
        gs = self._saved.get("grading_summary") or {}
        self.assertIn("overall_grade", gs)
        self.assertIn("overall_confidence", gs)


if __name__ == "__main__":
    unittest.main()
