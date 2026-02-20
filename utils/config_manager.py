"""Centralized helpers for loading and validating configuration."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"

ENV_API_KEY_VARS = {
    "vllm": "VLLM_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# Cloud-based providers that require internet access
CLOUD_PROVIDERS = {"openai", "anthropic", "gemini", "azure_openai", "mistral"}

# Offline-capable providers
OFFLINE_PROVIDERS = {"vllm"}

MISSING_API_KEY_SENTINELS = {"", "REPLACE_ME", "CHANGEME", "CHANGE_ME", "YOUR_KEY_HERE"}

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


def is_offline_mode() -> bool:
    offline_env = os.getenv("OFFLINE_MODE", "").lower()
    return offline_env in ("1", "true", "yes", "on")


def validate_provider_for_offline_mode(provider: str, config: Optional[Dict[str, Any]] = None) -> None:
    provider_lower = provider.lower() if provider else ""

    offline_mode = is_offline_mode()
    if config and config.get("offline_mode") is True:
        offline_mode = True

    if offline_mode and provider_lower in CLOUD_PROVIDERS:
        raise ValueError(
            f"Provider '{provider}' requires internet access and cannot be used in offline mode. "
            f"Please use one of the offline-capable providers: {', '.join(OFFLINE_PROVIDERS)}."
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


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _apply_profile_selection(config: Dict[str, Any]) -> None:
    run_cfg = config.get("run") if isinstance(config.get("run"), dict) else {}
    profile = run_cfg.get("profile")
    if profile is None or str(profile).strip() == "":
        return

    profile_key = str(profile).strip()
    profiles_cfg = config.get("profiles") if isinstance(config.get("profiles"), dict) else {}
    selected = profiles_cfg.get(profile_key) if isinstance(profiles_cfg, dict) else None
    if not isinstance(selected, dict):
        return

    selected_llm = selected.get("llm")
    if not isinstance(selected_llm, dict) or not selected_llm:
        return

    config["llm"] = _deep_merge(config.get("llm", {}) or {}, selected_llm)


def _apply_environment_overrides(config: Dict[str, Any]) -> None:
    llm_cfg = config.setdefault("llm", {})
    provider = (llm_cfg.get("provider") or "").lower()

    api_key_env_var = ENV_API_KEY_VARS.get(provider)
    if api_key_env_var:
        api_key_env = os.getenv(api_key_env_var)
        if api_key_env:
            llm_cfg["api_key"] = api_key_env

    for key, env_var in ENV_PROVIDER_SETTING_VARS.get(provider, {}).items():
        raw = os.getenv(env_var)
        if raw is None or raw == "":
            continue
        if key in {"timeout", "max_retries", "max_tokens"}:
            llm_cfg[key] = int(raw)
        elif key in {"retry_delay", "temperature"}:
            llm_cfg[key] = float(raw)
        else:
            llm_cfg[key] = raw

    if provider == "vllm" and not llm_cfg.get("api_key"):
        llm_cfg["api_key"] = "EMPTY"


def load_config(config_path: Optional[os.PathLike[str]] = None) -> Dict[str, Any]:
    path = _ensure_path(config_path, DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {path}\n"
            "Please create one at config/config.yaml"
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

    _apply_profile_selection(config)
    _apply_environment_overrides(config)

    llm_cfg = config.get("llm", {})
    provider_lower = str(llm_cfg.get("provider", "")).lower()
    api_key_val = llm_cfg.get("api_key")
    api_key_missing = api_key_val is None or str(api_key_val).strip() in MISSING_API_KEY_SENTINELS
    if provider_lower in CLOUD_PROVIDERS and api_key_missing:
        env_name = ENV_API_KEY_VARS.get(provider_lower, "<API_KEY_ENV_VAR>")
        raise ValueError(
            f"Provider '{provider_lower}' requires an API key.\n"
            f"- Export {env_name} in your environment."
        )

    provider = config.get("llm", {}).get("provider")
    if provider:
        validate_provider_for_offline_mode(provider, config)

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

