"""
RAG-powered resume tailoring.

Wraps src/ai_tailor.py (AIResumeTailorAsync) and adds:
    - JD-hash-based caching so the same JD never triggers two API calls
    - Automatic output directory management
    - Returns paths to both .html and .pdf outputs
    - Uses HTML/CSS pipeline instead of LaTeX for PDF generation
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Optional, Tuple

# Allow importing from parent src/ package
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.ai_tailor import AIResumeTailorAsync, TailoringResult
from .html_template import render_resume_html
from .html_to_pdf import html_to_pdf
from ..config import (
    ANTHROPIC_API_KEY,
    CANDIDATE_PROFILE,
    RESUME_OUTPUT_DIR,
)
from ..events import emit, EventType


def _jd_cache_key(jd_text: str, resume_path: str) -> str:
    """Deterministic cache key from JD + resume content."""
    resume_content = Path(resume_path).read_text(encoding="utf-8") if Path(resume_path).exists() else ""
    raw = jd_text[:1000] + resume_content[:500]
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_path(cache_key: str) -> Path:
    """Path to cached tailored JSON file."""
    cache_dir = Path(RESUME_OUTPUT_DIR) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{cache_key}.json"


async def tailor_resume_for_job(
    jd_text: str,
    job_uuid: str,
    master_resume_path: Optional[str] = None,
    max_experiences: int = 4,
    max_bullets: int = 4,
    use_cache: bool = True,
) -> Tuple[str, str, TailoringResult]:
    """
    Tailor the master resume for a specific job description.

    Uses the JSON + HTML/CSS pipeline:
        1. LLM outputs structured JSON (resume_data)
        2. HTML template renders it
        3. Playwright converts HTML → PDF

    Args:
        jd_text:              Full job description text.
        job_uuid:             UUID of the job (used for output filename).
        master_resume_path:   Path to master .tex resume. Defaults to config value.
        max_experiences:      Max experience entries in tailored resume.
        max_bullets:          Max bullet points per experience.
        use_cache:            Skip API call if this JD+resume combo was already processed.

    Returns:
        Tuple of (html_path, pdf_path, TailoringResult)
    """
    resume_path = master_resume_path or CANDIDATE_PROFILE["master_resume_path"]
    output_dir = Path(RESUME_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check cache
    cache_key = _jd_cache_key(jd_text, resume_path)
    cached_json = _cache_path(cache_key)

    if use_cache and cached_json.exists():
        emit(EventType.RESUME_CACHE_HIT,
             f"Cache hit for JD hash {cache_key[:8]}... — skipping AI call",
             cache_key=cache_key[:8], job_uuid=job_uuid)
        print(f"[ResumeGen] Cache hit for {cache_key[:8]}...")

        resume_data = json.loads(cached_json.read_text(encoding="utf-8"))
        html_str = render_resume_html(resume_data)

        html_path = str(output_dir / f"{job_uuid}.html")
        Path(html_path).write_text(html_str, encoding="utf-8")

        emit(EventType.RESUME_PDF_COMPILE,
             "Rendering cached resume to PDF...",
             job_uuid=job_uuid)
        pdf_path = await html_to_pdf(html_str, str(output_dir / f"{job_uuid}.pdf"))
        return html_path, pdf_path, None

    # Load master resume
    if not Path(resume_path).exists():
        raise FileNotFoundError(f"Master resume not found: {resume_path}")
    resume_latex = Path(resume_path).read_text(encoding="utf-8")

    emit(EventType.RESUME_AI_CALL,
         f"Calling AI tailor (JD: {len(jd_text)} chars, Resume: {len(resume_latex)} chars)...",
         jd_length=len(jd_text), resume_length=len(resume_latex),
         job_uuid=job_uuid)

    # Call the AI tailor
    tailor = AIResumeTailorAsync(api_key=ANTHROPIC_API_KEY)
    result: TailoringResult = await tailor.tailor(
        resume_latex=resume_latex,
        job_description=jd_text,
        max_experiences=max_experiences,
        max_bullets=max_bullets,
    )

    if not result or not result.resume_data:
        raise RuntimeError("AI tailoring returned empty result")

    # Cache the JSON for reuse
    cached_json.write_text(json.dumps(result.resume_data, indent=2), encoding="utf-8")

    # Render HTML
    html_str = render_resume_html(result.resume_data)
    html_path = str(output_dir / f"{job_uuid}.html")
    Path(html_path).write_text(html_str, encoding="utf-8")

    # Render PDF via Playwright
    emit(EventType.RESUME_PDF_COMPILE,
         "Rendering tailored resume to PDF...",
         job_uuid=job_uuid)
    pdf_path = await html_to_pdf(html_str, str(output_dir / f"{job_uuid}.pdf"))

    emit(EventType.RESUME_TAILOR_END,
         f"Resume tailored — coverage: {result.keyword_coverage}%, "
         f"matched: {len(result.matched_keywords)}, missing: {len(result.missing_keywords)}",
         keyword_coverage=result.keyword_coverage,
         matched_keywords=result.matched_keywords,
         missing_keywords=result.missing_keywords,
         html_path=html_path, pdf_path=pdf_path or "",
         job_uuid=job_uuid)

    print(
        f"[ResumeGen] Tailored resume saved: {html_path} → {pdf_path or '(no PDF)'} "
        f"(coverage: {result.keyword_coverage}%)"
    )

    return html_path, pdf_path, result
