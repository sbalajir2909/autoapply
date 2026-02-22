"""
AutoApply Main Pipeline

Orchestrates all four modules:
    1. Job Discovery
    2. Deduplication & Storage
    3. Resume Tailoring
    4. Form Filling & Submission

Usage:
    # Single run
    python -m autoapply.pipeline

    # Dry run (fills forms, no submission)
    python -m autoapply.pipeline --dry-run

    # Start nightly scheduler
    python -m autoapply.pipeline --schedule
"""

import argparse
import asyncio
import random
import sys
from pathlib import Path
from typing import List

# ---------------------------------------------------------------------------
# Internal imports
# ---------------------------------------------------------------------------
from .config import (
    CANDIDATE_PROFILE,
    SEARCH_QUERIES,
    JOB_BOARDS,
    MAX_APPS_PER_RUN,
    APPLY_DELAY_MIN,
    APPLY_DELAY_MAX,
    PIPELINE_CRON_HOUR,
    PIPELINE_CRON_MINUTE,
    AUTO_SUBMIT_ATS,
)
from .storage.database import init_db, insert_job, update_status, get_all_stats
from .storage.dedup import generate_job_uuid, generate_sig_hash, is_duplicate, extract_post_id_from_url
from .storage.rag_store import RagStore
from .discovery.job_boards import run_discovery, SCRAPERS
from .discovery.base import JobListing
from .discovery.playwright_scraper import PlaywrightScraper
from .resume.jd_analyzer import analyze_jd
from .resume.rag_tailor import tailor_resume_for_job
from .apply.classifier import classify_page
from .apply.navigator import navigate_to_apply_page
from .apply.submitter import decide_and_submit
from .reports.daily_report import print_daily_report


async def main_pipeline(dry_run: bool = False, max_apps: int = MAX_APPS_PER_RUN):
    """
    Full AutoApply pipeline run.

    Phases:
        1. Discover jobs across configured boards
        2. Deduplicate (UUID + sig hash + semantic)
        3. Store new jobs
        4. For each new job: tailor resume + apply
        5. Print daily report
    """
    print("\n" + "=" * 60)
    print("AUTOAPPLY PIPELINE STARTING")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print("=" * 60)

    # Initialize storage
    init_db()
    rag = RagStore()

    # ---------------------------------------------------------------------------
    # Phase 1: Job Discovery
    # ---------------------------------------------------------------------------
    print("\n[Phase 1] Discovering jobs...")
    raw_listings = await run_discovery(
        queries=SEARCH_QUERIES,
        boards=JOB_BOARDS,
        max_per_query=10,
    )
    print(f"  Raw listings found: {len(raw_listings)}")

    # Phase 1b: Fetch full JD for listings missing it (card-scraped results)
    missing_jd = [l for l in raw_listings if not l.job_description]
    if missing_jd:
        print(f"  Fetching JD for {len(missing_jd)} listings missing descriptions...")
        for listing in missing_jd:
            ats = listing.ats_platform or "unknown"
            scraper_cls = SCRAPERS.get(ats)
            if scraper_cls:
                try:
                    listing.job_description = await scraper_cls().fetch_job_description(listing.url)
                except Exception as e:
                    print(f"    [JD fetch failed] {listing.url}: {e}")

    # ---------------------------------------------------------------------------
    # Phase 2: Deduplication & Storage
    # ---------------------------------------------------------------------------
    print("\n[Phase 2] Deduplicating and storing...")
    new_listings: List[JobListing] = []

    from .storage.database import get_connection
    conn = get_connection()

    for listing in raw_listings:
        post_id = extract_post_id_from_url(listing.url)
        uuid = generate_job_uuid(listing.company, listing.url, post_id)
        sig = generate_sig_hash(listing.job_description)

        # UUID/sig hash dedup
        if is_duplicate(uuid, sig, conn):
            continue

        # Semantic dedup (ChromaDB)
        if listing.job_description and rag.check_semantic_duplicate(listing.job_description):
            print(f"  [Skip] Semantic duplicate: {listing.company} — {listing.title}")
            continue

        # Insert into DB
        inserted = insert_job(
            uuid=uuid,
            company_name=listing.company,
            job_url=listing.url,
            sig_hash=sig,
            job_title=listing.title,
            job_description=listing.job_description,
            job_post_id=post_id,
            ats_platform=listing.ats_platform,
        )
        if inserted:
            new_listings.append(listing)
            # Add to RAG store for future semantic dedup
            if listing.job_description:
                rag.add_job(
                    jd_text=listing.job_description,
                    metadata={
                        "company": listing.company,
                        "title": listing.title,
                        "url": listing.url,
                    },
                    job_uuid=uuid,
                )

    conn.close()
    print(f"  New unique jobs: {len(new_listings)}")

    if not new_listings:
        print("  No new jobs to process.")
        print_daily_report()
        return

    # Limit to max_apps per run
    jobs_to_apply = new_listings[:max_apps]
    print(f"  Processing up to {len(jobs_to_apply)} applications")

    # ---------------------------------------------------------------------------
    # Phase 3 + 4: Resume tailoring + Application (per job)
    # ---------------------------------------------------------------------------
    print("\n[Phase 3+4] Tailoring resumes and applying...")

    applied_count = 0
    queued_count = 0
    error_count = 0

    async with PlaywrightScraper() as scraper:
        for listing in jobs_to_apply:
            post_id = extract_post_id_from_url(listing.url)
            job_uuid = generate_job_uuid(listing.company, listing.url, post_id)

            print(f"\n  → {listing.company} | {listing.title or 'Position'}")
            print(f"    URL: {listing.url}")

            try:
                # Step 3: Tailor resume
                print("    Tailoring resume...")
                jd_text = listing.job_description or ""
                if not jd_text:
                    print("    [Skip] No JD text available")
                    update_status(job_uuid, "manual_review_needed", notes="No JD text")
                    continue

                tex_path, pdf_path, tailor_result = await tailor_resume_for_job(
                    jd_text=jd_text,
                    job_uuid=job_uuid,
                )

                if not pdf_path:
                    print("    [Warn] PDF compilation failed, using .tex path")
                    pdf_path = tex_path

                # Step 4: Navigate to apply page
                print("    Navigating to apply page...")
                page = await scraper.new_page()
                found, page_analysis = await navigate_to_apply_page(page, listing.url)

                if not found or page_analysis is None:
                    update_status(job_uuid, "manual_review_needed", notes="Apply page not found")
                    await page.close()
                    error_count += 1
                    continue

                # Step 4b: Submit or queue
                submitted, new_status = await decide_and_submit(
                    page=page,
                    page_analysis=page_analysis,
                    job_uuid=job_uuid,
                    resume_pdf_path=pdf_path,
                    dry_run=dry_run,
                )

                await page.close()

                if submitted:
                    applied_count += 1
                    print(f"    [Applied] {listing.company}")
                else:
                    queued_count += 1
                    print(f"    [Queued] {listing.company} → dashboard review")

                # Human-like delay between applications
                if not dry_run:
                    delay = random.uniform(APPLY_DELAY_MIN, APPLY_DELAY_MAX)
                    print(f"    Waiting {delay:.0f}s before next application...")
                    await asyncio.sleep(delay)

            except Exception as e:
                print(f"    [Error] {listing.company}: {e}")
                update_status(job_uuid, "error", notes=str(e))
                error_count += 1

    # ---------------------------------------------------------------------------
    # Phase 5: Report
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print(f"RUN COMPLETE — Applied: {applied_count}, Queued: {queued_count}, Errors: {error_count}")
    print("=" * 60)
    print_daily_report()


def run_scheduler():
    """Start APScheduler to run the pipeline nightly."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        print("APScheduler not installed. Run: pip install apscheduler")
        sys.exit(1)

    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: asyncio.run(main_pipeline()),
        trigger="cron",
        hour=PIPELINE_CRON_HOUR,
        minute=PIPELINE_CRON_MINUTE,
    )
    print(f"Scheduler started. Pipeline will run daily at {PIPELINE_CRON_HOUR:02d}:{PIPELINE_CRON_MINUTE:02d}.")
    print("Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("Scheduler stopped.")


def parse_args():
    parser = argparse.ArgumentParser(description="AutoApply pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill forms but do not submit or queue",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run as a nightly scheduled job",
    )
    parser.add_argument(
        "--max-apps",
        type=int,
        default=MAX_APPS_PER_RUN,
        help=f"Max applications per run (default: {MAX_APPS_PER_RUN})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.schedule:
        run_scheduler()
    else:
        asyncio.run(main_pipeline(dry_run=args.dry_run, max_apps=args.max_apps))
