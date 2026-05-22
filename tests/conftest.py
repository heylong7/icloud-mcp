# tests/conftest.py
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_db_path():
    """Create a temporary SQLite database path for dedup tests."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def sample_emails():
    """Return a list of sample email dicts matching the format from list_messages/get_message."""
    return [
        {
            "uid": "1001",
            "subject": "Meeting about Q3 planning",
            "from": "boss@company.com",
            "date": "2026-06-10",
            "body": "Hi team, let's meet on June 15 at 2:00 PM in Room 301 to discuss Q3 planning. The meeting will last about 2 hours.",
            "read": False,
        },
        {
            "uid": "1002",
            "subject": "Workshop invitation",
            "from": "training@company.com",
            "date": "2026-06-11",
            "body": "You are invited to a Python workshop next Friday at 10:00 AM. Location: Training Room B.",
            "read": False,
        },
        {
            "uid": "1003",
            "subject": "Catch up sometime?",
            "from": "friend@personal.com",
            "date": "2026-06-12",
            "body": "Hey, let's grab coffee sometime next week! Looking forward to catching up.",
            "read": False,
        },
        {
            "uid": "1004",
            "subject": "Paper submission deadline",
            "from": "editor@journal.com",
            "date": "2026-06-09",
            "body": "Please submit your final manuscript by June 20. Late submissions will not be accepted.",
            "read": False,
        },
    ]


@pytest.fixture
def mock_llm_response_explicit():
    """Mock LLM response with explicit-time events."""
    return [
        {
            "title": "Q3 Planning Meeting",
            "start": "2026-06-15T14:00:00",
            "end": "2026-06-15T16:00:00",
            "location": "Room 301",
            "description": "Discuss Q3 planning with team",
            "source_email_uid": "1001",
            "time_clarity": "explicit",
        },
        {
            "title": "Python Workshop",
            "start": "2026-06-19T10:00:00",
            "end": "2026-06-19T12:00:00",
            "location": "Training Room B",
            "description": "Python workshop",
            "source_email_uid": "1002",
            "time_clarity": "explicit",
        },
        {
            "title": "Paper Submission Deadline",
            "start": "2026-06-20T22:59:00",
            "end": "2026-06-20T23:59:00",
            "location": None,
            "description": "Submit final manuscript",
            "source_email_uid": "1004",
            "time_clarity": "explicit",
        },
    ]


@pytest.fixture
def mock_llm_response_mixed():
    """Mock LLM response with mixed explicit/vague events."""
    return [
        {
            "title": "Q3 Planning Meeting",
            "start": "2026-06-15T14:00:00",
            "end": "2026-06-15T16:00:00",
            "location": "Room 301",
            "description": "Discuss Q3 planning with team",
            "source_email_uid": "1001",
            "time_clarity": "explicit",
        },
        {
            "title": "Coffee catch-up",
            "start": "2026-06-16T12:00:00",
            "end": "2026-06-16T13:00:00",
            "location": None,
            "description": "Grab coffee next week",
            "source_email_uid": "1003",
            "time_clarity": "vague",
        },
    ]


@pytest.fixture
def mock_calendar():
    """Mock caldav DAVClient and calendar for testing auto-create."""
    with patch("caldav.davclient.DAVClient", autospec=True) as mock_client:
        mock_cal = MagicMock()
        mock_cal.name = "Calendar"
        mock_cal.url = "https://caldav.icloud.com/calendars/test"
        mock_client.return_value.principal.return_value.calendars.return_value = [mock_cal]
        yield mock_client
