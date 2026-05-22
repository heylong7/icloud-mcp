# tests/test_config.py
import os
from unittest.mock import patch

from event_extractor.config import (
    get_llm_provider_name,
    get_provider_config,
    require_env,
)


class TestRequireEnv:
    def test_returns_value_when_set(self):
        with patch.dict(os.environ, {"TEST_VAR": "hello"}, clear=True):
            assert require_env("TEST_VAR") == "hello"

    def test_raises_runtime_error_when_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            try:
                require_env("MISSING_VAR")
                assert False, "should have raised"
            except RuntimeError as e:
                assert "MISSING_VAR" in str(e)

    def test_returns_default_when_missing_and_default_provided(self):
        with patch.dict(os.environ, {}, clear=True):
            assert require_env("MISSING_VAR", default="fallback") == "fallback"

    def test_returns_value_over_default_when_set(self):
        with patch.dict(os.environ, {"VAR": "custom"}, clear=True):
            assert require_env("VAR", default="fallback") == "custom"


class TestGetLlmProviderName:
    def test_returns_env_value(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "deepseek"}, clear=True):
            assert get_llm_provider_name() == "deepseek"

    def test_returns_none_when_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_llm_provider_name() is None


class TestGetProviderConfig:
    def test_returns_deepseek_config(self):
        env = {
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-chat",
        }
        with patch.dict(os.environ, env, clear=True):
            config = get_provider_config()
            assert config["api_key"] == "sk-test"
            assert config["base_url"] == "https://api.deepseek.com"
            assert config["model"] == "deepseek-chat"

    def test_returns_openai_config(self):
        env = {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-openai-test",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_MODEL": "gpt-4o",
        }
        with patch.dict(os.environ, env, clear=True):
            config = get_provider_config()
            assert config["api_key"] == "sk-openai-test"
            assert config["base_url"] == "https://api.openai.com/v1"
            assert config["model"] == "gpt-4o"

    def test_uses_defaults_when_optionals_missing(self):
        env = {
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
        }
        with patch.dict(os.environ, env, clear=True):
            config = get_provider_config()
            assert config["api_key"] == "sk-test"
            assert config["base_url"] == "https://api.deepseek.com"
            assert config["model"] == "deepseek-chat"

    def test_raises_when_api_key_missing(self):
        env = {"LLM_PROVIDER": "deepseek"}
        with patch.dict(os.environ, env, clear=True):
            try:
                get_provider_config()
                assert False, "should have raised"
            except RuntimeError as e:
                assert "DEEPSEEK_API_KEY" in str(e)
