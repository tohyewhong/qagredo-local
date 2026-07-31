#!/usr/bin/env python3
"""
Verify the qag-v1 image: every package in requirements.txt is installed and
satisfies the specifier, and `pip check` reports no broken dependencies.

Intended to run inside the Docker image (same Python as the pipeline), e.g.:
  python /workspace/scripts/docker_verify_requirements.py /workspace/requirements.txt

Exit code 0 = OK, non-zero = fail build or offline verification.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement


def _parse_requirements(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "-c ", "-e ", "git+")):
            print(
                f"[FAIL] requirements.txt must be flat (unsupported line): {line!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        lines.append(line)
    return lines


def main() -> int:
    req_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/requirements.txt")
    if not req_path.is_file():
        print(f"[FAIL] Missing requirements file: {req_path}", file=sys.stderr)
        return 2

    print("[INFO] pip check …")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stdout, end="")
        print(r.stderr, end="", file=sys.stderr)
        print("[FAIL] pip check reported broken dependencies.", file=sys.stderr)
        return 1

    print("[INFO] Verifying distributions vs requirements.txt …")
    failed = 0
    for line in _parse_requirements(req_path):
        req = Requirement(line)
        name = req.name
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            print(f"[FAIL] Not installed: {name!r} (from {line!r})", file=sys.stderr)
            failed += 1
            continue
        ver = dist.version
        if req.specifier and not req.specifier.contains(ver, prereleases=True):
            print(
                f"[FAIL] {name}: installed {ver!s} does not satisfy {req.specifier!s}",
                file=sys.stderr,
            )
            failed += 1
        else:
            print(f"  OK  {name}=={ver}")

    if failed:
        print(f"[FAIL] {failed} requirement(s) missing or wrong version.", file=sys.stderr)
        return 1

    print("[OK] All requirements.txt entries satisfied and pip check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
