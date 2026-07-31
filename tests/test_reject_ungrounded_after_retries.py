"""Tests for reject_ungrounded_after_retries answer validation."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.answer_generator import (  # noqa: E402
    _reject_ungrounded_after_retries_enabled,
    _validate_and_regenerate_answer,
)
from run_qa_pipeline import (  # noqa: E402
    _dpo_pair_from_answer_attempts,
    _grading_from_answer_validation,
    _pair_passes_grounding_gate,
    _slot_answer_validation_rejected,
)


class RejectUngroundedConfigTest(unittest.TestCase):
    def test_default_enabled_when_key_missing(self) -> None:
        self.assertTrue(
            _reject_ungrounded_after_retries_enabled({})
        )

    def test_disabled_when_false(self) -> None:
        cfg = {
            "answer_generation": {
                "multi_turn": {
                    "reject_ungrounded_after_retries": False,
                }
            }
        }
        self.assertFalse(
            _reject_ungrounded_after_retries_enabled(cfg)
        )


class ValidateAndRegenerateRejectTest(unittest.TestCase):
    def _cfg(self, reject: bool = True) -> dict:
        return {
            "hallucination": {"method": "llm"},
            "answer_generation": {
                "multi_turn": {
                    "reject_ungrounded_after_retries": reject,
                }
            },
        }

    @patch("utils.answer_generator.check_hallucination")
    @patch("utils.answer_generator._call_llm")
    def test_rejects_empty_after_failed_retries(
        self, mock_llm, mock_check
    ) -> None:
        mock_check.return_value = {
            "is_grounded": False,
            "confidence": 0.2,
            "issues": ["not supported"],
        }
        mock_llm.return_value = "still bad"
        ans, info = _validate_and_regenerate_answer(
            answer="bad",
            question="Q?",
            document_content="doc",
            config=self._cfg(reject=True),
            min_confidence=0.7,
            max_attempts=2,
        )
        self.assertEqual(ans, "")
        self.assertFalse(info.get("accepted"))
        self.assertEqual(
            info.get("rejection_reason"), "ungrounded_after_retries"
        )

    @patch("utils.answer_generator.check_hallucination")
    def test_returns_answer_when_grounded(self, mock_check) -> None:
        mock_check.return_value = {
            "is_grounded": True,
            "confidence": 0.9,
            "issues": [],
        }
        ans, info = _validate_and_regenerate_answer(
            answer="good",
            question="Q?",
            document_content="doc",
            config=self._cfg(),
            min_confidence=0.7,
            max_attempts=2,
        )
        self.assertEqual(ans, "good")
        self.assertTrue(info.get("accepted"))

    @patch("utils.answer_generator.check_hallucination")
    @patch("utils.answer_generator._call_llm")
    def test_retains_rejected_attempt_before_accepted_retry(
        self, mock_llm, mock_check
    ) -> None:
        mock_check.side_effect = [
            {
                "is_grounded": False,
                "confidence": 0.2,
                "issues": ["not supported"],
            },
            {
                "is_grounded": True,
                "confidence": 0.9,
                "issues": [],
            },
        ]
        mock_llm.return_value = "grounded retry"
        ans, info = _validate_and_regenerate_answer(
            answer="unsupported first answer",
            question="Q?",
            document_content="doc",
            config=self._cfg(),
            min_confidence=0.7,
            max_attempts=2,
        )
        self.assertEqual(ans, "grounded retry")
        attempts = info.get("answer_attempts")
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[0]["accepted"])
        self.assertTrue(attempts[1]["accepted"])

    @patch("utils.answer_generator.check_hallucination")
    @patch("utils.answer_generator._call_llm")
    def test_legacy_keeps_last_answer_when_disabled(
        self, mock_llm, mock_check
    ) -> None:
        mock_check.return_value = {
            "is_grounded": False,
            "confidence": 0.2,
            "issues": [],
        }
        mock_llm.return_value = "retry bad"
        ans, info = _validate_and_regenerate_answer(
            answer="bad",
            question="Q?",
            document_content="doc",
            config=self._cfg(reject=False),
            min_confidence=0.7,
            max_attempts=2,
        )
        self.assertEqual(ans, "retry bad")
        self.assertNotEqual(info.get("accepted"), False)


class SlotPipelineHelpersTest(unittest.TestCase):
    def test_validation_rejected_detected(self) -> None:
        qa = {
            "generation_metadata": {
                "answer_quality_checks": [
                    {"validation": {"accepted": False}},
                ]
            }
        }
        self.assertTrue(_slot_answer_validation_rejected(qa))

    def test_grading_from_validation_skips_duplicate_shape(self) -> None:
        qa = {
            "answers": [""],
            "generation_metadata": {
                "answer_quality_checks": [
                    {
                        "validation": {
                            "accepted": False,
                            "is_grounded": False,
                            "confidence": 0.1,
                            "issues": ["x"],
                        }
                    },
                ]
            },
        }
        grading = _grading_from_answer_validation(qa, "Q?", "llm")
        self.assertIsNotNone(grading)
        assert grading is not None
        checks = grading.get("hallucination_checks")
        self.assertEqual(len(checks), 1)
        self.assertFalse(
            checks[0]["check_result"].get("is_grounded")
        )

    def test_ungrounded_pair_fails_gate(self) -> None:
        pair = {
            "answer": "",
            "hallucination_check": {
                "is_grounded": False,
                "confidence": 0.1,
            },
        }
        self.assertFalse(_pair_passes_grounding_gate(pair, 0.7))

    def test_builds_dpo_pair_from_same_question_retry(self) -> None:
        qa = {
            "generation_metadata": {
                "answer_quality_checks": [
                    {
                        "validation": {
                            "answer_attempts": [
                                {
                                    "answer": "wrong",
                                    "accepted": False,
                                    "confidence": 0.2,
                                },
                                {
                                    "answer": "right",
                                    "accepted": True,
                                    "confidence": 0.9,
                                },
                            ]
                        }
                    }
                ]
            }
        }
        accepted = {
            "question": "Q?",
            "answer": "right",
            "hallucination_check": {
                "is_grounded": True,
                "confidence": 0.9,
            },
        }
        pair = _dpo_pair_from_answer_attempts(qa, accepted)
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertEqual(pair["question"], "Q?")
        self.assertEqual(pair["chosen"], "right")
        self.assertEqual(pair["rejected"], "wrong")


if __name__ == "__main__":
    unittest.main()
