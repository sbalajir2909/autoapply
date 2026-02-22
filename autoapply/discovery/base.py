"""Abstract base class for job scrapers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse


@dataclass
class JobListing:
    """Normalized job posting returned by any scraper."""
    title: str
    company: str
    url: str
    job_description: str = ""
    job_post_id: str = ""
    ats_platform: str = ""
    location: str = ""
    date_posted: str = ""
    source: str = ""  # which scraper found it

    def is_valid(self) -> bool:
        """Check that this listing has a usable URL and company name."""
        if not self.company or not self.company.strip():
            return False
        parsed = urlparse(self.url)
        return bool(parsed.scheme in ("http", "https") and parsed.netloc)


class BaseJobScraper(ABC):
    """Abstract scraper interface."""

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> List[JobListing]:
        """Search for jobs matching the query and return listings."""

    @abstractmethod
    async def fetch_job_description(self, url: str) -> str:
        """Fetch the full job description text from a job posting URL."""
