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
from ..events import emit, EventType


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
        for l in listings:
            emit(EventType.SEARCH_RESULT,
                 f"[Greenhouse] {l.company} — {l.title}",
                 listing={"title": l.title, "company": l.company,
                          "url": l.url, "ats_platform": "greenhouse"},
                 board="greenhouse")
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
            emit(EventType.SEARCH_RESULT,
                 f"[Lever] {l.company} — {l.title}",
                 listing={"title": l.title, "company": l.company,
                          "url": l.url, "ats_platform": "lever"},
                 board="lever")
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
                    listing = JobListing(
                        title=title,
                        company=company,
                        url=link.split("?")[0],  # strip tracking params
                        ats_platform="linkedin",
                        source="linkedin",
                    )
                    listings.append(listing)
                    emit(EventType.SEARCH_RESULT,
                         f"[LinkedIn] {company} — {title}",
                         listing={"title": title, "company": company,
                                  "url": listing.url, "ats_platform": "linkedin"},
                         board="linkedin")
        except Exception as e:
            emit(EventType.WARNING, f"[LinkedIn] search failed: {e}",
                 board="linkedin", query=query, error=str(e))
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
                    listing = JobListing(
                        title=title,
                        company=company,
                        url=link,
                        ats_platform="indeed",
                        source="indeed",
                    )
                    listings.append(listing)
                    emit(EventType.SEARCH_RESULT,
                         f"[Indeed] {company} — {title}",
                         listing={"title": title, "company": company,
                                  "url": link, "ats_platform": "indeed"},
                         board="indeed")
        except Exception as e:
            emit(EventType.WARNING, f"[Indeed] search failed: {e}",
                 board="indeed", query=query, error=str(e))
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

from .github_scraper import GitHubRepoScraper

SCRAPERS = {
    "greenhouse": GreenHouseScraper,
    "lever": LeverScraper,
    "linkedin": LinkedInScraper,
    "indeed": IndeedScraper,
    "github_repos": GitHubRepoScraper,
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

        emit(EventType.SEARCH_BOARD_START,
             f"Searching {board_name.upper()}...",
             board=board_name, query_count=len(queries))

        # GitHub repos scraper ignores queries — only run it once
        query_list = [""] if board_name == "github_repos" else queries

        for query in query_list:
            try:
                emit(EventType.SEARCH_START,
                     f"Searching {board_name}" + (f": \"{query}\"" if query else ""),
                     board=board_name, query=query)
                results = await scraper.search(query, max_results=max_per_query)
                for listing in results:
                    if listing.url not in seen_urls:
                        seen_urls.add(listing.url)
                        all_listings.append(listing)
                emit(EventType.INFO,
                     f"{board_name}" + (f": \"{query}\"" if query else "") +
                     f" → {len(results)} results ({len(all_listings)} total unique)",
                     board=board_name, query=query, results=len(results))
                await asyncio.sleep(1.5)  # polite delay
            except Exception as e:
                emit(EventType.WARNING,
                     f"[{board_name}] query \"{query}\" failed: {e}",
                     board=board_name, query=query, error=str(e))
                print(f"[{board_name}] query {query!r} failed: {e}")

        emit(EventType.SEARCH_BOARD_END,
             f"Done searching {board_name.upper()} — {len(all_listings)} total so far",
             board=board_name, total=len(all_listings))

    return all_listings
