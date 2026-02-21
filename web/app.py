"""
FastAPI web application for ATS Resume Builder with AI-powered tailoring.
Full-featured version with streaming progress, validation, URL import, exports.
"""

import sys
import os
import re
import subprocess
import tempfile
import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Form, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
import traceback

app = FastAPI(title="ATS Resume Builder", version="3.0.0")

static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# === Models ===

class TailorRequest(BaseModel):
    resume_content: str
    job_description: str
    max_experiences: int = 4
    max_bullets: int = 4


class URLImportRequest(BaseModel):
    url: str


class EditBulletRequest(BaseModel):
    latex_content: str
    old_bullet: str
    new_bullet: str


# === Routes ===

@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = static_path / "index.html"
    return HTMLResponse(content=html_path.read_text())


@app.get("/api/health")
async def health_check():
    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "status": "healthy",
        "version": "3.0.0",
        "ai_enabled": api_key_set,
        "features": ["multi_pass", "validation", "url_import", "diff_view", "export", "streaming"]
    }


@app.post("/api/tailor/stream")
async def tailor_resume_stream(request: TailorRequest):
    """Streaming AI tailoring with real-time progress updates via SSE."""

    async def generate_events():
        try:
            from src.ai_tailor import AIResumeTailorAsync

            tailor = AIResumeTailorAsync()

            async for update in tailor.tailor_with_progress(
                resume_latex=request.resume_content,
                job_description=request.job_description,
                max_experiences=request.max_experiences,
                max_bullets=request.max_bullets,
                max_passes=3
            ):
                if update.get("step") == "result":
                    # Send final result
                    result = update["result"]
                    validation_data = {
                        "is_valid": result.validation.is_valid,
                        "total_bullets": result.validation.total_bullets,
                        "bullets_wrong_length": result.validation.bullets_wrong_length,
                        "bullets_no_quantification": result.validation.bullets_no_quantification,
                        "verb_counts": result.validation.verb_counts,
                        "repeated_verbs": result.validation.repeated_verbs,
                        "bullet_analyses": [
                            {
                                "text": b.text[:100] + "..." if len(b.text) > 100 else b.text,
                                "word_count": b.word_count,
                                "action_verb": b.action_verb,
                                "has_quantification": b.has_quantification,
                                "is_valid_length": b.is_valid_length,
                                "keywords_found": b.keywords_found
                            }
                            for b in result.validation.bullet_analyses
                        ]
                    }

                    final_data = {
                        "step": "result",
                        "tailored_resume": result.tailored_latex,
                        "original_resume": result.original_latex,
                        "keyword_coverage": result.keyword_coverage,
                        "matched_keywords": result.matched_keywords,
                        "missing_keywords": result.missing_keywords,
                        "suggestions": result.suggestions,
                        "validation": validation_data,
                        "bullet_suggestions": result.bullet_suggestions,
                        "diff_data": result.diff_data,
                        "passes_completed": result.passes_completed,
                        "job_analysis": result.match_analysis
                    }
                    yield f"data: {json.dumps(final_data)}\n\n"
                else:
                    # Send progress update
                    yield f"data: {json.dumps(update)}\n\n"

        except ValueError as e:
            if "ANTHROPIC_API_KEY" in str(e):
                yield f"data: {json.dumps({'step': 'error', 'message': 'ANTHROPIC_API_KEY not set'})}\n\n"
            else:
                yield f"data: {json.dumps({'step': 'error', 'message': str(e)})}\n\n"
        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'step': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/api/tailor")
async def tailor_resume(request: TailorRequest):
    """Full AI tailoring with multi-pass validation (non-streaming)."""
    try:
        from src.ai_tailor import AIResumeTailorAsync

        tailor = AIResumeTailorAsync()
        result = await tailor.tailor(
            resume_latex=request.resume_content,
            job_description=request.job_description,
            max_experiences=request.max_experiences,
            max_bullets=request.max_bullets,
            max_passes=3
        )

        # Convert validation to dict
        validation_data = {
            "is_valid": result.validation.is_valid,
            "total_bullets": result.validation.total_bullets,
            "bullets_wrong_length": result.validation.bullets_wrong_length,
            "bullets_no_quantification": result.validation.bullets_no_quantification,
            "verb_counts": result.validation.verb_counts,
            "repeated_verbs": result.validation.repeated_verbs,
            "bullet_analyses": [
                {
                    "text": b.text[:100] + "..." if len(b.text) > 100 else b.text,
                    "word_count": b.word_count,
                    "action_verb": b.action_verb,
                    "has_quantification": b.has_quantification,
                    "is_valid_length": b.is_valid_length,
                    "keywords_found": b.keywords_found
                }
                for b in result.validation.bullet_analyses
            ]
        }

        return JSONResponse({
            "tailored_resume": result.tailored_latex,
            "original_resume": result.original_latex,
            "keyword_coverage": result.keyword_coverage,
            "matched_keywords": result.matched_keywords,
            "missing_keywords": result.missing_keywords,
            "suggestions": result.suggestions,
            "validation": validation_data,
            "bullet_suggestions": result.bullet_suggestions,
            "diff_data": result.diff_data,
            "passes_completed": result.passes_completed,
            "job_analysis": result.match_analysis
        })

    except ValueError as e:
        if "ANTHROPIC_API_KEY" in str(e):
            raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY not set")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze-job")
async def analyze_job(job_description: str = Form(...)):
    """Fast job analysis using Haiku for speed."""
    try:
        import anthropic

        client = anthropic.AsyncAnthropic()

        prompt = f"""Extract keywords from this job description for ATS optimization.

JOB DESCRIPTION:
{job_description}

Return JSON with these exact keys:
{{
  "role_title": "the job title",
  "company": "company name if mentioned",
  "required_skills": ["skill1", "skill2", ...],
  "preferred_skills": ["skill1", "skill2", ...],
  "key_technologies": ["tech1", "tech2", ...],
  "soft_skills": ["skill1", "skill2", ...],
  "action_verbs": ["verb1", "verb2", ...],
  "industry_terms": ["term1", "term2", ...],
  "key_responsibilities": ["resp1", "resp2", ...],
  "culture_keywords": ["keyword1", "keyword2", ...],
  "must_include_phrases": ["phrase1", "phrase2", ...]
}}

Return ONLY the JSON, no markdown or explanation."""

        response = await client.messages.create(
            model="claude-haiku-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text
        text = re.sub(r'```json\n?|```\n?', '', text).strip()

        try:
            analysis = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                analysis = json.loads(match.group())
            else:
                analysis = {}

        return JSONResponse({
            "role_title": analysis.get("role_title", ""),
            "company": analysis.get("company", ""),
            "required_skills": analysis.get("required_skills", []),
            "preferred_skills": analysis.get("preferred_skills", []),
            "key_technologies": analysis.get("key_technologies", []),
            "soft_skills": analysis.get("soft_skills", []),
            "action_verbs": analysis.get("action_verbs", []),
            "industry_terms": analysis.get("industry_terms", []),
            "key_responsibilities": analysis.get("key_responsibilities", []),
            "culture_keywords": analysis.get("culture_keywords", []),
            "must_include_phrases": analysis.get("must_include_phrases", [])
        })

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/import-url")
async def import_job_url(request: URLImportRequest):
    """Import job description from URL."""
    try:
        import httpx
        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            response = await client.get(request.url, headers=headers)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script and style elements
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()

        # Try to find job description content
        job_text = ""

        # LinkedIn
        if "linkedin.com" in request.url:
            desc = soup.find("div", {"class": "description__text"})
            if desc:
                job_text = desc.get_text(separator="\n", strip=True)

        # Greenhouse
        elif "greenhouse.io" in request.url or "boards.greenhouse" in request.url:
            content = soup.find("div", {"id": "content"})
            if content:
                job_text = content.get_text(separator="\n", strip=True)

        # Lever
        elif "lever.co" in request.url:
            content = soup.find("div", {"class": "section-wrapper"})
            if content:
                job_text = content.get_text(separator="\n", strip=True)

        # Generic fallback - look for main content
        if not job_text:
            # Try common content containers
            for selector in ["main", "article", '[role="main"]', ".job-description", "#job-description"]:
                content = soup.select_one(selector)
                if content:
                    job_text = content.get_text(separator="\n", strip=True)
                    break

        # Final fallback - body text
        if not job_text:
            body = soup.find("body")
            if body:
                job_text = body.get_text(separator="\n", strip=True)

        # Clean up text
        lines = [line.strip() for line in job_text.split("\n") if line.strip()]
        job_text = "\n".join(lines)

        # Truncate if too long
        if len(job_text) > 15000:
            job_text = job_text[:15000] + "\n\n[Truncated...]"

        return JSONResponse({
            "success": True,
            "job_description": job_text,
            "url": request.url
        })

    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {str(e)}")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/validate")
async def validate_resume(latex_content: str = Form(...), keywords: str = Form("")):
    """Validate resume against rules."""
    try:
        from src.ai_tailor import FastValidator

        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
        result = FastValidator.validate(latex_content, keyword_list)

        return JSONResponse({
            "is_valid": result.is_valid,
            "total_bullets": result.total_bullets,
            "bullets_wrong_length": result.bullets_wrong_length,
            "bullets_no_quantification": result.bullets_no_quantification,
            "verb_counts": result.verb_counts,
            "repeated_verbs": result.repeated_verbs,
            "keyword_coverage": result.keyword_coverage,
            "matched_keywords": result.matched_keywords,
            "missing_keywords": result.missing_keywords,
            "suggestions": result.suggestions,
            "bullet_details": [
                {
                    "text": b.text,
                    "word_count": b.word_count,
                    "action_verb": b.action_verb,
                    "has_quantification": b.has_quantification,
                    "is_valid_length": b.is_valid_length
                }
                for b in result.bullet_analyses
            ]
        })

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/edit-bullet")
async def edit_bullet(request: EditBulletRequest):
    """Edit a single bullet in the resume."""
    try:
        # Replace old bullet with new bullet
        new_latex = request.latex_content.replace(request.old_bullet, request.new_bullet)

        # Validate the change using FastValidator
        from src.ai_tailor import FastValidator

        # Validate and find the edited bullet
        result = FastValidator.validate(new_latex, [])

        # Find the edited bullet analysis
        edited_analysis = None
        for b in result.bullet_analyses:
            if request.new_bullet in b.text or b.text in request.new_bullet:
                edited_analysis = b
                break

        return JSONResponse({
            "success": True,
            "updated_latex": new_latex,
            "bullet_analysis": {
                "word_count": edited_analysis.word_count if edited_analysis else 0,
                "has_quantification": edited_analysis.has_quantification if edited_analysis else False,
                "is_valid_length": edited_analysis.is_valid_length if edited_analysis else False,
                "action_verb": edited_analysis.action_verb if edited_analysis else ""
            } if edited_analysis else None
        })

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/export/pdf")
async def export_pdf(latex_content: str = Form(...)):
    """Export resume as PDF using pdflatex or online compiler fallback."""
    import shutil

    # Check if pdflatex is available
    pdflatex_available = shutil.which("pdflatex") is not None

    if pdflatex_available:
        # Use local pdflatex
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tex_path = Path(tmpdir) / "resume.tex"
                pdf_path = Path(tmpdir) / "resume.pdf"

                tex_path.write_text(latex_content)

                result = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, str(tex_path)],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if pdf_path.exists():
                    return FileResponse(
                        pdf_path,
                        media_type="application/pdf",
                        filename="tailored_resume.pdf"
                    )
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"LaTeX compilation failed: {result.stderr[:500]}"
                    )

        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="LaTeX compilation timed out")
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Use online LaTeX compiler (latex.ytotech.com)
        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                # Use latex.ytotech.com API
                response = await client.post(
                    "https://latex.ytotech.com/builds/sync",
                    json={
                        "compiler": "pdflatex",
                        "resources": [
                            {
                                "main": True,
                                "content": latex_content
                            }
                        ]
                    }
                )

                if response.status_code == 200:
                    from fastapi.responses import Response
                    return Response(
                        content=response.content,
                        media_type="application/pdf",
                        headers={"Content-Disposition": "attachment; filename=tailored_resume.pdf"}
                    )
                else:
                    error_msg = response.text[:500] if response.text else "Unknown error"
                    raise HTTPException(
                        status_code=400,
                        detail=f"Online LaTeX compilation failed: {error_msg}"
                    )

        except httpx.TimeoutException:
            raise HTTPException(status_code=500, detail="Online LaTeX compilation timed out")
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"PDF compilation failed: {str(e)}")


@app.post("/api/export/tex")
async def export_tex(latex_content: str = Form(...)):
    """Export resume as .tex file for manual compilation."""
    from fastapi.responses import Response
    return Response(
        content=latex_content,
        media_type="application/x-tex",
        headers={"Content-Disposition": "attachment; filename=tailored_resume.tex"}
    )


@app.post("/api/export/txt")
async def export_txt(latex_content: str = Form(...)):
    """Export resume as plain text."""
    try:
        # Remove LaTeX commands and extract text
        text = latex_content

        # Remove preamble
        doc_start = text.find(r"\begin{document}")
        if doc_start != -1:
            text = text[doc_start:]

        # Remove common LaTeX commands
        text = re.sub(r"\\begin\{[^}]+\}", "", text)
        text = re.sub(r"\\end\{[^}]+\}", "", text)
        text = re.sub(r"\\textbf\{([^}]*)\}", r"\1", text)
        text = re.sub(r"\\textit\{([^}]*)\}", r"\1", text)
        text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)
        text = re.sub(r"\\section\{([^}]*)\}", r"\n\n=== \1 ===\n", text)
        text = re.sub(r"\\resumeSubheading\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}",
                      r"\n\1 | \2\n\3 | \4", text)
        text = re.sub(r"\\resumeItem\{([^}]*)\}", r"  - \1", text)
        text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
        text = re.sub(r"\\[a-zA-Z]+", "", text)
        text = re.sub(r"[{}$\\]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        return JSONResponse({
            "success": True,
            "content": text.strip(),
            "filename": "tailored_resume.txt"
        })

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/compile-check")
async def compile_check(latex_content: str = Form(...)):
    """Check if LaTeX compiles successfully."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = Path(tmpdir) / "resume.tex"
            tex_path.write_text(latex_content)

            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, str(tex_path)],
                capture_output=True,
                text=True,
                timeout=30
            )

            pdf_path = Path(tmpdir) / "resume.pdf"
            success = pdf_path.exists()

            # Check page count
            pages = 1
            if success:
                # Simple check for page count in log
                log_path = Path(tmpdir) / "resume.log"
                if log_path.exists():
                    log_content = log_path.read_text()
                    page_match = re.search(r"Output written on .+ \((\d+) page", log_content)
                    if page_match:
                        pages = int(page_match.group(1))

            return JSONResponse({
                "success": success,
                "pages": pages,
                "is_one_page": pages == 1,
                "errors": result.stderr[:1000] if not success else None
            })

    except subprocess.TimeoutExpired:
        return JSONResponse({"success": False, "errors": "Compilation timed out"})
    except FileNotFoundError:
        return JSONResponse({"success": False, "errors": "pdflatex not found"})
    except Exception as e:
        return JSONResponse({"success": False, "errors": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
