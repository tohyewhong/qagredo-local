"""Centralized helpers for loading and validating configuration."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

PROFILE_CONFIG_FILENAMES: Dict[str, str] = {
    "ollama": "config.ollama.yaml",
    "kubeflow": "config.kubeflow.yaml",
    "vllm": "config.vllm.yaml",
}

SUPPORTED_PROFILES = tuple(PROFILE_CONFIG_FILENAMES.keys())


def normalize_profile(profile: Optional[str]) -> Optional[str]:
    """Map QAGREDO_PROFILE values to a supported profile key."""
    if profile is None:
        return None
    key = str(profile).strip().lower()
    if not key:
        return None
    if key == "dev":
        return "ollama"
    if key in PROFILE_CONFIG_FILENAMES:
        return key
    return None


def resolve_profile(profile: Optional[str] = None) -> str:
    """Resolve profile from argument or QAGREDO_PROFILE; default ollama."""
    resolved = normalize_profile(profile or os.getenv("QAGREDO_PROFILE"))
    return resolved if resolved else "ollama"


def profile_config_path(profile: Optional[str] = None) -> Path:
    """Return config/config.<profile>.yaml for a supported profile."""
    resolved = resolve_profile(profile)
    return CONFIG_DIR / PROFILE_CONFIG_FILENAMES[resolved]


def default_config_path() -> Path:
    """Config file used when --config is omitted (uses QAGREDO_PROFILE)."""
    return profile_config_path()


ENV_API_KEY_VARS = {
    "vllm": "VLLM_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "openai": "OPENAI_API_KEY",
}

ENV_JUDGE_API_KEY_VARS = {
    "vllm": "VLLM_JUDGE_API_KEY",
    "ollama": "OLLAMA_JUDGE_API_KEY",
    "openai": "OPENAI_JUDGE_API_KEY",
}

# Cloud-based providers that require internet access
CLOUD_PROVIDERS = {"openai", "anthropic", "gemini", "azure_openai", "mistral"}

# Offline-capable providers (local OpenAI-compatible or Ollama)
OFFLINE_PROVIDERS = {"vllm", "ollama"}

MISSING_API_KEY_SENTINELS = {
    "",
    "REPLACE_ME",
    "CHANGEME",
    "CHANGE_ME",
    "YOUR_KEY_HERE",
}

ENV_PROVIDER_SETTING_VARS: Dict[str, Dict[str, str]] = {
    "vllm": {
        "base_url": "VLLM_BASE_URL",
        "timeout": "VLLM_TIMEOUT",
        "max_retries": "VLLM_MAX_RETRIES",
        "retry_delay": "VLLM_RETRY_DELAY",
        "temperature": "VLLM_TEMPERATURE",
        "max_tokens": "VLLM_MAX_TOKENS",
        "model": "VLLM_MODEL",
    },
    "ollama": {
        "base_url": "OLLAMA_BASE_URL",
        "timeout": "OLLAMA_TIMEOUT",
        "max_retries": "OLLAMA_MAX_RETRIES",
        "retry_delay": "OLLAMA_RETRY_DELAY",
        "temperature": "OLLAMA_TEMPERATURE",
        "max_tokens": "OLLAMA_MAX_TOKENS",
        "model": "OLLAMA_MODEL",
    },
    "openai": {
        "timeout": "OPENAI_TIMEOUT",
        "max_retries": "OPENAI_MAX_RETRIES",
        "retry_delay": "OPENAI_RETRY_DELAY",
        "temperature": "OPENAI_TEMPERATURE",
        "max_tokens": "OPENAI_MAX_TOKENS",
        "model": "OPENAI_MODEL",
        "base_url": "OPENAI_BASE_URL",
    },
}

ENV_JUDGE_SETTING_VARS: Dict[str, Dict[str, str]] = {
    "vllm": {
        "base_url": "VLLM_JUDGE_BASE_URL",
        "timeout": "VLLM_JUDGE_TIMEOUT",
        "max_retries": "VLLM_JUDGE_MAX_RETRIES",
        "retry_delay": "VLLM_JUDGE_RETRY_DELAY",
        "temperature": "VLLM_JUDGE_TEMPERATURE",
        "max_tokens": "VLLM_JUDGE_MAX_TOKENS",
        "model": "VLLM_JUDGE_MODEL",
    },
    "ollama": {
        "base_url": "OLLAMA_JUDGE_BASE_URL",
        "timeout": "OLLAMA_JUDGE_TIMEOUT",
        "max_retries": "OLLAMA_JUDGE_MAX_RETRIES",
        "retry_delay": "OLLAMA_JUDGE_RETRY_DELAY",
        "temperature": "OLLAMA_JUDGE_TEMPERATURE",
        "max_tokens": "OLLAMA_JUDGE_MAX_TOKENS",
        "model": "OLLAMA_JUDGE_MODEL",
    },
    "openai": {
        "timeout": "OPENAI_JUDGE_TIMEOUT",
        "max_retries": "OPENAI_JUDGE_MAX_RETRIES",
        "retry_delay": "OPENAI_JUDGE_RETRY_DELAY",
        "temperature": "OPENAI_JUDGE_TEMPERATURE",
        "max_tokens": "OPENAI_JUDGE_MAX_TOKENS",
        "model": "OPENAI_JUDGE_MODEL",
        "base_url": "OPENAI_JUDGE_BASE_URL",
    },
}


def is_offline_mode() -> bool:
    offline_env = os.getenv("OFFLINE_MODE", "").lower()
    return offline_env in ("1", "true", "yes", "on")


def validate_provider_for_offline_mode(
    provider: str, config: Optional[Dict[str, Any]] = None
) -> None:
    provider_lower = provider.lower() if provider else ""

    offline_mode = is_offline_mode()
    if config and config.get("offline_mode") is True:
        offline_mode = True

    if offline_mode and provider_lower in CLOUD_PROVIDERS:
        raise ValueError(
            f"Provider '{provider}' requires internet access and cannot be used in offline mode. "  # noqa: E501
            f"Please use one of the offline-capable providers: {', '.join(OFFLINE_PROVIDERS)}."  # noqa: E501
        )


def _ensure_path(path: Optional[os.PathLike[str]], fallback: Path) -> Path:
    if path is None:
        return fallback
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = (REPO_ROOT / resolved).resolve()
    return resolved


def _expand_env_vars(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(v) for v in obj]
    if isinstance(obj, str):
        return os.path.expanduser(os.path.expandvars(obj))
    return obj


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return _expand_env_vars(loaded) or {}


def _deep_merge(
    base: Dict[str, Any], override: Dict[str, Any]
) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _apply_offline_input_preset(config: Dict[str, Any]) -> None:
    """
    When both env vars are set, override ``run.*`` for folder batch mode.

    QAGREDO_OFFLINE_HOST (Linux paths only):

    - ``repo`` — inputs under this install’s ``data/`` tree (alias: ``linux``).
    - ``data`` — inputs under a shared root, e.g. ``.../Data/txt`` or ``json``.
    - ``wsl`` — optional; same as ``windows``; only if you mount the
      “Downloads/txt|json” layout (default ``/mnt/c/Users/.../Downloads``).

    QAGREDO_OFFLINE_INPUT: txt | json

    ``data`` + txt|json → ``DATA_DIR`` = shared root / txt|json (see run.sh).
    """
    host = (os.environ.get("QAGREDO_OFFLINE_HOST") or "").strip()
    kind = (os.environ.get("QAGREDO_OFFLINE_INPUT") or "").strip().lower()
    if not host or not kind:
        return
    if kind not in ("txt", "json"):
        raise ValueError(
            "QAGREDO_OFFLINE_INPUT must be 'txt' or 'json' "
            f"(got {kind!r})."
        )
    host_l = host.strip().lower()
    run = config.setdefault("run", {})
    run["input_file"] = ""
    run["input_glob"] = (
        "*.json,*.jsonl" if kind == "json" else "*.txt"
    )
    run["auto_convert"] = kind == "json"
    if host_l in ("repo", "linux"):
        run["input_folder"] = kind
    elif host_l in ("wsl", "windows"):
        run["input_folder"] = "."
    elif host_l == "data":
        run["input_folder"] = "."
    else:
        raise ValueError(
            "QAGREDO_OFFLINE_HOST must be 'repo', 'data', or 'wsl' "
            f"(got {host!r}; aliases: linux=repo, windows=wsl)."
        )


def _coerce_env_value(key: str, raw: str) -> Any:
    if key in {"timeout", "max_retries", "max_tokens"}:
        return int(raw)
    if key in {"retry_delay", "temperature"}:
        return float(raw)
    return raw


def _apply_section_environment_overrides(
    section: Dict[str, Any],
    provider: str,
    setting_vars: Dict[str, Dict[str, str]],
    api_key_vars: Dict[str, str],
) -> None:
    api_key_env_var = api_key_vars.get(provider)
    if api_key_env_var:
        api_key_env = os.getenv(api_key_env_var)
        if api_key_env:
            section["api_key"] = api_key_env

    for key, env_var in setting_vars.get(provider, {}).items():
        raw = os.getenv(env_var)
        if raw is None or raw == "":
            continue
        section[key] = _coerce_env_value(key, raw)

    if provider in ("vllm", "ollama") and not section.get("api_key"):
        section["api_key"] = "EMPTY"


def _apply_environment_overrides(config: Dict[str, Any]) -> None:
    # Profile YAML is the source of truth for provider, base_url, model, and
    # api_key. Environment variables are only used as a narrow power-user
    # escape hatch for the provider that the YAML already selected.
    # To switch providers, edit the profile YAML or set QAGREDO_PROFILE.
    llm_cfg = config.setdefault("llm", {})
    provider = (llm_cfg.get("provider") or "").lower()

    _apply_section_environment_overrides(
        llm_cfg, provider, ENV_PROVIDER_SETTING_VARS, ENV_API_KEY_VARS
    )

    judge_cfg = config.get("judge")
    if not isinstance(judge_cfg, dict):
        return

    judge_provider = (
        judge_cfg.get("provider") or llm_cfg.get("provider") or ""
    ).lower()
    if judge_provider and not judge_cfg.get("provider"):
        judge_cfg["provider"] = judge_provider

    _apply_section_environment_overrides(
        judge_cfg,
        judge_provider,
        ENV_JUDGE_SETTING_VARS,
        ENV_JUDGE_API_KEY_VARS,
    )


def load_config(
    config_path: Optional[os.PathLike[str]] = None,
) -> Dict[str, Any]:
    if config_path is None:
        path = default_config_path()
    else:
        path = Path(config_path)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
    if not path.exists():
        profile = normalize_profile(os.getenv("QAGREDO_PROFILE"))
        hint = (
            f"config/config.{profile}.yaml (QAGREDO_PROFILE={profile})"
            if profile
            else (
                "set QAGREDO_PROFILE in .env (ollama | kubeflow | vllm) "
                "or pass --config"
            )
        )
        raise FileNotFoundError(
            f"Configuration file not found: {path}\n"
            f"Create or fix the profile YAML — expected: {hint}\n"
            "See config/README.md for which file to edit."
        )
    config = _load_yaml(path)
    if "llm" not in config:
        raise ValueError("Configuration must include the 'llm' section.")
    return config


def build_effective_config(
    config_path: Optional[os.PathLike[str]] = None,
    *,
    provider_override: Optional[str] = None,
    model_override: Optional[str] = None,
    extra_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = load_config(config_path)

    if provider_override:
        config.setdefault("llm", {})["provider"] = provider_override

    if model_override:
        config.setdefault("llm", {})["model"] = model_override

    if extra_overrides:
        config = _deep_merge(config, extra_overrides)

    _apply_environment_overrides(config)
    _apply_offline_input_preset(config)

    llm_cfg = config.get("llm", {})
    provider_lower = str(llm_cfg.get("provider", "")).lower()
    api_key_val = llm_cfg.get("api_key")
    api_key_missing = (
        api_key_val is None
        or str(api_key_val).strip() in MISSING_API_KEY_SENTINELS
    )
    if provider_lower in CLOUD_PROVIDERS and api_key_missing:
        env_name = ENV_API_KEY_VARS.get(provider_lower, "<API_KEY_ENV_VAR>")
        raise ValueError(
            f"Provider '{provider_lower}' requires an API key.\n"
            f"- Export {env_name} in your environment."
        )

    provider = config.get("llm", {}).get("provider")
    if provider:
        validate_provider_for_offline_mode(provider, config)

    judge = config.get("judge")
    judge_provider = judge.get("provider") if isinstance(judge, dict) else None
    if judge_provider:
        validate_provider_for_offline_mode(judge_provider, config)

    return config


def build_llm_config(
    base_config_path: Optional[os.PathLike[str]] = None,
    *,
    provider_override: Optional[str] = None,
    model_override: Optional[str] = None,
    extra_overrides: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> Dict[str, Any]:
    return build_effective_config(
        base_config_path,
        provider_override=provider_override,
        model_override=model_override,
        extra_overrides=extra_overrides,
    )


__all__ = [
    "build_effective_config",
    "build_llm_config",
    "load_config",
    "is_offline_mode",
    "validate_provider_for_offline_mode",
    "CLOUD_PROVIDERS",
    "OFFLINE_PROVIDERS",
    "DEFAULT_CONFIG_PATH",
]
