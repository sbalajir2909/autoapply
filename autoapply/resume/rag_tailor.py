"""
RAG-powered resume tailoring.

Wraps src/ai_tailor.py (AIResumeTailorAsync) and adds:
    - JD-hash-based caching so the same JD never triggers two API calls
    - Automatic output directory management
    - Returns paths to both .tex and .pdf outputs
"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional, Tuple, Dict

# Allow importing from parent src/ package
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.ai_tailor import AIResumeTailorAsync, TailoringResult
from .pdf_export import compile_pdf
from ..config import (
    ANTHROPIC_API_KEY,
    CANDIDATE_PROFILE,
    RESUME_OUTPUT_DIR,
)


def _jd_cache_key(jd_text: str, resume_path: str) -> str:
    """Deterministic cache key from JD + resume content."""
    resume_content = Path(resume_path).read_text(encoding="utf-8") if Path(resume_path).exists() else ""
    raw = jd_text[:1000] + resume_content[:500]
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_path(cache_key: str) -> Path:
    """Path to cached tailored .tex file."""
    cache_dir = Path(RESUME_OUTPUT_DIR) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{cache_key}.tex"


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

    Args:
        jd_text:              Full job description text.
        job_uuid:             UUID of the job (used for output filename).
        master_resume_path:   Path to master .tex resume. Defaults to config value.
        max_experiences:      Max experience entries in tailored resume.
        max_bullets:          Max bullet points per experience.
        use_cache:            Skip API call if this JD+resume combo was already processed.

    Returns:
        Tuple of (tex_path, pdf_path, TailoringResult)
    """
    resume_path = master_resume_path or CANDIDATE_PROFILE["master_resume_path"]
    output_dir = Path(RESUME_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check cache
    cache_key = _jd_cache_key(jd_text, resume_path)
    cached_tex = _cache_path(cache_key)

    if use_cache and cached_tex.exists():
        print(f"[ResumeGen] Cache hit for {cache_key[:8]}...")
        tex_path = str(output_dir / f"{job_uuid}.tex")
        Path(tex_path).write_text(cached_tex.read_text(encoding="utf-8"), encoding="utf-8")
        pdf_path = await compile_pdf(tex_path)
        # Return a minimal result for cached output
        return tex_path, pdf_path, None

    # Load master resume
    if not Path(resume_path).exists():
        raise FileNotFoundError(f"Master resume not found: {resume_path}")
    resume_latex = Path(resume_path).read_text(encoding="utf-8")

    # Call the AI tailor
    tailor = AIResumeTailorAsync(api_key=ANTHROPIC_API_KEY)
    result: TailoringResult = await tailor.tailor(
        resume_latex=resume_latex,
        job_description=jd_text,
        max_experiences=max_experiences,
        max_bullets=max_bullets,
    )

    if not result or not result.tailored_latex:
        raise RuntimeError("AI tailoring returned empty result")

    # Save outputs
    tex_path = str(output_dir / f"{job_uuid}.tex")
    Path(tex_path).write_text(result.tailored_latex, encoding="utf-8")

    # Cache for reuse
    cached_tex.write_text(result.tailored_latex, encoding="utf-8")

    # Compile PDF
    pdf_path = await compile_pdf(tex_path)

    print(
        f"[ResumeGen] Tailored resume saved: {tex_path} "
        f"(coverage: {result.keyword_coverage}%)"
    )

    return tex_path, pdf_path, result
