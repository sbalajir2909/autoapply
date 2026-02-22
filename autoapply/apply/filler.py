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
from ..events import emit, EventType


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
    from ..llm import synthesize_answer
    
    profile = profile or CANDIDATE_PROFILE
    filled: Dict[str, str] = {}

    # If classifier returned form_fields, use them; otherwise auto-discover
    fields_to_fill = form_fields or await _auto_discover_fields(page)

    emit(EventType.INFO,
         f"Filling {len(fields_to_fill)} form fields...",
         field_count=len(fields_to_fill))

    for field in fields_to_fill:
        field_id = field.name or field.label or field.selector
        value = fuzzy_match_field(field_id, profile)

        try:
            if field.field_type == "file":
                # Resume upload
                emit(EventType.FORM_FIELD_FILLING,
                     f"Uploading resume to [{field_id}]",
                     field_name=field_id, value=Path(resume_pdf_path).name,
                     field_type="file")
                await _fill_file_input(page, field, resume_pdf_path)
                filled[field_id] = resume_pdf_path
                emit(EventType.FORM_FIELD_FILLED,
                     f"Uploaded: {Path(resume_pdf_path).name} → [{field_id}]",
                     field_name=field_id, value=Path(resume_pdf_path).name,
                     field_type="file")
                continue

            if value is None:
                # LLM fallback for all interactable field types
                jd_text = profile.get("current_job_description", "")
                context = await _get_field_context(page, field)
                question_text = context or field_id

                if field.field_type in ("text", "textarea", "email", "tel", "url", "number"):
                    emit(EventType.INFO, f"LLM synthesis for unmatched field: [{field_id}]")
                    value = synthesize_answer(
                        question_text, profile, jd_text,
                        field_type=field.field_type,
                    )
                elif field.field_type == "select":
                    options = await _get_select_options(page, field)
                    if options:
                        emit(EventType.INFO, f"LLM picking option for select [{field_id}]: {options[:5]}")
                        value = synthesize_answer(
                            question_text, profile, jd_text,
                            field_type="select", options=options,
                        )
                elif field.field_type in ("radio", "checkbox"):
                    options = await _get_radio_checkbox_options(page, field)
                    if options:
                        emit(EventType.INFO, f"LLM picking option for {field.field_type} [{field_id}]: {options[:5]}")
                        value = synthesize_answer(
                            question_text, profile, jd_text,
                            field_type=field.field_type, options=options,
                        )

                if not value or value == "Please refer to my attached resume for more details.":
                    if field.required:
                        value = "Please refer to my attached resume for more details."
                    else:
                        emit(EventType.FORM_FIELD_SKIPPED,
                             f"No match for field [{field_id}] (type: {field.field_type})",
                             field_name=field_id, field_type=field.field_type,
                             reason="no_profile_match")
                        continue

            # Emit what we're about to fill
            display_value = value[:50] + "..." if len(value) > 50 else value
            emit(EventType.FORM_FIELD_FILLING,
                 f"Filling [{field_id}] → \"{display_value}\"",
                 field_name=field_id, value=display_value,
                 field_type=field.field_type)

            if field.field_type in ("text", "email", "tel", "url", "number"):
                await _fill_text(page, field, value)
            elif field.field_type == "textarea":
                await _fill_textarea(page, field, value)
            elif field.field_type == "select":
                await _fill_select(page, field, value)
            elif field.field_type in ("radio", "checkbox"):
                await _fill_radio_checkbox(page, field, value)

            filled[field_id] = value
            emit(EventType.FORM_FIELD_FILLED,
                 f"Filled: [{field_id}] = \"{display_value}\"",
                 field_name=field_id, value=display_value,
                 field_type=field.field_type)

        except Exception as e:
            emit(EventType.FORM_FIELD_ERROR,
                 f"Error filling [{field_id}]: {e}",
                 field_name=field_id, error=str(e),
                 field_type=field.field_type)
            print(f"[Filler] Could not fill field {field_id!r}: {e}")

    emit(EventType.FORM_FILL_COMPLETE,
         f"Form fill complete — {len(filled)}/{len(fields_to_fill)} fields filled",
         filled_count=len(filled), total_count=len(fields_to_fill),
         filled_fields=filled)

    return filled


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _wait_and_locate(page, selector: str, timeout: int = 5000):
    """Locate an element, waiting for it to be visible first."""
    el = page.locator(selector).first
    try:
        await el.wait_for(state="visible", timeout=timeout)
    except Exception:
        pass  # element may still be interactable even if wait times out
    return el


async def _fill_text(page, field: FormField, value: str):
    """Fill a text/email/tel input."""
    selector = _build_selector(field)
    el = await _wait_and_locate(page, selector)
    if await el.count() > 0:
        await el.click()
        await el.fill(value)


async def _fill_textarea(page, field: FormField, value: str):
    """Fill a textarea."""
    selector = _build_selector(field)
    el = await _wait_and_locate(page, selector)
    if await el.count() > 0:
        await el.fill(value)


async def _fill_select(page, field: FormField, value: str):
    """Select an option in a <select> dropdown."""
    selector = _build_selector(field)
    el = await _wait_and_locate(page, selector)
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

    emit(EventType.INFO, "Auto-discovering form fields from DOM...")

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

    fields = [
        FormField(
            name=f.get("name", ""),
            label=f.get("label", ""),
            field_type=f.get("type", "text"),
            required=bool(f.get("required", False)),
            selector=f.get("selector", ""),
        )
        for f in inputs
    ]

    emit(EventType.FORM_FIELDS_DETECTED,
         f"Auto-discovered {len(fields)} fields from DOM",
         fields=[{"name": f.name, "label": f.label, "type": f.field_type,
                  "required": f.required} for f in fields])

    return fields


def _build_selector(field: FormField) -> str:
    """Build a CSS selector from field metadata."""
    if field.selector:
        return field.selector
    if field.name:
        return f'[name="{field.name}"]'
    return f'[id="{field.name}"]'


async def _get_field_context(page, field: FormField) -> str:
    """
    Extract the question/label text surrounding a form field.

    Checks: associated <label>, aria-label, placeholder, parent text.
    Returns a descriptive string for the LLM, or empty string.
    """
    selector = _build_selector(field)
    try:
        context = await page.evaluate(f"""(selector) => {{
            const el = document.querySelector(selector);
            if (!el) return '';
            const parts = [];

            // Label element
            if (el.labels && el.labels[0]) {{
                parts.push(el.labels[0].textContent.trim());
            }}
            // aria-label
            if (el.getAttribute('aria-label')) {{
                parts.push(el.getAttribute('aria-label'));
            }}
            // placeholder
            if (el.placeholder) {{
                parts.push('Placeholder: ' + el.placeholder);
            }}
            // Parent text (nearby context)
            const parent = el.closest('div, fieldset, li, td');
            if (parent) {{
                const parentText = parent.textContent.trim().substring(0, 200);
                if (parentText && !parts.includes(parentText)) {{
                    parts.push('Context: ' + parentText);
                }}
            }}

            return parts.join(' | ');
        }}""", selector)
        return context or field.label or field.name or ""
    except Exception:
        return field.label or field.name or ""


async def _get_select_options(page, field: FormField) -> List[str]:
    """Extract option labels from a <select> dropdown."""
    selector = _build_selector(field)
    try:
        options = await page.evaluate(f"""(selector) => {{
            const el = document.querySelector(selector);
            if (!el || el.tagName !== 'SELECT') return [];
            return Array.from(el.options)
                .map(o => o.text.trim())
                .filter(t => t && t !== '' && t !== '--' && t !== 'Select...');
        }}""", selector)
        return options or []
    except Exception:
        return []


async def _get_radio_checkbox_options(page, field: FormField) -> List[str]:
    """Extract option labels for radio buttons or checkboxes in a group."""
    name = field.name
    if not name:
        return []
    try:
        options = await page.evaluate(f"""(name) => {{
            const inputs = document.querySelectorAll('input[name="' + name + '"]');
            return Array.from(inputs).map(inp => {{
                if (inp.labels && inp.labels[0]) return inp.labels[0].textContent.trim();
                const parent = inp.closest('label');
                if (parent) return parent.textContent.trim();
                return inp.value;
            }}).filter(t => t);
        }}""", name)
        return options or []
    except Exception:
        return []
