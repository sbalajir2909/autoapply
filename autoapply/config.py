"""
AutoApply configuration.

Edit CANDIDATE_PROFILE with your real details before running.
Sensitive values (API keys) are read from environment variables.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Candidate profile — fill in your details
# ---------------------------------------------------------------------------
CANDIDATE_PROFILE = {
    "first_name": os.environ.get("CANDIDATE_FIRST_NAME", "Jane"),
    "last_name": os.environ.get("CANDIDATE_LAST_NAME", "Doe"),
    "email": os.environ.get("CANDIDATE_EMAIL", "jane.doe@example.com"),
    "phone": os.environ.get("CANDIDATE_PHONE", "+1-555-000-0000"),
    "linkedin_url": os.environ.get("CANDIDATE_LINKEDIN", "https://linkedin.com/in/janedoe"),
    "github_url": os.environ.get("CANDIDATE_GITHUB", "https://github.com/janedoe"),
    "portfolio_url": os.environ.get("CANDIDATE_PORTFOLIO", ""),
    "salary_range": os.environ.get("CANDIDATE_SALARY", "120000-160000"),
    "available_start_date": os.environ.get("CANDIDATE_START_DATE", "Immediately"),
    "visa_sponsorship": "Yes",
    "work_authorization": "OPT - Requires Sponsorship",
    "graduation_date": "May 2026",
    "cover_letter": "",  # optional; set to a string or leave empty
    "master_resume_path": str(Path(__file__).parent.parent / "input" / "master_resume.tex"),
}

# ---------------------------------------------------------------------------
# Job search queries — customize for your target roles
# ---------------------------------------------------------------------------
SEARCH_QUERIES = [
    "cybersecurity engineer new grad 2025",
    "security engineer entry level visa sponsorship",
    "product security engineer new graduate",
    "application security engineer junior",
    "cloud security engineer new grad OPT sponsorship",
    "information security analyst entry level 2025",
    "security software engineer new grad",
    "site:greenhouse.io security engineer",
    "site:lever.co cybersecurity engineer",
]

# ---------------------------------------------------------------------------
# Target job boards
# ---------------------------------------------------------------------------
JOB_BOARDS = [
    "greenhouse",
    "lever",
    "linkedin",
    "indeed",
]

ROLE_KEYWORDS = [
    "security engineer",
    "cybersecurity engineer",
    "product security",
    "application security",
    "cloud security engineer",
    "information security",
]

# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
DB_PATH = str(BASE_DIR / "autoapply.db")
CHROMA_DIR = str(BASE_DIR / "chroma_db")
RESUME_OUTPUT_DIR = str(BASE_DIR / "output" / "tailored")
SCREENSHOT_DIR = str(BASE_DIR / "output" / "screenshots")

# ---------------------------------------------------------------------------
# Pipeline settings
# ---------------------------------------------------------------------------
MAX_APPS_PER_RUN = 25
AUTO_SUBMIT_ATS = ["greenhouse", "lever"]  # these are auto-submitted; others go to queue
APPLY_DELAY_MIN = 30   # seconds between applications (min)
APPLY_DELAY_MAX = 90   # seconds between applications (max)
MAX_CLICK_ATTEMPTS = 5  # max clicks trying to reach the apply page
PAGE_LOAD_TIMEOUT = 15000  # ms
SCROLL_PAUSE = 2.0  # seconds between scrolls

# ---------------------------------------------------------------------------
# LLM settings
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLASSIFIER_MODEL = "claude-haiku-4-20250514"     # fast + cheap for HTML analysis
TAILOR_MODEL = "claude-sonnet-4-20250514"         # best for resume tailoring

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
PIPELINE_CRON_HOUR = 21   # 9 PM local time
PIPELINE_CRON_MINUTE = 0

# ---------------------------------------------------------------------------
# Semantic dedup threshold (ChromaDB cosine similarity)
# ---------------------------------------------------------------------------
SEMANTIC_DEDUP_THRESHOLD = 0.95
