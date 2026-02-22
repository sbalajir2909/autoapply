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
    "first_name": os.environ.get("CANDIDATE_FIRST_NAME", "Udaya Vijay"),
    "last_name": os.environ.get("CANDIDATE_LAST_NAME", "Anand"),
    "email": os.environ.get("CANDIDATE_EMAIL", "udaya.vijayanand@gmail.com"),
    "phone": os.environ.get("CANDIDATE_PHONE", "+1-(774)-519-8830"),
    "linkedin_url": os.environ.get("CANDIDATE_LINKEDIN", "https://linkedin.com/in/udaya-vijay-anand/"),
    "github_url": os.environ.get("CANDIDATE_GITHUB", "https://github.com/udsy19"),
    "portfolio_url": os.environ.get("CANDIDATE_PORTFOLIO", "https://udsy.in"),
    "salary_range": os.environ.get("CANDIDATE_SALARY", "90000-130000"),
    "available_start_date": os.environ.get("CANDIDATE_START_DATE", "Immediately"),
    "visa_sponsorship": "Yes",
    "work_authorization": "OPT - Requires Sponsorship",
    "graduation_date": "May 2026",
    "cover_letter": "",  # optional; set to a string or leave empty
    "master_resume_path": str(Path(__file__).parent.parent / "input" / "master_resume.tex"),
}

# ---------------------------------------------------------------------------
# Job search queries
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
# Target job boards and keywords
# ---------------------------------------------------------------------------
JOB_BOARDS = [
    "github_repos",
    "greenhouse",
    "lever",
    "linkedin",
    "indeed",
]

GITHUB_REPOS = [
    "https://github.com/SimplifyJobs/Summer2026-Internships",
    "https://github.com/vanshb03/Summer2026-Internships",
    "https://github.com/speedyapply/2026-AI-College-Jobs",
    "https://github.com/speedyapply/2026-SWE-College-Jobs",
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
PIPELINE_TIMEOUT = 3600  # seconds — hard cap on total pipeline runtime
MAX_API_CALLS_PER_RUN = 200  # safety budget for LLM calls per run

# ---------------------------------------------------------------------------
# LLM settings
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLASSIFIER_MODEL = "claude-haiku-4-20250514"     # fast + cheap for HTML analysis
TAILOR_MODEL = "claude-sonnet-4-20250514"         # best for resume tailoring

# ---------------------------------------------------------------------------
# Purdue GenAI Studio (alternative LLM backend)
# ---------------------------------------------------------------------------
GENAI_STUDIO_API_KEY = os.environ.get("GENAI_STUDIO_API_KEY", "")
GENAI_STUDIO_URL = "https://genai.rcac.purdue.edu/api/chat/completions"
GENAI_STUDIO_MODEL = "llama3.1:latest"  # fast, good accuracy per leaderboard

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
PIPELINE_CRON_HOUR = 21   # 9 PM local time
PIPELINE_CRON_MINUTE = 0

# ---------------------------------------------------------------------------
# Semantic dedup threshold (ChromaDB cosine similarity)
# ---------------------------------------------------------------------------
SEMANTIC_DEDUP_THRESHOLD = 0.95

# ---------------------------------------------------------------------------
# Auth / Session persistence
# ---------------------------------------------------------------------------
DATA_DIR = str(BASE_DIR / "data")
SESSION_DIR = os.path.join(DATA_DIR, "sessions")
SESSION_STATE_PATH = os.path.join(SESSION_DIR, "browser_state.json")
CREDENTIALS_PATH = os.path.join(SESSION_DIR, "credentials.json")

# Gmail IMAP for OTP / verification email monitoring
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = os.environ.get("IMAP_USER", "")  # Gmail address
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")  # Gmail App Password

# Timeout (seconds) to wait for user action via dashboard before skipping
AUTH_PAUSE_TIMEOUT = int(os.environ.get("AUTH_PAUSE_TIMEOUT", "300"))  # 5 min

# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------
def validate_config() -> None:
    """Check that required config values are present. Raises ValueError on failure."""
    errors = []
    required_profile_fields = ["first_name", "last_name", "email"]
    for field in required_profile_fields:
        if not CANDIDATE_PROFILE.get(field):
            errors.append(f"CANDIDATE_PROFILE['{field}'] is empty")

    resume_path = Path(CANDIDATE_PROFILE.get("master_resume_path", ""))
    if not resume_path.exists():
        errors.append(f"Master resume not found: {resume_path}")

    if errors:
        raise ValueError("Config validation failed:\n  " + "\n  ".join(errors))
