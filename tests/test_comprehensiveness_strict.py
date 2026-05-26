"""Tests for comprehensiveness strict mode (reject failed question slots)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.question_generator import (  # noqa: E402
    comprehensiveness_passed,
    generate_questions,
)
from run_qa_pipeline import (  # noqa: E402
    _comprehensiveness_strict_enabled,
    _slot_questions_for_pipeline,
)


class ComprehensivenessPassedTest(unittest.TestCase):
    def test_passed_requires_both_flags(self):
        self.assertTrue(
            comprehensiveness_passed(
                {"is_comprehensive": True, "score": 0.7}, 0.6
            )
        )
        self.assertFalse(
            comprehensiveness_passed(
                {"is_comprehensive": True, "score": 0.5}, 0.6
            )
        )
        self.assertFalse(
            comprehensiveness_passed(
                {"is_comprehensive": False, "score": 0.9}, 0.6
            )
        )


class SlotQuestionsForPipelineTest(unittest.TestCase):
    def test_strict_uses_seed_list_only(self):
        seeds = ["Q1?", "Q2?"]
        out = _slot_questions_for_pipeline(
            seeds, 3, comprehensiveness_strict=True
        )
        self.assertEqual(out, seeds)

    def test_non_strict_pads_with_last_seed(self):
        seeds = ["Q1?"]
        out = _slot_questions_for_pipeline(
            seeds, 3, comprehensiveness_strict=False
        )
        self.assertEqual(out, ["Q1?", "Q1?", "Q1?"])


class GenerateQuestionsStrictTest(unittest.TestCase):
    def test_strict_drops_failed_comprehensiveness_slots(self):
        config = {
            "llm": {"model": "mock", "provider": "mock"},
            "question_generation": {
                "num_questions": 3,
                "complexity": "basic",
                "validation": {
                    "enable_rejection": False,
                    "enable_comprehensiveness_check": True,
                    "comprehensiveness_strict": True,
                    "comprehensiveness_min_score": 0.6,
                    "comprehensiveness_max_attempts": 0,
                },
            },
        }
        doc = {"id": "d1", "content": "Alpha beta gamma."}
        comp_results = [
            (
                "Good question one?",
                {
                    "is_comprehensive": True,
                    "score": 0.8,
                    "accepted": True,
                    "attempts": 1,
                    "was_regenerated": False,
                    "reason": "ok",
                },
            ),
            (
                "Bad question two?",
                {
                    "is_comprehensive": False,
                    "score": 0.0,
                    "accepted": False,
                    "attempts": 1,
                    "was_regenerated": False,
                    "reason": "too narrow",
                },
            ),
            (
                "Good question three?",
                {
                    "is_comprehensive": True,
                    "score": 0.75,
                    "accepted": True,
                    "attempts": 1,
                    "was_regenerated": False,
                    "reason": "ok",
                },
            ),
        ]

        seed = ["Good question one?", "Bad question two?", "Good question three?"]
        with patch(
            "utils.question_generator._call_llm",
            return_value="ignored",
        ), patch(
            "utils.question_generator._parse_questions_with_framework",
            return_value=seed,
        ), patch(
            "utils.question_generator._llm_duplicate_questions",
            side_effect=lambda existing, new, *_a, **_k: new,
        ), patch(
            "utils.question_generator._check_question_comprehensiveness",
            side_effect=comp_results,
        ):
            results = generate_questions([doc], config=config)

        self.assertEqual(len(results), 1)
        questions = results[0]["questions"]
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0], "Good question one?")
        self.assertEqual(questions[1], "Good question three?")

        details = results[0]["generation_metadata"]["question_validation"]
        self.assertEqual(len(details), 3)
        rejected = [d for d in details if d.get("accepted") is False]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(
            rejected[0]["rejection_reason"],
            "comprehensiveness_check_failed",
        )


class StrictConfigFlagTest(unittest.TestCase):
    def test_strict_enabled_from_config(self):
        cfg = {
            "question_generation": {
                "validation": {"comprehensiveness_strict": True}
            }
        }
        self.assertTrue(_comprehensiveness_strict_enabled(cfg))

    def test_strict_disabled_by_default(self):
        self.assertFalse(_comprehensiveness_strict_enabled({}))


if __name__ == "__main__":
    unittest.main()
