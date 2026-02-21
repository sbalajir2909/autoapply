"""
Deduplication logic using UUID and signature hashing.

Two-layer deduplication:
    1. uuid — SHA-256 of (company + url + post_id): exact duplicate detection
    2. sig_hash — MD5 of first 500 chars of JD: same JD posted on multiple boards
"""

import hashlib
import sqlite3
from typing import Optional


def generate_job_uuid(company: str, url: str, job_post_id: str = "") -> str:
    """
    Generate a deterministic UUID for a job posting.

    Combines company name, URL, and job post ID so the same position
    posted at the same URL always produces the same UUID.
    """
    raw = f"{company.lower().strip()}{url.strip()}{job_post_id.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_sig_hash(jd_text: str) -> str:
    """
    Generate a content signature hash for semantic deduplication.

    Uses the first 500 chars of the JD text — enough to catch the same
    job posted across multiple boards while ignoring minor formatting diffs.
    """
    condensed = jd_text[:500].lower().strip()
    return hashlib.md5(condensed.encode()).hexdigest()


def is_duplicate(uuid: str, sig_hash: str, conn: sqlite3.Connection) -> bool:
    """
    Return True if a job with this uuid OR sig_hash already exists in the DB.
    """
    cursor = conn.execute(
        "SELECT 1 FROM jobs WHERE uuid = ? OR sig_hash = ?",
        (uuid, sig_hash),
    )
    return cursor.fetchone() is not None


def extract_post_id_from_url(url: str) -> str:
    """
    Best-effort extraction of a job post ID from common ATS URL patterns.

    Examples:
        greenhouse.io/.../jobs/12345  → "12345"
        lever.co/company/job-slug     → "job-slug"
        linkedin.com/jobs/view/12345  → "12345"
    """
    import re
    patterns = [
        r'/jobs?/(\d+)',          # Greenhouse, LinkedIn numeric IDs
        r'/view/(\d+)',           # LinkedIn
        r'/([a-f0-9\-]{36})$',   # UUID-style slugs (Lever)
        r'/([^/?#]+)$',           # last path segment fallback
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""
