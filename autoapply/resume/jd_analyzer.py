"""
JD Analysis — extracts structured data from a job description.

Wraps src/extractor.py (regex-based keyword extraction) and adds a
Claude call for structured analysis: role title, domain, seniority, top skills.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# Allow importing from the parent src/ package
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore

from src.extractor import extract_keywords, KeywordProfile
from ..config import ANTHROPIC_API_KEY, CLASSIFIER_MODEL


def analyze_jd(jd_text: str) -> Dict[str, Any]:
    """
    Analyze a job description and return structured data.

    Returns:
        {
            "role_title": str,
            "company": str,
            "seniority": str,          # junior / mid / senior / lead
            "domain": str,             # AppSec / CloudSec / ProdSec / etc.
            "top_required_skills": [],
            "top_preferred_skills": [],
            "keywords": [],            # all ATS keywords
            "keyword_profile": KeywordProfile,
        }
    """
    # Layer 1: fast regex-based extraction (no API cost)
    profile = extract_keywords(jd_text)

    # Build keyword list sorted by priority
    keywords = profile.get_priority_keywords()

    # Layer 2: Claude structured analysis
    structured = _claude_analyze(jd_text)

    return {
        "role_title": structured.get("role_title", ""),
        "company": structured.get("company", ""),
        "seniority": structured.get("seniority", "mid"),
        "domain": structured.get("domain", ""),
        "top_required_skills": structured.get("top_required_skills", []),
        "top_preferred_skills": structured.get("top_preferred_skills", []),
        "keywords": keywords,
        "keyword_profile": profile,
    }


def _claude_analyze(jd_text: str) -> Dict[str, Any]:
    """
    Call Claude Haiku for fast structured JD analysis.
    Returns empty dict on failure (graceful degradation).
    """
    # Use OpenRouter client
    try:
        from ..llm import get_sync_client
        client = get_sync_client()
    except ImportError:
        if not ANTHROPIC_API_KEY or anthropic is None:
            return {}
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:

        prompt = f"""Analyze this job description and return ONLY a JSON object with these fields:
{{
  "role_title": "extracted job title",
  "company": "company name or empty string",
  "seniority": "junior|mid|senior|lead",
  "domain": "security domain focus (AppSec/CloudSec/ProdSec/GRC/IR/etc or empty)",
  "top_required_skills": ["skill1", "skill2", ...],
  "top_preferred_skills": ["skill1", "skill2", ...]
}}

JD:
{jd_text[:4000]}

Return ONLY the JSON. No explanation."""

        response = client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        result = _safe_parse_json(text)
        if result is None:
            print("[JDAnalyzer] Could not parse JSON from LLM response")
            return {}
        return result

    except json.JSONDecodeError as e:
        print(f"[JDAnalyzer] JSON parse error: {e}")
        return {}
    except Exception as e:
        print(f"[JDAnalyzer] Claude call failed: {e}")
        return {}


def _safe_parse_json(text: str) -> Dict[str, Any] | None:
    """Try to extract valid JSON from LLM response text."""
    text = re.sub(r"```json\n?|```\n?", "", text).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    return None
