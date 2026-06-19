#!/usr/bin/env python3
"""Emit presentation deliverables without running the LLM stack.

Writes docs/QAGRedo_Output_Sample.json (from the repo sample) and rebuilds
docs/QAGRedo_Output_Fields_Overview.pptx.

For real per-document JSON, start vLLM on :7100 and judge on :7101, then run
`run_qa_pipeline.py` (see `docs/OFFLINE_SETUP_GUIDE.md`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "examples" / "sample_qagredo_doc_analysis.json"
OUT_JSON = ROOT / "docs" / "QAGRedo_Output_Sample.json"
OUT_PPTX = ROOT / "docs" / "QAGRedo_Output_Fields_Overview.pptx"
BUILD_PPT = ROOT / "scripts" / "utils" / "build_qagredo_output_fields_ppt.py"


def main() -> None:
    if not SAMPLE.is_file():
        raise SystemExit(f"Missing sample file: {SAMPLE}")
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUT_JSON}", flush=True)
    subprocess.run(
        [
            sys.executable,
            str(BUILD_PPT),
            "--analysis",
            str(SAMPLE),
            "--out",
            str(OUT_PPTX),
        ],
        check=True,
    )
    print(
        "Real pipeline output: start vLLM on :7100 (and judge on :7101 if "
        "hybrid), then run run_qa_pipeline.py as above."
    )


if __name__ == "__main__":
    main()
