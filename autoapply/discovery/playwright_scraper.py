"""
Playwright-based headless browser scraper.

Provides:
    - scrape_html(url)          : fetch rendered HTML of any page
    - scroll_and_get_html(url)  : scroll to load lazy content, return HTML
    - scrape_text(url)          : return visible text content
"""

import asyncio
import random
from pathlib import Path

from ..config import PAGE_LOAD_TIMEOUT, SCROLL_PAUSE

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


async def _get_browser_context():
    """Create a stealth Playwright browser context."""
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 800},
        java_script_enabled=True,
        # Mimic a real browser's accepted languages
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    # Remove navigator.webdriver fingerprint
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return playwright, browser, context


async def scrape_html(url: str, wait_for: str = "networkidle") -> str:
    """
    Fetch the rendered HTML of a URL using headless Chromium.

    Args:
        url:      Target URL.
        wait_for: Playwright wait condition ("networkidle", "load", "domcontentloaded").

    Returns:
        Full page HTML as a string.
    """
    playwright, browser, context = await _get_browser_context()
    try:
        page = await context.new_page()
        await _goto_with_retry(page, url, wait_until=wait_for)
        html = await page.content()
        return html
    finally:
        await context.close()
        await browser.close()
        await playwright.stop()


async def _goto_with_retry(page, url: str, wait_until: str = "domcontentloaded", retries: int = 2):
    """Navigate to a URL with retry on timeout."""
    for attempt in range(retries + 1):
        try:
            await page.goto(url, wait_until=wait_until, timeout=PAGE_LOAD_TIMEOUT)
            return
        except Exception as e:
            if attempt < retries:
                delay = 3 * (attempt + 1)
                print(f"[Scraper] page.goto failed ({e}), retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                raise


async def scroll_and_get_html(url: str, scroll_count: int = 5) -> str:
    """
    Navigate to a URL, scroll down to trigger lazy-loaded content, return HTML.

    Args:
        url:          Target URL.
        scroll_count: Number of times to scroll to the bottom.

    Returns:
        Full page HTML after scrolling.
    """
    playwright, browser, context = await _get_browser_context()
    try:
        page = await context.new_page()
        await _goto_with_retry(page, url)

        for _ in range(scroll_count):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(SCROLL_PAUSE)

        html = await page.content()
        return html
    finally:
        await context.close()
        await browser.close()
        await playwright.stop()


async def scrape_text(url: str) -> str:
    """Return visible text content from a URL (no HTML tags)."""
    from bs4 import BeautifulSoup
    html = await scrape_html(url)
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style tags
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


class PlaywrightScraper:
    """
    Stateful scraper that reuses a single browser session across multiple
    pages for efficiency (avoids browser launch overhead per request).

    Supports cookie/session persistence via Playwright storage_state.
    """

    def __init__(self, persist_session: bool = True):
        self._playwright = None
        self._browser = None
        self._context = None
        self._persist_session = persist_session

    async def start(self):
        """Launch browser and create a context, loading saved session if available."""
        from playwright.async_api import async_playwright
        from ..config import SESSION_STATE_PATH, SESSION_DIR

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        # Load saved session (cookies, localStorage) if available
        context_kwargs = {
            "user_agent": random.choice(USER_AGENTS),
            "viewport": {"width": 1280, "height": 800},
        }
        if self._persist_session and Path(SESSION_STATE_PATH).exists():
            try:
                context_kwargs["storage_state"] = SESSION_STATE_PATH
            except Exception:
                pass  # ignore corrupt session files

        self._context = await self._browser.new_context(**context_kwargs)
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    async def stop(self):
        """Save session state and close browser."""
        from ..config import SESSION_STATE_PATH, SESSION_DIR

        if self._context and self._persist_session:
            try:
                Path(SESSION_DIR).mkdir(parents=True, exist_ok=True)
                await self._context.storage_state(path=SESSION_STATE_PATH)
            except Exception:
                pass  # best effort — don't crash on save failure
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_page(self):
        """Open a new tab in the shared context."""
        if self._context is None:
            await self.start()
        return await self._context.new_page()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.stop()
