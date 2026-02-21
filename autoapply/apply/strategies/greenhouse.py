"""
Greenhouse ATS form filling strategy.

Greenhouse uses a well-structured form at boards.greenhouse.io/<company>/jobs/<id>.
This strategy fills known Greenhouse field selectors and AUTO-SUBMITS.
"""

import asyncio
from pathlib import Path
from typing import Dict

from ..filler import fill_form, fuzzy_match_field
from ..classifier import PageAnalysis
from ...config import CANDIDATE_PROFILE


# Known Greenhouse field selectors
GH_SELECTORS = {
    "first_name":        "#first_name",
    "last_name":         "#last_name",
    "email":             "#email",
    "phone":             "#phone",
    "linkedin_url":      "#job_application_answers_attributes_0_text_value",  # varies
    "resume_file":       "input[name='resume']",
    "cover_letter_file": "input[name='cover_letter']",
    "submit":            "input[type='submit'][value*='Submit'], button[type='submit']",
}


async def apply_greenhouse(
    page,
    analysis: PageAnalysis,
    resume_pdf_path: str,
    profile: Dict = None,
) -> bool:
    """
    Fill a Greenhouse application form and submit it.

    Args:
        page:            Playwright Page on the Greenhouse apply page.
        analysis:        PageAnalysis from the classifier.
        resume_pdf_path: Path to the tailored PDF resume.
        profile:         Candidate profile dict.

    Returns:
        True if submitted successfully, False otherwise.
    """
    profile = profile or CANDIDATE_PROFILE

    # Fill standard fields using known selectors
    for field_key, selector in GH_SELECTORS.items():
        if field_key in ("resume_file", "cover_letter_file", "submit"):
            continue

        value = profile.get(field_key, "")
        if not value:
            continue
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                await el.fill(str(value))
        except Exception as e:
            print(f"[Greenhouse] Could not fill {field_key}: {e}")

    # Upload resume
    await _upload_resume(page, resume_pdf_path)

    # Fill any remaining discovered fields via generic filler
    await fill_form(page, analysis.form_fields, resume_pdf_path, profile)

    # AUTO-SUBMIT
    return await _submit(page)


async def _upload_resume(page, resume_pdf_path: str):
    """Upload resume to the Greenhouse file input."""
    if not Path(resume_pdf_path).exists():
        print(f"[Greenhouse] Resume not found: {resume_pdf_path}")
        return
    try:
        file_input = page.locator("input[type='file']").first
        if await file_input.count() > 0:
            await file_input.set_input_files(resume_pdf_path)
            await asyncio.sleep(1)
    except Exception as e:
        print(f"[Greenhouse] Resume upload failed: {e}")


async def _submit(page) -> bool:
    """Click the Greenhouse submit button."""
    submit_selectors = [
        "input[type='submit']",
        "button[type='submit']",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
    ]
    for selector in submit_selectors:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                await el.click()
                await asyncio.sleep(3)
                print("[Greenhouse] Application submitted!")
                return True
        except Exception:
            continue
    print("[Greenhouse] Submit button not found")
    return False


class GreenhouseStrategy:
    """Strategy class interface for the strategy map."""

    auto_submit = True

    async def execute(
        self,
        page,
        analysis: PageAnalysis,
        resume_pdf_path: str,
        profile: Dict = None,
    ) -> bool:
        return await apply_greenhouse(page, analysis, resume_pdf_path, profile)
