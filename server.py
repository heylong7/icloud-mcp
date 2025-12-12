# server.py
# iCloud CalDAV - MCP connector

from __future__ import annotations

import os
import logging
import datetime as dt
from pathlib import Path
from typing import List, Dict, Optional, Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from caldav.davclient import DAVClient
from caldav.lib import error as dav_error

# Configuration / Env

# Load .env that lives next to this file, regardless of CWD.
load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)


def _require_env(name: str, default: Optional[str] = None) -> str:
    """Return a required environment variable, or raise if missing."""
    value = os.environ.get(name, default)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value.strip()

APPLE_ID: str    = _require_env("APPLE_ID")
APP_PW: str      = _require_env("ICLOUD_APP_PASSWORD")
CALDAV_URL: str  = _require_env("CALDAV_URL", "https://caldav.icloud.com")
DEFAULT_TZID: str = os.environ.get("TZID", "America/New_York").strip()

LOOKBACK_YEARS = 3  # for UID searches
SERVER_HOST = os.environ.get("HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("PORT", "8000"))

# Add DR profile + scan window
DR_ONLY = os.environ.get("DR_PROFILE", "0") == "1"
SCAN_DAYS = int(os.environ.get("SCAN_DAYS", str(LOOKBACK_YEARS * 365)))

# Optional: simple logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("icloud-caldav")

# MCP app

mcp = FastMCP("icloud-caldav")

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")

# CalDAV helpers


def _client() -> DAVClient:
    """Return a new stateless DAV client."""
    return DAVClient(url=CALDAV_URL, username=APPLE_ID, password=APP_PW)


def _principal():
    """Return the authenticated CalDAV principal (raises on auth failure)."""
    return _client().principal()


def _all_calendars():
    """Return all calendars for the authenticated principal."""
    return _principal().calendars()


def _resolve_calendar(name_or_url: str):
    """Return a caldav.Calendar from a display name or absolute URL."""
    for calendar in _all_calendars():
        if calendar.name == name_or_url or str(calendar.url) == name_or_url:
            return calendar
    # Fallback: instantiate by URL directly
    return _client().calendar(url=name_or_url)

def _parse_iso(s: str) -> dt.datetime:
    """
    Accept 'YYYY-MM-DDTHH:MM:SS' (naive/local) or '...Z' (UTC) or with offset.
    """
    if s.endswith("Z"):
        return dt.datetime.fromisoformat(s[:-1]).replace(tzinfo=dt.timezone.utc)
    return dt.datetime.fromisoformat(s)


def _scan_window() -> tuple[dt.datetime, dt.datetime]:
    """Return the time window used for DR search/fetch operations."""
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=SCAN_DAYS)
    end = now + dt.timedelta(days=SCAN_DAYS)
    return start, end


def _uid_search_window() -> tuple[dt.datetime, dt.datetime]:
    """Return the wide time window used for UID-based lookups."""
    now = dt.datetime.now(dt.timezone.utc)
    delta = dt.timedelta(days=365 * LOOKBACK_YEARS)
    return now - delta, now + delta

def _fmt(ts: dt.datetime) -> str:
    """Format as 'YYYYMMDDTHHMMSS' for ICS."""
    return ts.strftime("%Y%m%dT%H%M%S")

def _fmt_utc(ts: dt.datetime) -> str:
    """Format as 'YYYYMMDDTHHMMSSZ' in UTC for ICS."""
    # If naive, assume default TZ, then convert to UTC
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo(DEFAULT_TZID))
    ts_utc = ts.astimezone(dt.timezone.utc)
    return ts_utc.strftime("%Y%m%dT%H%M%SZ")

def _ics_escape(text: str) -> str:
    """Minimal ICS escaping for SUMMARY/DESCRIPTION."""
    return (
        text.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace(",", "\\,")
            .replace(";", "\\;")
    )

def _to_iso(o) -> Optional[str]:
    """Best-effort ISO formatter for date/time values."""
    if o is None:
        return None
    if isinstance(o, dt.datetime):
        return o.isoformat()
    try:
        return o.isoformat()
    except Exception:
        return str(o)

def _build_rrule(
    recurrence: Optional[Dict[str, Any]],
    tzid: str,
    dtstart: Optional[dt.datetime] = None,
) -> Optional[str]:
    """
    Build an RFC5545 RRULE value from a high-level recurrence dict.

    recurrence:
      {
        "frequency": "daily" | "weekly" | "monthly" | "yearly" | "custom",
        "interval": int (default 1),
        "by_weekday": ["MO","TU",...],      # optional, for weekly/custom
        "by_monthday": [1,15,...],         # optional, for monthly/custom
        "end": {
          "type": "on_date",               # UNTIL
          "date": "YYYY-MM-DD" | ISO dt
          # or
          # "type": "after_occurrences",   # COUNT
          # "count": int
        },
        # for frequency == "custom":
        # "rrule": "FREQ=...;BYDAY=...;..."
      }
    """
    if not recurrence:
        return None

    freq = (recurrence.get("frequency") or "").lower()
    if not freq:
        return None

    # Custom raw RRULE passthrough
    if freq == "custom":
        raw = recurrence.get("rrule")
        return str(raw).strip() if raw else None

    freq_map = {
        "daily": "DAILY",
        "weekly": "WEEKLY",
        "monthly": "MONTHLY",
        "yearly": "YEARLY",
    }
    if freq not in freq_map:
        return None

    parts: List[str] = [f"FREQ={freq_map[freq]}"]

    interval = recurrence.get("interval")
    if isinstance(interval, int) and interval > 1:
        parts.append(f"INTERVAL={interval}")

    by_weekday = recurrence.get("by_weekday") or []
    if by_weekday:
        days = [str(d).upper() for d in by_weekday]
        parts.append(f"BYDAY={','.join(days)}")
    elif freq == "weekly" and dtstart is not None:
        # Default weekly: same weekday as dtstart
        weekday_map = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
        parts.append(f"BYDAY={weekday_map[dtstart.weekday()]}")

    by_monthday = recurrence.get("by_monthday") or []
    if by_monthday:
        days = [str(int(d)) for d in by_monthday]
        parts.append(f"BYMONTHDAY={','.join(days)}")

    end = recurrence.get("end") or {}
    end_type = (end.get("type") or "").lower()
    if end_type == "on_date":
        date_str = end.get("date")
        if date_str:
            # Interpret as local in tzid, convert to UTC, format as UNTIL=...Z
            try:
                if len(date_str) == 10:
                    y, m, d = map(int, date_str.split("-"))
                    local_dt = dt.datetime(y, m, d, 23, 59, 59)
                else:
                    local_dt = dt.datetime.fromisoformat(date_str)
                if local_dt.tzinfo is None:
                    local_dt = local_dt.replace(tzinfo=ZoneInfo(tzid))
                until_utc = local_dt.astimezone(dt.timezone.utc)
                until_str = until_utc.strftime("%Y%m%dT%H%M%SZ")
                parts.append(f"UNTIL={until_str}")
            except Exception:
                # If parsing fails, skip UNTIL
                pass
    elif end_type == "after_occurrences":
        count = end.get("count")
        if isinstance(count, int) and count > 0:
            parts.append(f"COUNT={count}")

    return ";".join(parts) if parts else None

# DR profile: read-only search/fetch
if DR_ONLY:

    @mcp.tool(name="search")
    def search(query: str) -> List[Dict[str, Any]]:
        """
        Read-only search across SUMMARY and DESCRIPTION within a time window.
        Returns [{ id, title, snippet }]
        - id: "{calendar_url}|{uid}"
        - title: SUMMARY
        - snippet: ISO start + calendar name
        """
        q = (query or "").strip().lower()
        if not q:
            return []

        start, end = _scan_window()

        rows: List[Dict[str, Any]] = []
        for cal in _all_calendars():
            calname = getattr(cal, "name", None) or str(cal.url)
            # expand=True to surface recurring instances as separate hits
            for ev in cal.search(event=True, start=start, end=end, expand=True):
                comp = ev.component
                summary = str(comp.get("summary", "") or "")
                descr = str(comp.get("description", "") or "")
                haystack = (summary + "\n" + descr).lower()
                if q in haystack:
                    uid = str(comp.get("uid", "") or "").strip()
                    dtstart = comp.decoded("dtstart")
                    when = _to_iso(dtstart) or ""
                    rows.append({
                        "id": f"{str(cal.url)}|{uid}",
                        "title": summary[:200],
                        "snippet": f"{when} — {calname}",
                    })
        return rows[:200]

    @mcp.tool(name="fetch")
    def fetch(ids: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch raw ICS for ids returned by search().
        Returns [{ id, mimeType: 'text/calendar', content }]
        """
        ids = ids or []
        calendars = {str(calendar.url): calendar for calendar in _all_calendars()}
        start, end = _scan_window()

        out: List[Dict[str, Any]] = []
        for ident in ids:
            try:
                cal_url, uid = ident.split("|", 1)
            except ValueError:
                continue
            cal = calendars.get(cal_url)
            if not cal:
                continue
            found_raw = None
            # expand=False to get the series VEVENT ICS blob
            for ev in cal.search(event=True, start=start, end=end, expand=False):
                comp = ev.component
                if str(comp.get("uid", "") or "").strip() == uid:
                    found_raw = ev.data
                    break
            if found_raw:
                out.append({
                    "id": ident,
                    "mimeType": "text/calendar",
                    "content": found_raw,
                })
        return out

# Write-capable tools (default mode)
if not DR_ONLY:

    @mcp.tool()
    def list_calendars() -> List[Dict[str, Any]]:
        """
        Return available calendar containers with their name and URL.
        """
        calendars = _all_calendars()
        out: List[Dict[str, Any]] = []
        for calendar in calendars:
            out.append(
                {
                    "name": getattr(calendar, "name", None),
                    "url": str(calendar.url),
                    "id": getattr(calendar, "id", None),
                }
            )
        return out

    @mcp.tool()
    def list_events(
        calendar_name_or_url: str,
        start: str,
        end: str,
        expand_recurring: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        List events between ISO datetimes [start, end).
        calendar_name_or_url: either display name or absolute CalDAV URL.
        """
        s = _parse_iso(start)
        e = _parse_iso(end)
        cal = _resolve_calendar(calendar_name_or_url)

        events = cal.search(event=True, start=s, end=e, expand=expand_recurring)
        out: List[Dict[str, Any]] = []
        for ev in events:
            comp = ev.component  # icalendar.Event
            summary = str(comp.get("summary", "")) if comp.get("summary") is not None else ""
            dtstart = comp.decoded("dtstart")
            dtend   = comp.decoded("dtend", default=None)
            uid     = str(comp.get("uid", "")) if comp.get("uid") is not None else ""

            out.append({
                "uid": uid,
                "summary": summary,
                "start": dtstart.isoformat() if hasattr(dtstart, "isoformat") else str(dtstart),
                "end":   dtend.isoformat() if (dtend and hasattr(dtend, "isoformat")) else (str(dtend) if dtend else None),
                "raw": ev.data,  # original ICS text
            })
        return out

    @mcp.tool()
    def create_event(
        calendar_name_or_url: str,
        summary: str,
        start: str,
        end: str,
        tzid: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        recurrence: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create an event in the given calendar.

        start/end: ISO datetimes, local or '...Z' for UTC.
        tzid:     IANA TZ name (e.g., 'America/New_York'); used if times are naive.
        recurrence: optional dict, e.g.:

          {
            "frequency": "daily" | "weekly" | "monthly" | "yearly" | "custom",
            "interval": 1,
            "by_weekday": ["MO","WE"],
            "by_monthday": [1,15],
            "end": {
              "type": "on_date",           # or "after_occurrences"
              "date": "2025-12-31",       # for on_date
              # or:
              # "type": "after_occurrences",
              # "count": 10
            },
            # for custom:
            # "rrule": "FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=1"
          }
        """
        tzid = tzid or DEFAULT_TZID

        s = _parse_iso(start)
        e = _parse_iso(end)

        # Normalize to the requested/default TZID so event times are in that zone
        tz = ZoneInfo(tzid)
        if s.tzinfo is None:
            s = s.replace(tzinfo=tz)
        else:
            s = s.astimezone(tz)
        if e.tzinfo is None:
            e = e.replace(tzinfo=tz)
        else:
            e = e.astimezone(tz)

        cal = _resolve_calendar(calendar_name_or_url)

        uid = os.urandom(16).hex() + "@chatgpt-mcp"

        ics_parts = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ChatGPT MCP iCloud CalDAV//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{_ics_escape(summary)}",
            # Store as local wall time in the env-specified / requested TZID
            f"DTSTART;TZID={tzid}:{_fmt(s)}",
            f"DTEND;TZID={tzid}:{_fmt(e)}",
        ]
        if location:
            ics_parts.append(f"LOCATION:{_ics_escape(location)}")
        if description:
            ics_parts.append(f"DESCRIPTION:{_ics_escape(description)}")

        rrule = _build_rrule(recurrence, tzid=tzid, dtstart=s)
        if rrule:
            ics_parts.append(f"RRULE:{rrule}")

        ics_parts += ["END:VEVENT", "END:VCALENDAR"]

        cal.save_event("\n".join(ics_parts))
        return uid

    @mcp.tool()
    def update_event(
        calendar_name_or_url: str,
        uid: str,
        summary: Optional[str] = None,
        start: Optional[str] = None,   # ISO datetime
        end: Optional[str] = None,     # ISO datetime
        tzid: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        recurrence: Optional[Dict[str, Any]] = None,
        clear_recurrence: bool = False,
    ) -> bool:
        """
        Update a VEVENT identified by UID.

        - If recurrence is provided, replaces existing RRULE.
        - If clear_recurrence is True, removes any RRULE.
        - If neither is provided, preserves existing RRULE.
        """
        tzid = tzid or DEFAULT_TZID

        cal = _resolve_calendar(calendar_name_or_url)

        # Search wide window for matching UID
        s_window, e_window = _uid_search_window()

        target = None
        for ev in cal.search(event=True, start=s_window, end=e_window, expand=False):
            comp = ev.component
            if str(comp.get("uid", "")) == uid:
                target = ev
                break
        if target is None:
            return False

        comp = target.component
        old_summary = str(comp.get("summary", "")) if comp.get("summary") is not None else ""
        old_desc    = str(comp.get("description", "")) if comp.get("description") is not None else ""
        old_loc     = str(comp.get("location", "")) if comp.get("location") is not None else ""
        old_dtstart = comp.decoded("dtstart")
        old_dtend   = comp.decoded("dtend", default=None)

        # Existing RRULE, if any
        old_rrule_str: Optional[str] = None
        try:
            old_rrule_prop = comp.get("rrule")
            if old_rrule_prop is not None:
                if hasattr(old_rrule_prop, "to_ical"):
                    raw = old_rrule_prop.to_ical()
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    old_rrule_str = str(raw).strip()
                else:
                    old_rrule_str = str(old_rrule_prop).strip()
        except Exception:
            old_rrule_str = None

        def _to_dt(s: Optional[str], fallback: dt.datetime) -> dt.datetime:
            if s is None:
                return fallback
            if s.endswith("Z"):
                return dt.datetime.fromisoformat(s[:-1]).replace(tzinfo=dt.timezone.utc)
            return dt.datetime.fromisoformat(s)

        new_summary = summary if summary is not None else old_summary
        new_desc    = description if description is not None else old_desc
        new_loc     = location if location is not None else old_loc
        new_start   = _to_dt(start, old_dtstart)
        new_end     = _to_dt(end,   old_dtend if old_dtend is not None else (new_start + dt.timedelta(hours=1)))

        # Normalize updated times into the requested/default TZID
        tz = ZoneInfo(tzid)
        if new_start.tzinfo is None:
            new_start = new_start.replace(tzinfo=tz)
        else:
            new_start = new_start.astimezone(tz)
        if new_end.tzinfo is None:
            new_end = new_end.replace(tzinfo=tz)
        else:
            new_end = new_end.astimezone(tz)

        # Decide final RRULE
        if clear_recurrence:
            effective_rrule: Optional[str] = None
        elif recurrence is not None:
            effective_rrule = _build_rrule(recurrence, tzid=tzid, dtstart=new_start)
        else:
            effective_rrule = old_rrule_str

        lines: List[str] = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ChatGPT MCP iCloud CalDAV//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{_ics_escape(new_summary)}",
            f"DTSTART;TZID={tzid}:{_fmt(new_start)}",
            f"DTEND;TZID={tzid}:{_fmt(new_end)}",
        ]
        if new_loc is not None and new_loc != "":
            lines.append(f"LOCATION:{_ics_escape(new_loc)}")
        if new_desc:
            lines.append(f"DESCRIPTION:{_ics_escape(new_desc)}")
        if effective_rrule:
            lines.append(f"RRULE:{effective_rrule}")
        lines += ["END:VEVENT", "END:VCALENDAR"]

        target.data = "\n".join(lines)
        target.save()
        return True

    @mcp.tool()
    def delete_event(calendar_name_or_url: str, uid: str) -> bool:
        """
        Delete a VEVENT by UID from the given calendar.
        Returns True if deleted, else False (not found).
        """
        cal = _resolve_calendar(calendar_name_or_url)

        start, end = _uid_search_window()

        for ev in cal.search(event=True, start=start, end=end, expand=False):
            comp = ev.component
            if str(comp.get("uid", "")) == uid:
                ev.delete()
                return True
        return False

# Main

if __name__ == "__main__":
    log.info("Starting MCP HTTP server on %s:%s", SERVER_HOST, SERVER_PORT)
    log.info("CalDAV: %s  Apple ID: %r  TZ: %s  DR_ONLY=%s", CALDAV_URL, APPLE_ID, DEFAULT_TZID, DR_ONLY)
    mcp.run(transport="http", host=SERVER_HOST, port=SERVER_PORT, path="/mcp")
