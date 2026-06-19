"""Tests for exporting per-document good/bad split files."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "utils" / "export_analysis_training_jsonl.py"


def _write_analysis(path: Path) -> None:
    payload = {
        "document": {
            "id": "doc_1",
            "title": "Doc 1",
            "content": "Document content here.",
        },
        "qa_pairs": [
            {
                "question": "good q",
                "answer": "good a",
                "hallucination_check": {
                    "is_grounded": True,
                    "confidence": 0.9,
                },
            },
            {
                "question": "bad q",
                "answer": "bad a",
                "hallucination_check": {
                    "is_grounded": False,
                    "confidence": 0.2,
                },
            },
        ],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class ExportTrainingSplitTest(unittest.TestCase):
    def test_exports_good_and_bad_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-05-26_102946"
            run_dir.mkdir(parents=True, exist_ok=True)
            src = run_dir / "doc_1_0001_analysis.json"
            _write_analysis(src)

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--mode",
                    "good",
                    str(run_dir),
                ],
                check=True,
                cwd=str(ROOT),
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--mode",
                    "bad",
                    str(run_dir),
                ],
                check=True,
                cwd=str(ROOT),
            )

            good_path = (
                run_dir / "doc_1_0001_analysis_minimal_good_pairs.json"
            )
            bad_path = (
                run_dir / "doc_1_0001_analysis_minimal_bad_pairs.json"
            )
            self.assertTrue(good_path.exists())
            self.assertTrue(bad_path.exists())

            good_rows = json.loads(good_path.read_text(encoding="utf-8"))[
                "qa_pairs"
            ]
            bad_rows = json.loads(bad_path.read_text(encoding="utf-8"))[
                "qa_pairs"
            ]
            good_doc = json.loads(good_path.read_text(encoding="utf-8"))[
                "document"
            ]
            self.assertEqual(len(good_rows), 1)
            self.assertEqual(len(bad_rows), 1)
            self.assertEqual(good_rows[0]["question"], "good q")
            self.assertEqual(bad_rows[0]["question"], "bad q")
            self.assertIn("content", good_doc)
            self.assertNotIn("content", good_rows[0])
            self.assertNotIn("is_grounded", good_rows[0])
            self.assertNotIn("confidence", good_rows[0])

    def test_threshold_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-05-26_102946"
            run_dir.mkdir(parents=True, exist_ok=True)
            src = run_dir / "doc_1_0001_analysis.json"
            _write_analysis(src)

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--mode",
                    "good",
                    "--min-confidence",
                    "0.95",
                    str(run_dir),
                ],
                check=True,
                cwd=str(ROOT),
            )
            good_path = (
                run_dir / "doc_1_0001_analysis_minimal_good_pairs.json"
            )
            rows = json.loads(good_path.read_text(encoding="utf-8"))[
                "qa_pairs"
            ]
            self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
