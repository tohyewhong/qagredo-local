"""Tests for supporting_evidence fragment splitting / deduplication."""

import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")

import run_qa_pipeline as rqp  # noqa: E402


class EvidenceFragmentsTest(unittest.TestCase):
    def test_strips_numbered_prefixes(self):
        text = (
            "1. First unique sentence here.\n"
            "2. Repeated line.\n"
            "3. Repeated line.\n"
            "4) Repeated line."
        )
        frags = rqp._split_evidence_fragments(text)
        self.assertEqual(len(frags), 2)
        self.assertTrue(frags[0].startswith("First unique"))
        self.assertEqual(frags[1], "Repeated line.")

    def test_dedupes_semicolon_separated_same_quote(self):
        text = "alpha beta; alpha beta; alpha beta"
        frags = rqp._split_evidence_fragments(text)
        self.assertEqual(frags, ["alpha beta"])

    def test_evidence_to_spans_single_span_for_dup_lines(self):
        doc = "The quick brown fox jumps."
        ev = (
            "1. The quick brown fox jumps.\n"
            "2. The quick brown fox jumps.\n"
            "3. The quick brown fox jumps."
        )
        spans, notes = rqp._evidence_to_citation_spans(doc, ev)
        self.assertEqual(len(spans), 1)
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
