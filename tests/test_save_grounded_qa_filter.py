"""Tests for grounded-only qa_pairs filtering (run_qa_pipeline)."""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from run_qa_pipeline import (  # noqa: E402
    _filter_pairs_and_validation_by_grounding_gate,
)


class TestFilterGroundedPairs(unittest.TestCase):
    def test_keeps_only_grounded_with_min_conf(self) -> None:
        pairs = [
            {
                "question": "q1",
                "answer": "a1",
                "hallucination_check": {
                    "is_grounded": True,
                    "confidence": 0.9,
                },
            },
            {
                "question": "q2",
                "answer": "a2",
                "hallucination_check": {
                    "is_grounded": True,
                    "confidence": 0.5,
                },
            },
            {
                "question": "q3",
                "answer": "a3",
                "hallucination_check": {
                    "is_grounded": False,
                    "confidence": 1.0,
                },
            },
        ]
        val = [{"slot": 1}, {"slot": 2}, {"slot": 3}]
        out_p, out_v = _filter_pairs_and_validation_by_grounding_gate(
            pairs, val, min_confidence=0.7
        )
        self.assertEqual(len(out_p), 1)
        self.assertEqual(out_p[0]["question"], "q1")
        self.assertEqual(out_v, [{"slot": 1}])

    def test_empty_inputs(self) -> None:
        self.assertEqual(
            _filter_pairs_and_validation_by_grounding_gate([], [], 0.7),
            ([], []),
        )


if __name__ == "__main__":
    unittest.main()
