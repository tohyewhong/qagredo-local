"""Tests for shared LoRA training helpers."""

import tempfile
import unittest
from pathlib import Path

try:
    import torch
    from safetensors.torch import save_file
except ImportError:
    raise unittest.SkipTest(
        "torch not installed (run with QAG_LORA_VENV or skip LoRA tests)"
    )

from scripts.lora.lora_utils import validate_adapter_finite


class LoraUtilsTest(unittest.TestCase):
    def test_validate_adapter_finite_rejects_nan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter_dir = Path(tmp)
            weights = {
                "layer.lora_A.weight": torch.tensor([1.0, 2.0]),
                "layer.lora_B.weight": torch.tensor([float("nan")]),
            }
            save_file(weights, adapter_dir / "adapter_model.safetensors")
            with self.assertRaises(SystemExit):
                validate_adapter_finite(adapter_dir, stage="test")

    def test_validate_adapter_finite_accepts_finite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter_dir = Path(tmp)
            weights = {
                "layer.lora_A.weight": torch.tensor([1.0, 2.0]),
            }
            save_file(weights, adapter_dir / "adapter_model.safetensors")
            validate_adapter_finite(adapter_dir, stage="test")


if __name__ == "__main__":
    unittest.main()
