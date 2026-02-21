"""Daily stats report for the AutoApply pipeline."""

from datetime import datetime, date
from typing import Dict, Any

from ..storage.database import get_all_stats, get_connection


def get_today_stats() -> Dict[str, Any]:
    """Return stats for applications discovered/applied today."""
    today = date.today().isoformat()
    conn = get_connection()

    discovered_today = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE date(date_discovered) = ?", (today,)
    ).fetchone()[0]

    applied_today = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE date(date_applied) = ? AND status = 'applied'",
        (today,),
    ).fetchone()[0]

    conn.close()

    return {
        "date": today,
        "discovered_today": discovered_today,
        "applied_today": applied_today,
        "overall": get_all_stats(),
    }


def generate_daily_report() -> str:
    """Generate a human-readable daily report string."""
    stats = get_today_stats()
    overall = stats["overall"]

    lines = [
        "=" * 50,
        "AUTOAPPLY DAILY REPORT",
        f"Date: {stats['date']}",
        "=" * 50,
        "",
        "TODAY:",
        f"  New jobs discovered : {stats['discovered_today']}",
        f"  Applications sent   : {stats['applied_today']}",
        "",
        "OVERALL TOTALS:",
        f"  Total jobs in DB    : {overall.get('total', 0)}",
        f"  Applied             : {overall.get('applied', 0)}",
        f"  Pending review      : {overall.get('pending_review', 0)}",
        f"  Discovered          : {overall.get('discovered', 0)}",
        f"  Rejected / skipped  : {overall.get('rejected', 0)}",
        "",
        "=" * 50,
    ]
    return "\n".join(lines)


def print_daily_report() -> None:
    """Print the daily report to stdout."""
    print(generate_daily_report())
