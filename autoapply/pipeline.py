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
import json
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
from .events import emit, EventType, bus
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


async def main_pipeline(dry_run: bool = False, max_apps: int = MAX_APPS_PER_RUN, resume: bool = False, auto_mode: bool = False):
    """
    Full AutoApply pipeline run.

    Phases:
        1. Discover jobs across configured boards
        2. Deduplicate (UUID + sig hash + semantic)
        3. Store new jobs
        4. For each new job: tailor resume + apply
        5. Print daily report

    If resume=True, skip phases 1+2 and re-process jobs with
    status 'queued_for_apply' or 'in_progress' from the database.
    """
    from .logging_config import setup_logging
    from .config import validate_config, PIPELINE_TIMEOUT
    setup_logging()

    # Validate config before doing anything
    validate_config()

    bus.running = True
    emit(EventType.PIPELINE_START,
         f"Pipeline starting — Mode: {'DRY RUN' if dry_run else 'LIVE'}, Max apps: {max_apps}",
         dry_run=dry_run, max_apps=max_apps)

    print("\n" + "=" * 60)
    print("AUTOAPPLY PIPELINE STARTING")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print("=" * 60)

    # Initialize storage
    init_db()
    rag = RagStore()

    # ---------------------------------------------------------------------------
    # Resume mode: skip discovery + dedup, load incomplete jobs from DB
    # ---------------------------------------------------------------------------
    if resume:
        emit(EventType.INFO, "Resume mode — skipping discovery, processing incomplete jobs from DB")
        print("\n[Resume mode] Skipping discovery, loading incomplete jobs...")
        from .storage.database import get_pending_jobs
        incomplete = get_pending_jobs("queued_for_apply") + get_pending_jobs("in_progress")
        if not incomplete:
            emit(EventType.INFO, "No incomplete jobs found. Pipeline ending.")
            print("  No incomplete jobs found.")
            bus.running = False
            emit(EventType.PIPELINE_END, "Pipeline complete — no incomplete jobs")
            return
        jobs_to_apply = []
        for row in incomplete[:max_apps]:
            jobs_to_apply.append(JobListing(
                title=row.get("job_title", ""),
                company=row.get("company_name", ""),
                url=row.get("job_url", ""),
                job_description=row.get("job_description", ""),
                ats_platform=row.get("ats_platform", ""),
            ))
        emit(EventType.INFO, f"Resuming {len(jobs_to_apply)} incomplete jobs")
        bus.update_stats(total_jobs_to_process=len(jobs_to_apply))
    else:
        # -------------------------------------------------------------------
        # Phase 1: Job Discovery
        # -------------------------------------------------------------------
        emit(EventType.PHASE_START, "Phase 1: Discovering jobs across all boards...",
             phase="Phase 1: Job Discovery")
        bus.update_stats(current_phase="Phase 1: Job Discovery")
        print("\n[Phase 1] Discovering jobs...")

        raw_listings = await run_discovery(
            queries=SEARCH_QUERIES,
            boards=JOB_BOARDS,
            max_per_query=10,
        )

        emit(EventType.SEARCH_END,
             f"Discovery complete — {len(raw_listings)} raw listings found",
             count=len(raw_listings))
        bus.update_stats(total_discovered=len(raw_listings))
        print(f"  Raw listings found: {len(raw_listings)}")

        # Phase 1b: Fetch full JD for listings missing it
        missing_jd = [l for l in raw_listings if not l.job_description]
        if missing_jd:
            emit(EventType.JD_FETCH_START,
                 f"Fetching JD for {len(missing_jd)} listings missing descriptions...",
                 count=len(missing_jd))
            print(f"  Fetching JD for {len(missing_jd)} listings missing descriptions...")
            for listing in missing_jd:
                ats = listing.ats_platform or "unknown"
                scraper_cls = SCRAPERS.get(ats)
                if scraper_cls:
                    try:
                        listing.job_description = await scraper_cls().fetch_job_description(listing.url)
                        emit(EventType.JD_FETCH_END,
                             f"Fetched JD for {listing.company} ({len(listing.job_description)} chars)",
                             company=listing.company, url=listing.url,
                             jd_length=len(listing.job_description))
                    except Exception as e:
                        emit(EventType.WARNING,
                             f"JD fetch failed for {listing.url}: {e}",
                             url=listing.url, error=str(e))
                        print(f"    [JD fetch failed] {listing.url}: {e}")

        emit(EventType.PHASE_END, "Phase 1 complete", phase="Phase 1: Job Discovery")

        # -------------------------------------------------------------------
        # Phase 2: Deduplication & Storage
        # -------------------------------------------------------------------
        emit(EventType.PHASE_START, "Phase 2: Deduplicating and storing...",
             phase="Phase 2: Deduplication & Storage")
        bus.update_stats(current_phase="Phase 2: Deduplication & Storage")
        print("\n[Phase 2] Deduplicating and storing...")

        new_listings: List[JobListing] = []

        for listing in raw_listings:
            # Validate listing before processing
            if not listing.is_valid():
                emit(EventType.WARNING,
                     f"Skipping invalid listing: {listing.company!r} — {listing.url!r}",
                     company=listing.company, url=listing.url, reason="validation_failed")
                continue

            post_id = extract_post_id_from_url(listing.url)
            uuid = generate_job_uuid(listing.company, listing.url, post_id)
            sig = generate_sig_hash(listing.job_description)

            emit(EventType.DEDUP_CHECK,
                 f"Checking: {listing.company} — {listing.title or 'Position'}",
                 company=listing.company, title=listing.title,
                 url=listing.url, uuid=uuid[:12])

            # UUID/sig hash dedup
            if is_duplicate(uuid, sig):
                emit(EventType.DEDUP_DUPLICATE,
                     f"Duplicate (UUID/sig): {listing.company} — {listing.title}",
                     company=listing.company, reason="uuid_or_sig")
                bus.update_stats(total_skipped=bus.stats["total_skipped"] + 1)
                continue

            # Semantic dedup (ChromaDB)
            if listing.job_description and rag.check_semantic_duplicate(listing.job_description):
                emit(EventType.DEDUP_SEMANTIC,
                     f"Semantic duplicate: {listing.company} — {listing.title}",
                     company=listing.company, title=listing.title)
                bus.update_stats(total_skipped=bus.stats["total_skipped"] + 1)
                print(f"  [Skip] Semantic duplicate: {listing.company} — {listing.title}")
                continue

            # Insert into DB with status queued_for_apply
            inserted = insert_job(
                uuid=uuid,
                company_name=listing.company,
                job_url=listing.url,
                sig_hash=sig,
                job_title=listing.title,
                job_description=listing.job_description,
                job_post_id=post_id,
                ats_platform=listing.ats_platform,
                job_board=listing.source,
                location=listing.location,
            )
            if inserted:
                update_status(uuid, "queued_for_apply")
                new_listings.append(listing)
                emit(EventType.DEDUP_NEW,
                     f"New job stored: {listing.company} — {listing.title}",
                     company=listing.company, title=listing.title,
                     url=listing.url, ats=listing.ats_platform,
                     job_uuid=uuid)
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

        bus.update_stats(total_new=len(new_listings))
        emit(EventType.PHASE_END,
             f"Phase 2 complete — {len(new_listings)} new unique jobs",
             phase="Phase 2: Deduplication & Storage", new_count=len(new_listings))
        print(f"  New unique jobs: {len(new_listings)}")

        if not new_listings:
            emit(EventType.INFO, "No new jobs to process. Pipeline ending.")
            print("  No new jobs to process.")
            print_daily_report()
            bus.running = False
            emit(EventType.PIPELINE_END, "Pipeline complete — no new jobs to process")
            return

        jobs_to_apply = new_listings[:max_apps]
        bus.update_stats(total_jobs_to_process=len(jobs_to_apply))

    emit(EventType.INFO, f"Processing {len(jobs_to_apply)} applications (limit: {max_apps})")
    print(f"  Processing up to {len(jobs_to_apply)} applications")

    # ---------------------------------------------------------------------------
    # Phase 3 + 4: Resume tailoring + Application (per job)
    # ---------------------------------------------------------------------------
    emit(EventType.PHASE_START, "Phase 3+4: Tailoring resumes and applying...",
         phase="Phase 3+4: Tailor & Apply")
    bus.update_stats(current_phase="Phase 3+4: Tailor & Apply")
    print("\n[Phase 3+4] Tailoring resumes and applying...")

    applied_count = 0
    queued_count = 0
    error_count = 0

    async with PlaywrightScraper() as scraper:
        for i, listing in enumerate(jobs_to_apply):
            post_id = extract_post_id_from_url(listing.url)
            job_uuid = generate_job_uuid(listing.company, listing.url, post_id)

            bus.update_stats(current_job_index=i + 1)
            bus.current_job = {
                "title": listing.title or "Position",
                "company": listing.company,
                "url": listing.url,
                "ats_platform": listing.ats_platform or "unknown",
                "uuid": job_uuid,
                "index": i + 1,
                "total": len(jobs_to_apply),
            }

            emit(EventType.JOB_START,
                 f"[{i+1}/{len(jobs_to_apply)}] {listing.company} — {listing.title or 'Position'}",
                 company=listing.company, title=listing.title,
                 url=listing.url, job_uuid=job_uuid,
                 index=i+1, total=len(jobs_to_apply))

            print(f"\n  → {listing.company} | {listing.title or 'Position'}")
            print(f"    URL: {listing.url}")

            # Mark job as in_progress for resumability
            update_status(job_uuid, "in_progress")

            try:
                # Step 3: Tailor resume
                emit(EventType.RESUME_TAILOR_START,
                     f"Tailoring resume for {listing.company}...",
                     company=listing.company, job_uuid=job_uuid)
                print("    Tailoring resume...")
                jd_text = listing.job_description or ""
                if not jd_text:
                    emit(EventType.JOB_SKIP,
                         f"Skipping {listing.company} — no JD text available",
                         company=listing.company, reason="no_jd", job_uuid=job_uuid)
                    print("    [Skip] No JD text available")
                    update_status(job_uuid, "manual_review_needed", notes="No JD text")
                    continue

                tex_path, pdf_path, tailor_result = await tailor_resume_for_job(
                    jd_text=jd_text,
                    job_uuid=job_uuid,
                )

                result_data = {}
                if tailor_result:
                    result_data = {
                        "keyword_coverage": getattr(tailor_result, "keyword_coverage", 0),
                        "matched_keywords": getattr(tailor_result, "matched_keywords", []),
                        "missing_keywords": getattr(tailor_result, "missing_keywords", []),
                    }
                emit(EventType.RESUME_TAILOR_END,
                     f"Resume tailored for {listing.company}" +
                     (f" — {result_data.get('keyword_coverage', '?')}% coverage" if result_data else " (cached)"),
                     company=listing.company, job_uuid=job_uuid,
                     tex_path=tex_path, pdf_path=pdf_path or "",
                     result=result_data)

                # Persist tailoring results to DB
                if result_data:
                    update_status(
                        job_uuid, "in_progress",
                        keyword_coverage=result_data.get("keyword_coverage"),
                        matched_keywords=json.dumps(result_data.get("matched_keywords", [])),
                        missing_keywords=json.dumps(result_data.get("missing_keywords", [])),
                    )

                if not pdf_path:
                    emit(EventType.WARNING,
                         f"PDF compilation failed for {listing.company}, using .tex",
                         company=listing.company, job_uuid=job_uuid)
                    print("    [Warn] PDF compilation failed, using .tex path")
                    pdf_path = tex_path

                # Step 4: Navigate to apply page
                emit(EventType.PAGE_LOAD,
                     f"Loading job page: {listing.url}",
                     url=listing.url, job_uuid=job_uuid)
                print("    Navigating to apply page...")
                page = await scraper.new_page()
                found, page_analysis = await navigate_to_apply_page(page, listing.url)

                if not found or page_analysis is None:
                    emit(EventType.PAGE_APPLY_NOT_FOUND,
                         f"Apply page not found for {listing.company}",
                         company=listing.company, url=listing.url, job_uuid=job_uuid)
                    update_status(job_uuid, "manual_review_needed", notes="Apply page not found")
                    await page.close()
                    error_count += 1
                    bus.update_stats(total_errors=error_count)
                    emit(EventType.JOB_ERROR,
                         f"Could not find apply page for {listing.company}",
                         company=listing.company, job_uuid=job_uuid)
                    continue

                # Emit page classification
                emit(EventType.PAGE_APPLY_FOUND,
                     f"Apply page found — ATS: {page_analysis.ats_platform}",
                     ats=page_analysis.ats_platform, job_uuid=job_uuid,
                     url=page.url)

                if page_analysis.form_fields:
                    fields_data = [
                        {"name": f.name, "label": f.label, "type": f.field_type,
                         "required": f.required, "selector": f.selector}
                        for f in page_analysis.form_fields
                    ]
                    emit(EventType.FORM_FIELDS_DETECTED,
                         f"Detected {len(page_analysis.form_fields)} form fields",
                         fields=fields_data, job_uuid=job_uuid)

                # Step 4b: Submit or queue
                emit(EventType.SUBMIT_DECISION,
                     f"Deciding submission strategy for {listing.company} (ATS: {page_analysis.ats_platform})",
                     ats=page_analysis.ats_platform, job_uuid=job_uuid,
                     dry_run=dry_run)

                submitted, new_status = await decide_and_submit(
                    page=page,
                    page_analysis=page_analysis,
                    job_uuid=job_uuid,
                    resume_pdf_path=pdf_path,
                    dry_run=dry_run,
                    auto_mode=auto_mode,
                )

                await page.close()

                if submitted:
                    applied_count += 1
                    bus.update_stats(total_applied=applied_count)
                    emit(EventType.SUBMIT_SUCCESS,
                         f"Applied to {listing.company}!",
                         company=listing.company, job_uuid=job_uuid, status="applied")
                    print(f"    [Applied] {listing.company}")
                else:
                    queued_count += 1
                    bus.update_stats(total_queued=queued_count)
                    emit(EventType.SUBMIT_QUEUED,
                         f"Queued {listing.company} for dashboard review (status: {new_status})",
                         company=listing.company, job_uuid=job_uuid, status=new_status)
                    print(f"    [Queued] {listing.company} → dashboard review")

                emit(EventType.JOB_END,
                     f"Finished processing {listing.company}",
                     company=listing.company, job_uuid=job_uuid,
                     submitted=submitted, status=new_status)

                # Human-like delay between applications
                if not dry_run:
                    delay = random.uniform(APPLY_DELAY_MIN, APPLY_DELAY_MAX)
                    emit(EventType.JOB_DELAY,
                         f"Waiting {delay:.0f}s before next application...",
                         delay_seconds=delay, job_uuid=job_uuid)
                    print(f"    Waiting {delay:.0f}s before next application...")
                    await asyncio.sleep(delay)

            except Exception as e:
                emit(EventType.JOB_ERROR,
                     f"Error processing {listing.company}: {e}",
                     company=listing.company, job_uuid=job_uuid, error=str(e))
                print(f"    [Error] {listing.company}: {e}")
                update_status(job_uuid, "error", notes=str(e))
                error_count += 1
                bus.update_stats(total_errors=error_count)

    # ---------------------------------------------------------------------------
    # Phase 5: Report
    # ---------------------------------------------------------------------------
    emit(EventType.PHASE_START, "Phase 5: Generating report...",
         phase="Phase 5: Report")
    bus.update_stats(current_phase="Phase 5: Report")

    print("\n" + "=" * 60)
    print(f"RUN COMPLETE — Applied: {applied_count}, Queued: {queued_count}, Errors: {error_count}")
    print("=" * 60)
    print_daily_report()

    bus.running = False
    bus.current_job = None
    emit(EventType.PIPELINE_END,
         f"Pipeline complete — Applied: {applied_count}, Queued: {queued_count}, Errors: {error_count}",
         applied=applied_count, queued=queued_count, errors=error_count)


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
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip discovery and resume processing incomplete jobs from DB",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Fully agentic mode — auto-submit on all ATS platforms, not just Greenhouse/Lever",
    )
    return parser.parse_args()


async def _run_with_timeout(dry_run, max_apps, resume, auto_mode=False):
    """Wrap main_pipeline with a hard timeout."""
    from .config import PIPELINE_TIMEOUT
    try:
        await asyncio.wait_for(
            main_pipeline(dry_run=dry_run, max_apps=max_apps, resume=resume, auto_mode=auto_mode),
            timeout=PIPELINE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"\n[TIMEOUT] Pipeline exceeded {PIPELINE_TIMEOUT}s limit. Shutting down.")
        bus.running = False
        emit(EventType.PIPELINE_END, f"Pipeline timed out after {PIPELINE_TIMEOUT}s")


if __name__ == "__main__":
    args = parse_args()
    if args.schedule:
        run_scheduler()
    else:
        asyncio.run(_run_with_timeout(
            dry_run=args.dry_run, max_apps=args.max_apps,
            resume=args.resume, auto_mode=args.auto,
        ))
