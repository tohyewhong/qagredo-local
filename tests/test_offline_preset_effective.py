"""build_effective_config() with the four main .env presets."""

import os
import unittest
from pathlib import Path

from utils.config_manager import build_effective_config

_ROOT = Path(__file__).resolve().parent.parent
_OLLAMA_CONFIG = _ROOT / "config" / "config.ollama.yaml"


class OfflinePresetEffectiveTest(unittest.TestCase):
    """End-to-end config load + offline preset (config/config.ollama.yaml)."""

    def setUp(self) -> None:
        os.environ["QAGREDO_PROFILE"] = "ollama"

    def tearDown(self) -> None:
        for key in (
            "QAGREDO_OFFLINE_HOST",
            "QAGREDO_OFFLINE_INPUT",
            "QAGREDO_PROFILE",
        ):
            os.environ.pop(key, None)

    def test_preset_1_data_txt(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "data"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "txt"
        cfg = build_effective_config()
        run = cfg["run"]
        self.assertEqual(run["input_folder"], ".")
        self.assertEqual(run["input_file"], "")
        self.assertEqual(run["input_glob"], "*.txt")
        self.assertIs(run["auto_convert"], False)

    def test_preset_2_data_json(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "data"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "json"
        cfg = build_effective_config()
        run = cfg["run"]
        self.assertEqual(run["input_folder"], ".")
        self.assertEqual(run["input_glob"], "*.json,*.jsonl")
        self.assertIs(run["auto_convert"], True)

    def test_preset_3_repo_txt(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "repo"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "txt"
        cfg = build_effective_config()
        run = cfg["run"]
        self.assertEqual(run["input_folder"], "txt")
        self.assertEqual(run["input_glob"], "*.txt")
        self.assertIs(run["auto_convert"], False)

    def test_preset_4_repo_json(self) -> None:
        os.environ["QAGREDO_OFFLINE_HOST"] = "repo"
        os.environ["QAGREDO_OFFLINE_INPUT"] = "json"
        cfg = build_effective_config()
        run = cfg["run"]
        self.assertEqual(run["input_folder"], "json")
        self.assertEqual(run["input_glob"], "*.json,*.jsonl")
        self.assertIs(run["auto_convert"], True)

    def test_provider_from_yaml_not_env(self) -> None:
        cfg = build_effective_config(_OLLAMA_CONFIG)
        self.assertEqual(cfg["llm"].get("provider"), "ollama")


if __name__ == "__main__":
    unittest.main()
