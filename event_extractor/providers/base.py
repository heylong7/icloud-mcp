# event_extractor/providers/base.py
"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from openai import OpenAI

SYSTEM_PROMPT = """You are an email event extractor. Extract time-sensitive events from the following emails.

Event types: meetings, lectures, seminars, workshops, job fairs, paper deadlines,
copyright signing, review invitations, flights, hotel bookings, exams, course schedules.

Output ONLY a JSON array. Each event:
{
  "title": "event title",
  "start": "2026-06-15T14:00:00",
  "end": "2026-06-15T16:00:00",
  "location": "location if present, otherwise null",
  "description": "key info extracted from email",
  "source_email_uid": "email UID",
  "time_clarity": "explicit"
}

Rules:
- If NO clear time is mentioned, DO NOT extract — skip the event entirely
- time_clarity: "explicit" when a specific date + time is given; "vague" when timing is only implied
- If end time is missing, use start + 1 hour
- For deadline dates (e.g. "by June 15"), set end to 23:59 on that date, start to 22:59
- Output ONLY valid JSON array, no markdown, no extra text"""


class BaseLLMProvider(ABC):
    """Abstract base for LLM providers. Subclasses must implement extract_events()."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=120)

    @abstractmethod
    def extract_events(self, emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract time-sensitive events from a list of email dicts.

        Args:
            emails: List of dicts with keys: uid, subject, from, date, body

        Returns:
            List of event dicts with keys: title, start, end, location,
            description, source_email_uid, time_clarity
        """
        ...

    def _build_prompt(self, emails: List[Dict[str, Any]]) -> str:
        """Build the user prompt from a list of email dicts."""
        lines = ["Extract time-sensitive events from these emails:\n"]
        for i, email in enumerate(emails, 1):
            lines.append(f"--- Email {i} ---")
            lines.append(f"UID: {email.get('uid', '?')}")
            lines.append(f"From: {email.get('from', '?')}")
            lines.append(f"Subject: {email.get('subject', '')}")
            lines.append(f"Date: {email.get('date', '')}")
            lines.append(f"Body: {email.get('body', '')}")
            lines.append("")
        return "\n".join(lines)
