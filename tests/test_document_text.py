"""Tests for utils.document_text (English vs native, grading-safe path)."""

import unittest

from utils.document_text import extract_document_text
from utils.hallucination_checker import _document_text_for_grading


class TestExtractDocumentText(unittest.TestCase):
    def test_priority_content_over_english(self) -> None:
        doc = {
            "content": "Primary body.",
            "english": {"article": "Should not win."},
        }
        self.assertEqual(extract_document_text(doc), "Primary body.")

    def test_top_level_english_skips_native(self) -> None:
        doc = {
            "id": "1",
            "english": {"article": "Hello EN."},
            "native": {"article": "Hola native should be ignored."},
        }
        self.assertEqual(extract_document_text(doc), "Hello EN.")

    def test_source_list_english_only(self) -> None:
        doc = {
            "title": "T",
            "source": [
                {
                    "english": {"article": "First EN."},
                    "native": {"article": "Native one."},
                },
                {"article": "Flat second."},
            ],
        }
        out = extract_document_text(doc)
        self.assertIn("First EN.", out)
        self.assertIn("Flat second.", out)
        self.assertNotIn("Native one.", out)

    def test_strict_raises_when_empty(self) -> None:
        with self.assertRaises(ValueError):
            extract_document_text({"id": "x"}, strict=True)

    def test_non_strict_returns_empty(self) -> None:
        self.assertEqual(
            extract_document_text({"id": "x"}, strict=False),
            "",
        )

    def test_grading_path_no_fallback_to_questions(self) -> None:
        merged = {
            "questions": ["What is X?"],
            "answers": ["Y."],
            "english": {"article": "Real doc body."},
        }
        self.assertEqual(
            _document_text_for_grading(merged),
            "Real doc body.",
        )

    def test_grading_path_empty_without_document_fields(self) -> None:
        merged = {
            "questions": ["Q1"],
            "answers": ["A1"],
        }
        self.assertEqual(_document_text_for_grading(merged), "")


if __name__ == "__main__":
    unittest.main()
