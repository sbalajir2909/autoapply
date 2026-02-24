"""
Settings store for the AutoApply dashboard.

Persists all configuration to dashboard_settings.json so the user never
has to edit config.py or set environment variables manually.
On load(), patches autoapply.config module-level variables in-process.
"""

import json
import sys
from pathlib import Path

# Make sure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from autoapply.config import SEARCH_QUERIES, JOB_BOARDS

SETTINGS_PATH = Path(__file__).parent / "dashboard_settings.json"

DEFAULTS: dict = {
    # ── API ────────────────────────────────────────────────────────────────
    "anthropic_api_key": "",

    # ── Candidate profile ──────────────────────────────────────────────────
    "first_name": "",
    "last_name": "",
    "email": "",
    "phone": "",
    "linkedin_url": "",
    "github_url": "",
    "portfolio_url": "",
    "salary_range": "120000-160000",
    "available_start_date": "Immediately",
    "visa_sponsorship": "Yes",
    "work_authorization": "OPT - Requires Sponsorship",
    "graduation_date": "May 2026",
    "cover_letter": "",
    "master_resume_path": "input/master_resume.tex",

    # ── Search ─────────────────────────────────────────────────────────────
    "search_queries": SEARCH_QUERIES,
    "job_boards": JOB_BOARDS,

    # ── Pipeline ───────────────────────────────────────────────────────────
    "max_apps_per_run": 25,
    "auto_submit_ats": ["greenhouse", "lever"],
    "apply_delay_min": 30,
    "apply_delay_max": 90,
    "semantic_dedup_threshold": 0.95,

    # ── Models ─────────────────────────────────────────────────────────────
    "classifier_model": "claude-haiku-4-20250514",
    "tailor_model": "claude-sonnet-4-20250514",

    # ── Scheduler ──────────────────────────────────────────────────────────
    "pipeline_cron_hour": 21,
    "pipeline_cron_minute": 0,
}


def load() -> dict:
    """
    Load settings from JSON. Missing keys fall back to DEFAULTS.
    Patches autoapply.config in-process so all imports see the new values.
    """
    settings = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            settings.update(saved)
        except Exception:
            pass
    apply_to_config(settings)
    return settings


def save(settings: dict) -> None:
    """Write settings to JSON and immediately apply to config."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    apply_to_config(settings)


def apply_to_config(s: dict) -> None:
    """
    Patch autoapply.config module-level variables in-process.
    This means any module that already imported from autoapply.config
    will see updated values on next attribute access (they import the module,
    not the value, in most places).
    """
    import autoapply.config as cfg

    # API key
    cfg.ANTHROPIC_API_KEY = s.get("anthropic_api_key", "")

    # Rebuild candidate profile
    cfg.CANDIDATE_PROFILE = {
        "first_name": s.get("first_name", ""),
        "last_name": s.get("last_name", ""),
        "email": s.get("email", ""),
        "phone": s.get("phone", ""),
        "linkedin_url": s.get("linkedin_url", ""),
        "github_url": s.get("github_url", ""),
        "portfolio_url": s.get("portfolio_url", ""),
        "salary_range": s.get("salary_range", ""),
        "available_start_date": s.get("available_start_date", ""),
        "visa_sponsorship": s.get("visa_sponsorship", "Yes"),
        "work_authorization": s.get("work_authorization", ""),
        "graduation_date": s.get("graduation_date", ""),
        "cover_letter": s.get("cover_letter", ""),
        "master_resume_path": s.get("master_resume_path", "input/master_resume.tex"),
    }

    # Search / discovery
    cfg.SEARCH_QUERIES = s.get("search_queries", DEFAULTS["search_queries"])
    cfg.JOB_BOARDS = s.get("job_boards", DEFAULTS["job_boards"])

    # Pipeline
    cfg.MAX_APPS_PER_RUN = int(s.get("max_apps_per_run", 25))
    cfg.AUTO_SUBMIT_ATS = s.get("auto_submit_ats", ["greenhouse", "lever"])
    cfg.APPLY_DELAY_MIN = int(s.get("apply_delay_min", 30))
    cfg.APPLY_DELAY_MAX = int(s.get("apply_delay_max", 90))
    cfg.SEMANTIC_DEDUP_THRESHOLD = float(s.get("semantic_dedup_threshold", 0.95))

    # Models
    cfg.CLASSIFIER_MODEL = s.get("classifier_model", DEFAULTS["classifier_model"])
    cfg.TAILOR_MODEL = s.get("tailor_model", DEFAULTS["tailor_model"])

    # Scheduler
    cfg.PIPELINE_CRON_HOUR = int(s.get("pipeline_cron_hour", 21))
    cfg.PIPELINE_CRON_MINUTE = int(s.get("pipeline_cron_minute", 0))

    # Also patch modules that cached ANTHROPIC_API_KEY at import time
    try:
        import autoapply.apply.classifier as _cls
        _cls.ANTHROPIC_API_KEY = cfg.ANTHROPIC_API_KEY
    except Exception:
        pass
    try:
        import autoapply.resume.jd_analyzer as _jda
        _jda.ANTHROPIC_API_KEY = cfg.ANTHROPIC_API_KEY
    except Exception:
        pass
