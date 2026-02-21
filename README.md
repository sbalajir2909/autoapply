# AutoApply

Autonomous job application pipeline with ATS-optimized resume builder.

## What it does

1. **Discovers** jobs on Greenhouse, Lever, LinkedIn, and Indeed using Playwright
2. **Deduplicates** via UUID hashing + ChromaDB semantic similarity
3. **Tailors** your LaTeX resume per job description using Claude AI
4. **Applies** automatically on Greenhouse and Lever; queues the rest for your review
5. **Dashboard** (Streamlit) lets you approve/reject queued applications

## Modules

```
src/           ATS resume builder (from Samyuktha's repo — unchanged)
web/           FastAPI web UI for the resume builder
autoapply/
├── config.py              Candidate profile + settings
├── pipeline.py            Main orchestrator
├── discovery/             Playwright scrapers (Greenhouse, Lever, LinkedIn, Indeed, DuckDuckGo)
├── storage/               SQLite + ChromaDB deduplication
├── apply/                 Page classifier, navigator, form filler, ATS strategies
├── resume/                JD analyzer + resume tailoring wrapper
├── dashboard/             Streamlit review UI
└── reports/               Daily stats
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Edit your profile
vim autoapply/config.py   # fill in CANDIDATE_PROFILE
```

## Run

```bash
# Single dry run (fills forms, no submissions)
python -m autoapply.pipeline --dry-run

# Live run (auto-submits Greenhouse/Lever, queues the rest)
python -m autoapply.pipeline

# Nightly scheduler
python -m autoapply.pipeline --schedule

# Review dashboard
streamlit run autoapply/dashboard/app.py

# Web UI (resume builder only)
uvicorn web.app:app --reload
```

## Auto-submit vs. Dashboard queue

| ATS | Behavior |
|-----|----------|
| Greenhouse | Auto-filled + auto-submitted |
| Lever | Auto-filled + auto-submitted |
| Workday | Filled, queued for dashboard approval |
| iCIMS / Unknown | Filled, queued for dashboard approval |

## Credit

ATS resume builder (`src/`, `web/`) by [Samyuktha](https://github.com/udsy19/samyuktha-job-apply).
