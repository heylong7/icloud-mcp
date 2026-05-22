# event_extractor/provider_factory.py
"""Dynamic provider loading via importlib."""

import importlib
import logging
from typing import Any, Dict

from event_extractor.providers.base import BaseLLMProvider

log = logging.getLogger(__name__)


def get_provider(config: Dict[str, Any]) -> BaseLLMProvider:
    """Dynamically load and instantiate the LLM provider from config.

    Args:
        config: Dict with keys: provider, api_key, base_url, model

    Returns:
        An instance of BaseLLMProvider subclass

    Raises:
        RuntimeError: If provider module cannot be loaded or no Provider class found
    """
    provider_name = config["provider"].lower()
    module_name = f"event_extractor.providers.{provider_name}"

    try:
        module = importlib.import_module(module_name)
    except ImportError:
        raise RuntimeError(
            f"Cannot find provider module '{module_name}'. "
            f"Create event_extractor/providers/{provider_name}.py with a "
            f"class ending in 'Provider' that extends BaseLLMProvider."
        )

    # Find class ending in "Provider" (case-insensitive)
    provider_class = None
    for attr_name in dir(module):
        if attr_name.lower().endswith("provider") and attr_name != "BaseLLMProvider":
            candidate = getattr(module, attr_name)
            if isinstance(candidate, type) and issubclass(candidate, BaseLLMProvider):
                provider_class = candidate
                break

    if provider_class is None:
        raise RuntimeError(
            f"No *Provider class found in {module_name}. "
            f"Create a class ending in 'Provider' that extends BaseLLMProvider."
        )

    return provider_class(
        api_key=config["api_key"],
        base_url=config["base_url"],
        model=config["model"],
    )
