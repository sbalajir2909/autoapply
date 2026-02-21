"""
AutoApply — Autonomous Job Application Pipeline

Modules:
    discovery   - Job discovery from Greenhouse, Lever, LinkedIn, Indeed
    storage     - SQLite + ChromaDB deduplication and persistence
    apply       - Page classification, navigation, form filling, submission
    resume      - JD analysis and resume tailoring (wraps src/ai_tailor.py)
    dashboard   - Streamlit review UI for queued applications
    reports     - Daily stats reporting
"""

__version__ = "0.1.0"
