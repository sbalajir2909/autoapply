"""
Workday ATS strategy — multi-page LLM-powered form filling.

Workday applications span multiple pages (My Information, My Experience,
Application Questions, etc.). This strategy fills each page, clicks Next,
re-classifies, and repeats until it finds a Submit button or hits the page limit.
"""

import asyncio
from typing import Dict

from ..filler import fill_form
from ..classifier import classify_page, PageAnalysis
from ...config import CANDIDATE_PROFILE
from ...events import emit, EventType

MAX_PAGES = 10  # Safety limit to prevent infinite loops


class WorkdayStrategy:
    """Workday form filler — multi-page LLM-powered fill and auto-submit."""

    auto_submit = True

    async def execute(
        self,
        page,
        analysis: PageAnalysis,
        resume_pdf_path: str,
        profile: Dict = None,
    ) -> bool:
        """
        Navigate through Workday's multi-page application flow.

        Loop: fill_form → click_next → re-classify → fill_form → ... → submit.
        Returns True if submitted, False if queued for review.
        """
        profile = profile or CANDIDATE_PROFILE
        emit(EventType.INFO, "[Workday] Starting multi-page LLM-powered fill")
        print("[Workday] Starting multi-page LLM-powered fill")

        current_analysis = analysis

        for page_num in range(1, MAX_PAGES + 1):
            emit(EventType.INFO,
                 f"[Workday] Page {page_num}: filling {len(current_analysis.form_fields)} fields",
                 page_num=page_num)
            print(f"[Workday] Page {page_num}: {len(current_analysis.form_fields)} fields")

            # Fill current page
            try:
                await fill_form(page, current_analysis.form_fields, resume_pdf_path, profile)
            except Exception as e:
                emit(EventType.WARNING, f"[Workday] Fill error on page {page_num}: {e}")
                print(f"[Workday] Fill error on page {page_num}: {e}")

            # Check for Submit button first
            submitted = await self._try_submit(page)
            if submitted:
                emit(EventType.INFO, f"[Workday] Submitted on page {page_num}!")
                print(f"[Workday] Submitted on page {page_num}!")
                return True

            # Try clicking Next / Save & Continue
            advanced = await self._try_next(page)
            if not advanced:
                emit(EventType.INFO,
                     f"[Workday] No Next button found on page {page_num}, queuing for review")
                print(f"[Workday] No Next button on page {page_num}, queuing for review")
                return False

            # Wait for new page to load
            await asyncio.sleep(2)

            # Re-classify the new page
            try:
                html = await page.content()
                current_analysis = classify_page(html)
                if not current_analysis.form_fields:
                    # Might be a confirmation page — try submit one more time
                    submitted = await self._try_submit(page)
                    if submitted:
                        emit(EventType.INFO, "[Workday] Submitted on confirmation page!")
                        return True
                    return False
            except Exception as e:
                emit(EventType.WARNING, f"[Workday] Re-classify failed: {e}")
                return False

        emit(EventType.WARNING,
             f"[Workday] Hit {MAX_PAGES}-page limit without submitting")
        print(f"[Workday] Hit {MAX_PAGES}-page limit, queuing for review")
        return False

    async def _try_submit(self, page) -> bool:
        """Look for and click a Submit button. Returns True if clicked."""
        selectors = [
            'button[data-automation-id="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Submit Application")',
            'input[type="submit"]',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(2)
                    return True
            except Exception:
                continue
        return False

    async def _try_next(self, page) -> bool:
        """Look for and click a Next / Save & Continue button. Returns True if clicked."""
        selectors = [
            'button[data-automation-id="bottom-navigation-next-button"]',
            'button:has-text("Next")',
            'button:has-text("Save & Continue")',
            'button:has-text("Save and Continue")',
            'button:has-text("Continue")',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(1)
                    return True
            except Exception:
                continue
        return False
