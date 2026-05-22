# tests/test_dedup.py
import sqlite3

from event_extractor.dedup import EmailDedup


class TestEmailDedup:
    def test_is_processed_returns_false_for_new_uid(self, temp_db_path):
        dedup = EmailDedup(db_path=temp_db_path)
        assert not dedup.is_processed("uid-001")

    def test_is_processed_returns_true_after_mark(self, temp_db_path):
        dedup = EmailDedup(db_path=temp_db_path)
        dedup.mark_processed("uid-001")
        assert dedup.is_processed("uid-001")

    def test_mark_processed_is_idempotent(self, temp_db_path):
        dedup = EmailDedup(db_path=temp_db_path)
        dedup.mark_processed("uid-001")
        dedup.mark_processed("uid-001")
        assert dedup.is_processed("uid-001")

    def test_filter_unprocessed_returns_only_new_uids(self, temp_db_path):
        dedup = EmailDedup(db_path=temp_db_path)
        dedup.mark_processed("uid-001")
        emails = [
            {"uid": "uid-001"},
            {"uid": "uid-002"},
            {"uid": "uid-003"},
        ]
        result = dedup.filter_unprocessed(emails)
        assert len(result) == 2
        uids = [e["uid"] for e in result]
        assert "uid-001" not in uids
        assert "uid-002" in uids
        assert "uid-003" in uids

    def test_filter_unprocessed_marks_as_processed(self, temp_db_path):
        dedup = EmailDedup(db_path=temp_db_path)
        emails = [{"uid": "uid-001"}, {"uid": "uid-002"}]
        result = dedup.filter_unprocessed(emails)
        assert len(result) == 2
        assert dedup.is_processed("uid-001")
        assert dedup.is_processed("uid-002")

    def test_creates_table_on_init(self, temp_db_path):
        dedup = EmailDedup(db_path=temp_db_path)
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_emails'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_handles_multiple_instances_same_db(self, temp_db_path):
        dedup1 = EmailDedup(db_path=temp_db_path)
        dedup1.mark_processed("uid-001")
        dedup2 = EmailDedup(db_path=temp_db_path)
        assert dedup2.is_processed("uid-001")
