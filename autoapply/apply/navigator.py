"""
Apply page navigator.

Navigates from a job listing page to the actual application form by
repeatedly classifying the current page and clicking the Apply button
until we land on a form page or exhaust our attempt budget.
"""

import asyncio
from typing import Tuple, Optional

from .classifier import classify_page, PageAnalysis
from ..config import MAX_CLICK_ATTEMPTS, PAGE_LOAD_TIMEOUT


async def navigate_to_apply_page(
    page,  # playwright Page object
    job_url: str,
    max_clicks: int = MAX_CLICK_ATTEMPTS,
) -> Tuple[bool, Optional[PageAnalysis]]:
    """
    Starting from job_url, navigate to the application form.

    Strategy:
        1. Go to job_url.
        2. Classify the page.
        3. If it's already an apply page → done.
        4. If there's an apply button → click it and repeat.
        5. If a new tab opens → switch to it and repeat.
        6. After max_clicks attempts → give up.

    Args:
        page:       Playwright Page object (from an active browser context).
        job_url:    URL of the job listing.
        max_clicks: Max number of click attempts.

    Returns:
        (True, PageAnalysis)  if an apply page was found.
        (False, None)         if no apply page was found within max_clicks.
    """
    await page.goto(job_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
    await asyncio.sleep(2)

    for attempt in range(max_clicks):
        html = await page.content()
        analysis = classify_page(html)

        if analysis.is_apply_page:
            print(f"[Navigator] Apply page found after {attempt} click(s): {page.url}")
            return True, analysis

        if not analysis.has_apply_button:
            print(f"[Navigator] No apply button found on {page.url}")
            return False, None

        # Try to click the apply button
        clicked = await _click_apply_button(page, analysis)
        if not clicked:
            return False, None

        # Handle new tab/window
        context = page.context
        if len(context.pages) > 1:
            new_page = context.pages[-1]
            await new_page.wait_for_load_state("domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            # Re-classify the new page
            html = await new_page.content()
            analysis = classify_page(html)
            if analysis.is_apply_page:
                # Return the new page's analysis — caller should use new_page from context
                print(f"[Navigator] Apply page found in new tab: {new_page.url}")
                return True, analysis

        await asyncio.sleep(2)

    print(f"[Navigator] Exhausted {max_clicks} attempts for {job_url}")
    return False, None


async def _click_apply_button(page, analysis: PageAnalysis) -> bool:
    """
    Attempt to click the apply button using the CSS selector from analysis,
    then fall back to common apply-button patterns.
    """
    selectors_to_try = []

    if analysis.apply_button_selector:
        selectors_to_try.append(analysis.apply_button_selector)

    # Common fallback selectors across ATS platforms
    selectors_to_try.extend([
        "button[data-qa='btn-apply']",           # Greenhouse
        "a.postings-btn[href*='apply']",          # Lever
        "a[href*='apply']:not([href*='linkedin'])",
        "button:has-text('Apply Now')",
        "button:has-text('Apply')",
        "a:has-text('Apply Now')",
        "a:has-text('Apply')",
        "[class*='apply-btn']",
        "[id*='apply-btn']",
        "input[type='submit'][value*='Apply']",
    ])

    for selector in selectors_to_try:
        try:
            element = page.locator(selector).first
            if await element.count() > 0:
                await element.click()
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue

    print(f"[Navigator] Could not find a clickable apply button on {page.url}")
    return False
