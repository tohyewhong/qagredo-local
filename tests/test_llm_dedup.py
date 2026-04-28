"""Unit tests for LLM-based question deduplication."""

import unittest
from unittest.mock import patch

from utils import question_generator as qg


class LlmDedupTest(unittest.TestCase):
    """Validate LLM dedup behavior and strict parsing policy."""

    def test_llm_dedup_filters_semantic_duplicates(self) -> None:
        """Duplicate verdict should drop semantically repeated question."""
        existing = ["What caused the market decline in 2023?"]
        new_questions = [
            "Why did the market decline in 2023?",
            "What policy changes were introduced after the decline?",
        ]
        config = {
            "llm": {"provider": "vllm"},
            "question_generation": {
                "dedup_llm": {"strict": True, "use_judge_model": False}
            },
        }

        # First compare -> duplicate, second compare -> non-duplicate.
        responses = iter(
            [
                ('{"duplicate": true}', True),
                ('{"duplicate": false}', True),
            ]
        )
        with patch.object(qg, "_call_dedup_llm", side_effect=lambda *_: next(responses)):
            kept = qg._llm_duplicate_questions(
                existing,
                new_questions,
                similarity_threshold=0.85,
                config=config,
            )

        self.assertEqual(
            kept, ["What policy changes were introduced after the decline?"]
        )

    def test_llm_dedup_strict_mode_raises_on_invalid_verdict(self) -> None:
        """Strict mode fails fast when dedup JSON is invalid."""
        config = {
            "llm": {"provider": "vllm"},
            "question_generation": {
                "dedup_llm": {"strict": True, "use_judge_model": False}
            },
        }
        with patch.object(qg, "_call_dedup_llm", return_value=("INVALID", True)):
            with self.assertRaises(RuntimeError):
                qg._llm_duplicate_questions(
                    ["What happened at launch?"],
                    ["Describe the launch event."],
                    similarity_threshold=0.85,
                    config=config,
                )

    def test_llm_dedup_non_strict_keeps_question_on_invalid_verdict(self) -> None:
        """Non-strict mode tolerates malformed judge output."""
        config = {
            "llm": {"provider": "vllm"},
            "question_generation": {
                "dedup_llm": {"strict": False, "use_judge_model": False}
            },
        }
        with patch.object(qg, "_call_dedup_llm", return_value=("INVALID", False)):
            kept = qg._llm_duplicate_questions(
                ["What happened at launch?"],
                ["Describe the launch event."],
                similarity_threshold=0.85,
                config=config,
            )
        self.assertEqual(kept, ["Describe the launch event."])


if __name__ == "__main__":
    unittest.main()
