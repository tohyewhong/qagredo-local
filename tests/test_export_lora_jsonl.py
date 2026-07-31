"""Tests for LoRA JSONL export."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "utils" / "export_lora_jsonl.py"


def _write_good_bad(run_dir: Path) -> None:
    good = {
        "document": {"id": "d1", "content": "Alpha beta gamma."},
        "qa_pairs": [{"question": "What is alpha?", "answer": "Alpha."}],
    }
    bad = {
        "document": {"id": "d1", "content": "Alpha beta gamma."},
        "qa_pairs": [
            {"question": "What is alpha?", "answer": "Wrong guess."},
        ],
    }
    good_path = run_dir / "doc_1_0001_analysis_minimal_good_pairs.json"
    bad_path = run_dir / "doc_1_0001_analysis_minimal_bad_pairs.json"
    good_path.write_text(json.dumps(good) + "\n", encoding="utf-8")
    bad_path.write_text(json.dumps(bad) + "\n", encoding="utf-8")


def _write_captured_dpo(run_dir: Path) -> None:
    payload = {
        "document": {"id": "d1", "content": "Alpha beta gamma."},
        "qa_pairs": [
            {
                "question": "What is alpha?",
                "answer": "Alpha.",
                "hallucination_check": {
                    "is_grounded": True,
                    "confidence": 0.9,
                },
            }
        ],
        "dpo_pairs": [
            {
                "question": "What is alpha?",
                "chosen": "Alpha.",
                "rejected": "Unsupported guess.",
            }
        ],
    }
    path = run_dir / "doc_1_0001_analysis.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


class ExportLoraJsonlTest(unittest.TestCase):
    def test_sharegpt_sft_and_dpo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            _write_good_bad(run_dir)
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(run_dir),
                    "--include-dpo",
                    "--eval-fraction",
                    "0",
                ],
                check=True,
                cwd=str(ROOT),
            )
            sft_path = run_dir / "lora_sft.jsonl"
            dpo_path = run_dir / "lora_dpo.jsonl"
            info_path = run_dir / "lora_dataset_info.json"
            self.assertTrue(sft_path.exists())
            self.assertTrue(dpo_path.exists())
            self.assertTrue(info_path.exists())
            sft_row = json.loads(sft_path.read_text(encoding="utf-8").strip())
            self.assertIn("messages", sft_row)
            self.assertEqual(len(sft_row["messages"]), 3)
            self.assertIn("Document:", sft_row["messages"][1]["content"])
            dpo_row = json.loads(dpo_path.read_text(encoding="utf-8").strip())
            self.assertEqual(dpo_row["chosen"], "Alpha.")
            self.assertEqual(dpo_row["rejected"], "Wrong guess.")

    def test_exports_captured_answer_retry_as_dpo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            _write_captured_dpo(run_dir)
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(run_dir),
                    "--include-dpo",
                    "--eval-fraction",
                    "0",
                ],
                check=True,
                cwd=str(ROOT),
            )
            dpo_path = run_dir / "lora_dpo.jsonl"
            self.assertTrue(dpo_path.exists())
            dpo_row = json.loads(
                dpo_path.read_text(encoding="utf-8").strip()
            )
            self.assertEqual(dpo_row["chosen"], "Alpha.")
            self.assertEqual(
                dpo_row["rejected"],
                "Unsupported guess.",
            )


if __name__ == "__main__":
    unittest.main()
