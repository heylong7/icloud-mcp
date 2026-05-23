# event_extractor/dedup.py
"""SQLite-based email deduplication."""

import logging
import sqlite3
from typing import Any, Dict, List

log = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS processed_emails (
    uid TEXT PRIMARY KEY,
    extracted_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


class EmailDedup:
    """Track processed email UIDs in SQLite to avoid re-extraction."""

    def __init__(self, db_path: str = "event_extractor.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.commit()

    def is_processed(self, uid: str) -> bool:
        """Check if a UID has already been processed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT 1 FROM processed_emails WHERE uid = ?", (uid,)
            )
            return cursor.fetchone() is not None

    def mark_processed(self, uid: str) -> None:
        """Mark a UID as processed."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_emails (uid) VALUES (?)",
                (uid,),
            )
            conn.commit()

    def filter_unprocessed(self, emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter a list of email dicts, returning only unprocessed ones.

        Does NOT mark as processed — call mark_processed after successful extraction.
        """
        unprocessed = []
        for email in emails:
            uid = email.get("uid", "")
            if uid and not self.is_processed(uid):
                unprocessed.append(email)
        return unprocessed
