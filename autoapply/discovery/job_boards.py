"""
Per-board job scrapers for Greenhouse, Lever, LinkedIn, and Indeed.

Each scraper implements:
    search(query, max_results) → List[JobListing]
    fetch_job_description(url) → str
"""

import asyncio
import re
from typing import List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .base import BaseJobScraper, JobListing
from .playwright_scraper import scrape_html, scroll_and_get_html
from .dedup_utils import _detect_ats
from .duckduckgo import DuckDuckGoDiscovery
from ..config import PAGE_LOAD_TIMEOUT


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

class GreenHouseScraper(BaseJobScraper):
    """Scrape job boards hosted on Greenhouse (boards.greenhouse.io)."""

    SEARCH_URL = "https://boards.greenhouse.io/embed/job_board?for={company}"

    async def search(self, query: str, max_results: int = 10) -> List[JobListing]:
        """
        Greenhouse doesn't have a global search; use DuckDuckGo to find
        Greenhouse postings matching the query, then fetch each one.
        """
        ddg = DuckDuckGoDiscovery(queries=[f"site:boards.greenhouse.io {query}"])
        listings = await ddg.discover(max_per_query=max_results)
        return listings

    async def fetch_job_description(self, url: str) -> str:
        """Fetch JD text from a Greenhouse job posting."""
        try:
            html = await scrape_html(url)
            soup = BeautifulSoup(html, "html.parser")
            # Greenhouse wraps JD in <div class="job-post">
            container = (
                soup.find("div", class_="job-post")
                or soup.find("div", id="content")
                or soup.find("article")
                or soup.body
            )
            if container:
                for tag in container(["script", "style", "nav", "header", "footer"]):
                    tag.decompose()
                return container.get_text(separator=" ", strip=True)
        except Exception as e:
            print(f"[Greenhouse] fetch_job_description failed for {url}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

class LeverScraper(BaseJobScraper):
    """Scrape job postings on Lever (jobs.lever.co)."""

    async def search(self, query: str, max_results: int = 10) -> List[JobListing]:
        ddg = DuckDuckGoDiscovery(queries=[f"site:jobs.lever.co {query}"])
        listings = await ddg.discover(max_per_query=max_results)
        for l in listings:
            l.ats_platform = "lever"
        return listings

    async def fetch_job_description(self, url: str) -> str:
        """Fetch JD from a Lever posting."""
        try:
            html = await scrape_html(url)
            soup = BeautifulSoup(html, "html.parser")
            container = (
                soup.find("div", class_="posting-content")
                or soup.find("div", class_="content")
                or soup.body
            )
            if container:
                for tag in container(["script", "style", "nav"]):
                    tag.decompose()
                return container.get_text(separator=" ", strip=True)
        except Exception as e:
            print(f"[Lever] fetch_job_description failed for {url}: {e}")
        return ""


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------

class LinkedInScraper(BaseJobScraper):
    """Scrape LinkedIn job search results."""

    SEARCH_URL = (
        "https://www.linkedin.com/jobs/search/"
        "?keywords={keywords}&location=United%20States&f_TPR=r86400"
    )

    async def search(self, query: str, max_results: int = 10) -> List[JobListing]:
        """Search LinkedIn jobs (public, no login required for listings)."""
        url = self.SEARCH_URL.format(keywords=quote_plus(query))
        listings: List[JobListing] = []
        try:
            html = await scroll_and_get_html(url, scroll_count=3)
            soup = BeautifulSoup(html, "html.parser")

            # LinkedIn job cards in the public search page
            cards = soup.find_all("div", class_=re.compile(r"job-search-card|base-card"))
            for card in cards[:max_results]:
                title_el = card.find(["h3", "a"], class_=re.compile(r"title|job-title"))
                company_el = card.find(class_=re.compile(r"company|subtitle"))
                link_el = card.find("a", href=True)

                title = title_el.get_text(strip=True) if title_el else ""
                company = company_el.get_text(strip=True) if company_el else ""
                link = link_el["href"] if link_el else ""

                if link and title:
                    # Normalize to absolute URL
                    if not link.startswith("http"):
                        link = "https://www.linkedin.com" + link
                    listings.append(JobListing(
                        title=title,
                        company=company,
                        url=link.split("?")[0],  # strip tracking params
                        ats_platform="linkedin",
                        source="linkedin",
                    ))
        except Exception as e:
            print(f"[LinkedIn] search failed for {query!r}: {e}")

        return listings

    async def fetch_job_description(self, url: str) -> str:
        """Fetch JD from a LinkedIn job posting."""
        try:
            html = await scrape_html(url)
            soup = BeautifulSoup(html, "html.parser")
            container = (
                soup.find("div", class_=re.compile(r"description|job-description"))
                or soup.find("section", class_=re.compile(r"description"))
                or soup.body
            )
            if container:
                for tag in container(["script", "style"]):
                    tag.decompose()
                return container.get_text(separator=" ", strip=True)
        except Exception as e:
            print(f"[LinkedIn] fetch_job_description failed for {url}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Indeed
# ---------------------------------------------------------------------------

class IndeedScraper(BaseJobScraper):
    """Scrape Indeed job search results."""

    SEARCH_URL = "https://www.indeed.com/jobs?q={keywords}&l=United+States&sort=date"

    async def search(self, query: str, max_results: int = 10) -> List[JobListing]:
        url = self.SEARCH_URL.format(keywords=quote_plus(query))
        listings: List[JobListing] = []
        try:
            html = await scroll_and_get_html(url, scroll_count=2)
            soup = BeautifulSoup(html, "html.parser")

            cards = soup.find_all("div", class_=re.compile(r"job_seen_beacon|jobCard"))
            for card in cards[:max_results]:
                title_el = card.find(["h2", "span"], class_=re.compile(r"title|jobTitle"))
                company_el = card.find(class_=re.compile(r"company"))
                link_el = card.find("a", href=re.compile(r"/rc/|/viewjob"))

                title = title_el.get_text(strip=True) if title_el else ""
                company = company_el.get_text(strip=True) if company_el else ""
                link = "https://www.indeed.com" + link_el["href"] if link_el else ""

                if link and title:
                    listings.append(JobListing(
                        title=title,
                        company=company,
                        url=link,
                        ats_platform="indeed",
                        source="indeed",
                    ))
        except Exception as e:
            print(f"[Indeed] search failed for {query!r}: {e}")

        return listings

    async def fetch_job_description(self, url: str) -> str:
        """Fetch JD from an Indeed job posting."""
        try:
            html = await scrape_html(url)
            soup = BeautifulSoup(html, "html.parser")
            container = (
                soup.find("div", id="jobDescriptionText")
                or soup.find("div", class_=re.compile(r"jobsearch-jobDescriptionText"))
                or soup.body
            )
            if container:
                for tag in container(["script", "style"]):
                    tag.decompose()
                return container.get_text(separator=" ", strip=True)
        except Exception as e:
            print(f"[Indeed] fetch_job_description failed for {url}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Aggregated discovery runner
# ---------------------------------------------------------------------------

SCRAPERS = {
    "greenhouse": GreenHouseScraper,
    "lever": LeverScraper,
    "linkedin": LinkedInScraper,
    "indeed": IndeedScraper,
}


async def run_discovery(
    queries: List[str],
    boards: Optional[List[str]] = None,
    max_per_query: int = 10,
) -> List[JobListing]:
    """
    Run queries across all specified job boards and return combined results.

    Args:
        queries:       List of search query strings.
        boards:        Subset of SCRAPERS keys to use. Defaults to all.
        max_per_query: Max results per query per board.

    Returns:
        Deduplicated list of JobListing objects.
    """
    boards = boards or list(SCRAPERS.keys())
    seen_urls: set = set()
    all_listings: List[JobListing] = []

    for board_name in boards:
        scraper_cls = SCRAPERS.get(board_name)
        if not scraper_cls:
            continue
        scraper = scraper_cls()

        for query in queries:
            try:
                results = await scraper.search(query, max_results=max_per_query)
                for listing in results:
                    if listing.url not in seen_urls:
                        seen_urls.add(listing.url)
                        all_listings.append(listing)
                await asyncio.sleep(1.5)  # polite delay
            except Exception as e:
                print(f"[{board_name}] query {query!r} failed: {e}")

    return all_listings
