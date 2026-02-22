"""
Claude-based HTML page classifier.

Analyzes a job page's HTML and returns:
    - Whether it's an apply page (has form fields)
    - Whether it has an apply button and where
    - Which ATS platform it is
    - All form fields present
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore

from ..config import ANTHROPIC_API_KEY, CLASSIFIER_MODEL
from ..events import emit, EventType


@dataclass
class FormField:
    """A single form field on the application page."""
    name: str
    label: str
    field_type: str      # text, email, tel, file, select, radio, checkbox, textarea
    required: bool = False
    selector: str = ""   # CSS selector hint


@dataclass
class PageAnalysis:
    """Structured result from classifying a job page."""
    is_apply_page: bool = False
    has_apply_button: bool = False
    apply_button_selector: str = ""
    ats_platform: str = "unknown"
    form_fields: List[FormField] = field(default_factory=list)
    raw_response: Dict = field(default_factory=dict)


def classify_page(html: str) -> PageAnalysis:
    """
    Send truncated HTML to Claude Haiku and parse the JSON response.

    Args:
        html: Raw HTML string from the job page.

    Returns:
        PageAnalysis dataclass.
    """
    # Use OpenRouter client (no Anthropic API key needed)
    try:
        from ..llm import get_sync_client
        client = get_sync_client()
    except ImportError:
        if not ANTHROPIC_API_KEY or anthropic is None:
            emit(EventType.INFO, "No API key — using heuristic page classifier")
            return _heuristic_classify(html)
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        emit(EventType.INFO,
             f"Sending {min(len(html), 8000)} chars of HTML to Claude for classification...",
             html_length=len(html))

        prompt = f"""Analyze this HTML from a job application website.

Return ONLY a JSON object:
{{
  "is_apply_page": <bool: true if this page has application form fields (name, email, resume upload, etc.)>,
  "has_apply_button": <bool: true if there's an "Apply", "Apply Now", or "Submit Application" button>,
  "apply_button_selector": "<CSS selector for the apply/submit button, e.g. 'button[data-qa=btn-apply]'>",
  "ats_platform": "<greenhouse|lever|workday|icims|taleo|linkedin|indeed|unknown>",
  "form_fields": [
    {{
      "name": "<field name or id attribute>",
      "label": "<human-readable label text>",
      "type": "<text|email|tel|file|select|radio|checkbox|textarea>",
      "required": <bool>,
      "selector": "<CSS selector for this field>"
    }}
  ]
}}

HTML (first 8000 chars):
{html[:8000]}

Return ONLY the JSON."""

        response = client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        data = _safe_parse_json(text)
        if data is None:
            emit(EventType.WARNING, "Could not parse classifier JSON — falling back to heuristic")
            return _heuristic_classify(html)
        return _parse_response(data)

    except json.JSONDecodeError as e:
        emit(EventType.WARNING, f"JSON parse error: {e} — falling back to heuristic", error=str(e))
        return _heuristic_classify(html)
    except (httpx.HTTPStatusError if 'httpx' in dir() else Exception) as e:
        emit(EventType.WARNING, f"HTTP error: {e} — falling back to heuristic", error=str(e))
        return _heuristic_classify(html)
    except Exception as e:
        emit(EventType.WARNING, f"Claude classifier failed: {e} — falling back to heuristic",
             error=str(e))
        print(f"[Classifier] Claude call failed: {e}")
        return _heuristic_classify(html)


def _safe_parse_json(text: str) -> Optional[Dict]:
    """Try to extract valid JSON from LLM response text."""
    # Strip markdown fences
    text = re.sub(r"```json\n?|```\n?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: extract first JSON object with regex
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def _parse_response(data: Dict) -> PageAnalysis:
    """Convert the raw JSON dict to a PageAnalysis dataclass."""
    if not isinstance(data, dict):
        return PageAnalysis()
    fields = []
    for f in data.get("form_fields", []):
        if not isinstance(f, dict):
            continue
        fields.append(FormField(
            name=f.get("name", ""),
            label=f.get("label", ""),
            field_type=f.get("type", "text"),
            required=bool(f.get("required", False)),
            selector=f.get("selector", ""),
        ))
    return PageAnalysis(
        is_apply_page=bool(data.get("is_apply_page", False)),
        has_apply_button=bool(data.get("has_apply_button", False)),
        apply_button_selector=data.get("apply_button_selector", ""),
        ats_platform=data.get("ats_platform", "unknown"),
        form_fields=fields,
        raw_response=data,
    )


def _heuristic_classify(html: str) -> PageAnalysis:
    """
    Fallback classifier using simple regex heuristics when Claude is unavailable.
    """
    html_lower = html.lower()

    # Detect ATS platform from HTML markers
    ats = "unknown"
    if "greenhouse" in html_lower or "boards.greenhouse.io" in html_lower:
        ats = "greenhouse"
    elif "lever.co" in html_lower or "jobs.lever.co" in html_lower:
        ats = "lever"
    elif "workday" in html_lower:
        ats = "workday"
    elif "icims" in html_lower:
        ats = "icims"
    elif "taleo" in html_lower:
        ats = "taleo"
    elif "linkedin" in html_lower:
        ats = "linkedin"
    elif "indeed" in html_lower:
        ats = "indeed"

    # Detect apply page
    form_indicators = ["<form", "type=\"file\"", "name=\"email\"", "name=\"resume\""]
    is_apply = any(ind in html_lower for ind in form_indicators)

    # Detect apply button
    apply_button = bool(re.search(
        r'(apply\s*now|submit\s*application|apply\s*for\s*this)', html_lower
    ))

    return PageAnalysis(
        is_apply_page=is_apply,
        has_apply_button=apply_button or not is_apply,
        apply_button_selector="",
        ats_platform=ats,
        form_fields=[],
    )
