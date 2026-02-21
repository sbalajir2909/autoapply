"""
Generic form filler using fuzzy field name matching.

Maps candidate profile fields to form fields using difflib close-matching,
then fills text inputs, selects, file uploads, radios, and checkboxes.
"""

import difflib
import os
from pathlib import Path
from typing import Dict, List, Optional

from .classifier import FormField
from ..config import CANDIDATE_PROFILE


# Canonical profile key → list of common field name variations
FIELD_ALIASES: Dict[str, List[str]] = {
    "first_name":        ["first_name", "firstname", "first", "fname", "given_name"],
    "last_name":         ["last_name", "lastname", "last", "lname", "surname", "family_name"],
    "email":             ["email", "email_address", "emailaddress", "e-mail"],
    "phone":             ["phone", "phone_number", "phonenumber", "telephone", "mobile", "cell"],
    "linkedin_url":      ["linkedin", "linkedin_url", "linkedin_profile", "linkedinurl"],
    "github_url":        ["github", "github_url", "github_profile", "githuburl"],
    "portfolio_url":     ["portfolio", "portfolio_url", "website", "personal_website"],
    "salary_range":      ["salary", "salary_expectation", "desired_salary", "expected_salary"],
    "available_start_date": ["start_date", "available_start", "availability", "when_available"],
    "visa_sponsorship":  ["visa", "visa_sponsorship", "sponsorship", "require_sponsorship"],
    "work_authorization":["work_auth", "work_authorization", "authorized_to_work", "eligible_to_work"],
    "graduation_date":   ["graduation", "graduation_date", "grad_date", "expected_graduation"],
    "cover_letter":      ["cover_letter", "coverletter", "cover_letter_text"],
}

# Build reverse lookup: alias → profile key
_ALIAS_TO_KEY: Dict[str, str] = {}
for _key, _aliases in FIELD_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_KEY[_alias] = _key


def fuzzy_match_field(field_name: str, profile: Dict = None) -> Optional[str]:
    """
    Match a form field name to a candidate profile value using fuzzy matching.

    Args:
        field_name: The raw name/id/label of the form field.
        profile:    Candidate profile dict. Defaults to config CANDIDATE_PROFILE.

    Returns:
        The matched profile value string, or None if no match found.
    """
    profile = profile or CANDIDATE_PROFILE
    normalized = field_name.lower().replace("-", "_").replace(" ", "_").strip()

    # Direct alias lookup first
    key = _ALIAS_TO_KEY.get(normalized)
    if key and key in profile:
        return str(profile[key])

    # Fuzzy match against all known aliases
    all_aliases = list(_ALIAS_TO_KEY.keys())
    matches = difflib.get_close_matches(normalized, all_aliases, n=1, cutoff=0.6)
    if matches:
        key = _ALIAS_TO_KEY[matches[0]]
        if key in profile:
            return str(profile[key])

    return None


async def fill_form(
    page,  # Playwright Page object
    form_fields: List[FormField],
    resume_pdf_path: str,
    profile: Dict = None,
) -> Dict[str, str]:
    """
    Fill all detected form fields on the current page.

    Args:
        page:            Playwright Page object.
        form_fields:     List of FormField objects from the classifier.
        resume_pdf_path: Absolute path to the tailored PDF resume.
        profile:         Candidate profile dict.

    Returns:
        Dict mapping field names to filled values (for logging).
    """
    profile = profile or CANDIDATE_PROFILE
    filled: Dict[str, str] = {}

    # If classifier returned form_fields, use them; otherwise auto-discover
    fields_to_fill = form_fields or await _auto_discover_fields(page)

    for field in fields_to_fill:
        field_id = field.name or field.label or field.selector
        value = fuzzy_match_field(field_id, profile)

        try:
            if field.field_type == "file":
                # Resume upload
                await _fill_file_input(page, field, resume_pdf_path)
                filled[field_id] = resume_pdf_path
                continue

            if value is None:
                continue  # Skip fields we don't recognize

            if field.field_type in ("text", "email", "tel", "url", "number"):
                await _fill_text(page, field, value)
            elif field.field_type == "textarea":
                await _fill_textarea(page, field, value)
            elif field.field_type == "select":
                await _fill_select(page, field, value)
            elif field.field_type in ("radio", "checkbox"):
                await _fill_radio_checkbox(page, field, value)

            filled[field_id] = value

        except Exception as e:
            print(f"[Filler] Could not fill field {field_id!r}: {e}")

    return filled


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _fill_text(page, field: FormField, value: str):
    """Fill a text/email/tel input."""
    selector = _build_selector(field)
    el = page.locator(selector).first
    if await el.count() > 0:
        await el.click()
        await el.fill(value)


async def _fill_textarea(page, field: FormField, value: str):
    """Fill a textarea."""
    selector = _build_selector(field)
    el = page.locator(selector).first
    if await el.count() > 0:
        await el.fill(value)


async def _fill_select(page, field: FormField, value: str):
    """Select an option in a <select> dropdown."""
    selector = _build_selector(field)
    el = page.locator(selector).first
    if await el.count() > 0:
        try:
            await el.select_option(label=value)
        except Exception:
            try:
                await el.select_option(value=value)
            except Exception:
                pass  # best effort


async def _fill_radio_checkbox(page, field: FormField, value: str):
    """Check a radio button or checkbox whose label matches value."""
    # Try to find by label text
    label = page.get_by_label(value)
    if await label.count() > 0:
        await label.check()


async def _fill_file_input(page, field: FormField, file_path: str):
    """Upload a file to a file input."""
    if not Path(file_path).exists():
        print(f"[Filler] Resume file not found: {file_path}")
        return
    selector = field.selector or "input[type='file']"
    el = page.locator(selector).first
    if await el.count() > 0:
        await el.set_input_files(file_path)


async def _auto_discover_fields(page) -> List[FormField]:
    """
    Fallback: discover form fields directly from the DOM when the classifier
    didn't return field info.
    """
    from .classifier import FormField

    inputs = await page.evaluate("""() => {
        const fields = [];
        document.querySelectorAll('input, textarea, select').forEach(el => {
            fields.push({
                name: el.name || el.id || '',
                label: el.labels && el.labels[0] ? el.labels[0].textContent.trim() : '',
                type: el.type || el.tagName.toLowerCase(),
                required: el.required,
                selector: el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : '')
            });
        });
        return fields;
    }""")

    return [
        FormField(
            name=f.get("name", ""),
            label=f.get("label", ""),
            field_type=f.get("type", "text"),
            required=bool(f.get("required", False)),
            selector=f.get("selector", ""),
        )
        for f in inputs
    ]


def _build_selector(field: FormField) -> str:
    """Build a CSS selector from field metadata."""
    if field.selector:
        return field.selector
    if field.name:
        return f'[name="{field.name}"]'
    return f'[id="{field.name}"]'
