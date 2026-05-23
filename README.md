# iCloud MCP Connector

An HTTP **Model Context Protocol (MCP)** server exposing iCloud services to MCP-aware clients (e.g., claude custom connectors, IDEs) using an iCloud **app-specific password**.

**Supported:** iCloud Calendar (CalDAV) + iCloud Mail (IMAP/SMTP).

> Unofficial. Keep this service private; it forwards your iCloud app-specific password to Apple’s servers.

---

## Why did I build this?

I built this to use in Claude Custom Connector, so I can change my iCloud Calendar compared to changing it manually. Came up with this idea on a Friday night before a TOP Pset was due, and this turned out to be a fun 1-day project.

---

## Features

- HTTP MCP server (`/mcp`) + `GET /health`
- **Calendar tools** (default write-capable profile):
  - `list_calendars()`
  - `list_calendars_with_events(start, end, expand_recurring=True)`
  - `list_events(calendar_name_or_url, start, end, expand_recurring=True)`
  - `create_event(calendar_name_or_url, summary, start, end, tzid?, description?, location?, recurrence?)`
  - `update_event(calendar_name_or_url, uid, summary?, start?, end?, tzid?, description?, location?, recurrence?, clear_recurrence=False)`
  - `delete_event(calendar_name_or_url, uid)`
- **Calendar tools** (Deep Research read-only profile, `DR_PROFILE=1`):
  - `search(query)` → basic text search over SUMMARY/DESCRIPTION in a time window
  - `fetch(ids)` → fetch raw `text/calendar` ICS blobs for search results
- **Mail tools** (opt-in, `MAIL_ENABLED=1`):
  - `list_mailboxes()` — list all folders
  - `list_messages(mailbox, limit, unread_only)` — list messages with headers
  - `get_message(uid, mailbox)` — fetch full message with body
  - `search_messages(query, mailbox, limit)` — IMAP TEXT search
  - `send_message(to, subject, body, cc?, bcc?)` — send via SMTP
  - `delete_message(uid, mailbox)` — move to Trash
  - `mark_message(uid, mailbox, read)` — mark read/unread
  - `extract_events_from_emails(mailbox, limit, since_days, auto_create)` — **AI event extraction** (LLM-powered)
- **Daily automation:** `run_daily.py` — scan recent emails, extract events via LLM, auto-create in calendar
- ISO datetime input (`YYYY-MM-DDTHH:MM:SS`, with optional `Z` or timezone offset)
- Minimal ICS generation (summary/description escaping), UID matching across a ±3-year window

---

## Requirements

- Python **3.11+**
- Apple ID (**email** identity, not phone number)
- iCloud **app-specific password** (revocable) — one password works for both calendar and mail
- Network access to `https://caldav.icloud.com`, `imap.mail.me.com`, `smtp.mail.me.com`

---

## Environment

Create a `.env` **next to** `server.py` (auto-loaded):

```env
APPLE_ID=you@example.com                 # Use your Apple ID email
ICLOUD_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx  # App-specific password (works for both calendar and mail)
CALDAV_URL=https://caldav.icloud.com     # optional, default shown
HOST=127.0.0.1                           # optional
PORT=8000                                # optional
TZID=America/New_York                    # default TZ for new/edited events

# Deep Research: read-only calendar profile (optional)
DR_PROFILE=0                             # Set to 1 to enable DR mode (default 0)
SCAN_DAYS=1095                           # Time window (days) scanned by DR search/fetch (default ~3 years)

# Mail (IMAP / SMTP) — optional, disabled by default
MAIL_ENABLED=1                           # Set to 1 to enable mail tools
IMAP_HOST=imap.mail.me.com              # optional, default shown
IMAP_PORT=993                            # optional, default shown
SMTP_HOST=smtp.mail.me.com              # optional, default shown
SMTP_PORT=587                            # optional, default shown
ICLOUD_TRASH_FOLDER=Deleted Messages     # optional, iCloud trash folder name

# Event extraction (LLM-powered) — required for extract_events_from_emails and run_daily.py
LLM_PROVIDER=deepseek                    # deepseek or openai
DEEPSEEK_API_KEY=sk-your-key             # DeepSeek API key
DEEPSEEK_BASE_URL=https://api.deepseek.com  # optional, default shown
DEEPSEEK_MODEL=deepseek-v4-flash         # optional, default shown
# OpenAI alternative:
# LLM_PROVIDER=openai
# OPENAI_API_KEY=sk-your-key
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_MODEL=gpt-4o-mini

# Daily scan settings
SINCE_DAYS=3                             # Days to look back for emails (default 3)
CALENDAR_NAME=Work                       # Target calendar for auto-created events
```

Required: `APPLE_ID`, `ICLOUD_APP_PASSWORD`.

---

## Quick Start (local)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Ensure .env exists (see above), then:
python server.py
# -> Listening on http://127.0.0.1:8000
curl http://127.0.0.1:8000/health   # OK
```

**MCP endpoint:** `http://127.0.0.1:8000/mcp`

---

## Tool Reference (functional details)

### `list_calendars() -> List[Calendar]`

Returns:

- `name: str | null`
- `url: str` (preferred identifier for other calls)
- `id: str | null`

### `list_calendars_with_events(start, end, expand_recurring=True) -> List[Calendar]`

Returns only the calendars that contain **at least one event** in the
given time window.

**Args**

- `start, end: str` — ISO datetimes; search is [**start**, **end**)
- `expand_recurring: bool` — treat recurring series as concrete instances

Each returned calendar has the same shape as `list_calendars()`.

### `list_events(calendar_name_or_url, start, end, expand_recurring=True) -> List[Event]`

**Args**

- `calendar_name_or_url: str` — display name or full CalDAV URL
- `start, end: str` — ISO datetimes; search is [**start**, **end**)
- `expand_recurring: bool` — include concrete instances of recurring series

**Returns** each event with:

- `uid: str`
- `summary: str`
- `start: str` (ISO)
- `end: str | null` (ISO)
- `raw: str` (original ICS text)

### `create_event(calendar_name_or_url, summary, start, end, tzid?, description?, location?, recurrence?) -> str`

Creates a minimal **VEVENT**.

- `tzid` defaults to `TZID` env if omitted; naive datetimes are assumed in that zone and stored as UTC.
- `description` is optional; omit or pass `null` to skip it.
- `location` is optional; omit or pass `null` to skip it.
- `recurrence` (optional) describes how the event should repeat, for example:

    ```jsonc
    {
        "frequency": "weekly",              // daily | weekly | monthly | yearly | custom
        "interval": 1,                       // optional, default 1
        "by_weekday": ["MO", "WE"],         // optional; for weekly/custom
        "by_monthday": [1, 15],             // optional; for monthly/custom
        "end": {                            // optional end condition
            "type": "on_date",              // or "after_occurrences"
            "date": "2025-12-31"            // when type == "on_date"
            // or: "count": 10               // when type == "after_occurrences"
        }
        // for custom frequency you can pass a raw RRULE:
        // "frequency": "custom",
        // "rrule": "FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=1"
    }
    ```

- Returns the generated `uid` (random hex + `@claude-mcp`).

### `update_event(calendar_name_or_url, uid, summary?, start?, end?, tzid?, description?, location?, recurrence?, clear_recurrence=False) -> bool`

Updates the **whole** event identified by `uid` (for recurring events this updates the series VEVENT, not a single instance).

- Preserves any omitted fields from the original component.
- `location`:
  - If omitted (`null` / not provided), keeps the existing location.
  - If provided as a non-empty string, updates the event’s location.
  - If provided as an empty string, clears the event’s location.
- `recurrence`:
  - If provided, replaces any existing RRULE using the same shape as in `create_event`.
- `clear_recurrence`:
  - If `True`, removes any RRULE and converts the event back to a single non-recurring instance.
  - If `True` and `recurrence` is also provided, `clear_recurrence` wins (no recurrence).
- Returns `True` on success, `False` if `uid` not found in ±3-year window.

### `delete_event(calendar_name_or_url, uid) -> bool`

Deletes the first matching `uid` in a ±3-year window.

- Returns `True` if deleted, `False` if not found.

**Date/Time Notes**

- Accepts naive or `Z`/offset datetimes (`YYYY-MM-DDTHH:MM:SS`, optionally `Z` or `-04:00` etc.)
- New/edited events emit `DTSTART;TZID=...` and `DTEND;TZID=...` using provided `tzid` or `TZID` env
- Updates attempt to reuse the original TZID when present
- `LOCATION` is emitted when `location` is provided and non-empty; passing an empty string when updating an event removes the existing location.

---

## Mail Tool Reference

Enable with `MAIL_ENABLED=1`. Uses the same `APPLE_ID` and `ICLOUD_APP_PASSWORD` as the calendar. No extra dependencies — pure Python stdlib (`imaplib`, `smtplib`).

### `list_mailboxes() -> List[{name}]`

Returns all IMAP folders (INBOX, Sent, Drafts, Junk, Deleted Messages, etc.).

### `list_messages(mailbox="INBOX", limit=20, unread_only=False) -> List[Message]`

Returns newest-first headers for up to `limit` messages. Each item:
- `uid: str`, `subject: str`, `from: str`, `date: str`, `read: bool`

### `get_message(uid, mailbox="INBOX") -> Message`

Fetches the full message including decoded body (`text/plain` preferred, HTML stripped as fallback). Returns:
- `uid, subject, from, to, cc, date, body, read`

### `search_messages(query, mailbox="INBOX", limit=20) -> List[Message]`

IMAP `TEXT` search — matches subject and body. Returns same header fields as `list_messages`.

### `send_message(to, subject, body, cc=None, bcc=None) -> bool`

Sends via SMTP (STARTTLS on port 587). `to` and `cc` may be comma-separated. Returns `True` on success.

### `delete_message(uid, mailbox="INBOX") -> bool`

Copies to Trash (`Deleted Messages` by default, override with `ICLOUD_TRASH_FOLDER`) then expunges. Returns `True` on success.

### `mark_message(uid, mailbox="INBOX", read=True) -> bool`

Sets or clears the `\Seen` flag. Returns `True` on success.

### `extract_events_from_emails(mailbox="INBOX", limit=50, since_days=3, auto_create=True) -> Dict`

AI-powered event extraction from emails. Fetches recent emails via IMAP, sends them to an LLM (DeepSeek or OpenAI) for analysis, and returns structured calendar events.

**Args**

- `mailbox: str` — mailbox to scan (default `"INBOX"`)
- `limit: int` — max emails to process (default 50)
- `since_days: int` — only process emails from the last N days (default 3)
- `auto_create: bool` — if `True`, explicit-time events are auto-created in iCloud Calendar

**Returns**

```jsonc
{
    "explicit": [...],           // events with clear dates/times
    "vague": [...],              // events with implied timing (pending confirmation)
    "total_emails_scanned": 5,
    "new_emails_processed": 2,
    "auto_created": 1            // number of events written to calendar
}
```

Each extracted event has: `title`, `start`, `end`, `location`, `description`, `source_email_uid`, `time_clarity` (explicit/vague).

**Event types detected:** meetings, lectures, seminars, workshops, job fairs, paper deadlines, copyright signing, review invitations, flights, hotel bookings, exams, course schedules, assessment deadlines.

**How it works:**
1. IMAP SINCE search retrieves emails from the last N days (regardless of read status)
2. SQLite-based deduplication skips previously processed emails
3. LLM (DeepSeek/OpenAI) extracts time-sensitive events from email content
4. Failed extractions are retried next run (emails marked processed only on success)
5. HTML-only emails are converted to plain text before analysis

**Required config:** `LLM_PROVIDER`, `DEEPSEEK_API_KEY` (or `OPENAI_API_KEY`), `CALENDAR_NAME` (optional, for auto-create target).

---

## Daily Automation

`run_daily.py` is a standalone script for unattended periodic execution (e.g., Windows Task Scheduler).

```powershell
# Run manually
python run_daily.py

# Schedule every 4 hours via Windows Task Scheduler
schtasks /Create /SC DAILY /TN "MailEventExtract" `
  /TR "C:\path\to\python.exe C:\path\to\run_daily.py" /ST 08:00
```

The script:
- Connects directly to iCloud IMAP (no MCP server needed)
- Searches emails from the last `SINCE_DAYS` days
- Processes newest 50, skips already-processed via SQLite dedup
- Extracts events via the same LLM pipeline as the MCP tool
- Auto-creates explicit events in the `CALENDAR_NAME` calendar
- Logs to `run_daily.log` in the project root

**Schedule settings for reliability:**
- `WakeToRun` — wakes computer from sleep
- `StartWhenAvailable` — runs missed tasks on next boot
- `MultipleInstancesPolicy=IgnoreNew` — skips if previous run still active

See `MailEventExtract.xml` section above for full Task Scheduler XML config.

---

## Deep Research read-only mode

Set DR_PROFILE=1 to run a read-only tool set for Deep Research. This exposes only:

- search(query) -> [{ id, title, snippet }]
- fetch(ids) -> [{ id, mimeType: 'text/calendar', content }]

Example:

```bash
DR_PROFILE=1 HOST=127.0.0.1 PORT=8000 python server.py
```

Notes:

- Write tools (list_events/create_event/update_event/delete_event) are disabled in this mode.
- SCAN_DAYS controls the search window around “now” (default: 1095 days ≈ 3 years).
- Keep this service private or add auth

---

## Example (programmatic client)

```python
import asyncio, json
from fastmcp import Client

MCP_URL = "http://127.0.0.1:8000/mcp"
CAL_URL = "<paste one of your calendar URLs>"

def unwrap(res):
    sc = getattr(res, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    return json.loads(res.content[0].text)

async def main():
    async with Client(MCP_URL) as c:
        cals = unwrap(await c.call_tool("list_calendars", {"confirm": True}))
        print("Calendars:", cals[:2])

        evs = unwrap(await c.call_tool("list_events", {
            "calendar_name_or_url": CAL_URL,
            "start": "2025-09-01T00:00:00",
            "end":   "2025-10-01T00:00:00",
            "expand_recurring": True
        }))
        print("Events:", len(evs))

        uid = unwrap(await c.call_tool("create_event", {
            "calendar_name_or_url": CAL_URL,
            "summary":"Demo",
            "start":"2025-09-29T15:00:00",
            "end":"2025-09-29T15:30:00",
            "tzid":"America/New_York",
            "location": "Bobst Library"
        }))
        print("Created:", uid)

asyncio.run(main())
```

---

## Deployment / Public HTTPS

To use this with claude Custom Connectors you need a public HTTPS endpoint that forwards to your local server.

See [DEPLOY.md](./DEPLOY.md) for:

- Cloudflare Tunnel (stable hostname, free)
- ngrok (quick test)
- VPS + Caddy/Nginx (permanent)

Security: add auth (Cloudflare Access, Basic Auth proxy, IP allowlist). Do **NOT** expose this unauthenticated; it holds live calendar write access.
You need a public HTTPS URL that forwards to your local `http://127.0.0.1:8000`.

---

## Troubleshooting

| Symptom              | Likely Cause / Fix                                                                |
| -------------------- | --------------------------------------------------------------------------------- |
| `401 Unauthorized`   | Wrong Apple ID or app-specific password; ensure `.env` uses **email**, not phone. |
| Empty event results  | Wrong calendar URL or time window; remember `end` is exclusive.                   |
| Update/Delete no-ops | UID not in ±3-year scan window or different calendar than you’re querying.        |
| Timezone drift       | Pass `tzid` explicitly (e.g., `America/New_York`) or use UTC `...Z`.              |

---

## Security

- Use **app-specific passwords** and rotate as needed
- Keep this server private (tunnel ACLs, IP allowlists, auth proxy)
- This project rewrites minimal VEVENTs; advanced fields (attendees, alarms, recurrence exceptions) are not preserved on update

---

## License

MIT License.

---

Happy scheduling, I hope this helps!
