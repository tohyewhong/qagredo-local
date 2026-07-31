"""Tests for QAG DPO training helpers."""

import json
import tempfile
import unittest
from pathlib import Path

try:
    import torch  # noqa: F401
except ImportError:
    raise unittest.SkipTest(
        "torch not installed (run with QAG_LORA_VENV or skip LoRA tests)"
    )

from scripts.lora.train_qwen_dpo import (
    build_dpo_dataset,
    resolve_dpo_data,
    validate_sft_adapter,
)


class LoraDpoTrainHelpersTest(unittest.TestCase):
    def test_resolve_dpo_data_requires_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                resolve_dpo_data(run_dir)

    def test_validate_sft_adapter_requires_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                validate_sft_adapter(adapter)

    def test_build_dpo_dataset_merges_system(self) -> None:
        rows = [
            {
                "system": "Answer from the document only.",
                "prompt": "Document:\nA\n\nQuestion: Q?",
                "chosen": "Good.",
                "rejected": "Bad.",
            }
        ]
        out = build_dpo_dataset(rows)
        self.assertEqual(len(out), 1)
        self.assertIn("Answer from the document only.", out[0]["prompt"])
        self.assertEqual(out[0]["chosen"], "Good.")
        self.assertEqual(out[0]["rejected"], "Bad.")

    def test_resolve_dpo_data_finds_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            row = {
                "prompt": "Q",
                "chosen": "A",
                "rejected": "B",
            }
            (run_dir / "lora_dpo.jsonl").write_text(
                json.dumps(row) + "\n",
                encoding="utf-8",
            )
            path = resolve_dpo_data(run_dir)
            self.assertEqual(path.name, "lora_dpo.jsonl")


if __name__ == "__main__":
    unittest.main()
