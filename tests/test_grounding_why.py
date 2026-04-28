"""Tests for grounding_why when citations are empty."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")

from utils.hallucination_checker import (  # noqa: E402
    apply_grounding_why_when_no_citations,
)


class GroundingWhyTest(unittest.TestCase):
    def test_reuse_llm_only_copies_reason(self):
        pair = {
            "question": "Q?",
            "answer": "A",
            "citation_spans": [],
            "citation_notes": [],
            "hallucination_check": {
                "is_grounded": True,
                "confidence": 0.9,
                "llm_verdict": {
                    "verdict": "SUPPORTED",
                    "confidence": 0.9,
                    "reason": "Dates match the timeline in paragraph one.",
                },
            },
        }
        apply_grounding_why_when_no_citations(
            pair,
            "doc text",
            "Q?",
            "reuse_llm_only",
        )
        g = pair["hallucination_check"]
        self.assertEqual(
            g.get("grounding_why"),
            "Dates match the timeline in paragraph one.",
        )

    def test_off_leaves_grading_unchanged(self):
        pair = {
            "hallucination_check": {
                "is_grounded": True,
                "llm_verdict": {"reason": "should not copy"},
            },
            "citation_spans": [],
            "citation_notes": [],
        }
        apply_grounding_why_when_no_citations(pair, "d", "q", "off")
        self.assertNotIn("grounding_why", pair["hallucination_check"])

    def test_skips_when_citations_present(self):
        pair = {
            "hallucination_check": {
                "is_grounded": True,
                "llm_verdict": {"reason": "x"},
            },
            "citation_spans": [{"start": 0, "end": 2, "text": "ab"}],
            "citation_notes": [],
        }
        apply_grounding_why_when_no_citations(
            pair, "doc", "q", "reuse_llm_only"
        )
        self.assertNotIn("grounding_why", pair["hallucination_check"])

    @patch(
        "utils.hallucination_checker.explain_grounding_brief",
        return_value="Synthetic brief from mock.",
    )
    def test_always_falls_back_to_explain(self, _mock_fn):
        pair = {
            "question": "Q?",
            "answer": "A",
            "citation_spans": [],
            "citation_notes": [],
            "hallucination_check": {
                "is_grounded": True,
                "confidence": 0.85,
                "method": "keyword",
            },
        }
        apply_grounding_why_when_no_citations(
            pair,
            "The document says alpha beta.",
            "Q?",
            "always",
        )
        self.assertEqual(
            pair["hallucination_check"].get("grounding_why"),
            "Synthetic brief from mock.",
        )


if __name__ == "__main__":
    unittest.main()
