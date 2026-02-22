"""
AutoApply Real-Time Dashboard

A comprehensive Streamlit UI that shows every step of the pipeline in real-time:
    - Job discovery progress
    - Webpage loading and understanding
    - Page navigation and classification
    - Form field detection and filling
    - Resume tailoring
    - Submission decisions

Run:
    streamlit run autoapply/dashboard/realtime_app.py
"""

import asyncio
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Page config — must be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AutoApply Live",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from autoapply.events import EventBus, EventType, PipelineEvent
from autoapply.config import (
    CANDIDATE_PROFILE,
    SEARCH_QUERIES,
    JOB_BOARDS,
    MAX_APPS_PER_RUN,
    AUTO_SUBMIT_ATS,
)

bus = EventBus()

# ---------------------------------------------------------------------------
# CSS styling
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* Global */
.stApp { background-color: #0e1117; }

/* Event cards */
.event-card {
    padding: 8px 12px;
    margin: 4px 0;
    border-radius: 6px;
    border-left: 4px solid #444;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 13px;
    background: #1a1d23;
}
.event-card .time { color: #6b7280; font-size: 11px; }
.event-card .msg { color: #e5e7eb; }

/* Category colors */
.cat-discovery { border-left-color: #3b82f6; }
.cat-dedup { border-left-color: #8b5cf6; }
.cat-navigation { border-left-color: #f59e0b; }
.cat-form_filling { border-left-color: #10b981; }
.cat-resume { border-left-color: #ec4899; }
.cat-submission { border-left-color: #ef4444; }
.cat-job { border-left-color: #06b6d4; }
.cat-pipeline { border-left-color: #f97316; }
.cat-general { border-left-color: #6b7280; }

/* Phase banner */
.phase-banner {
    padding: 12px 16px;
    background: linear-gradient(135deg, #1e3a5f, #1a1d23);
    border-radius: 8px;
    border: 1px solid #2563eb;
    margin: 8px 0;
    font-size: 16px;
    font-weight: 600;
    color: #93c5fd;
}

/* Job card */
.job-card {
    padding: 16px;
    background: #1a1d23;
    border-radius: 8px;
    border: 1px solid #374151;
    margin: 8px 0;
}
.job-card h4 { color: #f9fafb; margin: 0 0 4px 0; }
.job-card .company { color: #9ca3af; font-size: 14px; }
.job-card .url { color: #60a5fa; font-size: 12px; word-break: break-all; }

/* Form field row */
.field-row {
    display: flex;
    align-items: center;
    padding: 6px 10px;
    margin: 2px 0;
    background: #111827;
    border-radius: 4px;
    font-family: monospace;
    font-size: 12px;
}
.field-name { color: #93c5fd; min-width: 160px; }
.field-arrow { color: #4b5563; padding: 0 8px; }
.field-value { color: #34d399; }
.field-type { color: #6b7280; font-size: 11px; margin-left: auto; }

/* Stats pill */
.stat-pill {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 13px;
    font-weight: 600;
    margin: 2px;
}

/* Scrollable log */
.log-container {
    max-height: 600px;
    overflow-y: auto;
    padding: 8px;
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
if "pipeline_thread" not in st.session_state:
    st.session_state.pipeline_thread = None
if "last_event_ts" not in st.session_state:
    st.session_state.last_event_ts = 0
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = True
if "filter_category" not in st.session_state:
    st.session_state.filter_category = "all"


# ---------------------------------------------------------------------------
# Pipeline runner (runs in background thread)
# ---------------------------------------------------------------------------
def _run_pipeline_in_thread(dry_run: bool, max_apps: int, auto_mode: bool = False):
    """Run the async pipeline in a new event loop on a background thread."""
    from autoapply.pipeline import main_pipeline
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_pipeline(dry_run=dry_run, max_apps=max_apps, auto_mode=auto_mode))
    except Exception as e:
        bus.emit(EventType.ERROR, f"Pipeline crashed: {e}", error=str(e))
    finally:
        bus.running = False
        loop.close()


def start_pipeline(dry_run: bool, max_apps: int, auto_mode: bool = False):
    """Launch the pipeline on a background thread."""
    if bus.running:
        st.warning("Pipeline is already running!")
        return
    bus.clear()
    bus.running = True
    t = threading.Thread(
        target=_run_pipeline_in_thread,
        args=(dry_run, max_apps, auto_mode),
        daemon=True,
    )
    t.start()
    st.session_state.pipeline_thread = t


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

CATEGORY_ICONS = {
    "discovery": "🔍",
    "dedup": "🧬",
    "navigation": "🧭",
    "form_filling": "📝",
    "resume": "📄",
    "submission": "🚀",
    "job": "💼",
    "pipeline": "⚙️",
    "general": "ℹ️",
}

CATEGORY_LABELS = {
    "discovery": "Discovery",
    "dedup": "Dedup",
    "navigation": "Navigation",
    "form_filling": "Form Fill",
    "resume": "Resume",
    "submission": "Submit",
    "job": "Job",
    "pipeline": "Pipeline",
    "general": "Info",
}


def render_event_card(event: PipelineEvent):
    """Render a single event as a styled card."""
    cat = event.category
    icon = CATEGORY_ICONS.get(cat, "")
    css_cat = f"cat-{cat}"

    html = f"""
    <div class="event-card {css_cat}">
        <span class="time">{event.time_str}</span>
        &nbsp;{icon}&nbsp;
        <span class="msg">{event.message}</span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

    # Show extra data for certain event types
    data = event.data
    if event.event_type == EventType.FORM_FIELDS_DETECTED and "fields" in data:
        _render_form_fields(data["fields"])
    if event.event_type == EventType.FORM_FIELD_FILLING and "field_name" in data:
        _render_field_fill(data)
    if event.event_type == EventType.PAGE_CLASSIFIED and "analysis" in data:
        _render_page_analysis(data["analysis"])
    if event.event_type == EventType.PAGE_SCREENSHOT and "path" in data:
        _render_screenshot(data["path"])
    if event.event_type == EventType.SEARCH_RESULT and "listing" in data:
        _render_job_listing(data["listing"])
    if event.event_type == EventType.RESUME_TAILOR_END and "result" in data:
        _render_tailor_result(data["result"])


def _render_form_fields(fields):
    """Show detected form fields in a table."""
    if not fields:
        return
    html = ""
    for f in fields:
        name = f.get("name", "") or f.get("label", "")
        ftype = f.get("type", "text")
        required = "required" if f.get("required") else ""
        html += f"""
        <div class="field-row">
            <span class="field-name">{name}</span>
            <span class="field-type">[{ftype}] {required}</span>
        </div>
        """
    st.markdown(html, unsafe_allow_html=True)


def _render_field_fill(data):
    """Show a single field being filled."""
    name = data.get("field_name", "")
    value = data.get("value", "")
    ftype = data.get("field_type", "")
    html = f"""
    <div class="field-row">
        <span class="field-name">{name}</span>
        <span class="field-arrow">→</span>
        <span class="field-value">{value}</span>
        <span class="field-type">[{ftype}]</span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _render_page_analysis(analysis):
    """Show page classification results."""
    if isinstance(analysis, str):
        try:
            analysis = json.loads(analysis)
        except Exception:
            return
    if isinstance(analysis, dict):
        cols = st.columns(4)
        cols[0].metric("Apply Page", "Yes" if analysis.get("is_apply_page") else "No")
        cols[1].metric("ATS Platform", analysis.get("ats_platform", "unknown").upper())
        cols[2].metric("Apply Button", "Found" if analysis.get("has_apply_button") else "None")
        cols[3].metric("Form Fields", len(analysis.get("form_fields", [])))


def _render_screenshot(path):
    """Show a screenshot inline."""
    if path and Path(path).exists():
        st.image(path, caption="Page Screenshot", use_container_width=True)


def _render_job_listing(listing):
    """Show a discovered job listing."""
    if isinstance(listing, dict):
        html = f"""
        <div class="job-card">
            <h4>{listing.get('title', 'Unknown Position')}</h4>
            <div class="company">{listing.get('company', 'Unknown Company')} · {listing.get('ats_platform', 'unknown').upper()}</div>
            <div class="url">{listing.get('url', '')}</div>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)


def _render_tailor_result(result):
    """Show resume tailoring results."""
    if isinstance(result, dict):
        cols = st.columns(3)
        cols[0].metric("Keyword Coverage", f"{result.get('keyword_coverage', 0)}%")
        matched = result.get("matched_keywords", [])
        cols[1].metric("Matched Keywords", len(matched))
        missing = result.get("missing_keywords", [])
        cols[2].metric("Missing Keywords", len(missing))
        if matched:
            st.caption(f"Matched: {', '.join(matched[:10])}")
        if missing:
            st.caption(f"Missing: {', '.join(missing[:10])}")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🎯 AutoApply")
    st.caption("Real-Time Pipeline Dashboard")

    st.divider()

    # Pipeline controls
    st.subheader("Pipeline Controls")
    dry_run = st.toggle("Dry Run", value=True, help="Fill forms but don't submit")
    auto_mode = st.toggle("Auto Mode", value=False, help="Auto-submit on ALL ATS platforms")
    max_apps = st.slider("Max Applications", 1, 50, 5)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶ Start", type="primary", use_container_width=True, disabled=bus.running):
            start_pipeline(dry_run, max_apps, auto_mode=auto_mode)
            st.rerun()
    with col2:
        if st.button("⏹ Stop", use_container_width=True, disabled=not bus.running):
            bus.running = False
            bus.emit(EventType.PIPELINE_END, "Pipeline stopped by user")
            st.rerun()

    if bus.running:
        st.success("Pipeline RUNNING")
    else:
        st.info("Pipeline IDLE")

    st.divider()

    # Live stats
    st.subheader("Live Stats")
    stats = bus.stats
    stat_col1, stat_col2 = st.columns(2)
    stat_col1.metric("Discovered", stats.get("total_discovered", 0))
    stat_col2.metric("New (Unique)", stats.get("total_new", 0))
    stat_col1.metric("Applied", stats.get("total_applied", 0))
    stat_col2.metric("Queued", stats.get("total_queued", 0))
    stat_col1.metric("Errors", stats.get("total_errors", 0))
    stat_col2.metric("Skipped", stats.get("total_skipped", 0))

    if stats.get("total_jobs_to_process", 0) > 0:
        progress = stats["current_job_index"] / stats["total_jobs_to_process"]
        st.progress(progress, text=f"Job {stats['current_job_index']}/{stats['total_jobs_to_process']}")

    st.divider()

    # Filter
    st.subheader("Filter Events")
    categories = ["all", "discovery", "dedup", "navigation", "form_filling", "resume", "submission", "job", "pipeline"]
    selected_cat = st.selectbox(
        "Category",
        categories,
        format_func=lambda x: f"All Events" if x == "all" else f"{CATEGORY_ICONS.get(x, '')} {CATEGORY_LABELS.get(x, x)}",
    )
    st.session_state.filter_category = selected_cat

    st.divider()

    # Config preview
    with st.expander("Candidate Profile"):
        for key, val in CANDIDATE_PROFILE.items():
            if key != "master_resume_path" and val:
                st.text(f"{key}: {val}")

    with st.expander("Search Queries"):
        for q in SEARCH_QUERIES:
            st.text(f"• {q}")

    with st.expander("Settings"):
        st.text(f"Boards: {', '.join(JOB_BOARDS)}")
        st.text(f"Auto-submit: {', '.join(AUTO_SUBMIT_ATS)}")
        st.text(f"Max apps/run: {MAX_APPS_PER_RUN}")

    # Refresh controls
    st.divider()
    auto_ref = st.toggle("Auto-refresh", value=True)
    st.session_state.auto_refresh = auto_ref
    if st.button("🔄 Refresh Now", use_container_width=True):
        st.rerun()
    if st.button("🗑 Clear Events", use_container_width=True):
        bus.clear()
        st.rerun()

    st.caption(f"Events: {bus.event_count()}")


# ---------------------------------------------------------------------------
# Main content area
# ---------------------------------------------------------------------------

# Current phase banner
phase = bus.stats.get("current_phase", "")
if phase:
    st.markdown(f'<div class="phase-banner">⚡ {phase}</div>', unsafe_allow_html=True)

# Auth pause alert — show when pipeline is waiting for user input
_auth_events = [e for e in bus.get_all_events()
                if e.event_type.value == "auth_pause_for_user"]
if _auth_events:
    _latest_auth = _auth_events[-1]
    # Only show if no AUTH_SUCCESS or AUTH_TIMEOUT followed it
    _auth_resolved = any(
        e.timestamp > _latest_auth.timestamp and e.event_type.value in ("auth_success", "auth_timeout", "auth_failure")
        for e in bus.get_all_events()
    )
    if not _auth_resolved:
        st.warning("**Manual Action Required** — The pipeline is paused waiting for authentication.")
        _auth_screenshot = _latest_auth.data.get("screenshot", "")
        if _auth_screenshot and Path(_auth_screenshot).exists():
            st.image(_auth_screenshot, caption="Auth page screenshot", use_container_width=True)
        st.caption(f"Reason: {_latest_auth.data.get('auth_type', 'unknown')} | URL: {_latest_auth.data.get('url', '')}")
        _auth_input = st.text_input("Enter OTP code (or type 'done' if you handled it manually):", key="auth_otp_input")
        if st.button("Submit Auth Response", type="primary"):
            if _auth_input:
                bus.provide_auth_response(_auth_input.strip())
                st.success(f"Response sent: {_auth_input.strip()}")
                st.rerun()

# Tabs
tab_live, tab_current, tab_jobs, tab_forms, tab_log = st.tabs([
    "📡 Live Feed",
    "💼 Current Job",
    "📋 All Jobs Found",
    "📝 Form Activity",
    "📜 Full Log",
])

# ---------------------------------------------------------------------------
# Tab: Live Feed
# ---------------------------------------------------------------------------
with tab_live:
    events = bus.get_all_events()
    cat_filter = st.session_state.filter_category

    if cat_filter != "all":
        events = [e for e in events if e.category == cat_filter]

    if not events:
        st.info("No events yet. Start the pipeline to see real-time activity.")
    else:
        # Show newest first
        for event in reversed(events[-100:]):
            render_event_card(event)

# ---------------------------------------------------------------------------
# Tab: Current Job
# ---------------------------------------------------------------------------
with tab_current:
    job = bus.current_job
    if job:
        st.markdown(f"""
        <div class="job-card">
            <h4>{job.get('title', 'Unknown Position')}</h4>
            <div class="company">{job.get('company', 'Unknown')} · {job.get('ats_platform', 'unknown').upper()}</div>
            <div class="url">{job.get('url', '')}</div>
        </div>
        """, unsafe_allow_html=True)

        # Show events for this job
        job_uuid = job.get("uuid", "")
        job_events = [e for e in bus.get_all_events() if e.data.get("job_uuid") == job_uuid]

        if job_events:
            st.subheader("Job Activity Timeline")
            for event in job_events:
                render_event_card(event)
        else:
            st.caption("Processing...")
    else:
        st.info("No job is currently being processed.")

# ---------------------------------------------------------------------------
# Tab: All Jobs Found
# ---------------------------------------------------------------------------
with tab_jobs:
    discovery_events = [e for e in bus.get_all_events() if e.event_type == EventType.SEARCH_RESULT]
    if discovery_events:
        st.subheader(f"Jobs Discovered ({len(discovery_events)})")
        for event in discovery_events:
            listing = event.data.get("listing", {})
            if listing:
                col1, col2, col3 = st.columns([3, 2, 1])
                col1.write(f"**{listing.get('title', 'N/A')}**")
                col2.write(listing.get("company", "N/A"))
                col3.write(listing.get("ats_platform", "unknown").upper())
    else:
        st.info("No jobs discovered yet.")

# ---------------------------------------------------------------------------
# Tab: Form Activity
# ---------------------------------------------------------------------------
with tab_forms:
    form_events = [
        e for e in bus.get_all_events()
        if e.category == "form_filling"
    ]
    if form_events:
        st.subheader(f"Form Activity ({len(form_events)} events)")
        for event in form_events:
            render_event_card(event)
    else:
        st.info("No form activity yet.")

# ---------------------------------------------------------------------------
# Tab: Full Log
# ---------------------------------------------------------------------------
with tab_log:
    all_events = bus.get_all_events()
    if all_events:
        # Export option
        if st.button("Export Log as JSON"):
            log_data = [
                {
                    "time": e.time_str,
                    "type": e.event_type.value,
                    "category": e.category,
                    "message": e.message,
                    "data": {k: str(v) for k, v in e.data.items()},
                }
                for e in all_events
            ]
            st.download_button(
                "Download",
                json.dumps(log_data, indent=2),
                "autoapply_log.json",
                "application/json",
            )

        st.subheader(f"All Events ({len(all_events)})")
        for event in reversed(all_events):
            render_event_card(event)
    else:
        st.info("No events recorded.")


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
if st.session_state.auto_refresh and bus.running:
    time.sleep(2)
    st.rerun()
