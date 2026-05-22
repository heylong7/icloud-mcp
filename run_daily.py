#!/usr/bin/env python3
"""Daily email event extraction script for Windows Task Scheduler.

Usage:
    python run_daily.py

Intended to be scheduled via Windows Task Scheduler to run daily at 8:00 AM.
Fetches unread emails from the last 1 day, extracts explicit-time events,
and auto-creates them in iCloud Calendar. Vague events are logged for review.
"""

import logging
import sys
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
            if plain is not None:
                return plain
            return ""
        else:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            return payload.decode(charset, errors="replace") if payload else ""

    def uid_from_meta(meta: bytes) -> str:
        m = re.search(rb"\bUID\s+(\d+)\b", meta, re.IGNORECASE)
        return m.group(1).decode() if m else "?"

    log.info("Connecting to iCloud IMAP...")
    conn = imaplib.IMAP4_SSL(imap_host, imap_port)
    try:
        conn.login(apple_id, app_pw)
        conn.select('"INBOX"', readonly=True)

        status, data = conn.uid("SEARCH", None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            log.info("No unread emails found")
            return

        uids = data[0].split()[-50:][::-1]  # newest 50 first
        log.info("Found %d unread emails", len(uids))

        emails = []
        for uid_b in uids:
            status, fetch_data = conn.uid("FETCH", uid_b, "(FLAGS RFC822)")
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
            log.info("No unread emails to process")
            return

        result = extract_and_sync(
            provider=provider,
            emails=emails,
            auto_create=True,
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
