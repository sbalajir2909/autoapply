"""
AutoApply Control Center — Streamlit Dashboard

Full control panel for the autonomous job application pipeline.
Run with:
    streamlit run autoapply/dashboard/app.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from subprocess import PIPE, STDOUT

# ── project root on path ─────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st

# Load and apply saved settings before importing autoapply modules
from autoapply.dashboard import settings_store

# ── page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="AutoApply Control Center",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── imports after settings are applied ───────────────────────────────────────
from autoapply.storage.database import (
    init_db, get_pending_jobs, update_status,
    get_all_stats, get_job, get_connection,
)
from autoapply.reports.daily_report import get_today_stats

# Ensure DB exists
init_db()


# ═════════════════════════════════════════════════════════════════════════════
# Session state initialisation
# ═════════════════════════════════════════════════════════════════════════════
def _init_state():
    defaults = {
        "page": "📊 Dashboard",
        "settings": settings_store.load(),
        "pipeline_running": False,
        "pipeline_proc": None,
        "pipeline_log": [],
        "pipeline_exit_code": None,
        "submitted_uuids": set(),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ═════════════════════════════════════════════════════════════════════════════
# Sidebar
# ═════════════════════════════════════════════════════════════════════════════
def _sidebar():
    with st.sidebar:
        st.markdown("## 🤖 AutoApply")
        st.caption("Autonomous job application pipeline")
        st.divider()

        PAGES = ["📊 Dashboard", "🚀 Run Pipeline", "📋 Job Queue", "⚙️ Settings", "📁 All Jobs"]
        page = st.radio(
            "Navigation",
            PAGES,
            index=PAGES.index(st.session_state.page),
            label_visibility="collapsed",
        )
        st.session_state.page = page
        st.divider()

        # Mini stats
        stats = get_all_stats()
        st.metric("Total Jobs", stats.get("total", 0))
        c1, c2 = st.columns(2)
        c1.metric("Applied", stats.get("applied", 0))
        c2.metric("Queued", stats.get("pending_review", 0))

        # Pipeline status
        st.divider()
        if st.session_state.pipeline_running:
            st.markdown("🟢 **Pipeline running…**")
        elif st.session_state.pipeline_exit_code == 0:
            st.markdown("✅ **Last run succeeded**")
        elif st.session_state.pipeline_exit_code is not None:
            st.markdown("🔴 **Last run failed**")
        else:
            st.markdown("⚪ **Pipeline idle**")

        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

_sidebar()


# ═════════════════════════════════════════════════════════════════════════════
# Page: 📊 Dashboard
# ═════════════════════════════════════════════════════════════════════════════
def page_dashboard():
    st.title("📊 Dashboard")
    auto_refresh = st.toggle("Auto-refresh every 30s", value=False)

    stats = get_all_stats()
    today = get_today_stats()

    # Metric cards
    cols = st.columns(5)
    cols[0].metric("Total Jobs", stats.get("total", 0))
    cols[1].metric("Applied", stats.get("applied", 0))
    cols[2].metric("Pending Review", stats.get("pending_review", 0))
    cols[3].metric("Discovered", stats.get("discovered", 0))
    cols[4].metric("Errors", stats.get("error", 0) + stats.get("manual_review_needed", 0))

    st.divider()

    t1, t2 = st.columns(2)
    t1.info(f"**Today's applications sent:** {today.get('applied_today', 0)}")
    t2.info(f"**Today's jobs discovered:** {today.get('discovered_today', 0)}")

    st.divider()

    chart_data = {k: v for k, v in stats.items() if k != "total" and v > 0}
    if chart_data:
        st.subheader("Jobs by Status")
        st.bar_chart(chart_data)
    else:
        st.info("No jobs in the database yet. Run the pipeline to discover jobs.")

    st.divider()
    st.subheader("Recent Activity")
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT company_name, job_title, ats_platform, status, date_discovered "
            "FROM jobs ORDER BY date_discovered DESC LIMIT 15"
        ).fetchall()
        conn.close()
        if rows:
            import pandas as pd
            df = pd.DataFrame([dict(r) for r in rows])
            df.columns = ["Company", "Title", "ATS", "Status", "Discovered"]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.caption("No jobs yet.")
    except Exception as e:
        st.warning(f"Could not load recent activity: {e}")

    if auto_refresh:
        time.sleep(30)
        st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# Page: 🚀 Run Pipeline
# ═════════════════════════════════════════════════════════════════════════════
def page_run_pipeline():
    st.title("🚀 Run Pipeline")

    settings = st.session_state.settings
    api_key = settings.get("anthropic_api_key", "")

    if not api_key:
        st.warning("⚠️ No API key configured. Go to **⚙️ Settings** and save your Anthropic API key first.")

    left, right = st.columns([1, 2])

    with left:
        st.subheader("Controls")
        dry_run = st.toggle("Dry Run (no actual submissions)", value=True)
        max_apps = st.number_input(
            "Max apps this run", min_value=1, max_value=50,
            value=int(settings.get("max_apps_per_run", 5)),
        )

        running = st.session_state.pipeline_running

        if st.button("▶ Start Pipeline", type="primary",
                     disabled=(running or not api_key), use_container_width=True):
            _start_pipeline(dry_run, max_apps, api_key)

        if st.button("⏹ Stop", disabled=not running, use_container_width=True):
            _stop_pipeline()

        st.divider()
        st.subheader("What will run")
        boards = settings.get("job_boards", [])
        queries = settings.get("search_queries", [])
        auto_submit = settings.get("auto_submit_ats", [])
        st.markdown(f"**Boards:** {', '.join(boards) or 'none'}")
        st.markdown(f"**Queries:** {len(queries)} configured")
        st.markdown(f"**Auto-submit:** {', '.join(auto_submit) if auto_submit else 'None (all queued)'}")
        st.markdown(f"**Mode:** {'🔵 Dry run' if dry_run else '🔴 Live (will submit)'}")

    with right:
        st.subheader("Live Output")
        progress_bar = st.progress(0)
        log_box = st.empty()

        # Show existing log if pipeline just finished
        if st.session_state.pipeline_log:
            log_box.code("\n".join(st.session_state.pipeline_log[-150:]), language="")

        # Stream output if currently running
        if running and st.session_state.pipeline_proc:
            proc = st.session_state.pipeline_proc
            log_lines = list(st.session_state.pipeline_log)

            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                log_lines.append(line)
                st.session_state.pipeline_log = log_lines
                log_box.code("\n".join(log_lines[-150:]), language="")

                # Update progress bar from phase markers
                if "[Phase 1]" in line:
                    progress_bar.progress(0.15, "Phase 1: Discovering jobs…")
                elif "Phase 1b" in line:
                    progress_bar.progress(0.30, "Phase 1b: Fetching job descriptions…")
                elif "[Phase 2]" in line:
                    progress_bar.progress(0.45, "Phase 2: Deduplicating…")
                elif "[Phase 3+4]" in line:
                    progress_bar.progress(0.60, "Phase 3+4: Tailoring & applying…")
                elif "Tailoring resume" in line:
                    progress_bar.progress(0.70, "Tailoring resume…")
                elif "Navigating to apply" in line:
                    progress_bar.progress(0.80, "Navigating to apply page…")
                elif "RUN COMPLETE" in line:
                    progress_bar.progress(1.0, "Complete!")

            proc.wait()
            st.session_state.pipeline_exit_code = proc.returncode
            st.session_state.pipeline_running = False
            st.session_state.pipeline_proc = None

            if proc.returncode == 0:
                st.success("✅ Pipeline completed successfully!")
            else:
                st.error(f"❌ Pipeline exited with code {proc.returncode}")
            st.rerun()


def _start_pipeline(dry_run: bool, max_apps: int, api_key: str):
    st.session_state.pipeline_log = []
    st.session_state.pipeline_exit_code = None

    cmd = [sys.executable, "-m", "autoapply.pipeline", "--max-apps", str(max_apps)]
    if dry_run:
        cmd.append("--dry-run")

    proc = subprocess.Popen(
        cmd,
        stdout=PIPE, stderr=STDOUT, text=True,
        cwd=str(ROOT),
        env={**os.environ, "ANTHROPIC_API_KEY": api_key},
    )
    st.session_state.pipeline_proc = proc
    st.session_state.pipeline_running = True
    st.rerun()


def _stop_pipeline():
    proc = st.session_state.pipeline_proc
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    st.session_state.pipeline_running = False
    st.session_state.pipeline_proc = None
    st.session_state.pipeline_exit_code = -1
    st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# Page: 📋 Job Queue
# ═════════════════════════════════════════════════════════════════════════════
def page_job_queue():
    st.title("📋 Job Queue")
    st.caption("Applications filled automatically, awaiting your approval before submission.")

    f1, f2 = st.columns([2, 1])
    with f1:
        ats_filter = st.selectbox(
            "Filter by ATS",
            ["All", "greenhouse", "lever", "workday", "linkedin", "indeed", "unknown"],
        )
    with f2:
        if st.button("🔄 Refresh Queue"):
            st.rerun()

    pending = get_pending_jobs("pending_review")
    if ats_filter != "All":
        pending = [j for j in pending if j.get("ats_platform") == ats_filter]

    if not pending:
        st.info("No jobs pending review. Run the pipeline to discover and queue jobs.")
        if st.button("▶ Go to Run Pipeline"):
            st.session_state.page = "🚀 Run Pipeline"
            st.rerun()
        return

    st.markdown(f"**{len(pending)} job(s) awaiting review**")
    st.divider()

    for job in pending:
        if job["uuid"] not in st.session_state.submitted_uuids:
            _render_job_card(job)


def _ats_badge(ats: str) -> str:
    return {"greenhouse": "🟢", "lever": "🔵", "workday": "🟠",
            "linkedin": "🔷", "indeed": "🟣"}.get(ats or "unknown", "⚫")


def _render_job_card(job: dict):
    uuid = job["uuid"]
    ats = job.get("ats_platform") or "unknown"
    company = job.get("company_name") or "Unknown Company"
    title = job.get("job_title") or "Position"

    with st.container(border=True):
        h1, h2 = st.columns([4, 1])
        with h1:
            st.markdown(f"### {company}")
            st.markdown(f"**{title}** &nbsp; {_ats_badge(ats)} `{ats.upper()}`")
        with h2:
            st.caption(f"Found: {(job.get('date_discovered') or '')[:10]}")
            st.link_button("🌐 Open Job", job["job_url"])

        d1, d2 = st.columns(2)
        with d1:
            if job.get("form_data"):
                with st.expander("📄 Pre-filled Form Data"):
                    try:
                        fields = json.loads(job["form_data"])
                        if fields:
                            import pandas as pd
                            st.dataframe(pd.DataFrame(fields), use_container_width=True, hide_index=True)
                        else:
                            st.caption("No form fields captured.")
                    except Exception:
                        st.code(job["form_data"])
            if job.get("job_description"):
                with st.expander("📝 Job Description (preview)"):
                    jd = job["job_description"]
                    st.caption(jd[:1500] + ("…" if len(jd) > 1500 else ""))

        with d2:
            if job.get("screenshot_path") and Path(job["screenshot_path"]).exists():
                with st.expander("📸 Form Screenshot"):
                    st.image(job["screenshot_path"], use_container_width=True)
            if job.get("tailored_resume_path"):
                rpath = Path(job["tailored_resume_path"])
                with st.expander("📄 Tailored Resume"):
                    st.caption(f"`{job['tailored_resume_path']}`")
                    if rpath.exists():
                        st.download_button(
                            "⬇ Download", rpath.read_bytes(),
                            file_name=rpath.name, key=f"dl_{uuid}",
                        )

        st.divider()
        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("✅ Approve & Submit", key=f"approve_{uuid}",
                         type="primary", use_container_width=True):
                with st.spinner(f"Submitting to {company}…"):
                    _approve_application(job)
                st.session_state.submitted_uuids.add(uuid)
                st.success(f"Submitted to {company}!")
                st.rerun()
        with b2:
            if st.button("❌ Reject", key=f"reject_{uuid}", use_container_width=True):
                update_status(uuid, "rejected", notes="Rejected via dashboard")
                st.session_state.submitted_uuids.add(uuid)
                st.rerun()
        with b3:
            if st.button("⏭ Skip for now", key=f"skip_{uuid}", use_container_width=True):
                st.session_state.submitted_uuids.add(uuid)
                st.rerun()


def _approve_application(job: dict):
    script = f"""
import asyncio, sys
sys.path.insert(0, {str(ROOT)!r})
from playwright.async_api import async_playwright
from autoapply.apply.classifier import classify_page
from autoapply.apply.strategies import get_strategy
from autoapply.storage.database import update_status
from autoapply.config import CANDIDATE_PROFILE

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await (await browser.new_context()).new_page()
        await page.goto({job['job_url']!r}, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        html = await page.content()
        analysis = classify_page(html)
        strategy = get_strategy(analysis.ats_platform)()
        result = await strategy.execute(
            page, analysis,
            {(job.get('tailored_resume_path') or '')!r},
            CANDIDATE_PROFILE,
        )
        update_status(
            {job['uuid']!r},
            "applied" if result else "pending_review",
            notes="Processed via dashboard",
        )
        await asyncio.sleep(3)
        await browser.close()

asyncio.run(main())
"""
    try:
        subprocess.Popen([sys.executable, "-c", script], cwd=str(ROOT))
        update_status(job["uuid"], "applied", notes="Approved via dashboard")
    except Exception as e:
        st.error(f"Could not launch browser: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# Page: ⚙️ Settings
# ═════════════════════════════════════════════════════════════════════════════
def page_settings():
    st.title("⚙️ Settings")
    st.caption("Saved to `autoapply/dashboard/dashboard_settings.json` and applied immediately.")

    s = dict(st.session_state.settings)

    # ── API Key ──────────────────────────────────────────────────────────────
    with st.expander("🔑 API Configuration", expanded=True):
        api_key = st.text_input(
            "Anthropic API Key", value=s.get("anthropic_api_key", ""),
            type="password", placeholder="sk-ant-api03-…",
            help="Your Anthropic API key — used for resume tailoring and page classification.",
        )
        s["anthropic_api_key"] = api_key

        if st.button("🧪 Test Anthropic API Key"):
            _test_anthropic_key(api_key)

    # ── Candidate Profile ────────────────────────────────────────────────────
    with st.expander("👤 Candidate Profile", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            s["first_name"]  = st.text_input("First Name",  value=s.get("first_name", ""))
            s["last_name"]   = st.text_input("Last Name",   value=s.get("last_name", ""))
            s["email"]       = st.text_input("Email",       value=s.get("email", ""))
            s["phone"]       = st.text_input("Phone",       value=s.get("phone", ""), placeholder="+1-555-000-0000")
            s["linkedin_url"]= st.text_input("LinkedIn URL",value=s.get("linkedin_url", ""))
        with c2:
            s["github_url"]       = st.text_input("GitHub URL",        value=s.get("github_url", ""))
            s["portfolio_url"]    = st.text_input("Portfolio URL",      value=s.get("portfolio_url", ""))
            s["salary_range"]     = st.text_input("Salary Range",       value=s.get("salary_range", "120000-160000"))
            s["available_start_date"] = st.text_input("Available Start", value=s.get("available_start_date", "Immediately"))
            s["graduation_date"]  = st.text_input("Graduation Date",    value=s.get("graduation_date", "May 2026"))

        wa_opts = ["OPT - Requires Sponsorship", "H1B", "Green Card", "US Citizen", "Other"]
        wa_val  = s.get("work_authorization", wa_opts[0])
        wc1, wc2 = st.columns(2)
        with wc1:
            s["work_authorization"] = st.selectbox(
                "Work Authorization", wa_opts,
                index=wa_opts.index(wa_val) if wa_val in wa_opts else 0,
            )
        with wc2:
            s["visa_sponsorship"] = st.selectbox(
                "Visa Sponsorship Required", ["Yes", "No"],
                index=0 if s.get("visa_sponsorship", "Yes") == "Yes" else 1,
            )

        s["cover_letter"] = st.text_area(
            "Cover Letter (optional)", value=s.get("cover_letter", ""), height=100,
        )

        st.divider()
        st.markdown(f"**Current master resume:** `{s.get('master_resume_path', 'input/master_resume.tex')}`")
        uploaded = st.file_uploader("Upload new master resume (.tex)", type=["tex"])
        if uploaded:
            dest = ROOT / "input" / "master_resume.tex"
            dest.parent.mkdir(exist_ok=True)
            dest.write_bytes(uploaded.read())
            s["master_resume_path"] = "input/master_resume.tex"
            st.success(f"Saved to `{dest}`")

    # ── Job Search ───────────────────────────────────────────────────────────
    with st.expander("🔍 Job Search & Discovery", expanded=False):
        queries_raw = st.text_area(
            "Search Queries (one per line)",
            value="\n".join(s.get("search_queries", [])), height=180,
        )
        s["search_queries"] = [q.strip() for q in queries_raw.splitlines() if q.strip()]

        s["job_boards"] = st.multiselect(
            "Job Boards to Search",
            ["greenhouse", "lever", "linkedin", "indeed"],
            default=s.get("job_boards", ["greenhouse", "lever", "linkedin", "indeed"]),
        )

    # ── Pipeline ─────────────────────────────────────────────────────────────
    with st.expander("⚙️ Pipeline Settings", expanded=False):
        s["max_apps_per_run"] = st.number_input(
            "Max Applications Per Run", min_value=1, max_value=100,
            value=int(s.get("max_apps_per_run", 25)),
        )
        s["auto_submit_ats"] = st.multiselect(
            "Auto-Submit ATS (others queue for review)",
            ["greenhouse", "lever"],
            default=s.get("auto_submit_ats", ["greenhouse", "lever"]),
        )
        dc1, dc2 = st.columns(2)
        with dc1:
            s["apply_delay_min"] = st.slider("Min delay between apps (s)", 0, 120, int(s.get("apply_delay_min", 30)))
        with dc2:
            s["apply_delay_max"] = st.slider("Max delay between apps (s)", 0, 120, int(s.get("apply_delay_max", 90)))
        s["semantic_dedup_threshold"] = st.slider(
            "Semantic Dedup Threshold", 0.80, 1.00, float(s.get("semantic_dedup_threshold", 0.95)), step=0.01,
        )

    # ── Models ───────────────────────────────────────────────────────────────
    with st.expander("🧠 AI Models", expanded=False):
        MODEL_OPTS = [
            "claude-haiku-4-20250514", "claude-haiku-4-5-20251001",
            "claude-sonnet-4-20250514", "claude-sonnet-4-5-20250929",
        ]
        def _midx(key, default):
            v = s.get(key, default)
            return MODEL_OPTS.index(v) if v in MODEL_OPTS else 0

        s["classifier_model"] = st.selectbox(
            "Classifier Model (fast/cheap — HTML analysis)",
            MODEL_OPTS, index=_midx("classifier_model", MODEL_OPTS[0]),
        )
        s["tailor_model"] = st.selectbox(
            "Tailor Model (best quality — resume tailoring)",
            MODEL_OPTS, index=_midx("tailor_model", MODEL_OPTS[2]),
        )

    # ── Scheduler ────────────────────────────────────────────────────────────
    with st.expander("⏰ Nightly Scheduler", expanded=False):
        sc1, sc2 = st.columns(2)
        with sc1:
            s["pipeline_cron_hour"]   = st.number_input("Hour (0–23)",   0, 23, int(s.get("pipeline_cron_hour",   21)))
        with sc2:
            s["pipeline_cron_minute"] = st.number_input("Minute (0–59)", 0, 59, int(s.get("pipeline_cron_minute",  0)))
        h, m = s["pipeline_cron_hour"], s["pipeline_cron_minute"]
        ampm = "AM" if h < 12 else "PM"
        dh   = (h % 12) or 12
        st.caption(f"Pipeline will run at **{dh}:{m:02d} {ampm}** local time.")

    st.divider()
    if st.button("💾 Save Settings", type="primary", use_container_width=True):
        settings_store.save(s)
        st.session_state.settings = s
        st.success("✅ Settings saved and applied!")
        st.balloons()


def _test_anthropic_key(api_key: str):
    if not api_key:
        st.error("Enter an API key first.")
        return
    with st.spinner("Testing…"):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            models  = client.models.list()
            names   = [m.id for m in models.data[:3]] if hasattr(models, "data") else []
            st.success(f"✅ Valid! Models available: {', '.join(names)}")
        except Exception as e:
            st.error(f"❌ {e}")


# ═════════════════════════════════════════════════════════════════════════════
# Page: 📁 All Jobs
# ═════════════════════════════════════════════════════════════════════════════
def page_all_jobs():
    st.title("📁 All Jobs")

    try:
        import pandas as pd
    except ImportError:
        st.error("pandas required — run: pip install pandas")
        return

    try:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM jobs ORDER BY date_discovered DESC").fetchall()
        conn.close()
    except Exception as e:
        st.error(f"Database error: {e}")
        return

    if not rows:
        st.info("No jobs yet. Run the pipeline to discover jobs.")
        return

    df = pd.DataFrame([dict(r) for r in rows])

    # Filters
    f1, f2, f3 = st.columns(3)
    with f1:
        status_filter = st.multiselect("Filter by Status", sorted(df["status"].dropna().unique().tolist()))
    with f2:
        ats_filter = st.multiselect("Filter by ATS", sorted(df["ats_platform"].dropna().unique().tolist()))
    with f3:
        search = st.text_input("Search company / title", placeholder="e.g. Google")

    filtered = df.copy()
    if status_filter:
        filtered = filtered[filtered["status"].isin(status_filter)]
    if ats_filter:
        filtered = filtered[filtered["ats_platform"].isin(ats_filter)]
    if search:
        mask = (
            filtered["company_name"].str.contains(search, case=False, na=False)
            | filtered["job_title"].str.contains(search, case=False, na=False)
        )
        filtered = filtered[mask]

    st.markdown(f"**{len(filtered)} of {len(df)} jobs**")

    COLS = ["company_name", "job_title", "ats_platform", "status",
            "date_discovered", "date_applied", "job_url"]
    COLS = [c for c in COLS if c in filtered.columns]
    RENAME = {"company_name": "Company", "job_title": "Title", "ats_platform": "ATS",
               "status": "Status", "date_discovered": "Discovered",
               "date_applied": "Applied", "job_url": "URL"}
    display = filtered[COLS].rename(columns=RENAME)

    event = st.dataframe(
        display, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
    )

    # Row detail panel
    sel = event.selection.rows if hasattr(event, "selection") else []
    if sel:
        row = filtered.iloc[sel[0]]
        st.divider()
        st.subheader(f"{row.get('company_name','')} — {row.get('job_title','')}")
        d1, d2 = st.columns(2)
        with d1:
            url = row.get("job_url", "")
            st.markdown(f"**URL:** [{url}]({url})")
            st.markdown(f"**ATS:** {row.get('ats_platform','')}")
            st.markdown(f"**Status:** `{row.get('status','')}`")
            st.markdown(f"**Discovered:** {row.get('date_discovered','')}")
            st.markdown(f"**Applied:** {row.get('date_applied','') or 'N/A'}")
            if row.get("notes"):
                st.markdown(f"**Notes:** {row['notes']}")
        with d2:
            if row.get("job_description"):
                with st.expander("Full Job Description"):
                    st.text(row["job_description"][:3000])

    st.divider()
    a1, a2 = st.columns(2)
    with a1:
        st.download_button(
            "⬇ Export CSV", data=filtered.to_csv(index=False),
            file_name="autoapply_jobs.csv", mime="text/csv",
            use_container_width=True,
        )
    with a2:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# Router
# ═════════════════════════════════════════════════════════════════════════════
_PAGES = {
    "📊 Dashboard":    page_dashboard,
    "🚀 Run Pipeline": page_run_pipeline,
    "📋 Job Queue":    page_job_queue,
    "⚙️ Settings":     page_settings,
    "📁 All Jobs":     page_all_jobs,
}
_PAGES[st.session_state.page]()
