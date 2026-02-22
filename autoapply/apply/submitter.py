"""
Submit or queue decision logic.

Routes applications to auto-submit (Greenhouse, Lever) or the human
review dashboard (Workday, iCIMS, unknown ATS).
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from .classifier import PageAnalysis
from .filler import fill_form, fuzzy_match_field
from .strategies import get_strategy
from ..config import AUTO_SUBMIT_ATS, SCREENSHOT_DIR, CANDIDATE_PROFILE
from ..storage.database import update_status
from ..events import emit, EventType


async def decide_and_submit(
    page,
    page_analysis: PageAnalysis,
    job_uuid: str,
    resume_pdf_path: str,
    profile: Dict = None,
    dry_run: bool = False,
    auto_mode: bool = False,
) -> Tuple[bool, str]:
    """
    Choose the right strategy for the ATS, fill the form, and either
    submit automatically or queue for human review.

    Args:
        page:             Playwright Page on the application form.
        page_analysis:    PageAnalysis from the classifier.
        job_uuid:         UUID of the job (for DB updates and filenames).
        resume_pdf_path:  Path to the tailored PDF resume.
        profile:          Candidate profile dict.
        dry_run:          If True, fill but never submit.

    Returns:
        (submitted: bool, new_status: str)
        submitted=True means the form was submitted; False means queued.
        new_status is one of: "applied", "pending_review", "error"
    """
    profile = profile or CANDIDATE_PROFILE
    ats = page_analysis.ats_platform.lower()

    strategy_cls = get_strategy(ats)
    strategy = strategy_cls()

    emit(EventType.SUBMIT_DECISION,
         f"Strategy: {strategy_cls.__name__} (ATS={ats}, auto_submit={strategy.auto_submit}, dry_run={dry_run})",
         ats=ats, strategy=strategy_cls.__name__,
         auto_submit=strategy.auto_submit, dry_run=dry_run,
         job_uuid=job_uuid)

    print(f"[Submitter] ATS={ats}, auto_submit={strategy.auto_submit}, dry_run={dry_run}")

    if dry_run:
        emit(EventType.SUBMIT_DRY_RUN,
             "DRY RUN — filling form but not submitting",
             job_uuid=job_uuid)
        print("[Submitter] DRY RUN — filling form but not submitting")
        # Just fill, don't submit
        try:
            await fill_form(page, page_analysis.form_fields, resume_pdf_path, profile)
        except Exception as e:
            emit(EventType.ERROR, f"Dry-run fill error: {e}",
                 job_uuid=job_uuid, error=str(e))
            print(f"[Submitter] Dry-run fill error: {e}")
        return False, "dry_run"

    # Execute the ATS strategy
    try:
        emit(EventType.INFO,
             f"Executing {strategy_cls.__name__} strategy...",
             strategy=strategy_cls.__name__, job_uuid=job_uuid)
        submitted = await strategy.execute(page, page_analysis, resume_pdf_path, profile)
    except Exception as e:
        emit(EventType.SUBMIT_ERROR,
             f"Strategy execution error: {e}",
             strategy=strategy_cls.__name__, error=str(e), job_uuid=job_uuid)
        print(f"[Submitter] Strategy execution error: {e}")
        submitted = False

    if submitted and (strategy.auto_submit or auto_mode):
        emit(EventType.SUBMIT_AUTO,
             f"Auto-submitted via {strategy_cls.__name__}!" + (" (auto_mode)" if auto_mode else ""),
             strategy=strategy_cls.__name__, job_uuid=job_uuid)
        update_status(job_uuid, "applied", tailored_resume_path=resume_pdf_path)
        return True, "applied"

    # Queue for dashboard review
    emit(EventType.PAGE_SCREENSHOT,
         "Taking screenshot of filled form for dashboard...",
         job_uuid=job_uuid)
    screenshot_path = await _take_screenshot(page, job_uuid)
    if screenshot_path:
        emit(EventType.PAGE_SCREENSHOT,
             f"Screenshot saved: {screenshot_path}",
             path=screenshot_path, job_uuid=job_uuid)

    form_data = _serialize_form_state(page_analysis, profile)

    update_status(
        job_uuid,
        "pending_review",
        tailored_resume_path=resume_pdf_path,
        screenshot_path=screenshot_path,
        form_data=form_data,
    )

    emit(EventType.SUBMIT_QUEUED,
         f"Queued for dashboard review (screenshot + form data saved)",
         job_uuid=job_uuid, screenshot=screenshot_path)
    print(f"[Submitter] Queued for dashboard review: {job_uuid}")
    return False, "pending_review"


async def _take_screenshot(page, job_uuid: str) -> str:
    """Capture a screenshot of the filled form for dashboard display."""
    screenshot_dir = Path(SCREENSHOT_DIR)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = str(screenshot_dir / f"{job_uuid}.png")
    try:
        await page.screenshot(path=path, full_page=True)
    except Exception as e:
        print(f"[Submitter] Screenshot failed: {e}")
        path = ""
    return path


def _serialize_form_state(analysis: PageAnalysis, profile: Dict) -> str:
    """Serialize filled form data as JSON for dashboard display."""
    fields = []
    for f in analysis.form_fields:
        value = fuzzy_match_field(f.name or f.label, profile) or ""
        fields.append({"field": f.name or f.label, "type": f.field_type, "value": value})
    return json.dumps(fields)
