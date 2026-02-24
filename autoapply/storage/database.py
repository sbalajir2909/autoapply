"""
SQLite storage layer for AutoApply.

Schema:
    jobs — one row per unique job posting
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

import autoapply.config as _cfg   # import module, not value — so tests can patch cfg.DB_PATH


def _db_path() -> str:
    """Read DB_PATH from config at call time so test fixtures can redirect it."""
    return _cfg.DB_PATH


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set to dict-like rows."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    Path(_db_path()).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
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
    """)
    conn.commit()
    conn.close()


def insert_job(
    uuid: str,
    company_name: str,
    job_url: str,
    sig_hash: str,
    job_title: str = "",
    job_description: str = "",
    job_post_id: str = "",
    ats_platform: str = "",
) -> bool:
    """Insert a new job. Returns True if inserted, False if it was a duplicate."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO jobs
                (uuid, company_name, job_title, job_url, job_description,
                 job_post_id, sig_hash, ats_platform)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid, company_name, job_title, job_url, job_description,
             job_post_id, sig_hash, ats_platform),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def update_status(
    uuid: str,
    status: str,
    tailored_resume_path: str = "",
    screenshot_path: str = "",
    form_data: str = "",
    notes: str = "",
) -> None:
    """Update the status and optional metadata for a job."""
    conn = get_connection()
    date_applied = datetime.now(timezone.utc).isoformat() if status == "applied" else None
    conn.execute(
        """
        UPDATE jobs
        SET status = ?,
            tailored_resume_path = COALESCE(NULLIF(?, ''), tailored_resume_path),
            screenshot_path      = COALESCE(NULLIF(?, ''), screenshot_path),
            form_data            = COALESCE(NULLIF(?, ''), form_data),
            notes                = COALESCE(NULLIF(?, ''), notes),
            date_applied         = COALESCE(?, date_applied)
        WHERE uuid = ?
        """,
        (status, tailored_resume_path, screenshot_path, form_data, notes, date_applied, uuid),
    )
    conn.commit()
    conn.close()


def get_pending_jobs(status: str = "pending_review") -> List[Dict[str, Any]]:
    """Return jobs with the given status as a list of dicts."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = ? ORDER BY date_discovered DESC",
        (status,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_stats() -> Dict[str, Any]:
    """Return a summary of job counts by status."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
    ).fetchall()
    conn.close()
    stats = {r["status"]: r["count"] for r in rows}
    stats["total"] = sum(stats.values())
    return stats


def get_job(uuid: str) -> Optional[Dict[str, Any]]:
    """Return a single job dict or None."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE uuid = ?", (uuid,)).fetchone()
    conn.close()
    return dict(row) if row else None
