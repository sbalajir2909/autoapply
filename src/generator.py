"""
LaTeX resume generator.
Generates tailored LaTeX resume with dynamic spacing for one-page fit.
"""

import re
import subprocess
import tempfile
import os
from dataclasses import dataclass
from typing import List, Dict, Set, Optional, Tuple
from pathlib import Path

from .extractor import KeywordProfile
from .analyzer import ParsedResume, Experience, BulletPoint, SectionType
from .matcher import ContentMatcher, MatchResult, DuplicationChecker


@dataclass
class SpacingConfig:
    """Configuration for document spacing."""
    section_spacing: str = "6pt"
    item_spacing: str = "2pt"
    bullet_spacing: str = "-2pt"
    margin_top: str = "0.5in"
    margin_bottom: str = "0.5in"
    margin_left: str = "0.5in"
    margin_right: str = "0.5in"
    font_size: str = "10pt"


class ResumeGenerator:
    """Generate tailored LaTeX resumes."""

    def __init__(self, resume: ParsedResume, keywords: KeywordProfile):
        self.resume = resume
        self.keywords = keywords
        self.matcher = ContentMatcher(keywords, resume)
        self.dup_checker = DuplicationChecker()
        self.spacing = SpacingConfig()

    def generate(self, max_experiences: int = 4, max_bullets: int = 4) -> str:
        """Generate the tailored LaTeX resume."""
        # Get optimal content selection
        selection = self.matcher.select_best_content(
            max_bullets_per_exp=max_bullets,
            max_experiences=max_experiences
        )

        # Start with preamble
        latex_parts = [self._generate_preamble()]

        # Add document content
        latex_parts.append(r"\begin{document}")
        latex_parts.append("")

        # Generate header (preserve from original)
        header = self._extract_header()
        if header:
            latex_parts.append(header)
            latex_parts.append("")

        # Generate each section
        for section in self.resume.sections:
            if section.section_type == SectionType.EXPERIENCE:
                latex_parts.append(self._generate_experience_section(section, selection))
            elif section.section_type == SectionType.PROJECTS:
                latex_parts.append(self._generate_projects_section(section, selection))
            elif section.section_type == SectionType.EDUCATION:
                latex_parts.append(self._generate_education_section(section))
            elif section.section_type == SectionType.SKILLS:
                latex_parts.append(self._generate_skills_section(section))
            elif section.section_type != SectionType.HEADER:
                # Pass through other sections
                latex_parts.append(section.raw_latex)

            latex_parts.append("")

        latex_parts.append(r"\end{document}")

        return "\n".join(latex_parts)

    def _generate_preamble(self) -> str:
        """Generate document preamble with optimized spacing."""
        # Use original preamble as base, or generate a default
        if self.resume.preamble:
            preamble = self.resume.preamble

            # Adjust margins if geometry package is used
            preamble = re.sub(
                r'\\usepackage\[([^\]]*)\]\{geometry\}',
                f'\\\\usepackage[top={self.spacing.margin_top},bottom={self.spacing.margin_bottom},'
                f'left={self.spacing.margin_left},right={self.spacing.margin_right}]{{geometry}}',
                preamble
            )

            # Remove \begin{document} if present (we add it later)
            preamble = re.sub(r'\\begin\{document\}\s*$', '', preamble).rstrip()

            return preamble

        # Default preamble for ATS-friendly resume
        return f"""\\documentclass[{self.spacing.font_size},letterpaper]{{article}}

\\usepackage[utf8]{{inputenc}}
\\usepackage[T1]{{fontenc}}
\\usepackage{{lmodern}}
\\usepackage[top={self.spacing.margin_top},bottom={self.spacing.margin_bottom},left={self.spacing.margin_left},right={self.spacing.margin_right}]{{geometry}}
\\usepackage{{enumitem}}
\\usepackage{{titlesec}}
\\usepackage{{hyperref}}

% ATS-friendly formatting
\\pagestyle{{empty}}
\\setlength{{\\parindent}}{{0pt}}
\\setlength{{\\parskip}}{{0pt}}

% Section formatting
\\titleformat{{\\section}}{{\\large\\bfseries}}{{}}{{0em}}{{}}[\\titlerule]
\\titlespacing*{{\\section}}{{0pt}}{{{self.spacing.section_spacing}}}{{{self.spacing.section_spacing}}}

% List formatting
\\setlist[itemize]{{noitemsep, topsep=0pt, parsep=0pt, partopsep=0pt, leftmargin=*}}
"""

    def _extract_header(self) -> Optional[str]:
        """Extract header/contact info from original resume."""
        # Look for content before first section
        doc_start = self.resume.raw_latex.find(r'\begin{document}')
        if doc_start == -1:
            return None

        first_section = re.search(r'\\section\{', self.resume.raw_latex[doc_start:])
        if first_section:
            header_content = self.resume.raw_latex[doc_start + len(r'\begin{document}'):doc_start + first_section.start()]
            return header_content.strip()

        return None

    def _generate_experience_section(self, section, selection: Dict) -> str:
        """Generate the experience section with selected content."""
        lines = [f"\\section{{{section.title}}}"]
        lines.append(r"\begin{itemize}[leftmargin=0.15in, label={}]")

        selected_exps = selection.get('experiences', [])
        bullets_by_exp = selection.get('bullets_by_exp', {})

        for exp in selected_exps:
            if any(e.title == exp.title and e.company == exp.company for e in section.experiences):
                lines.append(self._format_experience_entry(exp, bullets_by_exp.get(id(exp), exp.bullets)))

        lines.append(r"\end{itemize}")
        return "\n".join(lines)

    def _generate_projects_section(self, section, selection: Dict) -> str:
        """Generate projects section."""
        lines = [f"\\section{{{section.title}}}"]
        lines.append(r"\begin{itemize}[leftmargin=0.15in, label={}]")

        # Include all projects but optimize bullets
        for exp in section.experiences:
            # Use original bullets if available, otherwise try to get from selection
            bullets_to_use = exp.bullets if exp.bullets else []
            if bullets_to_use:
                selected_bullets = self._select_best_bullets(bullets_to_use, max_bullets=3)
            else:
                selected_bullets = []
            lines.append(self._format_experience_entry(exp, selected_bullets))

        lines.append(r"\end{itemize}")
        return "\n".join(lines)

    def _format_experience_entry(self, exp: Experience, bullets: List[BulletPoint]) -> str:
        """Format a single experience entry."""
        lines = []

        # Entry header
        lines.append(r"  \item")
        lines.append(r"    \begin{tabular*}{\textwidth}[t]{l@{\extracolsep{\fill}}r}")
        lines.append(f"      \\textbf{{{self._escape_latex(exp.title)}}} & {self._escape_latex(exp.dates)} \\\\")
        lines.append(f"      \\textit{{{self._escape_latex(exp.company)}}} & \\textit{{{self._escape_latex(exp.location)}}} \\\\")
        lines.append(r"    \end{tabular*}")

        # Bullets - only add if there are actual bullets to include
        if bullets:
            bullet_lines = []
            for bullet in bullets:
                # Check for duplication
                if not self.dup_checker.is_duplicate(bullet.text):
                    formatted_text = self._enhance_bullet(bullet.text)
                    bullet_lines.append(f"      \\item {formatted_text}")

            # Only create itemize block if we have actual bullets
            if bullet_lines:
                lines.append(r"    \begin{itemize}[leftmargin=0.2in]")
                lines.extend(bullet_lines)
                lines.append(r"    \end{itemize}")

        return "\n".join(lines)

    def _generate_education_section(self, section) -> str:
        """Generate education section."""
        lines = [f"\\section{{{section.title}}}"]
        lines.append(r"\begin{itemize}[leftmargin=0.15in, label={}]")

        for edu in section.education:
            lines.append(r"  \item")
            lines.append(r"    \begin{tabular*}{\textwidth}[t]{l@{\extracolsep{\fill}}r}")
            lines.append(f"      \\textbf{{{self._escape_latex(edu.degree)}}} & {self._escape_latex(edu.dates)} \\\\")
            lines.append(f"      \\textit{{{self._escape_latex(edu.institution)}}} & \\textit{{{self._escape_latex(edu.location)}}} \\\\")
            lines.append(r"    \end{tabular*}")

        lines.append(r"\end{itemize}")
        return "\n".join(lines)

    def _generate_skills_section(self, section) -> str:
        """Generate skills section optimized for ATS."""
        lines = [f"\\section{{{section.title}}}"]
        lines.append(r"\begin{itemize}[leftmargin=0.15in, label={}]")

        # Organize skills by category, prioritizing job-relevant skills
        prioritized_skills = self._prioritize_skills(section.skills)

        for category, skills in prioritized_skills.items():
            # Filter out already-mentioned skills
            unique_skills = []
            for skill in skills:
                if not self.dup_checker.check_skill_repetition(skill):
                    unique_skills.append(skill)

            if unique_skills:
                skills_str = ", ".join(unique_skills)
                lines.append(f"  \\item \\textbf{{{self._escape_latex(category)}}}: {self._escape_latex(skills_str)}")

        lines.append(r"\end{itemize}")
        return "\n".join(lines)

    def _prioritize_skills(self, skills: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Reorder skills to put job-relevant ones first."""
        prioritized = {}
        target_skills = {k.lower() for k in self.keywords.all_keywords}

        for category, skill_list in skills.items():
            # Sort skills: matching keywords first
            sorted_skills = sorted(
                skill_list,
                key=lambda s: (s.lower() not in target_skills, s)
            )
            prioritized[category] = sorted_skills

        return prioritized

    def _select_best_bullets(self, bullets: List[BulletPoint], max_bullets: int = 4) -> List[BulletPoint]:
        """Select best bullets avoiding repetition."""
        scored = []
        for bullet in bullets:
            score = self._score_bullet_for_selection(bullet)
            if not self.dup_checker.is_duplicate(bullet.text, threshold=0.6):
                scored.append((bullet, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [b for b, _ in scored[:max_bullets]]

    def _score_bullet_for_selection(self, bullet: BulletPoint) -> float:
        """Score bullet for selection."""
        score = bullet.score
        text_lower = bullet.text.lower()

        # Boost for required keywords
        for kw in self.keywords.required_keywords:
            if kw.lower() in text_lower:
                score += 2.0

        # Boost for metrics
        if bullet.metrics:
            score += 1.5

        # Penalize very short bullets
        if len(bullet.text) < 50:
            score -= 1.0

        return score

    def _enhance_bullet(self, text: str) -> str:
        """Enhance bullet text with keyword emphasis where appropriate."""
        # This preserves the original text but could be extended to
        # add subtle emphasis or keyword injection if needed
        return self._escape_latex(text)

    def _escape_latex(self, text: str) -> str:
        """Escape special LaTeX characters."""
        if not text:
            return ""

        # Characters that need escaping
        special_chars = {
            '&': r'\&',
            '%': r'\%',
            '$': r'\$',
            '#': r'\#',
            '_': r'\_',
            '{': r'\{',
            '}': r'\}',
            '~': r'\textasciitilde{}',
            '^': r'\textasciicircum{}',
        }

        for char, escaped in special_chars.items():
            # Don't escape if already escaped or part of a command
            text = re.sub(f'(?<!\\\\){re.escape(char)}', escaped, text)

        return text


class MatchReportGenerator:
    """Generate match report for the tailored resume."""

    def __init__(self, keywords: KeywordProfile, match_result: MatchResult):
        self.keywords = keywords
        self.result = match_result

    def generate(self) -> str:
        """Generate markdown match report."""
        lines = ["# Resume Match Report", ""]

        # Overall score
        coverage_pct = self.result.keyword_coverage * 100
        lines.append(f"## Overall Keyword Coverage: {coverage_pct:.1f}%")
        lines.append("")

        # Visual indicator
        filled = int(coverage_pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"**Match Score:** [{bar}] {coverage_pct:.1f}%")
        lines.append("")

        # Matched keywords
        lines.append("## ✓ Matched Keywords")
        lines.append("")
        if self.result.matched_keywords:
            # Group by category
            for category, skills in self.keywords.technical_skills.items():
                matched_in_cat = [s for s in skills if s in self.result.matched_keywords]
                if matched_in_cat:
                    lines.append(f"**{category.title()}:** {', '.join(matched_in_cat)}")
            lines.append("")

            # Other matched
            other_matched = self.result.matched_keywords - set().union(
                *[set(s) for s in self.keywords.technical_skills.values()]
            )
            if other_matched:
                lines.append(f"**Other:** {', '.join(other_matched)}")
                lines.append("")
        else:
            lines.append("_No keywords matched_")
            lines.append("")

        # Missing keywords
        lines.append("## ✗ Missing Keywords")
        lines.append("")
        if self.result.missing_keywords:
            # Highlight required missing keywords
            missing_required = self.result.missing_keywords & self.keywords.required_keywords
            if missing_required:
                lines.append(f"**⚠️ Required (Critical):** {', '.join(missing_required)}")

            missing_preferred = self.result.missing_keywords & self.keywords.preferred_keywords
            if missing_preferred:
                lines.append(f"**Preferred:** {', '.join(missing_preferred)}")

            other_missing = self.result.missing_keywords - self.keywords.required_keywords - self.keywords.preferred_keywords
            if other_missing:
                lines.append(f"**Other:** {', '.join(list(other_missing)[:10])}")
        else:
            lines.append("_All keywords matched!_")
        lines.append("")

        # Action verbs
        lines.append("## Action Verbs Used")
        lines.append("")
        if self.result.action_verbs_used:
            lines.append(f"✓ {', '.join(self.result.action_verbs_used)}")
        else:
            lines.append("_Consider using more action verbs from the job description_")
        lines.append("")

        # Recommendations
        if self.result.recommendations:
            lines.append("## Recommendations")
            lines.append("")
            for rec in self.result.recommendations:
                lines.append(f"- {rec}")
            lines.append("")

        # Skill coverage breakdown
        lines.append("## Skill Coverage by Category")
        lines.append("")
        for category, skills in self.keywords.technical_skills.items():
            total = len(skills)
            matched = len([s for s in skills if s in self.result.matched_keywords])
            pct = (matched / total * 100) if total > 0 else 0
            status = "✓" if pct >= 70 else "⚠️" if pct >= 40 else "✗"
            lines.append(f"- {status} **{category.title()}**: {matched}/{total} ({pct:.0f}%)")
        lines.append("")

        # Footer
        lines.append("---")
        lines.append("*Generated by ATS Resume Builder*")

        return "\n".join(lines)


def generate_tailored_resume(
    resume: ParsedResume,
    keywords: KeywordProfile,
    max_experiences: int = 4,
    max_bullets: int = 4
) -> Tuple[str, str]:
    """
    Generate tailored resume and match report.

    Returns:
        Tuple of (latex_content, match_report_markdown)
    """
    generator = ResumeGenerator(resume, keywords)
    latex = generator.generate(max_experiences, max_bullets)

    # Generate match report
    matcher = ContentMatcher(keywords, resume)
    result = matcher.match()
    report_gen = MatchReportGenerator(keywords, result)
    report = report_gen.generate()

    return latex, report
