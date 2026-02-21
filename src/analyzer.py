"""
LaTeX resume analyzer and parser.
Parses resume sections, extracts content, and analyzes structure.
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


class SectionType(Enum):
    """Types of resume sections."""
    HEADER = "header"
    SUMMARY = "summary"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    SKILLS = "skills"
    PROJECTS = "projects"
    CERTIFICATIONS = "certifications"
    PUBLICATIONS = "publications"
    AWARDS = "awards"
    UNKNOWN = "unknown"


@dataclass
class BulletPoint:
    """A single bullet point from a resume."""
    text: str
    raw_latex: str
    keywords: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    action_verb: Optional[str] = None
    score: float = 0.0

    def extract_keywords(self, all_keywords: set):
        """Extract matching keywords from this bullet."""
        text_lower = self.text.lower()
        self.keywords = [k for k in all_keywords if k.lower() in text_lower]

    def extract_metrics(self):
        """Extract quantifiable metrics from the bullet."""
        # Patterns for metrics: percentages, numbers, dollar amounts, etc.
        patterns = [
            r'\d+%',  # Percentages
            r'\$[\d,]+[KMB]?',  # Dollar amounts
            r'\d+[xX]',  # Multipliers
            r'\d+\+?\s*(users?|customers?|clients?|engineers?|developers?|team members?)',  # People counts
            r'\d+[KMB]?\+?\s*(requests?|transactions?|records?|lines)',  # Volume metrics
            r'\d+\s*(ms|seconds?|minutes?|hours?|days?)',  # Time metrics
        ]
        for pattern in patterns:
            matches = re.findall(pattern, self.text, re.IGNORECASE)
            self.metrics.extend(matches)

    def extract_action_verb(self):
        """Extract the leading action verb."""
        # Get first word, handle common LaTeX escapes
        text_clean = re.sub(r'\\[a-z]+\{|\}', '', self.text)
        words = text_clean.split()
        if words:
            first_word = words[0].lower().rstrip('ed').rstrip('ing')
            self.action_verb = words[0]


@dataclass
class Experience:
    """A work experience or project entry."""
    title: str
    company: str
    location: str
    dates: str
    bullets: List[BulletPoint] = field(default_factory=list)
    raw_latex: str = ""

    def get_plain_text(self) -> str:
        """Get all text content without LaTeX formatting."""
        text = f"{self.title} {self.company} {self.location} {self.dates}"
        for bullet in self.bullets:
            text += " " + bullet.text
        return text


@dataclass
class Education:
    """An education entry."""
    degree: str
    institution: str
    location: str
    dates: str
    details: List[str] = field(default_factory=list)
    raw_latex: str = ""


@dataclass
class ResumeSection:
    """A section of the resume."""
    section_type: SectionType
    title: str
    content: str
    raw_latex: str
    experiences: List[Experience] = field(default_factory=list)
    education: List[Education] = field(default_factory=list)
    skills: Dict[str, List[str]] = field(default_factory=dict)
    items: List[str] = field(default_factory=list)


@dataclass
class ParsedResume:
    """Complete parsed resume structure."""
    preamble: str
    header: Optional[ResumeSection] = None
    sections: List[ResumeSection] = field(default_factory=list)
    raw_latex: str = ""

    def get_section(self, section_type: SectionType) -> Optional[ResumeSection]:
        """Get a section by type."""
        for section in self.sections:
            if section.section_type == section_type:
                return section
        return None

    def get_all_bullets(self) -> List[BulletPoint]:
        """Get all bullet points from experience/project sections."""
        bullets = []
        for section in self.sections:
            for exp in section.experiences:
                bullets.extend(exp.bullets)
        return bullets

    def get_all_skills(self) -> set:
        """Get all skills mentioned in the resume."""
        skills = set()
        for section in self.sections:
            if section.section_type == SectionType.SKILLS:
                for category, skill_list in section.skills.items():
                    skills.update(skill_list)
        return skills


class LaTeXResumeAnalyzer:
    """Parse and analyze LaTeX resumes."""

    # Section name mappings
    SECTION_MAPPINGS = {
        'experience': SectionType.EXPERIENCE,
        'work experience': SectionType.EXPERIENCE,
        'professional experience': SectionType.EXPERIENCE,
        'employment': SectionType.EXPERIENCE,
        'education': SectionType.EDUCATION,
        'academic background': SectionType.EDUCATION,
        'skills': SectionType.SKILLS,
        'technical skills': SectionType.SKILLS,
        'technologies': SectionType.SKILLS,
        'competencies': SectionType.SKILLS,
        'projects': SectionType.PROJECTS,
        'personal projects': SectionType.PROJECTS,
        'side projects': SectionType.PROJECTS,
        'certifications': SectionType.CERTIFICATIONS,
        'certificates': SectionType.CERTIFICATIONS,
        'publications': SectionType.PUBLICATIONS,
        'papers': SectionType.PUBLICATIONS,
        'awards': SectionType.AWARDS,
        'honors': SectionType.AWARDS,
        'achievements': SectionType.AWARDS,
        'summary': SectionType.SUMMARY,
        'objective': SectionType.SUMMARY,
        'profile': SectionType.SUMMARY,
    }

    def __init__(self):
        self.section_pattern = re.compile(
            r'\\section\{([^}]+)\}',
            re.IGNORECASE
        )

    def parse(self, latex_content: str) -> ParsedResume:
        """Parse a LaTeX resume into structured sections."""
        resume = ParsedResume(preamble="", raw_latex=latex_content)

        # Extract preamble (everything before \begin{document})
        doc_start = latex_content.find(r'\begin{document}')
        if doc_start != -1:
            resume.preamble = latex_content[:doc_start + len(r'\begin{document}')]
            body = latex_content[doc_start + len(r'\begin{document}'):]
        else:
            body = latex_content

        # Find all sections
        sections = self._split_into_sections(body)

        # Parse each section
        for title, content, raw in sections:
            section_type = self._identify_section_type(title)
            section = ResumeSection(
                section_type=section_type,
                title=title,
                content=content,
                raw_latex=raw
            )

            # Parse section-specific content
            if section_type == SectionType.EXPERIENCE:
                section.experiences = self._parse_experiences(content, raw)
            elif section_type == SectionType.PROJECTS:
                section.experiences = self._parse_experiences(content, raw)
            elif section_type == SectionType.EDUCATION:
                section.education = self._parse_education(content)
            elif section_type == SectionType.SKILLS:
                section.skills = self._parse_skills(content)

            resume.sections.append(section)

        return resume

    def _split_into_sections(self, body: str) -> List[Tuple[str, str, str]]:
        """Split document body into sections."""
        sections = []

        # Find all section commands
        matches = list(self.section_pattern.finditer(body))

        for i, match in enumerate(matches):
            title = match.group(1)
            start = match.end()

            # End at next section or end of document
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end_doc = body.find(r'\end{document}')
                end = end_doc if end_doc != -1 else len(body)

            content = body[start:end].strip()
            raw = body[match.start():end].strip()
            sections.append((title, content, raw))

        return sections

    def _identify_section_type(self, title: str) -> SectionType:
        """Identify the type of section from its title."""
        title_lower = title.lower().strip()

        # Direct match
        if title_lower in self.SECTION_MAPPINGS:
            return self.SECTION_MAPPINGS[title_lower]

        # Partial match
        for key, section_type in self.SECTION_MAPPINGS.items():
            if key in title_lower or title_lower in key:
                return section_type

        return SectionType.UNKNOWN

    def _parse_experiences(self, content: str, raw: str) -> List[Experience]:
        """Parse experience/project entries."""
        experiences = []

        # Common patterns for experience entries
        # Pattern 1: \resumeSubheading{Title}{Dates}{Company}{Location}
        subheading_pattern = r'\\resumeSubheading\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}'

        # Pattern 2: \textbf{Title} at \textbf{Company}
        textbf_pattern = r'\\textbf\{([^}]+)\}'

        # Find subheading-style entries
        for match in re.finditer(subheading_pattern, content):
            title, dates, company, location = match.groups()

            # Find bullets after this entry
            start_pos = match.end()
            next_match = re.search(subheading_pattern, content[start_pos:])
            end_pos = start_pos + next_match.start() if next_match else len(content)

            bullet_content = content[start_pos:end_pos]
            bullets = self._parse_bullets(bullet_content)

            exp = Experience(
                title=self._clean_latex(title),
                company=self._clean_latex(company),
                location=self._clean_latex(location),
                dates=self._clean_latex(dates),
                bullets=bullets,
                raw_latex=content[match.start():end_pos]
            )
            experiences.append(exp)

        # If no subheading pattern found, try alternative patterns
        if not experiences:
            # Try to find entries by \item patterns or other structures
            experiences = self._parse_generic_entries(content)

        return experiences

    def _parse_bullets(self, content: str) -> List[BulletPoint]:
        """Parse bullet points from content."""
        bullets = []

        # Pattern for \resumeItem{content} - handles nested braces
        resume_item_pattern = r'\\resumeItem\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}'

        # Pattern for simple \item followed by content until next \item or end
        simple_item_pattern = r'\\item\s+([^\n]+?)(?=\\item|\n\s*\\end|\n\s*$)'

        # Try resumeItem first
        matches = re.findall(resume_item_pattern, content)

        # If no resumeItem matches, try simple \item
        if not matches:
            matches = re.findall(simple_item_pattern, content, re.MULTILINE)

        for match in matches:
            text = self._clean_latex(match)
            if text.strip() and len(text.strip()) > 5:  # Filter out very short/empty matches
                bullet = BulletPoint(
                    text=text.strip(),
                    raw_latex=match
                )
                bullet.extract_metrics()
                bullet.extract_action_verb()
                bullets.append(bullet)

        return bullets

    def _parse_generic_entries(self, content: str) -> List[Experience]:
        """Parse entries when standard patterns don't match."""
        experiences = []

        # Split by common entry delimiters
        # Look for patterns like bold titles followed by content
        entry_pattern = r'\\textbf\{([^}]+)\}[^\\]*(?:\\textit\{([^}]+)\})?'

        matches = list(re.finditer(entry_pattern, content))
        for i, match in enumerate(matches):
            title = match.group(1)
            subtitle = match.group(2) or ""

            start_pos = match.end()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)

            bullet_content = content[start_pos:end_pos]
            bullets = self._parse_bullets(bullet_content)

            exp = Experience(
                title=self._clean_latex(title),
                company=self._clean_latex(subtitle),
                location="",
                dates="",
                bullets=bullets,
                raw_latex=content[match.start():end_pos]
            )
            experiences.append(exp)

        return experiences

    def _parse_education(self, content: str) -> List[Education]:
        """Parse education entries."""
        education = []

        # Pattern for education entries
        edu_pattern = r'\\resumeSubheading\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}'

        for match in re.finditer(edu_pattern, content):
            degree, dates, institution, location = match.groups()

            edu = Education(
                degree=self._clean_latex(degree),
                institution=self._clean_latex(institution),
                location=self._clean_latex(location),
                dates=self._clean_latex(dates),
                raw_latex=match.group(0)
            )
            education.append(edu)

        # Try alternative patterns if none found
        if not education:
            textbf_pattern = r'\\textbf\{([^}]+)\}'
            matches = re.findall(textbf_pattern, content)
            for match in matches:
                edu = Education(
                    degree=self._clean_latex(match),
                    institution="",
                    location="",
                    dates=""
                )
                education.append(edu)

        return education

    def _parse_skills(self, content: str) -> Dict[str, List[str]]:
        """Parse skills section into categories."""
        skills = {}

        # Pattern for \item \textbf{Category}: skill1, skill2, skill3
        # Match until end of line or next \item
        item_textbf_pattern = r'\\item\s*\\textbf\{([^}]+)\}[:\s]*([^\n\\]+)'

        # Pattern for categorized skills: \textbf{Category}: skill1, skill2
        category_pattern = r'\\textbf\{([^}]+)\}[:\s]*([^\\]+?)(?=\\textbf|\\item|$|\n\n)'

        # Also try resumeItem pattern
        resume_item_pattern = r'\\resumeItem\{\\textbf\{([^}]+)\}[:\s]*([^}]+)\}'

        for pattern in [item_textbf_pattern, category_pattern, resume_item_pattern]:
            for match in re.finditer(pattern, content, re.MULTILINE):
                category = self._clean_latex(match.group(1)).strip()
                skill_text = self._clean_latex(match.group(2))

                # Split skills by comma or pipe
                skill_list = re.split(r'[,|;]', skill_text)
                skill_list = [s.strip() for s in skill_list if s.strip()]

                if skill_list and category not in skills:
                    skills[category] = skill_list

        # If no categories found, treat as single list
        if not skills:
            all_skills = self._extract_skills_flat(content)
            if all_skills:
                skills['General'] = all_skills

        return skills

    def _extract_skills_flat(self, content: str) -> List[str]:
        """Extract skills as a flat list without categories."""
        # Remove LaTeX commands
        clean = self._clean_latex(content)

        # Split by common delimiters
        skills = re.split(r'[,|;•·]', clean)
        return [s.strip() for s in skills if s.strip() and len(s.strip()) > 1]

    def _clean_latex(self, text: str) -> str:
        """Remove LaTeX formatting from text."""
        if not text:
            return ""

        # Remove common LaTeX commands
        text = re.sub(r'\\textbf\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\textit\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\emph\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\underline\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\href\{[^}]*\}\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\url\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\[a-zA-Z]+\s*', '', text)
        text = re.sub(r'[{}]', '', text)
        text = re.sub(r'\s+', ' ', text)

        return text.strip()


def analyze_resume(latex_content: str) -> ParsedResume:
    """Convenience function to analyze a LaTeX resume."""
    analyzer = LaTeXResumeAnalyzer()
    return analyzer.parse(latex_content)
