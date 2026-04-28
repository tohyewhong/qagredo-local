"""Tests for run.input_folder batch merge."""

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

from run_qa_pipeline import _merge_input_folder_to_jsonl  # noqa: E402
from utils.data_loader import resolve_data_folder_path  # noqa: E402


class InputFolderBatchTest(unittest.TestCase):
    def test_merge_two_txt_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "b.txt").write_text("second", encoding="utf-8")
            (d / "a.txt").write_text("first", encoding="utf-8")
            run_cfg = {
                "input_folder": str(d),
                "input_glob": "*.txt",
                "max_files": 0,
                "input_type": "auto",
                "auto_convert": False,
            }
            path, label = _merge_input_folder_to_jsonl(run_cfg, {"run": {}})
            self.assertEqual(label, d.name)
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            r0 = json.loads(lines[0])
            r1 = json.loads(lines[1])
            self.assertEqual(r0["title"], "a")
            self.assertEqual(r1["title"], "b")

    def test_max_files_caps_merge(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for name in ("1.txt", "2.txt", "3.txt"):
                (d / name).write_text(name, encoding="utf-8")
            run_cfg = {
                "input_folder": str(d),
                "input_glob": "*.txt",
                "max_files": 2,
                "input_type": "auto",
                "auto_convert": False,
            }
            path, _ = _merge_input_folder_to_jsonl(run_cfg, {"run": {}})
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)

    def test_merge_comma_separated_globs(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "a.json").write_text(
                '{"id":"1","content":"x"}', encoding="utf-8"
            )
            (d / "b.jsonl").write_text(
                '{"id":"2","content":"y"}\n', encoding="utf-8"
            )
            run_cfg = {
                "input_folder": str(d),
                "input_glob": "*.json,*.jsonl",
                "max_files": 0,
                "input_type": "auto",
                "auto_convert": True,
            }
            path, _ = _merge_input_folder_to_jsonl(run_cfg, {"run": {}})
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)

    def test_merge_json_with_auto_convert(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            doc = {
                "id": "doc-a",
                "title": "A",
                "content": "alpha body",
                "type": "text_document",
            }
            (d / "a.json").write_text(
                json.dumps(doc, ensure_ascii=False), encoding="utf-8"
            )
            run_cfg = {
                "input_folder": str(d),
                "input_glob": "*.json",
                "max_files": 0,
                "input_type": "auto",
                "auto_convert": True,
            }
            path, _ = _merge_input_folder_to_jsonl(run_cfg, {"run": {}})
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row.get("id"), "doc-a")

    def test_resolve_data_folder_under_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            p = resolve_data_folder_path(td)
            self.assertEqual(p, Path(td))


if __name__ == "__main__":
    unittest.main()
