"""Strict llm-judge policy unit tests."""

import unittest
from unittest.mock import patch

from utils import hallucination_checker as hc


class StrictLlmJudgePolicyTest(unittest.TestCase):
    """Verify strict llm policy fails fast on invalid judge verdicts."""

    def setUp(self) -> None:
        hc.set_llm_config(
            {
                "llm": {
                    "provider": "vllm",
                    "model": "gen-model",
                    "base_url": "http://localhost:7100/v1",
                    "api_key": "EMPTY",
                },
                "judge": {
                    "provider": "vllm",
                    "model": "judge-model",
                    "base_url": "http://localhost:7101/v1",
                    "api_key": "EMPTY",
                },
                "hallucination": {
                    "method": "llm",
                    "judge_required": True,
                    "judge_strict_verdict": True,
                    "allow_semantic_fallback": False,
                },
            }
        )

    def test_check_llm_based_raises_on_unknown_verdict(self) -> None:
        """Strict mode rejects UNKNOWN judge verdicts."""
        with patch.object(
            hc,
            "_call_llm_judge",
            return_value={
                "verdict": "UNKNOWN",
                "confidence": 0.5,
                "reason": "judge timeout",
            },
        ):
            with self.assertRaises(RuntimeError):
                hc._check_llm_based(
                    answer="alpha",
                    document_content="alpha appears in the text",
                    question="Is alpha present?",
                )

    def test_grade_qa_results_raises_in_strict_llm_mode(self) -> None:
        """grade_qa_results should not swallow strict llm errors."""
        payload = {
            "id": "doc1",
            "content": "alpha appears in the text",
            "questions": ["Is alpha present?"],
            "answers": ["Alpha is present."],
        }
        with patch.object(
            hc,
            "_call_llm_judge",
            return_value={
                "verdict": "UNKNOWN",
                "confidence": 0.5,
                "reason": "judge timeout",
            },
        ):
            with self.assertRaises(RuntimeError):
                hc.grade_qa_results([payload], method="llm")


if __name__ == "__main__":
    unittest.main()
