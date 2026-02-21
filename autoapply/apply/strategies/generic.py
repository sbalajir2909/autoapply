"""
Generic ATS strategy — fuzzy fill + queue for human review.

Used as the fallback for any ATS not explicitly handled.
Fills all detected form fields via fuzzy matching, then queues
the application in the dashboard for human approval before submitting.
"""

from typing import Dict

from ..filler import fill_form
from ..classifier import PageAnalysis
from ...config import CANDIDATE_PROFILE


class GenericStrategy:
    """Generic form filler — fills fields but does NOT auto-submit."""

    auto_submit = False  # Goes to dashboard queue

    async def execute(
        self,
        page,
        analysis: PageAnalysis,
        resume_pdf_path: str,
        profile: Dict = None,
    ) -> bool:
        """
        Fill all detectable form fields on an unknown ATS.
        Returns False to trigger queuing in the dashboard.
        """
        profile = profile or CANDIDATE_PROFILE
        print(f"[Generic] Filling {len(analysis.form_fields)} detected fields — will queue for review")

        try:
            await fill_form(page, analysis.form_fields, resume_pdf_path, profile)
        except Exception as e:
            print(f"[Generic] Partial fill error: {e}")

        return False  # Always queue, never auto-submit
