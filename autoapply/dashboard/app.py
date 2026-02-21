"""
AutoApply Streamlit Dashboard

Displays queued applications (pending_review) and lets the user
approve or reject each one.

Run with:
    streamlit run autoapply/dashboard/app.py
"""

import asyncio
import json
import sys
from pathlib import Path

# Allow importing from parent project
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from autoapply.storage.database import (
    init_db,
    get_pending_jobs,
    update_status,
    get_all_stats,
    get_job,
)
from autoapply.config import CANDIDATE_PROFILE


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AutoApply Dashboard",
    page_icon="🤖",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------
def _init_state():
    if "submitted_uuids" not in st.session_state:
        st.session_state.submitted_uuids = set()


# ---------------------------------------------------------------------------
# Sidebar: stats
# ---------------------------------------------------------------------------
def _render_sidebar():
    st.sidebar.title("AutoApply")
    st.sidebar.caption("Autonomous Job Application Pipeline")
    st.sidebar.divider()

    init_db()
    stats = get_all_stats()

    st.sidebar.metric("Total Jobs Found", stats.get("total", 0))
    st.sidebar.metric("Applied", stats.get("applied", 0))
    st.sidebar.metric("Pending Review", stats.get("pending_review", 0))
    st.sidebar.metric("Rejected", stats.get("rejected", 0))
    st.sidebar.metric("Discovered", stats.get("discovered", 0))

    st.sidebar.divider()
    if st.sidebar.button("Refresh"):
        st.rerun()


# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------
def _render_queue():
    st.title("Application Review Queue")
    st.caption(
        "Applications queued here were filled automatically but need your approval "
        "before submission (Workday, iCIMS, and unknown ATS platforms)."
    )

    pending = get_pending_jobs("pending_review")

    if not pending:
        st.success("No pending applications. You're all caught up!")
        return

    st.info(f"{len(pending)} application(s) awaiting review")

    for job in pending:
        _render_job_card(job)


def _render_job_card(job: dict):
    uuid = job["uuid"]
    if uuid in st.session_state.submitted_uuids:
        return

    with st.expander(
        f"**{job['company_name']}** — {job['job_title'] or 'Position'} | {job['ats_platform'] or 'Unknown ATS'}",
        expanded=True,
    ):
        col1, col2 = st.columns([3, 1])

        with col1:
            st.markdown(f"**URL:** [{job['job_url']}]({job['job_url']})")
            st.markdown(f"**ATS:** {job['ats_platform'] or 'Unknown'}")
            st.markdown(f"**Discovered:** {job['date_discovered']}")

            # Show filled form data
            if job.get("form_data"):
                try:
                    fields = json.loads(job["form_data"])
                    st.markdown("**Pre-filled Form Fields:**")
                    for f in fields:
                        if f.get("value"):
                            st.markdown(f"- `{f['field']}`: {f['value']}")
                except Exception:
                    pass

            # Show screenshot
            if job.get("screenshot_path") and Path(job["screenshot_path"]).exists():
                st.image(job["screenshot_path"], caption="Filled form screenshot", use_container_width=True)

            # Resume link
            if job.get("tailored_resume_path") and Path(job["tailored_resume_path"]).exists():
                st.markdown(f"**Tailored Resume:** `{job['tailored_resume_path']}`")

        with col2:
            st.markdown("### Actions")

            if st.button("Approve & Submit", key=f"approve_{uuid}", type="primary"):
                _approve_application(job)
                st.session_state.submitted_uuids.add(uuid)
                st.success(f"Submitted to {job['company_name']}!")
                st.rerun()

            if st.button("Reject / Skip", key=f"reject_{uuid}"):
                update_status(uuid, "rejected", notes="Rejected via dashboard")
                st.session_state.submitted_uuids.add(uuid)
                st.warning("Application rejected.")
                st.rerun()

            if st.button("Open in Browser", key=f"open_{uuid}"):
                import webbrowser
                webbrowser.open(job["job_url"])


def _approve_application(job: dict):
    """
    Re-open the application URL in a visible Playwright browser,
    re-fill the form using the stored profile, and click submit.
    """
    import subprocess
    import sys

    # Run the approval in a subprocess so it can use Playwright with a visible browser
    script = f"""
import asyncio
import sys
sys.path.insert(0, '{str(Path(__file__).parent.parent.parent)}')

from playwright.async_api import async_playwright
from autoapply.apply.classifier import classify_page
from autoapply.apply.filler import fill_form
from autoapply.apply.strategies import get_strategy
from autoapply.storage.database import update_status
from autoapply.config import CANDIDATE_PROFILE

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("{job['job_url']}", wait_until="domcontentloaded")
        import asyncio; await asyncio.sleep(2)
        html = await page.content()
        from autoapply.apply.classifier import classify_page
        analysis = classify_page(html)
        strategy_cls = get_strategy(analysis.ats_platform)
        strategy = strategy_cls()
        resume_path = "{job.get('tailored_resume_path', '')}"
        result = await strategy.execute(page, analysis, resume_path, CANDIDATE_PROFILE)
        if result:
            update_status("{job['uuid']}", "applied")
        await asyncio.sleep(3)
        await browser.close()

asyncio.run(main())
"""
    try:
        subprocess.Popen([sys.executable, "-c", script])
        update_status(job["uuid"], "applied", notes="Approved via dashboard")
    except Exception as e:
        st.error(f"Could not launch browser: {e}")


# ---------------------------------------------------------------------------
# Applied tab
# ---------------------------------------------------------------------------
def _render_applied():
    st.title("Applied Applications")
    applied = get_pending_jobs("applied")
    if not applied:
        st.info("No applications submitted yet.")
        return
    for job in applied:
        st.markdown(
            f"- **{job['company_name']}** — {job['job_title'] or 'Position'} | "
            f"Applied: {job['date_applied'] or 'N/A'} | "
            f"[Link]({job['job_url']})"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
_init_state()
_render_sidebar()

tab1, tab2 = st.tabs(["Pending Review", "Applied"])
with tab1:
    _render_queue()
with tab2:
    _render_applied()
