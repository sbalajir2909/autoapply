"""Tests for Module 2: Storage Engine (database, dedup, rag_store)."""

import os
import sqlite3
import tempfile
import pytest

# ── patch DB_PATH to a temp file before importing anything ──────────────────
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["AUTOAPPLY_TEST_DB"] = _tmp.name

import autoapply.config as cfg
cfg.DB_PATH = _tmp.name          # redirect all storage calls to temp DB


from autoapply.storage.database import (
    init_db, insert_job, update_status,
    get_pending_jobs, get_all_stats, get_job, get_connection,
)
from autoapply.storage.dedup import (
    generate_job_uuid, generate_sig_hash, is_duplicate, extract_post_id_from_url,
)


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    """Wipe and reinitialise the DB before every test."""
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.execute("DROP TABLE IF EXISTS jobs")
    conn.commit()
    conn.close()
    init_db()
    yield


# ────────────────────────────────────────────────────────────────────────────
# init_db
# ────────────────────────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_jobs_table(self):
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        assert any(t["name"] == "jobs" for t in tables)

    def test_idempotent(self):
        """Calling init_db twice must not raise."""
        init_db()
        init_db()


# ────────────────────────────────────────────────────────────────────────────
# insert_job
# ────────────────────────────────────────────────────────────────────────────

class TestInsertJob:
    def _job(self, suffix=""):
        return dict(
            uuid=f"uuid-{suffix or 'a'}",
            company_name="Acme",
            job_url=f"https://example.com/job/{suffix or 'a'}",
            sig_hash=f"sig-{suffix or 'a'}",
            job_title="Security Engineer",
            job_description="We need Python and AWS skills.",
            job_post_id=suffix or "a",
            ats_platform="greenhouse",
        )

    def test_insert_returns_true(self):
        assert insert_job(**self._job("1")) is True

    def test_duplicate_uuid_returns_false(self):
        j = self._job("2")
        insert_job(**j)
        j2 = j.copy(); j2["job_url"] = "https://other.com/job"
        assert insert_job(**j2) is False     # same uuid → duplicate

    def test_duplicate_url_returns_false(self):
        j = self._job("3")
        insert_job(**j)
        j2 = j.copy(); j2["uuid"] = "different-uuid"
        assert insert_job(**j2) is False     # same url → unique constraint

    def test_duplicate_sig_hash_returns_false(self):
        j = self._job("4")
        insert_job(**j)
        j2 = j.copy()
        j2["uuid"] = "uid-x"; j2["job_url"] = "https://unique.com"
        assert insert_job(**j2) is False     # same sig_hash

    def test_defaults_status_to_discovered(self):
        j = self._job("5")
        insert_job(**j)
        row = get_job(j["uuid"])
        assert row["status"] == "discovered"

    def test_multiple_unique_jobs(self):
        for i in range(5):
            assert insert_job(**self._job(str(i))) is True
        assert get_all_stats()["total"] == 5


# ────────────────────────────────────────────────────────────────────────────
# update_status
# ────────────────────────────────────────────────────────────────────────────

class TestUpdateStatus:
    def _insert(self):
        insert_job(
            uuid="u1", company_name="Corp", job_url="https://corp.com",
            sig_hash="s1", job_title="Eng", job_description="desc",
        )

    def test_update_to_applied(self):
        self._insert()
        update_status("u1", "applied")
        assert get_job("u1")["status"] == "applied"

    def test_date_applied_set_when_applied(self):
        self._insert()
        update_status("u1", "applied")
        assert get_job("u1")["date_applied"] is not None

    def test_date_applied_not_set_for_other_statuses(self):
        self._insert()
        update_status("u1", "pending_review")
        assert get_job("u1")["date_applied"] is None

    def test_update_resume_path(self):
        self._insert()
        update_status("u1", "applied", tailored_resume_path="/tmp/r.pdf")
        assert get_job("u1")["tailored_resume_path"] == "/tmp/r.pdf"

    def test_update_notes(self):
        self._insert()
        update_status("u1", "rejected", notes="Not a fit")
        assert "Not a fit" in get_job("u1")["notes"]

    def test_unknown_uuid_does_not_raise(self):
        """Updating a non-existent UUID should silently do nothing."""
        update_status("no-such-uuid", "applied")


# ────────────────────────────────────────────────────────────────────────────
# get_pending_jobs
# ────────────────────────────────────────────────────────────────────────────

class TestGetPendingJobs:
    def test_returns_only_matching_status(self):
        for i, status in enumerate(["pending_review", "applied", "pending_review"]):
            insert_job(uuid=f"u{i}", company_name="C", job_url=f"https://c.com/{i}",
                       sig_hash=f"s{i}")
            update_status(f"u{i}", status)

        rows = get_pending_jobs("pending_review")
        assert len(rows) == 2
        assert all(r["status"] == "pending_review" for r in rows)

    def test_empty_when_none_match(self):
        assert get_pending_jobs("pending_review") == []


# ────────────────────────────────────────────────────────────────────────────
# get_all_stats
# ────────────────────────────────────────────────────────────────────────────

class TestGetAllStats:
    def test_counts_by_status(self):
        for i, status in enumerate(["applied", "applied", "pending_review", "rejected"]):
            insert_job(uuid=f"u{i}", company_name="C", job_url=f"https://c.com/{i}",
                       sig_hash=f"s{i}")
            update_status(f"u{i}", status)
        stats = get_all_stats()
        assert stats["applied"] == 2
        assert stats["pending_review"] == 1
        assert stats["rejected"] == 1
        assert stats["total"] == 4

    def test_empty_db(self):
        assert get_all_stats()["total"] == 0


# ────────────────────────────────────────────────────────────────────────────
# dedup helpers
# ────────────────────────────────────────────────────────────────────────────

class TestDedup:
    def test_uuid_deterministic(self):
        a = generate_job_uuid("Acme", "https://example.com", "123")
        b = generate_job_uuid("Acme", "https://example.com", "123")
        assert a == b

    def test_uuid_differs_by_company(self):
        a = generate_job_uuid("Acme", "https://example.com", "123")
        b = generate_job_uuid("Beta", "https://example.com", "123")
        assert a != b

    def test_uuid_normalises_case(self):
        a = generate_job_uuid("ACME", "https://example.com", "")
        b = generate_job_uuid("acme", "https://example.com", "")
        assert a == b

    def test_sig_hash_deterministic(self):
        assert generate_sig_hash("hello world") == generate_sig_hash("hello world")

    def test_sig_hash_uses_first_500_chars(self):
        text = "A" * 600
        assert generate_sig_hash(text) == generate_sig_hash("A" * 600)
        # Different at char 501 shouldn't matter
        assert generate_sig_hash("A" * 500 + "B") == generate_sig_hash("A" * 500 + "C")

    def test_is_duplicate_false_when_db_empty(self):
        conn = get_connection()
        assert is_duplicate("uuid-new", "sig-new", conn) is False
        conn.close()

    def test_is_duplicate_true_by_uuid(self):
        insert_job(uuid="u1", company_name="C", job_url="https://x.com",
                   sig_hash="s1")
        conn = get_connection()
        assert is_duplicate("u1", "sig-other", conn) is True
        conn.close()

    def test_is_duplicate_true_by_sig(self):
        insert_job(uuid="u1", company_name="C", job_url="https://x.com",
                   sig_hash="s1")
        conn = get_connection()
        assert is_duplicate("uuid-other", "s1", conn) is True
        conn.close()

    def test_is_duplicate_false_when_different(self):
        insert_job(uuid="u1", company_name="C", job_url="https://x.com",
                   sig_hash="s1")
        conn = get_connection()
        assert is_duplicate("u-new", "s-new", conn) is False
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# extract_post_id_from_url
# ────────────────────────────────────────────────────────────────────────────

class TestExtractPostId:
    def test_greenhouse_numeric(self):
        assert extract_post_id_from_url(
            "https://boards.greenhouse.io/acme/jobs/12345") == "12345"

    def test_linkedin_view(self):
        assert extract_post_id_from_url(
            "https://www.linkedin.com/jobs/view/99999") == "99999"

    def test_lever_slug(self):
        result = extract_post_id_from_url(
            "https://jobs.lever.co/company/some-job-slug")
        assert result == "some-job-slug"

    def test_empty_url(self):
        # Should not raise
        result = extract_post_id_from_url("")
        assert isinstance(result, str)

    def test_no_pattern_match_returns_something(self):
        result = extract_post_id_from_url("https://example.com/careers")
        assert isinstance(result, str)
