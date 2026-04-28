"""Tests for run output folder naming (input stem vs timestamp)."""

import importlib
import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class OutputRunFolderTest(unittest.TestCase):
    def test_input_basename_segment(self):
        import utils.output_manager as om

        importlib.reload(om)
        seg = om.input_file_output_segment(_ROOT / "data" / "foo-bar.jsonl")
        self.assertEqual(seg, "foo-bar")

    def test_timestamp_folder_when_no_segment(self):
        import utils.output_manager as om

        importlib.reload(om)
        name = om.init_run_timestamp(None)
        self.assertRegex(
            name,
            re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$"),
        )

    def test_custom_segment_sanitized(self):
        import utils.output_manager as om

        importlib.reload(om)
        name = om.init_run_timestamp("bad/name*here")
        self.assertEqual(name, "bad_name_here")

    def test_analysis_filename_no_per_file_timestamp(self):
        """Run folder is dated; files use stem only (no YYYYMMDD_ prefix)."""
        import utils.output_manager as om

        importlib.reload(om)
        om.init_run_timestamp(None)
        p = om.get_timestamped_output_path(
            provider="vllm",
            model="m",
            output_type="linux_doc_0001_analysis",
            create_dirs=False,
        )
        self.assertEqual(p.name, "linux_doc_0001_analysis.json")
        self.assertRegex(str(p.parent.name), r"^\d{4}-\d{2}-\d{2}_\d{6}$")


if __name__ == "__main__":
    unittest.main()
