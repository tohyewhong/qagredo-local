"""Tests for run.min_content_words / min_content_chars document filter."""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from run_qa_pipeline import (  # noqa: E402
    _count_words,
    _document_content_metrics,
    _document_skip_reason_for_min_length,
)


class MinContentLengthTest(unittest.TestCase):
    def test_count_words(self):
        self.assertEqual(_count_words("one two three"), 3)
        self.assertEqual(_count_words(""), 0)

    def test_skip_when_below_min_words(self):
        doc = {"content": " ".join(["word"] * 100)}
        run_cfg = {"min_content_words": 500, "min_content_chars": 0}
        reason = _document_skip_reason_for_min_length(doc, run_cfg)
        self.assertIsNotNone(reason)
        self.assertIn("500", reason)

    def test_process_when_at_or_above_min_words(self):
        doc = {"content": " ".join(["word"] * 500)}
        run_cfg = {"min_content_words": 500, "min_content_chars": 0}
        self.assertIsNone(_document_skip_reason_for_min_length(doc, run_cfg))
        words, _chars = _document_content_metrics(doc)
        self.assertEqual(words, 500)

    def test_zero_min_words_disables_filter(self):
        doc = {"content": "short"}
        self.assertIsNone(
            _document_skip_reason_for_min_length(
                doc, {"min_content_words": 0}
            )
        )


if __name__ == "__main__":
    unittest.main()
