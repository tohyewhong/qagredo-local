"""Backend selection tests for local Ollama and vLLM profiles."""

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from utils.config_manager import build_effective_config
from utils import hallucination_checker as hc


class BackendSelectionTest(unittest.TestCase):
    """Verify generator and judge backend selection stays explicit."""

    def _write_config(self, text: str) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_judge_ollama_env_overrides_are_provider_scoped(self) -> None:
        config_path = self._write_config(
            """
offline_mode: true
llm:
  provider: ollama
  model: generator
  base_url: http://localhost:11434/v1
judge:
  provider: ollama
  model: judge-from-yaml
  base_url: http://localhost:11434/v1
"""
        )
        env = {
            "OLLAMA_JUDGE_MODEL": "judge-from-ollama-env",
            "VLLM_JUDGE_MODEL": "judge-from-vllm-env",
        }

        with patch.dict(os.environ, env, clear=True):
            cfg = build_effective_config(config_path)

        self.assertEqual(cfg["judge"]["model"], "judge-from-ollama-env")
        self.assertEqual(cfg["judge"]["provider"], "ollama")

    def test_judge_vllm_env_overrides_are_provider_scoped(self) -> None:
        config_path = self._write_config(
            """
offline_mode: true
llm:
  provider: vllm
  model: generator
  base_url: http://vllm:7100/v1
judge:
  provider: vllm
  model: judge-from-yaml
  base_url: http://vllm-judge:7101/v1
"""
        )
        env = {
            "OLLAMA_JUDGE_MODEL": "judge-from-ollama-env",
            "VLLM_JUDGE_MODEL": "judge-from-vllm-env",
        }

        with patch.dict(os.environ, env, clear=True):
            cfg = build_effective_config(config_path)

        self.assertEqual(cfg["judge"]["model"], "judge-from-vllm-env")
        self.assertEqual(cfg["judge"]["provider"], "vllm")

    def test_ollama_judge_provider_uses_native_chat(self) -> None:
        hc.set_llm_config(
            {
                "offline_mode": True,
                "llm": {
                    "provider": "ollama",
                    "model": "gen-model",
                    "base_url": "http://localhost:11434/v1",
                    "api_key": "EMPTY",
                },
                "judge": {
                    "provider": "ollama",
                    "model": "judge-model",
                    "base_url": "http://localhost:11434/v1",
                    "api_key": "EMPTY",
                },
            }
        )

        with patch.object(
            hc,
            "_call_ollama_chat_native",
            return_value='{"verdict":"SUPPORTED","confidence":0.9}',
        ) as native_chat:
            verdict = hc._call_llm_judge("answer", "document", "question")

        native_chat.assert_called_once()
        self.assertEqual(verdict["verdict"], "SUPPORTED")

    def test_vllm_judge_provider_uses_openai_compatible_client(self) -> None:
        hc.set_llm_config(
            {
                "offline_mode": True,
                "llm": {
                    "provider": "vllm",
                    "model": "gen-model",
                    "base_url": "http://vllm:7100/v1",
                    "api_key": "gen-key",
                },
                "judge": {
                    "provider": "vllm",
                    "model": "judge-model",
                    "base_url": "http://localhost:11434/v1",
                    "api_key": "judge-key",
                },
            }
        )
        message = types.SimpleNamespace(
            content='{"verdict":"SUPPORTED","confidence":0.8}'
        )
        choice = types.SimpleNamespace(message=message)
        response = types.SimpleNamespace(choices=[choice])
        completions = types.SimpleNamespace(
            create=Mock(return_value=response)
        )
        chat = types.SimpleNamespace(completions=completions)
        client = types.SimpleNamespace(chat=chat)
        openai_module = types.SimpleNamespace(
            OpenAI=Mock(return_value=client)
        )

        with patch.dict("sys.modules", {"openai": openai_module}):
            verdict = hc._call_llm_judge("answer", "document", "question")

        openai_module.OpenAI.assert_called_once_with(
            base_url="http://localhost:11434/v1",
            api_key="judge-key",
            timeout=60,
        )
        completions.create.assert_called_once()
        self.assertEqual(verdict["verdict"], "SUPPORTED")


if __name__ == "__main__":
    unittest.main()
