"""Tests for profile-aware default config path resolution."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from utils.config_manager import (
    default_config_path,
    normalize_profile,
    profile_config_path,
    resolve_profile,
)


class TestProfileConfigPath(unittest.TestCase):
    def test_normalize_profile_maps_dev_to_ollama(self) -> None:
        self.assertEqual(normalize_profile("dev"), "ollama")

    def test_normalize_profile_unknown_returns_none(self) -> None:
        self.assertIsNone(normalize_profile("azure"))

    def test_profile_config_path_explicit_vllm(self) -> None:
        path = profile_config_path("vllm")
        self.assertTrue(path.name.endswith("config.vllm.yaml"))

    @mock.patch.dict(os.environ, {"QAG_PROFILE": "kubeflow"}, clear=False)
    def test_default_config_path_from_env(self) -> None:
        path = default_config_path()
        self.assertTrue(path.name.endswith("config.kubeflow.yaml"))

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_default_config_path_without_profile_defaults_ollama(self) -> None:
        os.environ.pop("QAG_PROFILE", None)
        path = default_config_path()
        self.assertEqual(path.name, "config.ollama.yaml")

    @mock.patch.dict(os.environ, {"QAG_PROFILE": "dev"}, clear=False)
    def test_resolve_profile_maps_dev_to_ollama(self) -> None:
        self.assertEqual(resolve_profile(), "ollama")


if __name__ == "__main__":
    unittest.main()
