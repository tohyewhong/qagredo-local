"""Tests for run.auto_convert JSONL preparation."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("PYDANTIC_DISABLE_PLUGIN_LOADING", "1")

from run_qa_pipeline import (  # noqa: E402
    _config_bool,
    _prepare_jsonl_input_if_needed,
)


class AutoConvertPrepareTest(unittest.TestCase):
    def test_config_bool(self):
        self.assertFalse(_config_bool(False))
        self.assertTrue(_config_bool(True))
        self.assertFalse(_config_bool(None))
        self.assertFalse(_config_bool("false"))
        self.assertTrue(_config_bool("true"))
        self.assertFalse(_config_bool("unknown"))

    def test_jsonl_unchanged(self):
        with tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write('{"id":"a","title":"T","content":"x"}\n')
            path = f.name
        try:
            out = _prepare_jsonl_input_if_needed(
                path,
                {"auto_convert": True},
                {"run": {}},
            )
            self.assertEqual(out, path)
        finally:
            os.unlink(path)

    def test_auto_convert_txt_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "sample.txt"
            src.write_text("Hello conversion test.", encoding="utf-8")
            out = _prepare_jsonl_input_if_needed(
                str(src),
                {"auto_convert": True},
                {"run": {}},
            )
            outp = Path(out)
            self.assertEqual(outp.suffix, ".jsonl")
            self.assertTrue(outp.is_file())
            lines = outp.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertIn("content", row)
            self.assertIn("Hello conversion test", row["content"])


if __name__ == "__main__":
    unittest.main()
