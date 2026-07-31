"""Tests for QAG LoRA training helpers."""

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

from scripts.lora.train_qwen_lora import (
    resolve_run_data,
    validate_training_paths,
)


class LoraTrainHelpersTest(unittest.TestCase):
    def test_validate_training_paths_rejects_same_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            (base / "config.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_training_paths(base, base)

    def test_resolve_run_data_requires_sft_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                resolve_run_data(run_dir)

    def test_resolve_run_data_finds_train_and_eval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            row = {"messages": [{"role": "user", "content": "hi"}]}
            (run_dir / "lora_sft.jsonl").write_text(
                json.dumps(row) + "\n",
                encoding="utf-8",
            )
            (run_dir / "lora_sft_eval.jsonl").write_text(
                json.dumps(row) + "\n",
                encoding="utf-8",
            )
            train_path, eval_path = resolve_run_data(run_dir)
            self.assertEqual(train_path.name, "lora_sft.jsonl")
            self.assertIsNotNone(eval_path)


if __name__ == "__main__":
    unittest.main()
