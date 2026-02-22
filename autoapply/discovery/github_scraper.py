"""
GitHub repo scraper for internship listing tables.

Fetches README.md from curated GitHub repos (SimplifyJobs, vanshb03,
speedyapply) and parses their markdown tables into JobListing objects.
"""

import logging
import re
from typing import List
from urllib.parse import urlparse

import httpx

from .base import BaseJobScraper, JobListing
from .playwright_scraper import scrape_text
from ..config import GITHUB_REPOS
from ..events import emit, EventType

logger = logging.getLogger(__name__)

# Raw GitHub content URL pattern
_RAW_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"

# Branches to try in order
_BRANCHES = ["dev", "main", "master"]


def _parse_repo_url(repo_url: str) -> tuple:
    """Extract (owner, repo) from a GitHub URL."""
    parsed = urlparse(repo_url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""


def _extract_url_from_cell(cell: str) -> str:
    """Extract the first HTTP(S) URL from a markdown table cell."""
    # Match markdown links: [text](url)
    md_match = re.search(r'\[.*?\]\((https?://[^)]+)\)', cell)
    if md_match:
        return md_match.group(1)
    # Match HTML links: <a href="url">
    html_match = re.search(r'href="(https?://[^"]+)"', cell)
    if html_match:
        return html_match.group(1)
    # Match bare URLs
    bare_match = re.search(r'(https?://\S+)', cell)
    if bare_match:
        return bare_match.group(1)
    return ""


def _extract_company_name(cell: str) -> str:
    """Extract company name from cell, stripping HTML/markdown formatting."""
    # Strip HTML tags
    clean = re.sub(r'<[^>]+>', '', cell)
    # Strip markdown bold/links
    clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean)
    clean = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', clean)
    # Strip emoji prefixes
    clean = re.sub(r'^[^\w\s]+\s*', '', clean.strip())
    return clean.strip()


def _is_closed(row_text: str) -> bool:
    """Check if a listing is marked as closed."""
    return "🔒" in row_text


def _parse_table_rows(readme_text: str) -> List[dict]:
    """
    Parse markdown table rows from a README.

    Handles both SimplifyJobs-style (Company | Role | Location | Application | Age)
    and speedyapply-style (Company | Position | Location | Salary | Posting | Age).

    Returns list of dicts with keys: company, title, location, url, date_posted.
    """
    rows = []
    lines = readme_text.split("\n")

    header_line = None
    header_cols = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue

        cells = [c.strip() for c in stripped.split("|")]
        # Remove empty first/last from leading/trailing pipes
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]

        if not cells:
            continue

        # Detect header row
        if header_line is None:
            lower_cells = [c.lower() for c in cells]
            if any(k in " ".join(lower_cells) for k in ("company", "role", "position")):
                header_line = i
                header_cols = lower_cells
                continue

        # Skip separator row (|---|---|)
        if all(re.match(r'^[-:]+$', c) for c in cells):
            continue

        # Skip if no header found yet
        if header_line is None:
            continue

        # Map cells by header position
        row_data = {}
        for j, col_name in enumerate(header_cols):
            if j < len(cells):
                row_data[col_name] = cells[j]

        # Skip closed listings
        raw_row = stripped
        if _is_closed(raw_row):
            continue

        # Extract fields with flexible column name matching
        company_cell = (
            row_data.get("company", "")
        )
        title_cell = (
            row_data.get("role", "")
            or row_data.get("position", "")
        )
        location_cell = (
            row_data.get("location", "")
        )
        link_cell = (
            row_data.get("application", "")
            or row_data.get("application/link", "")
            or row_data.get("posting", "")
        )
        date_cell = (
            row_data.get("date posted", "")
            or row_data.get("age", "")
        )

        company = _extract_company_name(company_cell)
        url = _extract_url_from_cell(link_cell)
        # Also try extracting URL from company cell if link cell is empty
        if not url:
            url = _extract_url_from_cell(company_cell)

        title = re.sub(r'<[^>]+>', '', title_cell).strip()
        location = re.sub(r'<br\s*/?>', ', ', location_cell)
        location = re.sub(r'<[^>]+>', '', location).strip()

        if company and url:
            rows.append({
                "company": company,
                "title": title,
                "location": location,
                "url": url,
                "date_posted": date_cell.strip(),
            })

    return rows


class GitHubRepoScraper(BaseJobScraper):
    """Scrape internship listings from curated GitHub repos."""

    async def search(self, query: str = "", max_results: int = 100) -> List[JobListing]:
        """
        Fetch and parse all 4 GitHub repos for job listings.

        The `query` parameter is ignored since these repos are
        already curated lists. We return all non-closed listings.
        """
        all_listings: List[JobListing] = []

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for repo_url in GITHUB_REPOS:
                owner, repo = _parse_repo_url(repo_url)
                if not owner or not repo:
                    logger.warning(f"Could not parse repo URL: {repo_url}")
                    continue

                readme_text = await self._fetch_readme(client, owner, repo)
                if not readme_text:
                    emit(EventType.WARNING,
                         f"[GitHub] Could not fetch README for {owner}/{repo}",
                         board="github_repos", repo=f"{owner}/{repo}")
                    logger.warning(f"Could not fetch README for {owner}/{repo}")
                    continue

                rows = _parse_table_rows(readme_text)
                emit(EventType.INFO,
                     f"[GitHub] {owner}/{repo}: parsed {len(rows)} listings",
                     board="github_repos", repo=f"{owner}/{repo}", count=len(rows))
                logger.info(f"[GitHub] {owner}/{repo}: {len(rows)} listings")

                for row in rows:
                    listing = JobListing(
                        title=row["title"],
                        company=row["company"],
                        url=row["url"],
                        location=row["location"],
                        date_posted=row["date_posted"],
                        source=f"github:{owner}/{repo}",
                    )
                    all_listings.append(listing)
                    emit(EventType.SEARCH_RESULT,
                         f"[GitHub] {listing.company} — {listing.title}",
                         listing={"title": listing.title, "company": listing.company,
                                  "url": listing.url},
                         board="github_repos")

        # Deduplicate by URL within this scraper
        seen_urls = set()
        unique = []
        for l in all_listings:
            if l.url not in seen_urls:
                seen_urls.add(l.url)
                unique.append(l)

        logger.info(f"[GitHub] Total unique listings: {len(unique)} (from {len(all_listings)} raw)")
        return unique[:max_results]

    async def _fetch_readme(self, client: httpx.AsyncClient, owner: str, repo: str) -> str:
        """Try fetching README.md from dev, main, or master branch."""
        for branch in _BRANCHES:
            url = _RAW_URL.format(owner=owner, repo=repo, branch=branch)
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
            except httpx.TransportError as e:
                logger.debug(f"Transport error fetching {url}: {e}")
                continue
        return ""

    async def fetch_job_description(self, url: str) -> str:
        """Fetch the full JD from the linked application page."""
        try:
            return await scrape_text(url)
        except Exception as e:
            logger.warning(f"[GitHub] JD fetch failed for {url}: {e}")
            return ""
