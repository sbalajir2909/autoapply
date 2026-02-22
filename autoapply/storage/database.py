"""
SQLite storage layer for AutoApply.

Schema:
    jobs           — one row per unique job posting
    status_history — audit trail of every status change (for timeline display)
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

import autoapply.config as _cfg   # import module, not value — so tests can patch cfg.DB_PATH


def _db_path() -> str:
    """Read DB_PATH from config at call time so test fixtures can redirect it."""
    return _cfg.DB_PATH


@contextmanager
def get_connection():
    """Yield a SQLite connection that auto-closes on exit."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# Columns added after initial release — ALTER TABLE is used to migrate.
_MIGRATION_COLUMNS = [
    ("job_board", "TEXT"),
    ("location", "TEXT"),
    ("keyword_coverage", "REAL"),
    ("matched_keywords", "TEXT"),
    ("missing_keywords", "TEXT"),
    ("resume_diff", "TEXT"),
    ("date_status_changed", "TIMESTAMP"),
    ("company_logo_url", "TEXT"),
]


def init_db() -> None:
    """Create tables if they don't exist, then run lightweight migrations."""
    Path(_db_path()).parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                uuid            TEXT PRIMARY KEY,
                company_name    TEXT NOT NULL,
                job_title       TEXT,
                job_url         TEXT UNIQUE NOT NULL,
                job_description TEXT,
                job_post_id     TEXT,
                sig_hash        TEXT UNIQUE,
                ats_platform    TEXT,
                tailored_resume_path TEXT,
                screenshot_path TEXT,
                form_data       TEXT,
                date_discovered TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                date_applied    TIMESTAMP,
                status          TEXT DEFAULT 'discovered',
                notes           TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_sig_hash ON jobs(sig_hash);
            CREATE INDEX IF NOT EXISTS idx_jobs_date ON jobs(date_discovered);

            CREATE TABLE IF NOT EXISTS status_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_uuid    TEXT NOT NULL,
                old_status  TEXT,
                new_status  TEXT NOT NULL,
                changed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                changed_by  TEXT DEFAULT 'pipeline',
                notes       TEXT,
                FOREIGN KEY (job_uuid) REFERENCES jobs(uuid)
            );
            CREATE INDEX IF NOT EXISTS idx_history_uuid ON status_history(job_uuid);
        """)
        conn.commit()

        # Migrate: add columns that may be missing from older databases
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        for col_name, col_type in _MIGRATION_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
        conn.commit()


def insert_job(
    uuid: str,
    company_name: str,
    job_url: str,
    sig_hash: str,
    job_title: str = "",
    job_description: str = "",
    job_post_id: str = "",
    ats_platform: str = "",
    job_board: str = "",
    location: str = "",
) -> bool:
    """Insert a new job. Returns True if inserted, False if it was a duplicate."""
    with get_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO jobs
                    (uuid, company_name, job_title, job_url, job_description,
                     job_post_id, sig_hash, ats_platform, job_board, location)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (uuid, company_name, job_title, job_url, job_description,
                 job_post_id, sig_hash, ats_platform, job_board, location),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def update_status(
    uuid: str,
    status: str,
    tailored_resume_path: str = "",
    screenshot_path: str = "",
    form_data: str = "",
    notes: str = "",
    keyword_coverage: Optional[float] = None,
    matched_keywords: str = "",
    missing_keywords: str = "",
    resume_diff: str = "",
    changed_by: str = "pipeline",
) -> None:
    """Update the status and optional metadata for a job, logging to status_history."""
    with get_connection() as conn:
        # Fetch old status for history
        row = conn.execute("SELECT status FROM jobs WHERE uuid = ?", (uuid,)).fetchone()
        old_status = row["status"] if row else None

        now = datetime.now(timezone.utc).isoformat()
        date_applied = now if status == "applied" else None

        conn.execute(
            """
            UPDATE jobs
            SET status = ?,
                tailored_resume_path = COALESCE(NULLIF(?, ''), tailored_resume_path),
                screenshot_path      = COALESCE(NULLIF(?, ''), screenshot_path),
                form_data            = COALESCE(NULLIF(?, ''), form_data),
                notes                = COALESCE(NULLIF(?, ''), notes),
                date_applied         = COALESCE(?, date_applied),
                keyword_coverage     = COALESCE(?, keyword_coverage),
                matched_keywords     = COALESCE(NULLIF(?, ''), matched_keywords),
                missing_keywords     = COALESCE(NULLIF(?, ''), missing_keywords),
                resume_diff          = COALESCE(NULLIF(?, ''), resume_diff),
                date_status_changed  = ?
            WHERE uuid = ?
            """,
            (status, tailored_resume_path, screenshot_path, form_data, notes,
             date_applied, keyword_coverage, matched_keywords, missing_keywords,
             resume_diff, now, uuid),
        )

        # Log status change
        if old_status != status:
            conn.execute(
                """
                INSERT INTO status_history (job_uuid, old_status, new_status, changed_by, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uuid, old_status, status, changed_by, notes or None),
            )

        conn.commit()


def get_pending_jobs(status: str = "pending_review") -> List[Dict[str, Any]]:
    """Return jobs with the given status as a list of dicts."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY date_discovered DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_stats() -> Dict[str, Any]:
    """Return a summary of job counts by status."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["count"] for r in rows}
        stats["total"] = sum(stats.values())
        return stats


def get_job(uuid: str) -> Optional[Dict[str, Any]]:
    """Return a single job dict or None."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE uuid = ?", (uuid,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Query helpers for tracker dashboard
# ---------------------------------------------------------------------------

def get_jobs_by_status(status: str) -> List[Dict[str, Any]]:
    """Return all jobs with a given status, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY date_discovered DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_jobs(
    status_filter: str = "",
    company_filter: str = "",
    search_query: str = "",
) -> List[Dict[str, Any]]:
    """Return jobs with optional filters, newest first."""
    clauses, params = [], []
    if status_filter:
        clauses.append("status = ?")
        params.append(status_filter)
    if company_filter:
        clauses.append("company_name = ?")
        params.append(company_filter)
    if search_query:
        clauses.append("(company_name LIKE ? OR job_title LIKE ?)")
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY date_discovered DESC", params
        ).fetchall()
        return [dict(r) for r in rows]


def get_status_history(job_uuid: str) -> List[Dict[str, Any]]:
    """Return the status change timeline for a job, oldest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM status_history WHERE job_uuid = ? ORDER BY changed_at ASC",
            (job_uuid,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pipeline_stats() -> Dict[str, Any]:
    """Aggregate counts for the tracker funnel."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["count"] for r in rows}

    total = sum(counts.values())
    applied = sum(counts.get(s, 0) for s in ("applied", "interview", "offer"))
    return {
        "total": total,
        "saved": counts.get("discovered", 0) + counts.get("queued_for_apply", 0),
        "applied": counts.get("applied", 0),
        "interview": counts.get("interview", 0),
        "offer": counts.get("offer", 0),
        "rejected": counts.get("rejected_after_apply", 0) + counts.get("error", 0),
        "in_progress": counts.get("in_progress", 0),
        "manual_review": counts.get("manual_review_needed", 0),
        "no_response": counts.get("no_response", 0),
        "by_status": counts,
        "success_rate": round((counts.get("interview", 0) + counts.get("offer", 0)) / applied * 100, 1) if applied else 0.0,
    }


def get_job_detail(uuid: str) -> Optional[Dict[str, Any]]:
    """Return full job data + status history for the detail view."""
    job = get_job(uuid)
    if not job:
        return None
    job["history"] = get_status_history(uuid)
    # Parse JSON fields for display
    for field in ("matched_keywords", "missing_keywords"):
        val = job.get(field)
        if val and isinstance(val, str):
            try:
                job[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                job[field] = []
    return job


def get_distinct_companies() -> List[str]:
    """Return sorted list of distinct company names."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT company_name FROM jobs ORDER BY company_name"
        ).fetchall()
        return [r["company_name"] for r in rows]


def get_distinct_statuses() -> List[str]:
    """Return sorted list of distinct statuses."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT status FROM jobs ORDER BY status"
        ).fetchall()
        return [r["status"] for r in rows]
