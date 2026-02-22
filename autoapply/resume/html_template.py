"""
HTML/CSS resume template that replicates the LaTeX formatting exactly.

Converts structured JSON resume_data → self-contained HTML string.
Designed for Playwright page.pdf() rendering at Letter size with 0.5in margins.
"""

import re
from typing import Dict, List


# ---------------------------------------------------------------------------
# CSS that mirrors the LaTeX template spacing, fonts, and layout
# ---------------------------------------------------------------------------
RESUME_CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,wght@0,400;0,700;1,400;1,700&display=swap');

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

@page {
    size: Letter;
    margin: 0;
}

body {
    font-family: 'Source Serif 4', 'CMU Serif', 'Computer Modern', 'Times New Roman', serif;
    font-size: 10pt;
    line-height: 1.15;
    color: #000;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}

.resume-page {
    width: 8.5in;
    min-height: 11in;
    max-height: 11in;
    overflow: hidden;
    padding: 0.5in;
    padding-top: 0.35in;
}

/* ---------- HEADER ---------- */
.header {
    text-align: center;
    margin-bottom: 2pt;
}

.header .name {
    font-size: 17pt;
    font-weight: 700;
    letter-spacing: 0.5pt;
}

.header .location {
    font-size: 10pt;
    margin-top: 1pt;
}

.header .links {
    font-size: 10pt;
    margin-top: 1pt;
}

.header .links a {
    color: #00e;
    text-decoration: none;
}

.header .links .sep {
    margin: 0 3pt;
}

/* ---------- SECTIONS ---------- */
.section {
    margin-top: 4pt;
}

.section-title {
    font-size: 12pt;
    font-variant: small-caps;
    font-weight: 400;
    letter-spacing: 0.5pt;
    border-bottom: 1pt solid #000;
    padding-bottom: 1pt;
    margin-bottom: 3pt;
}

/* ---------- SUBHEADING (experience / education) ---------- */
.subheading-list {
    list-style: none;
    padding-left: 0.15in;
}

.subheading {
    margin-bottom: 0pt;
    margin-top: 2pt;
}

.subheading-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    width: 97%;
}

.subheading-row .left {
    font-weight: 700;
    font-size: 10pt;
}

.subheading-row .right {
    font-weight: 700;
    font-size: 10pt;
    text-align: right;
    white-space: nowrap;
}

.subheading-row2 {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    width: 97%;
}

.subheading-row2 .left {
    font-style: italic;
    font-size: 9pt;
}

.subheading-row2 .right {
    font-weight: 700;
    font-style: italic;
    font-size: 9pt;
    text-align: right;
    white-space: nowrap;
}

/* ---------- BULLET ITEMS ---------- */
.bullet-list {
    padding-left: 0.3in;
    margin-top: 1pt;
    margin-bottom: 0pt;
}

.bullet-list li {
    font-size: 9pt;
    line-height: 1.2;
    margin-bottom: 0pt;
    padding-bottom: 0pt;
    list-style-type: disc;
}

.bullet-list li::marker {
    font-size: 5pt;
}

/* Post-list spacing to match \resumeItemListEnd \vspace{-5pt} */
.bullet-list-end {
    margin-bottom: -3pt;
}

/* Post-subheading spacing to match \vspace{-7pt} */
.subheading-end {
    margin-bottom: -2pt;
}

/* ---------- PROJECTS ---------- */
.project-heading {
    display: flex;
    align-items: baseline;
    gap: 4pt;
    margin-bottom: 1pt;
    padding-left: 0.15in;
}

.project-heading .project-name a {
    font-weight: 700;
    color: #00e;
    text-decoration: none;
    font-size: 10pt;
}

.project-heading .project-name {
    font-weight: 700;
    font-size: 10pt;
}

.project-heading .sep {
    margin: 0 2pt;
}

.project-heading .project-subtitle {
    font-style: italic;
    font-size: 9pt;
}

/* ---------- SKILLS ---------- */
.skills-list {
    list-style-type: disc;
    padding-left: 0.3in;
}

.skills-list li {
    font-size: 9pt;
    line-height: 1.25;
    margin-bottom: 1pt;
}

.skills-list li::marker {
    font-size: 5pt;
}

/* ---------- BOLD METRICS ---------- */
strong {
    font-weight: 700;
}
"""


def _escape_html(text: str) -> str:
    """Escape HTML special chars but preserve <strong> tags."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Restore strong tags
    text = text.replace("&lt;strong&gt;", "<strong>").replace("&lt;/strong&gt;", "</strong>")
    return text


def _markdown_bold_to_html(text: str) -> str:
    """Convert **bold** markdown to <strong> tags."""
    return re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)


def _process_bullet(text: str) -> str:
    """Process a bullet string: convert markdown bold → HTML strong, escape rest."""
    text = _markdown_bold_to_html(text)
    text = _escape_html(text)
    return text


def render_resume_html(resume_data: Dict) -> str:
    """
    Render structured resume JSON to a self-contained HTML string.

    Args:
        resume_data: Dict with keys: header, education, experience, projects, skills

    Returns:
        Complete HTML document string ready for Playwright PDF rendering.
    """
    header = resume_data.get("header", {})
    education = resume_data.get("education", [])
    experience = resume_data.get("experience", [])
    projects = resume_data.get("projects", [])
    skills = resume_data.get("skills", [])

    parts: List[str] = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
{RESUME_CSS}
</style>
</head>
<body>
<div class="resume-page">
""")

    # --- Header ---
    parts.append('<div class="header">')
    parts.append(f'<div class="name">{_escape_html(header.get("name", ""))}</div>')
    if header.get("location"):
        parts.append(f'<div class="location">{_escape_html(header["location"])}</div>')

    links = []
    if header.get("phone"):
        links.append(_escape_html(header["phone"]))
    if header.get("email"):
        email = header["email"]
        links.append(f'<a href="mailto:{email}">{_escape_html(email)}</a>')
    if header.get("linkedin"):
        li = header["linkedin"]
        url = li if li.startswith("http") else f"https://{li}"
        links.append(f'<a href="{url}">{_escape_html(li)}</a>')
    if header.get("website"):
        ws = header["website"]
        url = ws if ws.startswith("http") else f"https://{ws}"
        links.append(f'<a href="{url}">{_escape_html(ws)}</a>')
    if header.get("github"):
        gh = header["github"]
        url = gh if gh.startswith("http") else f"https://{gh}"
        links.append(f'<a href="{url}">{_escape_html(gh)}</a>')

    if links:
        sep = ' <span class="sep">|</span> '
        parts.append(f'<div class="links">{sep.join(links)}</div>')
    parts.append('</div>')  # header

    # --- Education ---
    if education:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Education</div>')
        parts.append('<div class="subheading-list">')
        for edu in education:
            parts.append('<div class="subheading">')
            parts.append('<div class="subheading-row">')
            parts.append(f'<span class="left">{_escape_html(edu.get("school", ""))}</span>')
            parts.append(f'<span class="right">{_escape_html(edu.get("location", ""))}</span>')
            parts.append('</div>')
            parts.append('<div class="subheading-row2">')
            parts.append(f'<span class="left">{_escape_html(edu.get("degree", ""))}</span>')
            parts.append(f'<span class="right">{_escape_html(edu.get("date", ""))}</span>')
            parts.append('</div>')
            if edu.get("bullets"):
                parts.append('<ul class="bullet-list bullet-list-end">')
                for b in edu["bullets"]:
                    parts.append(f'<li>{_process_bullet(b)}</li>')
                parts.append('</ul>')
            parts.append('</div>')  # subheading
        parts.append('</div>')  # subheading-list
        parts.append('</div>')  # section

    # --- Experience ---
    if experience:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Experience</div>')
        parts.append('<div class="subheading-list">')
        for exp in experience:
            parts.append('<div class="subheading subheading-end">')
            parts.append('<div class="subheading-row">')
            parts.append(f'<span class="left">{_escape_html(exp.get("company", ""))}</span>')
            parts.append(f'<span class="right">{_escape_html(exp.get("location", ""))}</span>')
            parts.append('</div>')
            parts.append('<div class="subheading-row2">')
            parts.append(f'<span class="left">{_escape_html(exp.get("title", ""))}</span>')
            parts.append(f'<span class="right">{_escape_html(exp.get("dates", ""))}</span>')
            parts.append('</div>')
            if exp.get("bullets"):
                parts.append('<ul class="bullet-list bullet-list-end">')
                for b in exp["bullets"]:
                    parts.append(f'<li>{_process_bullet(b)}</li>')
                parts.append('</ul>')
            parts.append('</div>')  # subheading
        parts.append('</div>')  # subheading-list
        parts.append('</div>')  # section

    # --- Projects ---
    if projects:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Projects</div>')
        parts.append('<div class="subheading-list">')
        for proj in projects:
            parts.append('<div class="project-heading">')
            name = _escape_html(proj.get("name", ""))
            url = proj.get("url", "")
            if url:
                parts.append(f'<span class="project-name"><a href="{url}">{name}</a></span>')
            else:
                parts.append(f'<span class="project-name">{name}</span>')
            if proj.get("subtitle"):
                parts.append('<span class="sep">|</span>')
                parts.append(f'<span class="project-subtitle">{_escape_html(proj["subtitle"])}</span>')
            parts.append('</div>')
            if proj.get("bullets"):
                parts.append('<ul class="bullet-list bullet-list-end">')
                for b in proj["bullets"]:
                    parts.append(f'<li>{_process_bullet(b)}</li>')
                parts.append('</ul>')
        parts.append('</div>')  # subheading-list
        parts.append('</div>')  # section

    # --- Skills ---
    if skills:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Skills</div>')
        parts.append('<ul class="skills-list">')
        for skill in skills:
            cat = _escape_html(skill.get("category", ""))
            items = _escape_html(skill.get("items", ""))
            parts.append(f'<li><strong>{cat}:</strong> {items}</li>')
        parts.append('</ul>')
        parts.append('</div>')  # section

    parts.append('</div>')  # resume-page
    parts.append('</body>')
    parts.append('</html>')

    return "\n".join(parts)
