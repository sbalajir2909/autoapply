"""
Lever ATS form filling strategy.

Lever uses a clean form at jobs.lever.co/<company>/<slug>/apply.
This strategy fills known Lever field selectors and AUTO-SUBMITS.
"""

import asyncio
from pathlib import Path
from typing import Dict

from ..filler import fill_form
from ..classifier import PageAnalysis
from ...config import CANDIDATE_PROFILE


# Lever field selectors (stable across Lever boards)
LEVER_SELECTORS = {
    "first_name": "input[name='name']",        # Lever uses "name" for full name or splits
    "full_name":  "input[name='name']",
    "email":      "input[name='email']",
    "phone":      "input[name='phone']",
    "org":        "input[name='org']",          # current company/org
    "urls_LinkedIn": "input[name='urls[LinkedIn]']",
    "urls_GitHub":   "input[name='urls[GitHub]']",
    "urls_Other":    "input[name='urls[Other]']",
    "resume_file":   "input[type='file']",
    "submit":        "button[type='submit'], button:has-text('Submit application')",
}


async def apply_lever(
    page,
    analysis: PageAnalysis,
    resume_pdf_path: str,
    profile: Dict = None,
) -> bool:
    """
    Fill a Lever application form and submit it.

    Returns True if submitted successfully, False otherwise.
    """
    profile = profile or CANDIDATE_PROFILE

    # Lever uses full name in a single field
    full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()

    field_values = {
        "input[name='name']":              full_name,
        "input[name='email']":             profile.get("email", ""),
        "input[name='phone']":             profile.get("phone", ""),
        "input[name='urls[LinkedIn]']":    profile.get("linkedin_url", ""),
        "input[name='urls[GitHub]']":      profile.get("github_url", ""),
        "input[name='urls[Other]']":       profile.get("portfolio_url", ""),
    }

    for selector, value in field_values.items():
        if not value:
            continue
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                await el.fill(str(value))
        except Exception as e:
            print(f"[Lever] Could not fill {selector!r}: {e}")

    # Cover letter textarea (optional)
    cover = profile.get("cover_letter", "")
    if cover:
        try:
            cl = page.locator("textarea[name='comments']").first
            if await cl.count() > 0:
                await cl.fill(cover)
        except Exception:
            pass

    # Upload resume
    await _upload_resume(page, resume_pdf_path)

    # Fill any remaining fields via generic filler
    await fill_form(page, analysis.form_fields, resume_pdf_path, profile)

    # AUTO-SUBMIT
    return await _submit(page)


async def _upload_resume(page, resume_pdf_path: str):
    """Upload resume to Lever's file input."""
    if not Path(resume_pdf_path).exists():
        print(f"[Lever] Resume not found: {resume_pdf_path}")
        return
    try:
        file_input = page.locator("input[type='file']").first
        if await file_input.count() > 0:
            await file_input.set_input_files(resume_pdf_path)
            await asyncio.sleep(1)
    except Exception as e:
        print(f"[Lever] Resume upload failed: {e}")


async def _submit(page) -> bool:
    """Click the Lever submit button."""
    selectors = [
        "button[type='submit']",
        "button:has-text('Submit application')",
        "button:has-text('Submit')",
        "input[type='submit']",
    ]
    for selector in selectors:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                await el.click()
                await asyncio.sleep(3)
                print("[Lever] Application submitted!")
                return True
        except Exception:
            continue
    print("[Lever] Submit button not found")
    return False


class LeverStrategy:
    """Strategy class interface."""

    auto_submit = True

    async def execute(
        self,
        page,
        analysis: PageAnalysis,
        resume_pdf_path: str,
        profile: Dict = None,
    ) -> bool:
        return await apply_lever(page, analysis, resume_pdf_path, profile)
