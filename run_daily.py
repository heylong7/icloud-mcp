#!/usr/bin/env python3
"""Daily email event extraction script for Windows Task Scheduler.

Usage:
    python run_daily.py

Intended to be scheduled via Windows Task Scheduler to run daily at 8:00 AM.
Fetches emails from the last N days (configurable via SINCE_DAYS env var, default 3),
extracts explicit-time events, and auto-creates them in iCloud Calendar.
Vague events are logged for review.
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from event_extractor.config import get_provider_config
from event_extractor.provider_factory import get_provider
from event_extractor.extractor import extract_and_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "run_daily.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("run_daily")


def main() -> None:
    log.info("=== Starting daily email event extraction ===")

    try:
        config = get_provider_config()
        log.info("Using LLM provider: %s (model: %s)", config["provider"], config["model"])
    except RuntimeError as exc:
        log.error("Configuration error: %s", exc)
        sys.exit(1)

    provider = get_provider(config)

    # For auto mode, we use the IMAP connection directly (no MCP server needed)
    import os
    import imaplib
    import email as _email_mod
    import re
    from email.header import decode_header as _decode_rfc2047

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

    apple_id = os.environ.get("APPLE_ID", "")
    app_pw = os.environ.get("ICLOUD_APP_PASSWORD", "")
    imap_host = os.environ.get("IMAP_HOST", "imap.mail.me.com")
    imap_port = int(os.environ.get("IMAP_PORT", "993"))
    try:
        since_days = int(os.environ.get("SINCE_DAYS", "3"))
    except (ValueError, TypeError):
        log.warning("Invalid SINCE_DAYS value, defaulting to 3")
        since_days = 3
    calendar_name = os.environ.get("CALENDAR_NAME", "Calendar")

    if not apple_id or not app_pw:
        log.error("Missing iCloud credentials. Set APPLE_ID and ICLOUD_APP_PASSWORD in .env")
        sys.exit(1)

    def decode_header(value: str) -> str:
        parts = _decode_rfc2047(value or "")
        out = []
        for raw, charset in parts:
            if isinstance(raw, bytes):
                out.append(raw.decode(charset or "utf-8", errors="replace"))
            else:
                out.append(str(raw))
        return "".join(out)

    def extract_body(msg: _email_mod.message.Message) -> str:
        if msg.is_multipart():
            plain = None
            html = None
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition", ""))
                if "attachment" in cd:
                    continue
                if ct == "text/plain" and plain is None:
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        plain = payload.decode(charset, errors="replace")
                elif ct == "text/html" and html is None:
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        html = payload.decode(charset, errors="replace")
            if plain is not None:
                return plain
            if html is not None:
                return re.sub(r"<[^>]+>", "", html)
            return ""
        else:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            return payload.decode(charset, errors="replace") if payload else ""

    def uid_from_meta(meta: bytes) -> str:
        m = re.search(rb"\bUID\s+(\d+)\b", meta, re.IGNORECASE)
        return m.group(1).decode() if m else "?"

    log.info("Connecting to iCloud IMAP...")
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=30)
        conn.login(apple_id, app_pw)
        conn.select('"INBOX"', readonly=True)
    except Exception:
        log.exception("Failed to connect to iCloud IMAP, skipping this run")
        return

    try:
        _months = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                   7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
        cutoff = datetime.now() - timedelta(days=since_days)
        since_date = cutoff.strftime(f"%d-{_months[cutoff.month]}-%Y")
        status, data = conn.uid("SEARCH", None, f"SINCE {since_date}")
        if status != "OK" or not data or not data[0]:
            log.info("No emails found since %d day(s) ago", since_days)
            return

        uids = data[0].split()
        uids = uids[-50:][::-1]  # newest 50 first
        log.info("Found %d emails since %d day(s) ago, processing newest 50", len(uids), since_days)

        emails = []
        for uid_b in uids:
            status, fetch_data = conn.uid("FETCH", uid_b, "(FLAGS BODY[])")
            if status != "OK":
                continue
            for item in fetch_data:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                meta, raw = item
                if not isinstance(meta, bytes):
                    meta = str(meta).encode()
                msg = _email_mod.message_from_bytes(raw)
                emails.append({
                    "uid": uid_from_meta(meta),
                    "subject": decode_header(msg.get("Subject", "")),
                    "from": decode_header(msg.get("From", "")),
                    "date": msg.get("Date", ""),
                    "body": extract_body(msg),
                    "read": False,
                })

        if not emails:
            log.info("No emails to process")
            return

        result = extract_and_sync(
            provider=provider,
            emails=emails,
            auto_create=True,
            calendar_name=calendar_name,
            dedup_db_path=str(Path(__file__).parent / "event_extractor.db"),
        )

        log.info(
            "Extraction complete: %d scanned, %d new, %d explicit, %d auto-created, %d vague",
            result["total_emails_scanned"],
            result["new_emails_processed"],
            len(result["explicit"]),
            result["auto_created"],
            len(result["vague"]),
        )

        if result["vague"]:
            log.info("Vague events pending manual review:")
            for ev in result["vague"]:
                log.info("  - %s (from email UID %s)", ev.get("title"), ev.get("source_email_uid"))

    finally:
        try:
            conn.logout()
        except Exception:
            pass

    log.info("=== Daily extraction complete ===")


if __name__ == "__main__":
    main()
