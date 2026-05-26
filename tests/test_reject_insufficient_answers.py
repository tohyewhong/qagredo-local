"""Tests for run.reject_insufficient_answers strict slot omission."""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from run_qa_pipeline import (  # noqa: E402
    _answer_is_insufficient,
    _pair_passes_grounding_gate,
)


class AnswerIsInsufficientTest(unittest.TestCase):
    def test_detects_standard_phrase_case_insensitive(self):
        self.assertTrue(
            _answer_is_insufficient(
                "Insufficient information in the document."
            )
        )
        self.assertTrue(
            _answer_is_insufficient(
                "Answer: insufficient information in the document."
            )
        )

    def test_normal_answer_not_insufficient(self):
        self.assertFalse(
            _answer_is_insufficient("The treaty was signed in 1871.")
        )


class PairPassesGroundingGateTest(unittest.TestCase):
    def test_insufficient_fails_gate_even_if_grounded(self):
        pair = {
            "answer": "Insufficient information in the document.",
            "hallucination_check": {
                "is_grounded": True,
                "confidence": 0.95,
            },
        }
        self.assertFalse(_pair_passes_grounding_gate(pair, 0.7))


if __name__ == "__main__":
    unittest.main()
