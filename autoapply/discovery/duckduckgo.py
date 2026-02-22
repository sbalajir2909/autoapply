"""
DuckDuckGo-based job discovery.

Sends search queries to DuckDuckGo HTML interface (no API key required)
and extracts job listing URLs that match known ATS patterns.
"""

import re
import asyncio
from typing import List
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .base import JobListing
from .playwright_scraper import scrape_html
from .dedup_utils import _detect_ats
from ..config import SEARCH_QUERIES


# URL patterns that indicate a job posting on a supported ATS
JOB_URL_PATTERNS = [
    r"greenhouse\.io/",
    r"lever\.co/",
    r"linkedin\.com/jobs/",
    r"indeed\.com/viewjob",
    r"workday\.com/",
    r"myworkdayjobs\.com/",
    r"icims\.com/",
    r"taleo\.net/",
    r"jobs\.lever\.co/",
    r"boards\.greenhouse\.io/",
]

_JOB_PATTERN_RE = re.compile("|".join(JOB_URL_PATTERNS), re.IGNORECASE)


def _extract_urls_from_ddg_html(html: str) -> List[str]:
    """Parse DuckDuckGo result HTML and extract result URLs."""
    soup = BeautifulSoup(html, "html.parser")
    urls = []

    # DuckDuckGo wraps each result in an <article> with a data-url attribute
    for article in soup.find_all("article"):
        url = article.get("data-url", "")
        if url and _JOB_PATTERN_RE.search(url):
            urls.append(url)

    # Fallback: look for <a> tags with matching hrefs
    if not urls:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and _JOB_PATTERN_RE.search(href):
                urls.append(href)

    return list(dict.fromkeys(urls))  # deduplicate while preserving order


class DuckDuckGoDiscovery:
    """Discover job URLs via DuckDuckGo searches."""

    DDG_URL = "https://html.duckduckgo.com/html/?q={query}"

    def __init__(self, queries: List[str] = None):
        self.queries = queries or SEARCH_QUERIES

    async def discover(self, max_per_query: int = 10) -> List[JobListing]:
        """
        Run all configured queries and return unique job URLs.

        Args:
            max_per_query: Maximum number of URLs to take per query.

        Returns:
            List of JobListing objects (with url and ats_platform set;
            job_description is empty and should be fetched separately).
        """
        seen_urls: set = set()
        listings: List[JobListing] = []

        for query in self.queries:
            try:
                url = self.DDG_URL.format(query=quote_plus(query))
                html = await scrape_html(url, wait_for="domcontentloaded")
                urls = _extract_urls_from_ddg_html(html)[:max_per_query]

                for job_url in urls:
                    if job_url not in seen_urls:
                        seen_urls.add(job_url)
                        listings.append(JobListing(
                            title="",        # will be filled after JD fetch
                            company="",
                            url=job_url,
                            ats_platform=_detect_ats(job_url),
                            source="duckduckgo",
                        ))

                # Polite delay between searches
                await asyncio.sleep(2.0)

            except Exception as e:
                print(f"[DuckDuckGo] Query failed: {query!r} — {e}")
                continue

        return listings
