# server.py
# iCloud CalDAV - MCP connector

from __future__ import annotations

import os
import logging
import datetime as dt
import secrets
import hashlib
import base64
import time
import html as html_lib
from pathlib import Path
from typing import List, Dict, Optional, Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse, HTMLResponse, RedirectResponse

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

# OAuth config — if both vars are set, Bearer-token auth is enforced on /mcp
OAUTH_CLIENT_ID     = os.environ.get("OAUTH_CLIENT_ID", "").strip()
OAUTH_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
OAUTH_ENABLED       = bool(OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET)

CODE_TTL  = 60           # auth codes expire in 60 seconds
TOKEN_TTL = 86400 * 30   # access tokens live 30 days

# In-memory OAuth state (tokens lost on restart — user re-authorizes after deploys)
_auth_codes: dict[str, dict] = {}    # code → {client_id, redirect_uri, code_challenge, ...}
_access_tokens: dict[str, dict] = {} # token → {client_id, expires_at}

# Optional: simple logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("icloud-caldav")


# OAuth helpers

def _verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    """Verify an OAuth 2.0 PKCE code_verifier against a stored code_challenge."""
    if method == "S256":
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return secrets.compare_digest(expected, challenge)
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    return False


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require a valid Bearer token on /mcp when OAuth is enabled."""
    _SKIP = {"/.well-known/oauth-authorization-server", "/authorize", "/token", "/health"}

    async def dispatch(self, request, call_next):
        if not OAUTH_ENABLED or request.url.path in self._SKIP:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="icloud-mcp"'},
            )
        token = auth[7:].strip()
        entry = _access_tokens.get(token)
        if not entry or entry["expires_at"] < time.time():
            _access_tokens.pop(token, None)
            return JSONResponse(
                {"error": "invalid_token"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        return await call_next(request)


# MCP app

mcp = FastMCP("icloud-caldav")

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_metadata(request: Request) -> JSONResponse:
    base = str(request.base_url).rstrip("/")
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "grant_types_supported": ["authorization_code"],
    })


@mcp.custom_route("/authorize", methods=["GET", "POST"])
async def authorize(request: Request):
    esc = html_lib.escape
    if request.method == "GET":
        params = dict(request.query_params)
        hidden = "".join(
            f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">'
            for k, v in params.items()
        )
        page = f"""<!DOCTYPE html>
<html><head><title>iCloud MCP — Authorize</title>
<style>
  body {{font-family:system-ui;max-width:440px;margin:4em auto;padding:0 1.5em;color:#1d1d1f}}
  h2 {{font-size:1.4rem;margin-bottom:.5em}}
  p  {{color:#6e6e73;margin-bottom:1.5em}}
  button {{background:#0071e3;color:#fff;border:none;padding:.75em 1.75em;
           border-radius:8px;font-size:1rem;cursor:pointer}}
  button:hover {{background:#0077ed}}
</style></head><body>
<h2>Allow access to your iCloud Calendar?</h2>
<p>Client: <strong>{esc(params.get("client_id", ""))}</strong></p>
<form method="POST">{hidden}
  <button type="submit">Authorize</button>
</form></body></html>"""
        return HTMLResponse(page)

    # POST — user clicked Authorize
    form = await request.form()
    client_id             = str(form.get("client_id", ""))
    redirect_uri          = str(form.get("redirect_uri", ""))
    code_challenge        = str(form.get("code_challenge", ""))
    code_challenge_method = str(form.get("code_challenge_method", "S256"))
    state                 = str(form.get("state", ""))

    if not OAUTH_ENABLED or client_id != OAUTH_CLIENT_ID:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if not redirect_uri:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "redirect_uri required"},
            status_code=400,
        )

    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "expires_at": time.time() + CODE_TTL,
    }
    sep = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{sep}code={code}" + (f"&state={state}" if state else "")
    return RedirectResponse(location, status_code=302)


@mcp.custom_route("/token", methods=["POST"])
async def token_endpoint(request: Request) -> JSONResponse:
    form          = await request.form()
    grant_type    = str(form.get("grant_type", ""))
    code          = str(form.get("code", ""))
    redirect_uri  = str(form.get("redirect_uri", ""))
    code_verifier = str(form.get("code_verifier", ""))
    client_id     = str(form.get("client_id", ""))
    client_secret = str(form.get("client_secret", ""))

    # Also accept HTTP Basic Auth (some clients send credentials this way)
    basic = request.headers.get("Authorization", "")
    if basic.startswith("Basic "):
        try:
            decoded = base64.b64decode(basic[6:]).decode()
            client_id, _, client_secret = decoded.partition(":")
        except Exception:
            pass

    if not OAUTH_ENABLED:
        return JSONResponse({"error": "oauth_not_configured"}, status_code=503)
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    if not client_id or not client_secret:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if not secrets.compare_digest(client_id, OAUTH_CLIENT_ID) or \
       not secrets.compare_digest(client_secret, OAUTH_CLIENT_SECRET):
        return JSONResponse({"error": "invalid_client"}, status_code=401)

    entry = _auth_codes.pop(code, None)
    if not entry or entry["expires_at"] < time.time():
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry["redirect_uri"] != redirect_uri or entry["client_id"] != client_id:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry["code_challenge"] and not _verify_pkce(
        code_verifier, entry["code_challenge"], entry["code_challenge_method"]
    ):
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "PKCE mismatch"},
            status_code=400,
        )

    token = secrets.token_urlsafe(48)
    _access_tokens[token] = {"client_id": client_id, "expires_at": time.time() + TOKEN_TTL}
    log.info("OAuth: issued access token for client %r", client_id)
    return JSONResponse({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": TOKEN_TTL,
    })


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


def _parse_iso_or_default(value: Optional[str], fallback: dt.datetime) -> dt.datetime:
    """Parse an ISO datetime string or return the fallback if missing."""
    if value is None:
        return fallback
    return _parse_iso(value)


def _normalize_to_tz(ts: dt.datetime, tzid: str) -> dt.datetime:
    """Return ``ts`` normalized into the given IANA timezone."""
    tz = ZoneInfo(tzid)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=tz)
    return ts.astimezone(tz)


def _build_vevent_ics(
    uid: str,
    summary: str,
    start: dt.datetime,
    end: dt.datetime,
    tzid: str,
    description: Optional[str],
    location: Optional[str],
    rrule: Optional[str],
    *,
    include_location: bool,
) -> str:
    """Build a minimal VEVENT ICS blob."""
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ChatGPT MCP iCloud CalDAV//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DTSTART;TZID={tzid}:{_fmt(start)}",
        f"DTEND;TZID={tzid}:{_fmt(end)}",
    ]

    if include_location and location is not None and location != "":
        lines.append(f"LOCATION:{_ics_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_ics_escape(description)}")
    if rrule:
        lines.append(f"RRULE:{rrule}")

    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\n".join(lines)

def _build_rrule(
    recurrence: Optional[Dict[str, Any]],
    tzid: str,
    dtstart: Optional[dt.datetime] = None,
) -> Optional[str]:
    """Build an RFC5545 RRULE value from a high-level recurrence dict."""
    if not recurrence:
        return None

    freq = (recurrence.get("frequency") or "").lower()
    if not freq:
        return None

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
        """
        q = (query or "").strip().lower()
        if not q:
            return []

        start, end = _scan_window()

        rows: List[Dict[str, Any]] = []
        for cal in _all_calendars():
            calname = getattr(cal, "name", None) or str(cal.url)
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
        """Return available calendar containers with their name and URL."""
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
    def list_calendars_with_events(
        start: str,
        end: str,
        expand_recurring: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Return calendars that have at least one event between ISO datetimes [start, end).
        """
        s = _parse_iso(start)
        e = _parse_iso(end)

        calendars = _all_calendars()
        out: List[Dict[str, Any]] = []

        for calendar in calendars:
            try:
                has_event = False
                for _ in calendar.search(event=True, start=s, end=e, expand=expand_recurring):
                    has_event = True
                    break
                if has_event:
                    out.append(
                        {
                            "name": getattr(calendar, "name", None),
                            "url": str(calendar.url),
                            "id": getattr(calendar, "id", None),
                        }
                    )
            except dav_error.DAVError as exc:
                log.warning("CalDAV search failed for calendar %s: %s", getattr(calendar, "name", calendar), exc)
            except Exception:
                log.exception("Unexpected error while scanning calendar %r for events", getattr(calendar, "name", calendar))

        return out

    @mcp.tool()
    def list_events(
        calendar_name_or_url: str,
        start: str,
        end: str,
        expand_recurring: bool = True,
    ) -> List[Dict[str, Any]]:
        """List events between ISO datetimes [start, end)."""
        s = _parse_iso(start)
        e = _parse_iso(end)
        cal = _resolve_calendar(calendar_name_or_url)

        events = cal.search(event=True, start=s, end=e, expand=expand_recurring)
        out: List[Dict[str, Any]] = []
        for ev in events:
            comp = ev.component
            summary = str(comp.get("summary", "")) if comp.get("summary") is not None else ""
            dtstart = comp.decoded("dtstart")
            dtend   = comp.decoded("dtend", default=None)
            uid     = str(comp.get("uid", "")) if comp.get("uid") is not None else ""

            out.append({
                "uid": uid,
                "summary": summary,
                "start": dtstart.isoformat() if hasattr(dtstart, "isoformat") else str(dtstart),
                "end":   dtend.isoformat() if (dtend and hasattr(dtend, "isoformat")) else (str(dtend) if dtend else None),
                "raw": ev.data,
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
        """Create an event in the given calendar."""
        tzid = tzid or DEFAULT_TZID

        s = _normalize_to_tz(_parse_iso(start), tzid)
        e = _normalize_to_tz(_parse_iso(end), tzid)

        cal = _resolve_calendar(calendar_name_or_url)

        uid = os.urandom(16).hex() + "@chatgpt-mcp"
        rrule = _build_rrule(recurrence, tzid=tzid, dtstart=s)

        ics_text = _build_vevent_ics(
            uid=uid,
            summary=summary,
            start=s,
            end=e,
            tzid=tzid,
            description=description,
            location=location,
            rrule=rrule,
            include_location=bool(location),
        )

        cal.save_event(ics_text)
        return uid

    @mcp.tool()
    def update_event(
        calendar_name_or_url: str,
        uid: str,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        tzid: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        recurrence: Optional[Dict[str, Any]] = None,
        clear_recurrence: bool = False,
    ) -> bool:
        """Update a VEVENT identified by UID."""
        tzid = tzid or DEFAULT_TZID

        cal = _resolve_calendar(calendar_name_or_url)

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

        new_summary = summary if summary is not None else old_summary
        new_desc    = description if description is not None else old_desc
        new_loc     = location if location is not None else old_loc
        new_start   = _parse_iso_or_default(start, old_dtstart)
        new_end_fallback = old_dtend if old_dtend is not None else (new_start + dt.timedelta(hours=1))
        new_end     = _parse_iso_or_default(end, new_end_fallback)

        new_start = _normalize_to_tz(new_start, tzid)
        new_end = _normalize_to_tz(new_end, tzid)

        if clear_recurrence:
            effective_rrule: Optional[str] = None
        elif recurrence is not None:
            effective_rrule = _build_rrule(recurrence, tzid=tzid, dtstart=new_start)
        else:
            effective_rrule = old_rrule_str

        ics_text = _build_vevent_ics(
            uid=uid,
            summary=new_summary,
            start=new_start,
            end=new_end,
            tzid=tzid,
            description=new_desc,
            location=new_loc,
            rrule=effective_rrule,
            include_location=new_loc is not None and new_loc != "",
        )

        target.data = ics_text
        target.save()
        return True

    @mcp.tool()
    def delete_event(calendar_name_or_url: str, uid: str) -> bool:
        """Delete a VEVENT by UID from the given calendar."""
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
    import uvicorn

    log.info(
        "Starting MCP HTTP server on %s:%s  OAuth=%s",
        SERVER_HOST, SERVER_PORT, OAUTH_ENABLED,
    )
    log.info(
        "CalDAV: %s  Apple ID: %r  TZ: %s  DR_ONLY=%s",
        CALDAV_URL, APPLE_ID, DEFAULT_TZID, DR_ONLY,
    )

    # Obtain the Starlette ASGI app from FastMCP so we can attach middleware
    app = mcp.http_app(path="/mcp")

    if OAUTH_ENABLED:
        app.add_middleware(_BearerAuthMiddleware)
        log.info("OAuth enabled — /mcp requires Bearer token (client_id=%r)", OAUTH_CLIENT_ID)
    else:
        log.warning("OAuth is DISABLED — /mcp is publicly accessible. Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET to enable auth.")

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
