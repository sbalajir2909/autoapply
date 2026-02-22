"""End-to-end pipeline tests (dry-run, mocked network and AI)."""

import asyncio
import os
import tempfile
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from autoapply.discovery.base import JobListing
from autoapply.apply.classifier import PageAnalysis


SAMPLE_JD = """
We need a Security Engineer with Python, AWS, Docker, and CI/CD experience.
Visa sponsorship available. Entry level / new grad welcome.
"""

SAMPLE_LATEX = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading{SWE}{2023}{Acme}{NYC}
\resumeItem{Built things with Python and AWS}
\end{document}
"""


@pytest.fixture()
def isolated_db(tmp_path):
    """Point the pipeline at a throw-away SQLite DB."""
    import autoapply.config as cfg
    orig = cfg.DB_PATH
    cfg.DB_PATH = str(tmp_path / "test.db")
    from autoapply.storage.database import init_db
    init_db()
    yield cfg.DB_PATH
    cfg.DB_PATH = orig


@pytest.fixture()
def isolated_resume(tmp_path):
    """Write a minimal master resume and point config at it."""
    import autoapply.config as cfg
    resume_path = tmp_path / "master_resume.tex"
    resume_path.write_text(SAMPLE_LATEX, encoding="utf-8")
    orig = cfg.CANDIDATE_PROFILE["master_resume_path"]
    cfg.CANDIDATE_PROFILE["master_resume_path"] = str(resume_path)
    yield str(resume_path)
    cfg.CANDIDATE_PROFILE["master_resume_path"] = orig


@pytest.fixture()
def isolated_output(tmp_path):
    """Redirect all output dirs to tmp."""
    import autoapply.config as cfg
    for attr in ("RESUME_OUTPUT_DIR", "SCREENSHOT_DIR"):
        setattr(cfg, attr, str(tmp_path / attr))
    import autoapply.resume.rag_tailor as rt
    rt.RESUME_OUTPUT_DIR = cfg.RESUME_OUTPUT_DIR
    yield tmp_path


# ────────────────────────────────────────────────────────────────────────────
# Full pipeline dry-run
# ────────────────────────────────────────────────────────────────────────────

class TestPipelineDryRun:
    """Mock every external call; verify data flows correctly end-to-end."""

    def _make_listing(self, suffix="1"):
        return JobListing(
            title="Security Engineer",
            company=f"Company{suffix}",
            url=f"https://boards.greenhouse.io/co{suffix}/jobs/{suffix}",
            job_description=SAMPLE_JD + f"\nPosting ID: {suffix}",
            job_post_id=suffix,
            ats_platform="greenhouse",
            source="duckduckgo",
        )

    @pytest.mark.asyncio
    async def test_dry_run_no_db_applied_status(
        self, isolated_db, isolated_resume, isolated_output
    ):
        from autoapply.pipeline import main_pipeline
        from autoapply.storage.database import get_all_stats

        listings = [self._make_listing("1"), self._make_listing("2")]

        mock_tailor_result = MagicMock()
        mock_tailor_result.tailored_latex = SAMPLE_LATEX
        mock_tailor_result.keyword_coverage = 80.0

        with (
            patch("autoapply.pipeline.run_discovery",
                  new=AsyncMock(return_value=listings)),
            patch("autoapply.pipeline.RagStore") as MockRag,
            patch("autoapply.pipeline.tailor_resume_for_job",
                  new=AsyncMock(return_value=("/tmp/r.tex", "/tmp/r.pdf", mock_tailor_result))),
            patch("autoapply.pipeline.PlaywrightScraper") as MockScraper,
            patch("autoapply.pipeline.navigate_to_apply_page",
                  new=AsyncMock(return_value=(
                      True,
                      PageAnalysis(is_apply_page=True, ats_platform="greenhouse", form_fields=[])
                  ))),
            patch("autoapply.pipeline.decide_and_submit",
                  new=AsyncMock(return_value=(False, "dry_run"))),
        ):
            MockRag.return_value.check_semantic_duplicate.return_value = False
            MockRag.return_value.add_job.return_value = None

            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.new_page = AsyncMock(return_value=AsyncMock())
            MockScraper.return_value = mock_ctx

            await main_pipeline(dry_run=True, max_apps=10)

        stats = get_all_stats()
        # dry_run → no status='applied'
        assert stats.get("applied", 0) == 0
        # both jobs should be in DB as discovered
        assert stats.get("total", 0) >= 2

    @pytest.mark.asyncio
    async def test_pipeline_skips_semantic_duplicates(
        self, isolated_db, isolated_resume, isolated_output
    ):
        from autoapply.pipeline import main_pipeline
        from autoapply.storage.database import get_all_stats

        listings = [self._make_listing("A"), self._make_listing("B")]

        with (
            patch("autoapply.pipeline.run_discovery",
                  new=AsyncMock(return_value=listings)),
            patch("autoapply.pipeline.RagStore") as MockRag,
            patch("autoapply.pipeline.tailor_resume_for_job",
                  new=AsyncMock(return_value=("/tmp/r.tex", "/tmp/r.pdf", MagicMock()))),
            patch("autoapply.pipeline.PlaywrightScraper") as MockScraper,
            patch("autoapply.pipeline.navigate_to_apply_page",
                  new=AsyncMock(return_value=(False, None))),
            patch("autoapply.pipeline.decide_and_submit",
                  new=AsyncMock(return_value=(False, "dry_run"))),
        ):
            # Both listings are "semantic duplicates"
            MockRag.return_value.check_semantic_duplicate.return_value = True
            MockRag.return_value.add_job.return_value = None

            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            MockScraper.return_value = mock_ctx

            await main_pipeline(dry_run=True, max_apps=10)

        stats = get_all_stats()
        # Semantic duplicates skipped → nothing inserted
        assert stats.get("total", 0) == 0

    @pytest.mark.asyncio
    async def test_pipeline_handles_empty_discovery(
        self, isolated_db, isolated_resume, isolated_output
    ):
        from autoapply.pipeline import main_pipeline
        from autoapply.storage.database import get_all_stats

        with (
            patch("autoapply.pipeline.run_discovery",
                  new=AsyncMock(return_value=[])),
            patch("autoapply.pipeline.RagStore") as MockRag,
        ):
            MockRag.return_value.check_semantic_duplicate.return_value = False
            await main_pipeline(dry_run=True, max_apps=10)

        assert get_all_stats().get("total", 0) == 0

    @pytest.mark.asyncio
    async def test_pipeline_skips_job_with_no_jd(
        self, isolated_db, isolated_resume, isolated_output
    ):
        from autoapply.pipeline import main_pipeline
        from autoapply.storage.database import get_all_stats

        listing_no_jd = JobListing(
            title="Eng", company="NoJD Corp",
            url="https://boards.greenhouse.io/nojd/jobs/1",
            job_description="",   # ← empty
            job_post_id="1", ats_platform="greenhouse",
        )

        with (
            patch("autoapply.pipeline.run_discovery",
                  new=AsyncMock(return_value=[listing_no_jd])),
            patch("autoapply.pipeline.RagStore") as MockRag,
            patch("autoapply.pipeline.tailor_resume_for_job",
                  new=AsyncMock(return_value=("/tmp/r.tex", "/tmp/r.pdf", MagicMock()))),
            patch("autoapply.pipeline.PlaywrightScraper") as MockScraper,
            patch("autoapply.pipeline.navigate_to_apply_page",
                  new=AsyncMock(return_value=(False, None))),
            patch("autoapply.pipeline.decide_and_submit",
                  new=AsyncMock(return_value=(False, "dry_run"))),
        ):
            MockRag.return_value.check_semantic_duplicate.return_value = False
            MockRag.return_value.add_job.return_value = None

            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.new_page = AsyncMock(return_value=AsyncMock())
            MockScraper.return_value = mock_ctx

            await main_pipeline(dry_run=True, max_apps=10)

        # Job inserted but marked manual_review_needed, not applied
        stats = get_all_stats()
        assert stats.get("applied", 0) == 0

    @pytest.mark.asyncio
    async def test_pipeline_handles_tailor_exception_gracefully(
        self, isolated_db, isolated_resume, isolated_output
    ):
        from autoapply.pipeline import main_pipeline
        from autoapply.storage.database import get_all_stats

        listing = self._make_listing("err")

        with (
            patch("autoapply.pipeline.run_discovery",
                  new=AsyncMock(return_value=[listing])),
            patch("autoapply.pipeline.RagStore") as MockRag,
            patch("autoapply.pipeline.tailor_resume_for_job",
                  new=AsyncMock(side_effect=RuntimeError("API exploded"))),
            patch("autoapply.pipeline.PlaywrightScraper") as MockScraper,
        ):
            MockRag.return_value.check_semantic_duplicate.return_value = False
            MockRag.return_value.add_job.return_value = None

            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            MockScraper.return_value = mock_ctx

            # Should NOT raise — pipeline must be resilient
            await main_pipeline(dry_run=True, max_apps=10)

        stats = get_all_stats()
        assert stats.get("error", 0) >= 1

    @pytest.mark.asyncio
    async def test_pipeline_max_apps_respected(
        self, isolated_db, isolated_resume, isolated_output
    ):
        from autoapply.pipeline import main_pipeline
        from autoapply.storage.database import get_all_stats

        listings = [self._make_listing(str(i)) for i in range(20)]
        applied_calls = []

        async def fake_decide(*args, **kwargs):
            applied_calls.append(1)
            return (False, "dry_run")

        with (
            patch("autoapply.pipeline.run_discovery",
                  new=AsyncMock(return_value=listings)),
            patch("autoapply.pipeline.RagStore") as MockRag,
            patch("autoapply.pipeline.tailor_resume_for_job",
                  new=AsyncMock(return_value=("/tmp/r.tex", "/tmp/r.pdf", MagicMock()))),
            patch("autoapply.pipeline.PlaywrightScraper") as MockScraper,
            patch("autoapply.pipeline.navigate_to_apply_page",
                  new=AsyncMock(return_value=(
                      True,
                      PageAnalysis(is_apply_page=True, ats_platform="greenhouse", form_fields=[])
                  ))),
            patch("autoapply.pipeline.decide_and_submit", new=AsyncMock(side_effect=fake_decide)),
        ):
            MockRag.return_value.check_semantic_duplicate.return_value = False
            MockRag.return_value.add_job.return_value = None

            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.new_page = AsyncMock(return_value=AsyncMock())
            MockScraper.return_value = mock_ctx

            await main_pipeline(dry_run=True, max_apps=5)

        assert len(applied_calls) <= 5


# ────────────────────────────────────────────────────────────────────────────
# Reports
# ────────────────────────────────────────────────────────────────────────────

class TestDailyReport:
    def test_report_is_string(self, isolated_db):
        from autoapply.reports.daily_report import generate_daily_report
        report = generate_daily_report()
        assert isinstance(report, str)
        assert "AUTOAPPLY" in report

    def test_report_shows_zeros_on_empty_db(self, isolated_db):
        from autoapply.reports.daily_report import generate_daily_report
        report = generate_daily_report()
        assert "0" in report

    def test_report_reflects_applied(self, isolated_db):
        from autoapply.storage.database import insert_job, update_status
        from autoapply.reports.daily_report import generate_daily_report
        insert_job(uuid="rpt-1", company_name="C", job_url="https://c.com",
                   sig_hash="s-rpt")
        update_status("rpt-1", "applied")
        report = generate_daily_report()
        assert "1" in report
