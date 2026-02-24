"""Tests for Module 1: Discovery Engine (base, dedup helpers, board parsers)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from autoapply.discovery.base import JobListing
from autoapply.discovery.dedup_utils import _detect_ats  # we'll add this helper
from autoapply.discovery.duckduckgo import _extract_urls_from_ddg_html, DuckDuckGoDiscovery
from autoapply.discovery.job_boards import (
    GreenHouseScraper, LeverScraper, LinkedInScraper, IndeedScraper,
)


# ────────────────────────────────────────────────────────────────────────────
# JobListing dataclass
# ────────────────────────────────────────────────────────────────────────────

class TestJobListing:
    def test_default_fields(self):
        j = JobListing(title="SWE", company="Acme", url="https://x.com")
        assert j.job_description == ""
        assert j.ats_platform == ""
        assert j.source == ""

    def test_all_fields(self):
        j = JobListing(
            title="Sec Eng", company="Beta", url="https://beta.com",
            job_description="We need Python",
            job_post_id="123",
            ats_platform="greenhouse",
            source="duckduckgo",
        )
        assert j.ats_platform == "greenhouse"
        assert j.job_post_id == "123"


# ────────────────────────────────────────────────────────────────────────────
# DuckDuckGo URL extraction (pure function, no network)
# ────────────────────────────────────────────────────────────────────────────

class TestDuckDuckGoExtraction:
    def _html_with_urls(self, urls):
        articles = "".join(
            f'<article data-url="{u}"></article>' for u in urls
        )
        return f"<html><body>{articles}</body></html>"

    def test_extracts_greenhouse_url(self):
        html = self._html_with_urls([
            "https://boards.greenhouse.io/acme/jobs/123",
            "https://notajob.com",
        ])
        urls = _extract_urls_from_ddg_html(html)
        assert any("greenhouse" in u for u in urls)
        assert all("notajob" not in u for u in urls)

    def test_extracts_lever_url(self):
        html = self._html_with_urls(["https://jobs.lever.co/company/slug"])
        urls = _extract_urls_from_ddg_html(html)
        assert len(urls) == 1

    def test_extracts_linkedin_url(self):
        html = self._html_with_urls(["https://linkedin.com/jobs/view/9999"])
        urls = _extract_urls_from_ddg_html(html)
        assert len(urls) == 1

    def test_deduplicates_urls(self):
        url = "https://boards.greenhouse.io/acme/jobs/1"
        html = self._html_with_urls([url, url, url])
        urls = _extract_urls_from_ddg_html(html)
        assert urls.count(url) == 1

    def test_empty_html_returns_empty(self):
        assert _extract_urls_from_ddg_html("<html></html>") == []

    def test_fallback_anchor_tags(self):
        html = """
        <html><body>
        <a href="https://boards.greenhouse.io/co/jobs/999">Job</a>
        </body></html>
        """
        urls = _extract_urls_from_ddg_html(html)
        assert any("greenhouse" in u for u in urls)


# ────────────────────────────────────────────────────────────────────────────
# ATS detection
# ────────────────────────────────────────────────────────────────────────────

class TestDetectAts:
    def test_greenhouse(self):
        assert _detect_ats("https://boards.greenhouse.io/co/jobs/1") == "greenhouse"

    def test_lever(self):
        assert _detect_ats("https://jobs.lever.co/company/slug") == "lever"

    def test_linkedin(self):
        assert _detect_ats("https://linkedin.com/jobs/view/1") == "linkedin"

    def test_indeed(self):
        assert _detect_ats("https://www.indeed.com/viewjob?jk=abc") == "indeed"

    def test_workday(self):
        assert _detect_ats("https://acme.wd5.myworkdayjobs.com/jobs/1") == "workday"

    def test_unknown(self):
        assert _detect_ats("https://randomcompany.com/careers/apply") == "unknown"


# ────────────────────────────────────────────────────────────────────────────
# DuckDuckGoDiscovery (mocked network)
# ────────────────────────────────────────────────────────────────────────────

class TestDuckDuckGoDiscovery:
    @pytest.mark.asyncio
    async def test_discover_returns_listings(self):
        fake_html = """
        <html><body>
        <article data-url="https://boards.greenhouse.io/co/jobs/1"></article>
        <article data-url="https://jobs.lever.co/co/slug"></article>
        </body></html>
        """
        with patch("autoapply.discovery.duckduckgo.scrape_html",
                   new=AsyncMock(return_value=fake_html)):
            ddg = DuckDuckGoDiscovery(queries=["test query"])
            listings = await ddg.discover(max_per_query=10)
        assert len(listings) == 2
        urls = [l.url for l in listings]
        assert any("greenhouse" in u for u in urls)
        assert any("lever" in u for u in urls)

    @pytest.mark.asyncio
    async def test_discover_deduplicates_across_queries(self):
        fake_html = """
        <html><body>
        <article data-url="https://boards.greenhouse.io/co/jobs/1"></article>
        </body></html>
        """
        with patch("autoapply.discovery.duckduckgo.scrape_html",
                   new=AsyncMock(return_value=fake_html)):
            ddg = DuckDuckGoDiscovery(queries=["q1", "q2"])
            listings = await ddg.discover(max_per_query=10)
        assert len(listings) == 1   # same URL across 2 queries → 1 result

    @pytest.mark.asyncio
    async def test_discover_handles_scrape_failure(self):
        with patch("autoapply.discovery.duckduckgo.scrape_html",
                   new=AsyncMock(side_effect=Exception("network error"))):
            ddg = DuckDuckGoDiscovery(queries=["test"])
            listings = await ddg.discover()
        assert listings == []

    @pytest.mark.asyncio
    async def test_discover_respects_max_per_query(self):
        urls = [f"https://boards.greenhouse.io/co/jobs/{i}" for i in range(20)]
        articles = "".join(f'<article data-url="{u}"></article>' for u in urls)
        fake_html = f"<html><body>{articles}</body></html>"
        with patch("autoapply.discovery.duckduckgo.scrape_html",
                   new=AsyncMock(return_value=fake_html)):
            ddg = DuckDuckGoDiscovery(queries=["test"])
            listings = await ddg.discover(max_per_query=5)
        assert len(listings) <= 5


# ────────────────────────────────────────────────────────────────────────────
# Per-board scrapers – fetch_job_description (mocked)
# ────────────────────────────────────────────────────────────────────────────

GH_HTML = """
<html><body>
<div class='job-post'>
  <h1>Security Engineer</h1>
  <p>We need Python, AWS, and Docker skills.</p>
</div>
</body></html>
"""

LEVER_HTML = """
<html><body>
<div class='posting-content'>
  <h2>Product Security Engineer</h2>
  <p>Strong Python and security fundamentals required.</p>
</div>
</body></html>
"""

LI_HTML = """
<html><body>
<div class='description__text'>
  <p>Looking for a cloud security engineer with AWS and Kubernetes.</p>
</div>
</body></html>
"""

INDEED_HTML = """
<html><body>
<div id='jobDescriptionText'>
  <p>Join us as a security analyst. Python and SQL required.</p>
</div>
</body></html>
"""


class TestJobBoardFetch:
    @pytest.mark.asyncio
    async def test_greenhouse_fetch_extracts_text(self):
        with patch("autoapply.discovery.job_boards.scrape_html",
                   new=AsyncMock(return_value=GH_HTML)):
            scraper = GreenHouseScraper()
            text = await scraper.fetch_job_description("https://boards.greenhouse.io/co/jobs/1")
        assert "Python" in text
        assert "AWS" in text

    @pytest.mark.asyncio
    async def test_lever_fetch_extracts_text(self):
        with patch("autoapply.discovery.job_boards.scrape_html",
                   new=AsyncMock(return_value=LEVER_HTML)):
            scraper = LeverScraper()
            text = await scraper.fetch_job_description("https://jobs.lever.co/co/slug")
        assert "Python" in text

    @pytest.mark.asyncio
    async def test_linkedin_fetch_extracts_text(self):
        with patch("autoapply.discovery.job_boards.scrape_html",
                   new=AsyncMock(return_value=LI_HTML)):
            scraper = LinkedInScraper()
            text = await scraper.fetch_job_description("https://linkedin.com/jobs/view/1")
        assert "AWS" in text or "cloud" in text.lower()

    @pytest.mark.asyncio
    async def test_indeed_fetch_extracts_text(self):
        with patch("autoapply.discovery.job_boards.scrape_html",
                   new=AsyncMock(return_value=INDEED_HTML)):
            scraper = IndeedScraper()
            text = await scraper.fetch_job_description("https://indeed.com/viewjob?jk=abc")
        assert "Python" in text

    @pytest.mark.asyncio
    async def test_fetch_returns_empty_on_error(self):
        with patch("autoapply.discovery.job_boards.scrape_html",
                   new=AsyncMock(side_effect=Exception("timeout"))):
            scraper = GreenHouseScraper()
            text = await scraper.fetch_job_description("https://example.com")
        assert text == ""

    @pytest.mark.asyncio
    async def test_greenhouse_search_delegates_to_ddg(self):
        with patch("autoapply.discovery.job_boards.DuckDuckGoDiscovery") as MockDDG:
            instance = MockDDG.return_value
            instance.discover = AsyncMock(return_value=[
                JobListing(title="T", company="C", url="https://boards.greenhouse.io/co/jobs/1")
            ])
            scraper = GreenHouseScraper()
            results = await scraper.search("security engineer")
        assert len(results) == 1
