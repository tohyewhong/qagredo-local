"""Tests for QAGREDO_OFFLINE_* env presets (config_manager)."""

import os
import unittest

from utils.config_manager import _apply_offline_input_preset


class OfflinePresetTest(unittest.TestCase):
    def tearDown(self) -> None:
        for key in (
            "QAGREDO_OFFLINE_HOST",
            "QAGREDO_OFFLINE_INPUT",
        ):
            os.environ.pop(key, None)

    def test_repo_txt(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "repo"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "txt"
        cfg: dict = {"run": {"input_folder": "x", "input_file": "y"}}
        _apply_offline_input_preset(cfg)
        run = cfg["run"]
        self.assertEqual(run["input_folder"], "txt")
        self.assertEqual(run["input_file"], "")
        self.assertEqual(run["input_glob"], "*.txt")
        self.assertIs(run["auto_convert"], False)

    def test_repo_json(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "repo"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "json"
        cfg: dict = {"run": {}}
        _apply_offline_input_preset(cfg)
        run = cfg["run"]
        self.assertEqual(run["input_folder"], "json")
        self.assertEqual(run["input_glob"], "*.json,*.jsonl")
        self.assertIs(run["auto_convert"], True)

    def test_data_txt(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "data"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "txt"
        cfg: dict = {"run": {}}
        _apply_offline_input_preset(cfg)
        run = cfg["run"]
        self.assertEqual(run["input_folder"], ".")
        self.assertEqual(run["input_glob"], "*.txt")

    def test_data_json(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "data"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "json"
        cfg: dict = {"run": {}}
        _apply_offline_input_preset(cfg)
        self.assertEqual(cfg["run"]["input_folder"], ".")
        self.assertEqual(cfg["run"]["input_glob"], "*.json,*.jsonl")

    def test_windows_txt(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "wsl"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "txt"
        cfg: dict = {"run": {}}
        _apply_offline_input_preset(cfg)
        self.assertEqual(cfg["run"]["input_folder"], ".")

    def test_empty_env_noop(self) -> None:
        cfg: dict = {"run": {"input_folder": "keep"}}
        _apply_offline_input_preset(cfg)
        self.assertEqual(cfg["run"]["input_folder"], "keep")

    def test_linux_alias_txt(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "linux"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "txt"
        cfg: dict = {"run": {}}
        _apply_offline_input_preset(cfg)
        self.assertEqual(cfg["run"]["input_folder"], "txt")

    def test_bad_kind_raises(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "repo"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "pdf"
        with self.assertRaises(ValueError):
            _apply_offline_input_preset({"run": {}})

    def test_bad_host_raises(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "macos"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "txt"
        with self.assertRaises(ValueError):
            _apply_offline_input_preset({"run": {}})


if __name__ == "__main__":
    unittest.main()
