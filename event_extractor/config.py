# event_extractor/config.py
"""Environment-variable configuration for the event extractor."""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

_PROVIDER_DEFAULTS = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_default": "https://api.deepseek.com",
        "model_default": "deepseek-chat",
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url_default": "https://api.openai.com/v1",
        "model_default": "gpt-4o",
    },
}


def require_env(name: str, default: Optional[str] = None) -> str:
    """Return a required environment variable, or raise if missing."""
    value = os.environ.get(name, default)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value.strip()


def get_llm_provider_name() -> Optional[str]:
    """Return the configured LLM provider name, or None if not set."""
    return os.environ.get("LLM_PROVIDER", "").strip() or None


def get_provider_config() -> dict:
    """Return the full configuration dict for the configured LLM provider."""
    provider = get_llm_provider_name()
    if not provider:
        raise RuntimeError("LLM_PROVIDER env var is not set")

    provider = provider.lower()
    if provider not in _PROVIDER_DEFAULTS:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER: {provider}. Known: {list(_PROVIDER_DEFAULTS)}"
        )

    defaults = _PROVIDER_DEFAULTS[provider]
    prefix = provider.upper()

    api_key = require_env(defaults["api_key_env"])
    base_url = os.environ.get(f"{prefix}_BASE_URL", defaults["base_url_default"]).strip()
    model = os.environ.get(f"{prefix}_MODEL", defaults["model_default"]).strip()

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }
