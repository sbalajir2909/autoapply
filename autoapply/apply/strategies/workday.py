"""
Workday ATS strategy — queues for human review.

Workday applications are highly dynamic and require account creation.
We fill what we can, then save state for dashboard review instead of auto-submitting.
"""

import asyncio
from typing import Dict

from ..filler import fill_form
from ..classifier import PageAnalysis
from ...config import CANDIDATE_PROFILE


class WorkdayStrategy:
    """Workday form filler — fills fields but does NOT auto-submit."""

    auto_submit = False  # Always goes to dashboard queue

    async def execute(
        self,
        page,
        analysis: PageAnalysis,
        resume_pdf_path: str,
        profile: Dict = None,
    ) -> bool:
        """
        Attempt best-effort form filling on Workday.
        Returns False to signal that the application needs human review.
        """
        profile = profile or CANDIDATE_PROFILE
        print("[Workday] Best-effort fill — will queue for dashboard review")

        try:
            await fill_form(page, analysis.form_fields, resume_pdf_path, profile)
        except Exception as e:
            print(f"[Workday] Partial fill error: {e}")

        # Do NOT click submit — return False to trigger queuing
        return False
