"""Tests for Module 4: Resume Generation (jd_analyzer, rag_tailor, pdf_export, dedup caching)."""

import asyncio
import hashlib
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ────────────────────────────────────────────────────────────────────────────
# JD Analyzer
# ────────────────────────────────────────────────────────────────────────────

SAMPLE_JD = """
We are looking for a Senior Security Engineer to join our team.
Required: Python, AWS, Docker, Kubernetes, CI/CD, OAuth, JWT.
Preferred: Go, Terraform, GCP.
You will lead cross-functional security reviews and penetration testing.
5+ years of experience required.
"""


class TestJdAnalyzer:
    def test_analyze_jd_returns_dict(self):
        from autoapply.resume.jd_analyzer import analyze_jd
        import autoapply.resume.jd_analyzer as mod
        orig = mod.ANTHROPIC_API_KEY
        mod.ANTHROPIC_API_KEY = ""   # disable Claude, use regex only
        try:
            result = analyze_jd(SAMPLE_JD)
        finally:
            mod.ANTHROPIC_API_KEY = orig

        assert isinstance(result, dict)
        assert "keywords" in result
        assert "keyword_profile" in result

    def test_keywords_include_python(self):
        from autoapply.resume.jd_analyzer import analyze_jd
        import autoapply.resume.jd_analyzer as mod
        orig = mod.ANTHROPIC_API_KEY
        mod.ANTHROPIC_API_KEY = ""
        try:
            result = analyze_jd(SAMPLE_JD)
        finally:
            mod.ANTHROPIC_API_KEY = orig
        kws = [k.lower() for k in result["keywords"]]
        assert any("python" in k for k in kws)

    def test_keywords_include_aws(self):
        from autoapply.resume.jd_analyzer import analyze_jd
        import autoapply.resume.jd_analyzer as mod
        orig = mod.ANTHROPIC_API_KEY
        mod.ANTHROPIC_API_KEY = ""
        try:
            result = analyze_jd(SAMPLE_JD)
        finally:
            mod.ANTHROPIC_API_KEY = orig
        kws = [k.lower() for k in result["keywords"]]
        assert any("aws" in k for k in kws)

    def test_empty_jd_does_not_crash(self):
        from autoapply.resume.jd_analyzer import analyze_jd
        import autoapply.resume.jd_analyzer as mod
        orig = mod.ANTHROPIC_API_KEY
        mod.ANTHROPIC_API_KEY = ""
        try:
            result = analyze_jd("")
        finally:
            mod.ANTHROPIC_API_KEY = orig
        assert isinstance(result, dict)

    def test_claude_failure_returns_empty_structured(self):
        from autoapply.resume.jd_analyzer import _claude_analyze
        import autoapply.resume.jd_analyzer as mod
        orig = mod.ANTHROPIC_API_KEY
        mod.ANTHROPIC_API_KEY = "fake"
        try:
            with patch("autoapply.resume.jd_analyzer.anthropic") as m:
                m.Anthropic.return_value.messages.create.side_effect = Exception("API down")
                result = _claude_analyze(SAMPLE_JD)
        finally:
            mod.ANTHROPIC_API_KEY = orig
        assert result == {}

    def test_claude_result_merged_into_analysis(self):
        from autoapply.resume.jd_analyzer import analyze_jd
        import autoapply.resume.jd_analyzer as mod
        orig = mod.ANTHROPIC_API_KEY
        mod.ANTHROPIC_API_KEY = "fake"
        try:
            with patch("autoapply.resume.jd_analyzer._claude_analyze") as m:
                m.return_value = {
                    "role_title": "Security Engineer",
                    "company": "Acme",
                    "seniority": "senior",
                    "domain": "AppSec",
                    "top_required_skills": ["Python", "AWS"],
                    "top_preferred_skills": ["Go"],
                }
                result = analyze_jd(SAMPLE_JD)
        finally:
            mod.ANTHROPIC_API_KEY = orig
        assert result["role_title"] == "Security Engineer"
        assert result["seniority"] == "senior"
        assert "Python" in result["top_required_skills"]


# ────────────────────────────────────────────────────────────────────────────
# RAG Tailor – caching logic
# ────────────────────────────────────────────────────────────────────────────

SAMPLE_LATEX = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading{Software Engineer}{Jan 2022 - Present}{Acme Corp}{NYC}
\resumeItem{Built web scraper using Python}
\end{document}
"""


class TestRagTailor:
    @pytest.mark.asyncio
    async def test_tailor_calls_ai_tailor(self):
        import autoapply.resume.rag_tailor as mod
        orig_output = mod.RESUME_OUTPUT_DIR

        with tempfile.TemporaryDirectory() as tmpdir:
            mod.RESUME_OUTPUT_DIR = tmpdir

            # Write a fake master resume
            resume_path = os.path.join(tmpdir, "master.tex")
            Path(resume_path).write_text(SAMPLE_LATEX, encoding="utf-8")

            # Mock the AIResumeTailorAsync
            mock_result = MagicMock()
            mock_result.tailored_latex = SAMPLE_LATEX
            mock_result.keyword_coverage = 85.0

            with patch("autoapply.resume.rag_tailor.AIResumeTailorAsync") as MockTailor:
                instance = MockTailor.return_value
                instance.tailor = AsyncMock(return_value=mock_result)

                with patch("autoapply.resume.rag_tailor.compile_pdf",
                           new=AsyncMock(return_value=os.path.join(tmpdir, "out.pdf"))):
                    tex, pdf, result = await mod.tailor_resume_for_job(
                        jd_text=SAMPLE_JD,
                        job_uuid="test-uuid-001",
                        master_resume_path=resume_path,
                        use_cache=False,
                    )

            assert tex.endswith(".tex")
            assert result is mock_result
            assert Path(tex).exists()

        mod.RESUME_OUTPUT_DIR = orig_output

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self):
        import autoapply.resume.rag_tailor as mod
        orig_output = mod.RESUME_OUTPUT_DIR

        with tempfile.TemporaryDirectory() as tmpdir:
            mod.RESUME_OUTPUT_DIR = tmpdir

            resume_path = os.path.join(tmpdir, "master.tex")
            Path(resume_path).write_text(SAMPLE_LATEX, encoding="utf-8")

            # Pre-populate the cache
            from autoapply.resume.rag_tailor import _jd_cache_key, _cache_path
            cache_key = _jd_cache_key(SAMPLE_JD, resume_path)
            cached = _cache_path(cache_key)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(SAMPLE_LATEX, encoding="utf-8")

            with patch("autoapply.resume.rag_tailor.AIResumeTailorAsync") as MockTailor:
                with patch("autoapply.resume.rag_tailor.compile_pdf",
                           new=AsyncMock(return_value=os.path.join(tmpdir, "cached.pdf"))):
                    tex, pdf, result = await mod.tailor_resume_for_job(
                        jd_text=SAMPLE_JD,
                        job_uuid="cached-uuid",
                        master_resume_path=resume_path,
                        use_cache=True,
                    )
                MockTailor.assert_not_called()  # cache hit → no API call

            assert result is None   # cache hit returns None for result

        mod.RESUME_OUTPUT_DIR = orig_output

    @pytest.mark.asyncio
    async def test_missing_resume_raises(self):
        import autoapply.resume.rag_tailor as mod
        with pytest.raises(FileNotFoundError):
            await mod.tailor_resume_for_job(
                jd_text=SAMPLE_JD,
                job_uuid="err-uuid",
                master_resume_path="/nonexistent/path/resume.tex",
                use_cache=False,
            )

    @pytest.mark.asyncio
    async def test_empty_tailored_latex_raises(self):
        import autoapply.resume.rag_tailor as mod
        orig_output = mod.RESUME_OUTPUT_DIR

        with tempfile.TemporaryDirectory() as tmpdir:
            mod.RESUME_OUTPUT_DIR = tmpdir
            resume_path = os.path.join(tmpdir, "master.tex")
            Path(resume_path).write_text(SAMPLE_LATEX, encoding="utf-8")

            mock_result = MagicMock()
            mock_result.tailored_latex = ""   # empty → should raise

            with patch("autoapply.resume.rag_tailor.AIResumeTailorAsync") as MockTailor:
                instance = MockTailor.return_value
                instance.tailor = AsyncMock(return_value=mock_result)
                with pytest.raises(RuntimeError, match="empty"):
                    await mod.tailor_resume_for_job(
                        jd_text=SAMPLE_JD, job_uuid="err2",
                        master_resume_path=resume_path, use_cache=False,
                    )

        mod.RESUME_OUTPUT_DIR = orig_output


# ────────────────────────────────────────────────────────────────────────────
# PDF Export
# ────────────────────────────────────────────────────────────────────────────

class TestPdfExport:
    @pytest.mark.asyncio
    async def test_compile_returns_pdf_path_on_online_success(self):
        from autoapply.resume.pdf_export import compile_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "resume.tex")
            Path(tex_path).write_text(SAMPLE_LATEX, encoding="utf-8")

            fake_pdf_bytes = b"%PDF-1.4 fake content"

            with patch("autoapply.resume.pdf_export._pdflatex_available", return_value=False):
                with patch("autoapply.resume.pdf_export._compile_online",
                           new=AsyncMock(return_value=fake_pdf_bytes)):
                    result = await compile_pdf(tex_path)

            assert result.endswith(".pdf")
            assert Path(result).exists()
            assert Path(result).read_bytes() == fake_pdf_bytes

    @pytest.mark.asyncio
    async def test_compile_returns_empty_on_total_failure(self):
        from autoapply.resume.pdf_export import compile_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "resume.tex")
            Path(tex_path).write_text(SAMPLE_LATEX, encoding="utf-8")

            with patch("autoapply.resume.pdf_export._pdflatex_available", return_value=False):
                with patch("autoapply.resume.pdf_export._compile_online",
                           new=AsyncMock(return_value=None)):
                    result = await compile_pdf(tex_path)

            assert result == ""

    @pytest.mark.asyncio
    async def test_pdflatex_tried_first(self):
        from autoapply.resume.pdf_export import compile_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "resume.tex")
            Path(tex_path).write_text(SAMPLE_LATEX, encoding="utf-8")
            pdf_path = tex_path.replace(".tex", ".pdf")
            Path(pdf_path).write_bytes(b"%PDF fake")

            with patch("autoapply.resume.pdf_export._pdflatex_available", return_value=True):
                with patch("autoapply.resume.pdf_export._compile_local",
                           new=AsyncMock(return_value=True)) as mock_local:
                    result = await compile_pdf(tex_path)

            mock_local.assert_called_once()
            assert result.endswith(".pdf")
