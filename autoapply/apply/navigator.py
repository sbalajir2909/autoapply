"""
Apply page navigator.

Navigates from a job listing page to the actual application form by
repeatedly classifying the current page and clicking the Apply button
until we land on a form page or exhaust our attempt budget.
"""

import asyncio
from typing import Tuple, Optional

from .classifier import classify_page, PageAnalysis
from .auth_handler import detect_auth_wall, handle_auth
from ..config import MAX_CLICK_ATTEMPTS, PAGE_LOAD_TIMEOUT
from ..events import emit, EventType


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
    emit(EventType.PAGE_LOAD, f"Loading page: {job_url}", url=job_url)
    await page.goto(job_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
    await asyncio.sleep(2)

    for attempt in range(max_clicks):
        await _dismiss_overlays(page)

        # Check for auth walls before classifying
        auth_type = await detect_auth_wall(page)
        if auth_type != "none":
            resolved = await handle_auth(page, auth_type, original_url=job_url)
            if not resolved:
                emit(EventType.AUTH_FAILURE,
                     f"Auth wall could not be resolved: {auth_type}",
                     auth_type=auth_type, url=page.url)
                return False, None
            # Auth resolved — continue with classification
            await asyncio.sleep(2)

        html = await page.content()
        html_preview = html[:500].replace('\n', ' ').strip()
        emit(EventType.PAGE_HTML_CAPTURED,
             f"Captured HTML ({len(html)} chars) — classifying page (attempt {attempt + 1}/{max_clicks})",
             html_length=len(html), attempt=attempt + 1, max_attempts=max_clicks,
             url=page.url, html_preview=html_preview)

        analysis = classify_page(html)

        # Emit classification result
        analysis_data = {
            "is_apply_page": analysis.is_apply_page,
            "has_apply_button": analysis.has_apply_button,
            "apply_button_selector": analysis.apply_button_selector,
            "ats_platform": analysis.ats_platform,
            "form_fields": [
                {"name": f.name, "label": f.label, "type": f.field_type,
                 "required": f.required, "selector": f.selector}
                for f in analysis.form_fields
            ],
        }
        emit(EventType.PAGE_CLASSIFIED,
             f"Page classified — Apply page: {analysis.is_apply_page}, "
             f"ATS: {analysis.ats_platform}, Fields: {len(analysis.form_fields)}",
             analysis=analysis_data, url=page.url)

        if analysis.is_apply_page:
            emit(EventType.PAGE_APPLY_FOUND,
                 f"Apply page found after {attempt} click(s): {page.url}",
                 url=page.url, clicks=attempt)
            print(f"[Navigator] Apply page found after {attempt} click(s): {page.url}")
            return True, analysis

        if not analysis.has_apply_button:
            emit(EventType.PAGE_APPLY_NOT_FOUND,
                 f"No apply button found on {page.url}",
                 url=page.url)
            print(f"[Navigator] No apply button found on {page.url}")
            return False, None

        # Try to click the apply button
        emit(EventType.PAGE_NAVIGATE_CLICK,
             f"Clicking apply button (attempt {attempt + 1}) — selector: {analysis.apply_button_selector or 'fallback'}",
             selector=analysis.apply_button_selector, attempt=attempt + 1,
             url=page.url)

        clicked = await _click_apply_button(page, analysis)
        if not clicked:
            emit(EventType.PAGE_APPLY_NOT_FOUND,
                 f"Could not click apply button on {page.url}",
                 url=page.url, reason="click_failed")
            return False, None

        # Handle new tab/window
        context = page.context
        if len(context.pages) > 1:
            new_page = context.pages[-1]
            emit(EventType.INFO,
                 f"New tab opened — switching to: {new_page.url}",
                 url=new_page.url)
            await new_page.wait_for_load_state("domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)

            # Check for auth walls in the new tab
            new_tab_auth = await detect_auth_wall(new_page)
            if new_tab_auth != "none":
                resolved = await handle_auth(new_page, new_tab_auth, original_url=job_url)
                if not resolved:
                    return False, None
                await asyncio.sleep(2)

            # Re-classify the new page
            html = await new_page.content()
            analysis = classify_page(html)

            new_analysis_data = {
                "is_apply_page": analysis.is_apply_page,
                "has_apply_button": analysis.has_apply_button,
                "ats_platform": analysis.ats_platform,
                "form_fields": [
                    {"name": f.name, "label": f.label, "type": f.field_type,
                     "required": f.required}
                    for f in analysis.form_fields
                ],
            }
            emit(EventType.PAGE_CLASSIFIED,
                 f"New tab classified — Apply page: {analysis.is_apply_page}, ATS: {analysis.ats_platform}",
                 analysis=new_analysis_data, url=new_page.url)

            if analysis.is_apply_page:
                # Return the new page's analysis — caller should use new_page from context
                emit(EventType.PAGE_APPLY_FOUND,
                     f"Apply page found in new tab: {new_page.url}",
                     url=new_page.url, clicks=attempt + 1)
                print(f"[Navigator] Apply page found in new tab: {new_page.url}")
                return True, analysis

        await asyncio.sleep(2)

    emit(EventType.PAGE_APPLY_NOT_FOUND,
         f"Exhausted {max_clicks} attempts for {job_url}",
         url=job_url, max_clicks=max_clicks)
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
                emit(EventType.PAGE_NAVIGATE_CLICK,
                     f"Clicking: {selector}",
                     selector=selector, url=page.url)
                await element.click()
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue

    print(f"[Navigator] Could not find a clickable apply button on {page.url}")
    return False


async def _dismiss_overlays(page) -> None:
    """Best-effort dismissal of cookie banners, modals, and popups."""
    dismiss_selectors = [
        # Cookie consent banners
        "button:has-text('Accept')",
        "button:has-text('Accept All')",
        "button:has-text('Accept Cookies')",
        "[id*='cookie'] button",
        "[class*='cookie'] button",
        # Generic modal close buttons
        "button[aria-label='Close']",
        "button[aria-label='Dismiss']",
        "[class*='modal'] button[class*='close']",
        "[class*='overlay'] button[class*='close']",
        # Sign-in nags
        "button:has-text('No thanks')",
        "button:has-text('Not now')",
    ]
    for selector in dismiss_selectors:
        try:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible():
                await el.click(timeout=2000)
                await asyncio.sleep(0.5)
        except Exception:
            continue
