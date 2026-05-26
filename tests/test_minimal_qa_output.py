"""Tests for minimal Q/A export text sanitization."""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from run_qa_pipeline import (  # noqa: E402
    _minimal_document_for_output,
    _minimal_qa_pairs_for_output,
)
from utils.minimal_text import (  # noqa: E402
    looks_like_thinking_blob,
    plain_text_for_minimal_output,
    sanitize_llm_answer_response,
    sanitize_llm_question_response,
)

_THINK_OPEN = "<" + "think" + ">"
_THINK_CLOSE = "</" + "think" + ">"


class TestMinimalQaOutput(unittest.TestCase):
    def test_strips_to_question_answer(self) -> None:
        pairs = [
            {
                "question": "Q?",
                "answer": "A.",
                "hallucination_check": {"is_grounded": True},
                "citation_spans": [1, 2],
            }
        ]
        out = _minimal_qa_pairs_for_output(pairs)
        self.assertEqual(out, [{"question": "Q?", "answer": "A."}])

    def test_skips_non_dict(self) -> None:
        self.assertEqual(_minimal_qa_pairs_for_output([None, {"x": 1}]), [])

    def test_minimal_document_content_only(self) -> None:
        doc = {"id": "d1", "title": "T", "content": "Hello body"}
        out = _minimal_document_for_output(doc, "d1")
        self.assertEqual(out, {"content": "Hello body"})

    def test_plain_text_strips_thinking_process_and_answer_label(self) -> None:
        raw = (
            "**Thinking Process:**\n"
            "Step 1: read the doc.\n\n"
            "**Answer:**\n"
            "It began as a newsletter in 1963.\n\n"
            "Supporting evidence: founded in 1963"
        )
        self.assertEqual(
            plain_text_for_minimal_output(raw, field="answer"),
            "It began as a newsletter in 1963.",
        )

    def test_plain_text_strips_think_blocks(self) -> None:
        raw = (
            f"{_THINK_OPEN}\ninternal reasoning\n{_THINK_CLOSE}\n"
            "The Collegian adopted a newspaper format in 1990."
        )
        self.assertEqual(
            plain_text_for_minimal_output(raw, field="answer"),
            "The Collegian adopted a newspaper format in 1990.",
        )

    def test_thinking_blob_question_extracts_draft(self) -> None:
        raw = (
            "Thinking Process:\n\n"
            "1.  **Analyze the Request:**\n"
            "    *   meta notes\n\n"
            "4.  **Drafting the Question:**\n"
            "    *   *Draft 1:* How did the fall of Malakoff lead to the city's surrender?\n"
        )
        self.assertTrue(looks_like_thinking_blob(raw))
        out = plain_text_for_minimal_output(raw, field="question")
        self.assertIn("Malakoff", out)
        self.assertNotIn("Analyze the Request", out)

    def test_thinking_blob_answer_without_answer_returns_empty(self) -> None:
        raw = (
            "Thinking Process:\n\n"
            "1.  **Analyze the Request:**\n"
            "    *   meta analysis only\n"
        )
        self.assertEqual(plain_text_for_minimal_output(raw, field="answer"), "")

    def test_sanitize_question_response_from_thinking_blob(self) -> None:
        raw = (
            "Thinking Process:\n\n"
            "4.  **Drafting the Question:**\n"
            "    *   *Draft 1:* What role did railways play in the siege?\n"
        )
        out = sanitize_llm_question_response(raw, max_items=3)
        self.assertEqual(len(out), 1)
        self.assertIn("railways", out[0])
        self.assertNotIn("Thinking Process", out[0])

    def test_sanitize_answer_response_strips_labels(self) -> None:
        raw = (
            "Answer: The treaty was signed in 1871.\n"
            "Supporting evidence: signed in 1871"
        )
        ans, ev = sanitize_llm_answer_response(raw)
        self.assertEqual(ans, "The treaty was signed in 1871.")
        self.assertIn("1871", ev)

    def test_minimise_pair_strips_answer_reasoning(self) -> None:
        pairs = [
            {
                "question": "When did format change?",
                "answer": (
                    "Thinking Process:\n"
                    "Compare dates in the text.\n\n"
                    "Answer: In 1990.\n"
                    "Supporting evidence: format in 1990"
                ),
            }
        ]
        self.assertEqual(
            _minimal_qa_pairs_for_output(pairs),
            [{"question": "When did format change?", "answer": "In 1990."}],
        )


if __name__ == "__main__":
    unittest.main()
