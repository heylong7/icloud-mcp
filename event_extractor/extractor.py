# event_extractor/extractor.py
"""Core event extraction and calendar sync logic."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from event_extractor.dedup import EmailDedup
from event_extractor.providers.base import BaseLLMProvider

log = logging.getLogger(__name__)


def classify_events(
    events: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split events into (explicit, vague) by time_clarity."""
    explicit = [e for e in events if e.get("time_clarity") == "explicit"]
    vague = [e for e in events if e.get("time_clarity") == "vague"]
    return explicit, vague


def calendar_dedup_check(
    event: Dict[str, Any],
    calendar_name: str = "Calendar",
) -> bool:
    """Check if a similar event (same title + same date) already exists in calendar.

    Returns True if a duplicate is found, False otherwise.
    """
    try:
        from caldav.davclient import DAVClient
    except ImportError:
        log.warning("caldav not available, skipping calendar dedup")
        return False

    import os
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

    apple_id = os.environ.get("APPLE_ID", "")
    app_pw = os.environ.get("ICLOUD_APP_PASSWORD", "")
    caldav_url = os.environ.get("CALDAV_URL", "https://caldav.icloud.com")

    if not apple_id or not app_pw:
        log.warning("Missing iCloud credentials, skipping calendar dedup")
        return False

    try:
        client = DAVClient(url=caldav_url, username=apple_id, password=app_pw)
        principal = client.principal()
        calendars = principal.calendars()
        target_cal = None
        for cal in calendars:
            if cal.name == calendar_name or str(cal.url) == calendar_name:
                target_cal = cal
                break

        if target_cal is None:
            log.warning("Calendar %r not found", calendar_name)
            return False

        event_date = event.get("start", "")[:10]
        start_dt = datetime.fromisoformat(event_date)
        end_dt = start_dt + timedelta(days=1)

        for ev in target_cal.search(event=True, start=start_dt, end=end_dt, expand=True):
            existing_summary = str(ev.component.get("summary", "") or "")
            if _title_similarity(event.get("title", ""), existing_summary) > 0.8:
                return True
    except Exception as exc:
        log.warning("Calendar dedup check failed: %s", exc)

    return False


def _title_similarity(a: str, b: str) -> float:
    """Simple character-level similarity ratio for title matching."""
    a = a.lower().strip()
    b = b.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter = min(a, b, key=len)
    longer = max(a, b, key=len)
    matches = sum(1 for c in shorter if c in longer)
    return matches / max(len(longer), 1)


def _auto_create_event(
    event: Dict[str, Any],
    calendar_name: str = "Calendar",
) -> bool:
    """Create a calendar event via CalDAV. Returns True on success."""
    if calendar_dedup_check(event, calendar_name):
        log.info("Skipping duplicate event: %s", event.get("title"))
        return False

    import os
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

    from caldav.davclient import DAVClient

    apple_id = os.environ.get("APPLE_ID", "")
    app_pw = os.environ.get("ICLOUD_APP_PASSWORD", "")
    caldav_url = os.environ.get("CALDAV_URL", "https://caldav.icloud.com")
    tzid = os.environ.get("TZID", "Asia/Shanghai")

    if not apple_id or not app_pw:
        log.warning("Cannot auto-create event: missing credentials")
        return False

    try:
        client = DAVClient(url=caldav_url, username=apple_id, password=app_pw)
        principal = client.principal()
        calendars = principal.calendars()
        target_cal = None
        for cal in calendars:
            if cal.name == calendar_name or str(cal.url) == calendar_name:
                target_cal = cal
                break

        if target_cal is None:
            calendars_list = principal.calendars()
            if calendars_list:
                target_cal = calendars_list[0]
            else:
                log.warning("No calendars found")
                return False

        uid = os.urandom(16).hex() + "@email-organizer"
        summary = event.get("title", "Untitled Event")
        start = event.get("start", "")
        end = event.get("end", "")
        description = event.get("description", "")
        location = event.get("location", "")

        ics = _build_ics(uid, summary, start, end, tzid, description, location)
        target_cal.save_event(ics)
        log.info("Created calendar event: %s", summary)
        return True
    except Exception as exc:
        log.error("Failed to create calendar event: %s", exc)
        return False


def _build_ics(
    uid: str,
    summary: str,
    start: str,
    end: str,
    tzid: str,
    description: str,
    location: Optional[str],
) -> str:
    """Build a minimal VEVENT ICS string."""
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Email Organizer//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{esc(summary)}",
        f"DTSTART;TZID={tzid}:{start.replace('-', '').replace(':', '')}",
        f"DTEND;TZID={tzid}:{end.replace('-', '').replace(':', '')}",
    ]
    if location:
        lines.append(f"LOCATION:{esc(location)}")
    if description:
        lines.append(f"DESCRIPTION:{esc(description)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\n".join(lines)


def run_extraction_pipeline(
    provider: BaseLLMProvider,
    emails: List[Dict[str, Any]],
    auto_create: bool = False,
    calendar_name: str = "Calendar",
    dedup_db_path: str = "event_extractor.db",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run the full extraction pipeline: dedup → LLM extract → classify → auto-create.

    Args:
        provider: An LLM provider instance
        emails: List of email dicts with uid, subject, from, date, body
        auto_create: If True, auto-create explicit events in calendar
        calendar_name: Target calendar name for auto-create
        dedup_db_path: Path to SQLite dedup database

    Returns:
        Tuple of (explicit_events, vague_events)
    """
    dedup = EmailDedup(db_path=dedup_db_path)
    new_emails = dedup.filter_unprocessed(emails)

    if not new_emails:
        log.info("No new emails to process (all already extracted)")
        return [], []

    events = provider.extract_events(new_emails)
    explicit, vague = classify_events(events)

    if auto_create:
        for event in explicit:
            _auto_create_event(event, calendar_name)

    return explicit, vague


def extract_and_sync(
    provider: BaseLLMProvider,
    emails: List[Dict[str, Any]],
    auto_create: bool = False,
    calendar_name: str = "Calendar",
    dedup_db_path: str = "event_extractor.db",
) -> Dict[str, Any]:
    """High-level extraction entry point returning a structured result dict.

    Returns:
        {
            "explicit": [...],
            "vague": [...],
            "total_emails_scanned": int,
            "new_emails_processed": int,
            "auto_created": int,
        }
    """
    dedup = EmailDedup(db_path=dedup_db_path)
    new_emails = dedup.filter_unprocessed(emails)

    if not new_emails:
        return {
            "explicit": [],
            "vague": [],
            "total_emails_scanned": len(emails),
            "new_emails_processed": 0,
            "auto_created": 0,
        }

    events = provider.extract_events(new_emails)
    explicit, vague = classify_events(events)

    auto_created = 0
    if auto_create:
        for event in explicit:
            if _auto_create_event(event, calendar_name):
                auto_created += 1

    return {
        "explicit": explicit,
        "vague": vague,
        "total_emails_scanned": len(emails),
        "new_emails_processed": len(new_emails),
        "auto_created": auto_created,
    }
