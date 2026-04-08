"""Tests for run_qa_pipeline minimal export helpers."""

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


if __name__ == "__main__":
    unittest.main()
