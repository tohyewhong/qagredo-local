"""DATA_DIR from .env presets — keep bash snippet aligned with run.sh."""

import os
import subprocess
import unittest

# Mirrors run.sh: preset branch + final DATA_DIR export (lines ~36–62).
_BASH_DATA_DIR = r"""
set -euo pipefail
HOST_DIR="${HOST_DIR:?}"
die() { echo "[test][ERROR] $*" >&2; exit 1; }
DATA_DIR=""
if [[ -n "${QAGREDO_OFFLINE_HOST:-}" && \
    -n "${QAGREDO_OFFLINE_INPUT:-}" ]]; then
  case "${QAGREDO_OFFLINE_INPUT}" in
    txt|json) ;;
    *) die "QAGREDO_OFFLINE_INPUT must be txt or json" ;;
  esac
  case "${QAGREDO_OFFLINE_HOST}" in
    [Rr]epo|[Ll]inux)
      _repo="${QAGREDO_REPO_DATA_ROOT:-${QAGREDO_LINUX_DATA_ROOT:-}}"
      export DATA_DIR="${_repo:-$HOST_DIR/data}"
      ;;
    [Ww]indows|[Ww][Ss][Ll])
      _dw="${QAGREDO_WINDOWS_DOWNLOADS_ROOT:-/mnt/c/Users/tyewhong/Downloads}"
      export DATA_DIR="${_dw}/${QAGREDO_OFFLINE_INPUT}"
      ;;
    [Dd]ata)
      _droot="${QAGREDO_SHARED_DATA_ROOT:-/data/local/tyewhong/Data}"
      export DATA_DIR="${_droot}/${QAGREDO_OFFLINE_INPUT}"
      ;;
    *) die "bad host" ;;
  esac
fi
export DATA_DIR="${DATA_DIR:-${QAGREDO_DATA_DIR:-$HOST_DIR/data}}"
printf '%s' "$DATA_DIR"
"""


def _compute_data_dir(env: dict) -> str:
    """Run the same preset logic as run.sh; return DATA_DIR."""
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("QAGREDO_")
    }
    clean.update(env)
    clean.setdefault("HOST_DIR", "/fake/qagredo/repo")
    proc = subprocess.run(
        ["bash", "-c", _BASH_DATA_DIR],
        env=clean,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


class OfflinePresetDataDirTest(unittest.TestCase):
    """Four main .env presets: data+txt, data+json, repo+txt, repo+json."""

    def test_preset_1_data_txt(self) -> None:
        out = _compute_data_dir(
            {
                "QAGREDO_OFFLINE_HOST": "data",
                "QAGREDO_OFFLINE_INPUT": "txt",
                "QAGREDO_SHARED_DATA_ROOT": "/srv/shared/Data",
            }
        )
        self.assertEqual(out, "/srv/shared/Data/txt")

    def test_preset_2_data_json(self) -> None:
        out = _compute_data_dir(
            {
                "QAGREDO_OFFLINE_HOST": "data",
                "QAGREDO_OFFLINE_INPUT": "json",
                "QAGREDO_SHARED_DATA_ROOT": "/srv/shared/Data",
            }
        )
        self.assertEqual(out, "/srv/shared/Data/json")

    def test_preset_3_repo_txt_default_root(self) -> None:
        out = _compute_data_dir(
            {
                "HOST_DIR": "/home/me/qagredo",
                "QAGREDO_OFFLINE_HOST": "repo",
                "QAGREDO_OFFLINE_INPUT": "txt",
            }
        )
        self.assertEqual(out, "/home/me/qagredo/data")

    def test_preset_3_repo_txt_explicit_repo_root(self) -> None:
        out = _compute_data_dir(
            {
                "HOST_DIR": "/home/me/qagredo",
                "QAGREDO_OFFLINE_HOST": "repo",
                "QAGREDO_OFFLINE_INPUT": "txt",
                "QAGREDO_REPO_DATA_ROOT": "/opt/qagredo-data",
            }
        )
        self.assertEqual(out, "/opt/qagredo-data")

    def test_preset_4_repo_json(self) -> None:
        out = _compute_data_dir(
            {
                "HOST_DIR": "/home/me/qagredo",
                "QAGREDO_OFFLINE_HOST": "repo",
                "QAGREDO_OFFLINE_INPUT": "json",
            }
        )
        self.assertEqual(out, "/home/me/qagredo/data")

    def test_linux_alias_same_as_repo(self) -> None:
        a = _compute_data_dir(
            {
                "HOST_DIR": "/r",
                "QAGREDO_OFFLINE_HOST": "linux",
                "QAGREDO_OFFLINE_INPUT": "txt",
            }
        )
        b = _compute_data_dir(
            {
                "HOST_DIR": "/r",
                "QAGREDO_OFFLINE_HOST": "repo",
                "QAGREDO_OFFLINE_INPUT": "txt",
            }
        )
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
