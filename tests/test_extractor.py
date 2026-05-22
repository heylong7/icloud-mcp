# tests/test_extractor.py
from unittest.mock import MagicMock, patch

import pytest

from event_extractor.extractor import (
    calendar_dedup_check,
    classify_events,
    extract_and_sync,
    run_extraction_pipeline,
)
from event_extractor.dedup import EmailDedup


class TestClassifyEvents:
    def test_splits_explicit_and_vague(self):
        events = [
            {"title": "A", "time_clarity": "explicit", "source_email_uid": "1"},
            {"title": "B", "time_clarity": "vague", "source_email_uid": "2"},
            {"title": "C", "time_clarity": "explicit", "source_email_uid": "3"},
        ]
        explicit, vague = classify_events(events)
        assert len(explicit) == 2
        assert len(vague) == 1
        assert all(e["time_clarity"] == "explicit" for e in explicit)
        assert all(e["time_clarity"] == "vague" for e in vague)

    def test_handles_empty_list(self):
        explicit, vague = classify_events([])
        assert explicit == []
        assert vague == []


class TestCalendarDedupCheck:
    def test_returns_true_when_similar_event_exists(self, mock_calendar, monkeypatch):
        monkeypatch.setenv("APPLE_ID", "test@icloud.com")
        monkeypatch.setenv("ICLOUD_APP_PASSWORD", "xxxx-xxxx-xxxx-xxxx")
        mock_cal = mock_calendar.return_value.principal.return_value.calendars.return_value[0]
        fake_event = MagicMock()
        fake_event.component.get.return_value = "Q3 Planning Meeting"
        mock_cal.search.return_value = [fake_event]

        event = {
            "title": "Q3 Planning Meeting",
            "start": "2026-06-15T14:00:00",
        }
        assert calendar_dedup_check(event, calendar_name="Calendar") is True

    def test_returns_false_when_no_similar_event(self, mock_calendar, monkeypatch):
        monkeypatch.setenv("APPLE_ID", "test@icloud.com")
        monkeypatch.setenv("ICLOUD_APP_PASSWORD", "xxxx-xxxx-xxxx-xxxx")
        mock_cal = mock_calendar.return_value.principal.return_value.calendars.return_value[0]
        mock_cal.search.return_value = []

        event = {
            "title": "Unique Event Title",
            "start": "2026-06-15T14:00:00",
        }
        assert calendar_dedup_check(event, calendar_name="Calendar") is False


class TestRunExtractionPipeline:
    def test_full_pipeline_returns_classified_events(self, mock_llm_response_mixed, temp_db_path):
        from event_extractor.providers.base import BaseLLMProvider

        class FakeProvider(BaseLLMProvider):
            def extract_events(self, emails):
                return mock_llm_response_mixed

        provider = FakeProvider(api_key="sk", base_url="http://x", model="m")
        emails = [
            {"uid": "1001", "subject": "Meeting", "from": "a@b.com", "date": "", "body": "test"},
            {"uid": "1003", "subject": "Coffee", "from": "c@d.com", "date": "", "body": "test"},
        ]

        explicit, vague = run_extraction_pipeline(provider, emails, auto_create=False, dedup_db_path=temp_db_path)
        assert len(explicit) == 1
        assert explicit[0]["time_clarity"] == "explicit"
        assert len(vague) == 1
        assert vague[0]["time_clarity"] == "vague"

    def test_auto_create_creates_explicit_events(self, mock_llm_response_explicit, mock_calendar, monkeypatch, temp_db_path):
        from event_extractor.providers.base import BaseLLMProvider

        monkeypatch.setenv("APPLE_ID", "test@icloud.com")
        monkeypatch.setenv("ICLOUD_APP_PASSWORD", "xxxx-xxxx-xxxx-xxxx")
        mock_cal = mock_calendar.return_value.principal.return_value.calendars.return_value[0]
        mock_cal.search.return_value = []  # no existing events

        class FakeProvider(BaseLLMProvider):
            def extract_events(self, emails):
                return mock_llm_response_explicit

        provider = FakeProvider(api_key="sk", base_url="http://x", model="m")
        emails = [
            {"uid": "1001", "subject": "Meeting", "from": "a@b.com", "date": "", "body": "test"},
        ]

        explicit, vague = run_extraction_pipeline(provider, emails, auto_create=True, dedup_db_path=temp_db_path)
        assert len(explicit) >= 1
        assert mock_cal.save_event.called

    def test_pipeline_returns_empty_for_empty_emails(self, temp_db_path):
        from event_extractor.providers.base import BaseLLMProvider

        class FakeProvider(BaseLLMProvider):
            def extract_events(self, emails):
                return []

        provider = FakeProvider(api_key="sk", base_url="http://x", model="m")
        explicit, vague = run_extraction_pipeline(provider, [], auto_create=False, dedup_db_path=temp_db_path)
        assert explicit == []
        assert vague == []
