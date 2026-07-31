"""Tests for question answerability pre-check."""

import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class AnswerabilityCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.qg = importlib.reload(
            importlib.import_module("utils.question_generator")
        )
        self.rqp = importlib.import_module("run_qa_pipeline")
        self.config = {
            "llm": {"provider": "mock", "model": "mock"},
            "question_generation": {
                "validation": {
                    "enable_answerability_check": True,
                    "answerability_min_score": 0.8,
                },
            },
        }

    def test_parse_answerability_result_json(self) -> None:
        raw = json.dumps(
            {
                "is_answerable": False,
                "score": 0.2,
                "reason": "2017 salary missing",
                "missing_facts": ["2017-18 salary"],
            }
        )
        parsed = self.qg._parse_answerability_result(raw)
        self.assertFalse(parsed["is_answerable"])
        self.assertEqual(parsed["score"], 0.2)
        self.assertEqual(parsed["missing_facts"], ["2017-18 salary"])

    def test_answerability_passed_threshold(self) -> None:
        self.assertTrue(
            self.qg.answerability_passed(
                {"is_answerable": True, "score": 0.85},
                0.8,
            )
        )
        self.assertFalse(
            self.qg.answerability_passed(
                {"is_answerable": True, "score": 0.7},
                0.8,
            )
        )
        self.assertFalse(
            self.qg.answerability_passed(
                {"is_answerable": False, "score": 1.0},
                0.8,
            )
        )

    @patch.object(
        importlib.import_module("utils.question_generator"),
        "_call_llm",
    )
    def test_evaluate_question_answerability_rejects(
        self, mock_llm
    ) -> None:
        mock_llm.return_value = json.dumps(
            {
                "is_answerable": False,
                "score": 0.1,
                "reason": "Only 2018-19 salary is in the document.",
                "missing_facts": ["2017-18 salary"],
            }
        )
        passed, info = self.qg.evaluate_question_answerability(
            "How much more in 2019 vs 2018?",
            "Curry earned 37M in 2018-19.",
            self.config,
        )
        self.assertFalse(passed)
        self.assertIn("2018", info.get("reason", ""))

    def test_answerability_strict_enabled_from_config(self) -> None:
        cfg = {
            "question_generation": {
                "validation": {"answerability_strict": True},
            },
        }
        self.assertTrue(self.rqp._answerability_strict_enabled(cfg))
        self.assertFalse(
            self.rqp._answerability_strict_enabled(
                {"question_generation": {"validation": {}}}
            )
        )

    def test_answerability_strict_enabled_from_config_legacy(self) -> None:
        cfg = {
            "question_generation": {
                "validation": {"enable_answerability_check": True},
            },
        }
        self.assertTrue(self.rqp._answerability_check_enabled(cfg))

    def test_synthetic_pair_fails_grounding_gate(self) -> None:
        pair = self.rqp._synthetic_unanswerable_slot_pair(
            "Q?",
            {"title": "t"},
            "doc1",
            "missing year",
        )
        self.assertFalse(
            self.rqp._pair_passes_grounding_gate(pair, 0.7)
        )

    def test_empty_answer_fails_grounding_gate(self) -> None:
        pair = {
            "answer": "",
            "hallucination_check": {
                "is_grounded": True,
                "confidence": 1.0,
            },
        }
        self.assertFalse(
            self.rqp._pair_passes_grounding_gate(pair, 0.7)
        )


if __name__ == "__main__":
    unittest.main()
