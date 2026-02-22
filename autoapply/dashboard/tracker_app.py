"""
AutoApply Application Tracker

A comprehensive Streamlit dashboard for tracking job applications:
    - Pipeline funnel (Saved → Applied → Interview → Offer → Rejected)
    - Filterable job list with search
    - Per-job detail view with timeline, resume changes, form data, screenshot

Run:
    streamlit run autoapply/dashboard/tracker_app.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import streamlit as st

# ---------------------------------------------------------------------------
# Page config — must be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AutoApply Tracker",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from autoapply.storage.database import (
    init_db,
    get_all_jobs,
    get_job_detail,
    get_pipeline_stats,
    get_distinct_companies,
    get_distinct_statuses,
    update_status,
)

# Initialize DB (runs migration if needed)
init_db()

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* Dark theme base */
.stApp { background-color: #0a0e14; }

/* Metric cards */
.metric-card {
    background: linear-gradient(135deg, #141926, #1a2035);
    border: 1px solid #1e2a42;
    border-radius: 12px;
    padding: 20px 16px;
    text-align: center;
}
.metric-card .metric-value {
    font-size: 36px;
    font-weight: 700;
    color: #f0f4ff;
    line-height: 1.1;
}
.metric-card .metric-label {
    font-size: 13px;
    color: #6b7fa3;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* Funnel bar */
.funnel-bar {
    display: flex;
    border-radius: 8px;
    overflow: hidden;
    height: 36px;
    margin: 8px 0;
    background: #111827;
}
.funnel-segment {
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 600;
    color: #fff;
    min-width: 60px;
    transition: flex-grow 0.3s;
}

/* Job row card */
.job-row {
    display: flex;
    align-items: center;
    padding: 14px 16px;
    margin: 4px 0;
    background: #111827;
    border: 1px solid #1e2a42;
    border-radius: 8px;
    transition: border-color 0.2s;
}
.job-row:hover { border-color: #3b82f6; }
.job-row .job-company {
    font-weight: 600;
    color: #f0f4ff;
    font-size: 15px;
}
.job-row .job-title {
    color: #8b9dc3;
    font-size: 13px;
    margin-top: 2px;
}
.job-row .job-meta {
    color: #4b5e80;
    font-size: 12px;
    margin-top: 4px;
}

/* Status badge */
.status-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.status-applied { background: #1e3a5f; color: #60a5fa; }
.status-interview { background: #1a3a2a; color: #34d399; }
.status-offer { background: #3a2a1a; color: #fbbf24; }
.status-rejected, .status-error { background: #3a1a1a; color: #f87171; }
.status-discovered, .status-queued_for_apply, .status-saved { background: #1a1a2e; color: #818cf8; }
.status-in_progress { background: #1a2e3a; color: #67e8f9; }
.status-pending_review, .status-manual_review_needed { background: #2e2a1a; color: #fde68a; }

/* Detail page */
.detail-header {
    padding: 24px;
    background: linear-gradient(135deg, #111827, #1a2035);
    border: 1px solid #1e2a42;
    border-radius: 12px;
    margin-bottom: 16px;
}
.detail-header .company-name {
    font-size: 28px;
    font-weight: 700;
    color: #f0f4ff;
}
.detail-header .job-title {
    font-size: 18px;
    color: #8b9dc3;
    margin-top: 4px;
}

/* Timeline */
.timeline-item {
    display: flex;
    align-items: flex-start;
    padding: 8px 0;
    border-left: 2px solid #1e2a42;
    margin-left: 8px;
    padding-left: 16px;
    position: relative;
}
.timeline-item::before {
    content: '';
    position: absolute;
    left: -5px;
    top: 12px;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #3b82f6;
}
.timeline-date {
    color: #4b5e80;
    font-size: 12px;
    min-width: 100px;
}
.timeline-text {
    color: #c4d0e4;
    font-size: 13px;
}

/* Keyword tag */
.kw-tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    margin: 2px;
}
.kw-matched { background: #1a3a2a; color: #34d399; }
.kw-missing { background: #3a1a1a; color: #f87171; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_badge(status: str) -> str:
    css_cls = status.replace(" ", "_").lower()
    return f'<span class="status-badge status-{css_cls}">{status}</span>'


def _time_ago(dt_str: Optional[str]) -> str:
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        delta = datetime.now(dt.tzinfo) - dt if dt.tzinfo else datetime.now() - dt
        days = delta.days
        if days == 0:
            hours = delta.seconds // 3600
            return f"{hours}h ago" if hours > 0 else "just now"
        elif days == 1:
            return "1 day ago"
        else:
            return f"{days} days ago"
    except Exception:
        return dt_str[:10] if dt_str else ""


def _format_date(dt_str: Optional[str]) -> str:
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return dt_str[:10]


# ---------------------------------------------------------------------------
# Routing: overview vs detail
# ---------------------------------------------------------------------------
params = st.query_params
detail_uuid = params.get("job", None)


if detail_uuid:
    # ===================================================================
    # DETAIL VIEW
    # ===================================================================
    job = get_job_detail(detail_uuid)
    if not job:
        st.error(f"Job not found: {detail_uuid}")
        st.stop()

    # Back button
    if st.button("← Back to All Applications"):
        st.query_params.clear()
        st.rerun()

    # Header
    st.markdown(f"""
    <div class="detail-header">
        <div class="company-name">{job.get("company_name", "Unknown")}</div>
        <div class="job-title">{job.get("job_title", "Position")}
            {(' · ' + job.get("location", "")) if job.get("location") else ""}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Status + actions row
    col_status, col_actions = st.columns([1, 3])
    with col_status:
        st.markdown(f"**Status:** {_status_badge(job.get('status', 'unknown'))}", unsafe_allow_html=True)
    with col_actions:
        new_status = st.selectbox(
            "Move to:",
            ["", "interview", "offer", "rejected_after_apply", "no_response", "applied"],
            format_func=lambda x: "— Select —" if x == "" else x.replace("_", " ").title(),
            key="status_select",
        )
        if new_status:
            if st.button("Update Status", type="primary"):
                update_status(detail_uuid, new_status, changed_by="user")
                st.success(f"Status updated to: {new_status}")
                st.rerun()

    st.divider()

    # Two-column layout: details + timeline
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("Job Details")
        info_items = [
            ("Link", f"[Open job page →]({job.get('job_url', '#')})"),
            ("ATS Platform", (job.get("ats_platform") or "unknown").upper()),
            ("Job Board", (job.get("job_board") or "—").title()),
            ("Location", job.get("location") or "—"),
            ("Discovered", _format_date(job.get("date_discovered"))),
            ("Applied", _format_date(job.get("date_applied"))),
        ]
        for label, value in info_items:
            st.markdown(f"**{label}:** {value}")

        # Keyword coverage
        coverage = job.get("keyword_coverage")
        if coverage is not None:
            st.divider()
            st.subheader("Resume Match")
            st.metric("Keyword Coverage", f"{coverage:.0f}%")
            matched = job.get("matched_keywords", [])
            missing = job.get("missing_keywords", [])
            if matched:
                tags = " ".join(f'<span class="kw-tag kw-matched">{k}</span>' for k in matched)
                st.markdown(f"**Matched:** {tags}", unsafe_allow_html=True)
            if missing:
                tags = " ".join(f'<span class="kw-tag kw-missing">{k}</span>' for k in missing)
                st.markdown(f"**Missing:** {tags}", unsafe_allow_html=True)

    with col_right:
        st.subheader("Application Timeline")
        history = job.get("history", [])
        if history:
            for entry in history:
                date_str = _format_date(entry.get("changed_at"))
                old = entry.get("old_status") or "—"
                new = entry.get("new_status", "")
                by = entry.get("changed_by", "pipeline")
                note = entry.get("notes") or ""
                st.markdown(f"""
                <div class="timeline-item">
                    <span class="timeline-date">{date_str}</span>
                    <span class="timeline-text">
                        {old} → <strong>{new}</strong>
                        <span style="color:#4b5e80"> · {by}</span>
                        {f'<br><span style="color:#6b7fa3">{note}</span>' if note else ""}
                    </span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.caption("No status changes recorded yet.")

    st.divider()

    # Job Description
    with st.expander("Job Description", expanded=False):
        jd = job.get("job_description", "")
        if jd:
            st.text(jd[:5000])
        else:
            st.caption("No job description stored.")

    # Form Data
    form_data_str = job.get("form_data", "")
    if form_data_str:
        with st.expander("Form Data Filled", expanded=False):
            try:
                fields = json.loads(form_data_str)
                for f in fields:
                    st.markdown(f"**{f.get('field', '?')}** ({f.get('type', 'text')}): `{f.get('value', '')}`")
            except (json.JSONDecodeError, TypeError):
                st.text(form_data_str)

    # Tailored Resume
    resume_path = job.get("tailored_resume_path", "")
    if resume_path and Path(resume_path).exists():
        with st.expander("Tailored Resume", expanded=False):
            st.markdown(f"**File:** `{resume_path}`")
            if resume_path.endswith(".tex"):
                st.code(Path(resume_path).read_text()[:3000], language="latex")

    # Screenshot
    screenshot = job.get("screenshot_path", "")
    if screenshot and Path(screenshot).exists():
        with st.expander("Application Screenshot", expanded=False):
            st.image(screenshot, use_container_width=True)

    # Notes
    st.divider()
    st.subheader("Notes")
    current_notes = job.get("notes") or ""
    new_notes = st.text_area("Edit notes:", value=current_notes, key="notes_edit")
    if st.button("Save Notes"):
        update_status(detail_uuid, job.get("status", "applied"), notes=new_notes, changed_by="user")
        st.success("Notes saved.")

else:
    # ===================================================================
    # OVERVIEW PAGE
    # ===================================================================
    st.markdown("## AutoApply Tracker")

    # Stats
    stats = get_pipeline_stats()

    # Top metric cards
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{stats['total']}</div>
            <div class="metric-label">Total Applications</div>
        </div>""", unsafe_allow_html=True)
    with m2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{stats['interview']}</div>
            <div class="metric-label">Active Interviews</div>
        </div>""", unsafe_allow_html=True)
    with m3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{stats['offer']}</div>
            <div class="metric-label">Offers Received</div>
        </div>""", unsafe_allow_html=True)
    with m4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{stats['success_rate']}%</div>
            <div class="metric-label">Success Rate</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("")

    # Pipeline funnel
    funnel_stages = [
        ("Saved", stats["saved"], "#818cf8"),
        ("Applied", stats["applied"], "#3b82f6"),
        ("Interview", stats["interview"], "#34d399"),
        ("Offer", stats["offer"], "#fbbf24"),
        ("Rejected", stats["rejected"], "#f87171"),
    ]
    total_for_bar = max(sum(s[1] for s in funnel_stages), 1)
    segments_html = ""
    for label, count, color in funnel_stages:
        width_pct = max(count / total_for_bar * 100, 0)
        if count > 0:
            segments_html += (
                f'<div class="funnel-segment" style="flex-grow:{width_pct};background:{color};">'
                f'{label} ({count})</div>'
            )
    if segments_html:
        st.markdown(f'<div class="funnel-bar">{segments_html}</div>', unsafe_allow_html=True)
    else:
        st.caption("No applications yet.")

    st.markdown("")

    # Filters
    fc1, fc2, fc3 = st.columns([1, 1, 2])
    with fc1:
        all_statuses = [""] + get_distinct_statuses()
        status_filter = st.selectbox(
            "Status",
            all_statuses,
            format_func=lambda x: "All Statuses" if x == "" else x.replace("_", " ").title(),
        )
    with fc2:
        all_companies = [""] + get_distinct_companies()
        company_filter = st.selectbox(
            "Company",
            all_companies,
            format_func=lambda x: "All Companies" if x == "" else x,
        )
    with fc3:
        search_query = st.text_input("Search", placeholder="Search by company or title...")

    # Job list
    jobs = get_all_jobs(
        status_filter=status_filter,
        company_filter=company_filter,
        search_query=search_query,
    )

    st.caption(f"{len(jobs)} applications")

    for job in jobs:
        col_info, col_meta, col_action = st.columns([4, 2, 1])
        with col_info:
            company = job.get("company_name", "Unknown")
            title = job.get("job_title") or "Position"
            status = job.get("status", "unknown")
            board = (job.get("job_board") or "").title()
            location = job.get("location") or ""
            discovered = _time_ago(job.get("date_discovered"))
            coverage = job.get("keyword_coverage")

            meta_parts = [s for s in [status.replace("_", " ").title(), discovered, board, location] if s]
            coverage_str = f" · Coverage: {coverage:.0f}%" if coverage else ""

            st.markdown(
                f'<div class="job-row">'
                f'<div style="flex:1">'
                f'<div class="job-company">{company}</div>'
                f'<div class="job-title">{title}</div>'
                f'<div class="job-meta">{" · ".join(meta_parts)}{coverage_str}</div>'
                f'</div>'
                f'<div>{_status_badge(status)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with col_meta:
            pass
        with col_action:
            if st.button("View →", key=f"view_{job['uuid']}"):
                st.query_params["job"] = job["uuid"]
                st.rerun()
