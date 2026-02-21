"""
PDF compilation from LaTeX source.

Tries pdflatex locally first; falls back to the online latex.ytotech.com API.
Mirrors the logic already used in web/app.py.
"""

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import httpx


async def compile_pdf(tex_path: str) -> str:
    """
    Compile a .tex file to PDF.

    Tries pdflatex first (requires a local LaTeX installation).
    Falls back to the ytotech online API.

    Args:
        tex_path: Absolute or relative path to the .tex file.

    Returns:
        Path to the compiled .pdf file, or empty string on failure.
    """
    tex_path = str(Path(tex_path).resolve())
    pdf_path = tex_path.replace(".tex", ".pdf")

    # Try local pdflatex
    if _pdflatex_available():
        success = await _compile_local(tex_path, pdf_path)
        if success:
            return pdf_path

    # Fallback: online compilation
    tex_content = Path(tex_path).read_text(encoding="utf-8")
    pdf_bytes = await _compile_online(tex_content)
    if pdf_bytes:
        Path(pdf_path).write_bytes(pdf_bytes)
        return pdf_path

    print(f"[PDFExport] Failed to compile {tex_path}")
    return ""


def _pdflatex_available() -> bool:
    """Check whether pdflatex is installed."""
    try:
        result = subprocess.run(
            ["pdflatex", "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


async def _compile_local(tex_path: str, pdf_path: str) -> bool:
    """Run pdflatex in a temp dir and copy the result."""
    tex_dir = str(Path(tex_path).parent)
    try:
        process = await asyncio.create_subprocess_exec(
            "pdflatex",
            "-interaction=nonstopmode",
            "-output-directory", tex_dir,
            tex_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), timeout=60)
        return Path(pdf_path).exists()
    except Exception as e:
        print(f"[PDFExport] pdflatex failed: {e}")
        return False


async def _compile_online(tex_content: str) -> Optional[bytes]:
    """Use ytotech API for online LaTeX→PDF compilation."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://latex.ytotech.com/builds/sync",
                json={
                    "compiler": "pdflatex",
                    "resources": [{"main": True, "content": tex_content}],
                },
            )
            if response.status_code == 200:
                return response.content
    except Exception as e:
        print(f"[PDFExport] Online compilation failed: {e}")
    return None
