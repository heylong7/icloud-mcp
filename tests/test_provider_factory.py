# tests/test_provider_factory.py
from unittest.mock import patch

import pytest

from event_extractor.provider_factory import get_provider


class TestGetProvider:
    def test_returns_deepseek_provider(self):
        from event_extractor.providers.deepseek import DeepSeekProvider

        config = {
            "provider": "deepseek",
            "api_key": "sk-test",
            "base_url": "https://platform.deepseek.com",
            "model": "deepseek-chat",
        }
        provider = get_provider(config)
        assert isinstance(provider, DeepSeekProvider)
        assert provider.api_key == "sk-test"
        assert provider.model == "deepseek-chat"

    def test_returns_openai_provider(self):
        from event_extractor.providers.openai import OpenAIProvider

        config = {
            "provider": "openai",
            "api_key": "sk-test",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
        }
        provider = get_provider(config)
        assert isinstance(provider, OpenAIProvider)

    def test_raises_on_unknown_provider(self):
        config = {
            "provider": "unknown_xyz",
            "api_key": "sk-test",
            "base_url": "http://localhost",
            "model": "test",
        }
        with pytest.raises(RuntimeError, match="Cannot find provider module"):
            get_provider(config)

    def test_raises_on_missing_provider_module(self):
        with patch("importlib.import_module", side_effect=ImportError("No module")):
            config = {
                "provider": "missing_provider",
                "api_key": "sk-test",
                "base_url": "http://localhost",
                "model": "test",
            }
            with pytest.raises(RuntimeError, match="Cannot find provider module"):
                get_provider(config)

    def test_raises_when_no_provider_class_found_in_module(self):
        mock_module = type("module", (), {})()
        mock_module.NotAProvider = type("X", (), {})
        with patch("importlib.import_module", return_value=mock_module):
            config = {
                "provider": "bad_module",
                "api_key": "sk-test",
                "base_url": "http://localhost",
                "model": "test",
            }
            with pytest.raises(RuntimeError, match="No.*Provider class found"):
                get_provider(config)
