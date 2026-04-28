"""Detect OpenAI-compatible base URLs that point at Ollama (port 11434)."""

from __future__ import annotations

from urllib.parse import urlparse


def is_ollama_openai_base_url(base_url: str) -> bool:
    """True when ``base_url`` is the /v1 shim on a typical Ollama listen address."""
    try:
        p = urlparse(base_url)
        if p.port != 11434:
            return False
        host = (p.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}:
            return True
        if host == "ollama":
            return True
        return False
    except Exception:
        return False
