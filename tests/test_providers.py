# tests/test_providers.py
import json
from unittest.mock import MagicMock, patch

import pytest
from openai import OpenAI

from event_extractor.providers.base import BaseLLMProvider


class TestBaseLLMProvider:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseLLMProvider(api_key="sk-test", base_url="http://localhost", model="test")

    def test_concrete_subclass_must_implement_extract_events(self):
        class IncompleteProvider(BaseLLMProvider):
            pass

        with pytest.raises(TypeError):
            IncompleteProvider(api_key="sk-test", base_url="http://localhost", model="test")

    def test_concrete_subclass_instantiates(self):
        class CompleteProvider(BaseLLMProvider):
            def extract_events(self, emails):
                return []

        provider = CompleteProvider(api_key="sk-test", base_url="http://localhost", model="test")
        assert provider.api_key == "sk-test"
        assert provider.base_url == "http://localhost"
        assert provider.model == "test"
        assert isinstance(provider.client, OpenAI)

    def test_client_is_configured_correctly(self):
        class TestProvider(BaseLLMProvider):
            def extract_events(self, emails):
                return []

        provider = TestProvider(
            api_key="sk-mykey",
            base_url="https://custom.api.com/v1",
            model="custom-model",
        )
        assert provider.client.api_key == "sk-mykey"
        assert str(provider.client.base_url) == "https://custom.api.com/v1/"

    def test_build_prompt_includes_emails(self):
        class TestProvider(BaseLLMProvider):
            def extract_events(self, emails):
                return []

        provider = TestProvider(api_key="sk", base_url="http://x.com", model="m")
        emails = [
            {"uid": "1", "subject": "Meeting", "from": "a@b.com", "date": "2026-06-10", "body": "Let's meet"},
        ]
        prompt = provider._build_prompt(emails)
        assert "Meeting" in prompt
        assert "Let's meet" in prompt
        assert "a@b.com" in prompt
        assert "UID: 1" in prompt

    def test_build_prompt_includes_system_instructions(self):
        from event_extractor.providers.base import SYSTEM_PROMPT
        assert "event extractor" in SYSTEM_PROMPT.lower()
        assert "JSON" in SYSTEM_PROMPT
        assert "time_clarity" in SYSTEM_PROMPT


class TestDeepSeekProvider:
    def test_instantiation(self):
        from event_extractor.providers.deepseek import DeepSeekProvider
        provider = DeepSeekProvider(
            api_key="sk-ds-test",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )
        assert provider.api_key == "sk-ds-test"
        assert provider.model == "deepseek-chat"

    def test_extract_events_calls_api_and_returns_parsed_json(self):
        from event_extractor.providers.deepseek import DeepSeekProvider

        provider = DeepSeekProvider(
            api_key="sk-ds-test",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )

        fake_response = MagicMock()
        fake_response.choices = [
            MagicMock(message=MagicMock(content='[{"title":"Test Event","start":"2026-06-15T14:00:00","end":"2026-06-15T16:00:00","location":"Room 1","description":"test desc","source_email_uid":"1001","time_clarity":"explicit"}]'))
        ]

        with patch.object(provider.client.chat.completions, "create", return_value=fake_response):
            emails = [
                {"uid": "1001", "subject": "Meeting", "from": "a@b.com", "date": "2026-06-10", "body": "Meet on June 15 2pm"},
            ]
            events = provider.extract_events(emails)
            assert len(events) == 1
            assert events[0]["title"] == "Test Event"
            assert events[0]["time_clarity"] == "explicit"
            assert events[0]["source_email_uid"] == "1001"

    def test_extract_events_uses_correct_system_prompt(self):
        from event_extractor.providers.deepseek import DeepSeekProvider

        provider = DeepSeekProvider(
            api_key="sk-ds-test",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )

        fake_response = MagicMock()
        fake_response.choices = [
            MagicMock(message=MagicMock(content='[]'))
        ]

        with patch.object(provider.client.chat.completions, "create", return_value=fake_response) as mock_create:
            provider.extract_events([{"uid": "1", "subject": "Hi", "from": "x@y.com", "date": "", "body": "Hello"}])
            call_args = mock_create.call_args.kwargs
            assert call_args["model"] == "deepseek-chat"
            assert call_args["messages"][0]["role"] == "system"
            assert "event extractor" in call_args["messages"][0]["content"].lower()
            assert call_args["messages"][1]["role"] == "user"

    def test_extract_events_handles_empty_email_list(self):
        from event_extractor.providers.deepseek import DeepSeekProvider

        provider = DeepSeekProvider(
            api_key="sk-ds-test",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )

        fake_response = MagicMock()
        fake_response.choices = [
            MagicMock(message=MagicMock(content='[]'))
        ]

        with patch.object(provider.client.chat.completions, "create", return_value=fake_response):
            events = provider.extract_events([])
            assert events == []

    def test_extract_events_handles_malformed_json_response(self):
        from event_extractor.providers.deepseek import DeepSeekProvider

        provider = DeepSeekProvider(
            api_key="sk-ds-test",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )

        fake_response = MagicMock()
        fake_response.choices = [
            MagicMock(message=MagicMock(content="not valid json"))
        ]

        with patch.object(provider.client.chat.completions, "create", return_value=fake_response):
            events = provider.extract_events([{"uid": "1", "subject": "Hi", "from": "x@y.com", "date": "", "body": "Hello"}])
            assert events == []


class TestOpenAIProvider:
    def test_instantiation(self):
        from event_extractor.providers.openai import OpenAIProvider
        provider = OpenAIProvider(
            api_key="sk-oai-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        assert provider.api_key == "sk-oai-test"
        assert provider.model == "gpt-4o"

    def test_extract_events_calls_api(self):
        from event_extractor.providers.openai import OpenAIProvider

        provider = OpenAIProvider(
            api_key="sk-oai-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )

        fake_response = MagicMock()
        fake_response.choices = [
            MagicMock(message=MagicMock(content='[{"title":"OAI Event","start":"2026-07-01T09:00:00","end":"2026-07-01T10:00:00","location":null,"description":"desc","source_email_uid":"2001","time_clarity":"vague"}]'))
        ]

        with patch.object(provider.client.chat.completions, "create", return_value=fake_response):
            emails = [{"uid": "2001", "subject": "Test", "from": "x@y.com", "date": "", "body": "Sometime in July"}]
            events = provider.extract_events(emails)
            assert len(events) == 1
            assert events[0]["title"] == "OAI Event"
            assert events[0]["time_clarity"] == "vague"
