"""
AI-powered resume tailoring - FAST but ROBUST version.
One comprehensive API call with detailed prompting for maximum ATS effectiveness.
"""

import os
import re
import json
from typing import Optional, Dict, List, AsyncGenerator, Tuple
from dataclasses import dataclass
from collections import Counter


@dataclass
class BulletAnalysis:
    """Analysis of a single bullet point."""
    text: str
    word_count: int
    action_verb: str
    has_quantification: bool
    is_valid_length: bool
    keywords_found: List[str]


@dataclass
class ValidationResult:
    """Result of resume validation."""
    is_valid: bool
    bullet_analyses: List[BulletAnalysis]
    verb_counts: Dict[str, int]
    repeated_verbs: List[str]
    bullets_wrong_length: int
    bullets_no_quantification: int
    total_bullets: int
    keyword_coverage: float
    matched_keywords: List[str]
    missing_keywords: List[str]
    suggestions: List[str]


@dataclass
class TailoringResult:
    """Result from AI tailoring."""
    resume_data: Dict  # Structured JSON resume data
    tailored_latex: str  # Kept for backward compat (empty when using JSON pipeline)
    original_latex: str
    match_analysis: Dict
    keyword_coverage: float
    matched_keywords: List[str]
    missing_keywords: List[str]
    suggestions: List[str]
    validation: ValidationResult
    bullet_suggestions: List[Dict]
    diff_data: Dict
    passes_completed: int


class KeywordMatcher:
    """Smart keyword matching with stemming and synonyms."""

    SYNONYMS = {
        'javascript': ['js', 'javascript', 'ecmascript'],
        'typescript': ['ts', 'typescript'],
        'python': ['python', 'python3', 'py'],
        'kubernetes': ['kubernetes', 'k8s', 'kube'],
        'amazon web services': ['aws', 'amazon web services'],
        'google cloud platform': ['gcp', 'google cloud', 'google cloud platform'],
        'microsoft azure': ['azure', 'microsoft azure'],
        'ci/cd': ['ci/cd', 'cicd', 'ci cd', 'continuous integration', 'continuous deployment'],
        'machine learning': ['ml', 'machine learning'],
        'artificial intelligence': ['ai', 'artificial intelligence'],
        'api': ['api', 'apis', 'rest api', 'restful api'],
    }

    @classmethod
    def matches(cls, keyword: str, text: str) -> bool:
        text_lower = text.lower()
        keyword_lower = keyword.lower()

        if keyword_lower in text_lower:
            return True

        for group in cls.SYNONYMS.values():
            if keyword_lower in group:
                for syn in group:
                    if syn in text_lower:
                        return True
        return False


class FastValidator:
    """Fast local validation - no API calls."""

    @staticmethod
    def extract_bullets(latex: str) -> List[str]:
        pattern = r'\\resumeItem\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}'
        return re.findall(pattern, latex)

    @staticmethod
    def extract_bullets_from_json(resume_data: Dict) -> List[str]:
        """Extract all bullet strings from structured resume JSON."""
        bullets = []
        for section in ("education", "experience", "projects"):
            for entry in resume_data.get(section, []):
                bullets.extend(entry.get("bullets", []))
        return bullets

    @staticmethod
    def count_words(bullet: str) -> int:
        # Strip bold markers: **text** → text
        clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', bullet)
        # Strip LaTeX-style bold if present
        clean = re.sub(r'\\textbf\{([^}]*)\}', r'\1', clean)
        clean = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', clean)
        clean = re.sub(r'[\\$%&]', '', clean)
        return len(clean.split())

    @staticmethod
    def has_metric(bullet: str) -> bool:
        # Check for **bold metrics** or \textbf{} or raw numbers with context
        if re.search(r'\*\*[^*]+\*\*', bullet):
            return True
        if re.search(r'\\textbf\{[^}]+\}', bullet):
            return True
        # Also accept raw numbers/percentages in plain text
        return bool(re.search(r'\b\d+[\d,]*[+%]?\b', bullet))

    @staticmethod
    def get_verb(bullet: str) -> str:
        clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', bullet)
        clean = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', clean)
        words = clean.split()
        return words[0].lower() if words else ""

    @classmethod
    def validate(cls, text_or_data, keywords: List[str]) -> ValidationResult:
        """Validate bullets from LaTeX string or JSON resume_data dict."""
        if isinstance(text_or_data, dict):
            bullets = cls.extract_bullets_from_json(text_or_data)
            full_text = " ".join(bullets)
        else:
            bullets = cls.extract_bullets(text_or_data)
            full_text = text_or_data

        analyses = []
        verb_counts = Counter()

        for b in bullets:
            wc = cls.count_words(b)
            verb = cls.get_verb(b)
            verb_counts[verb] += 1

            # Find keywords in this bullet
            found = [k for k in keywords if KeywordMatcher.matches(k, b)]

            analyses.append(BulletAnalysis(
                text=b,
                word_count=wc,
                action_verb=verb,
                has_quantification=cls.has_metric(b),
                is_valid_length=24 <= wc <= 28,
                keywords_found=found
            ))

        repeated = [v for v, c in verb_counts.items() if c > 2]
        wrong_len = sum(1 for a in analyses if not a.is_valid_length)
        no_quant = sum(1 for a in analyses if not a.has_quantification)

        # Keyword matching with synonyms
        matched = [k for k in keywords if KeywordMatcher.matches(k, full_text)]
        missing = [k for k in keywords if not KeywordMatcher.matches(k, full_text)]
        coverage = len(matched) / len(keywords) * 100 if keywords else 100

        suggestions = []
        if wrong_len:
            suggestions.append(f"{wrong_len} bullets not 24-28 words")
        if no_quant:
            suggestions.append(f"{no_quant} bullets lack bold metrics")
        if repeated:
            suggestions.append(f"Verbs used >2x: {', '.join(repeated)}")
        if missing:
            suggestions.append(f"Missing keywords: {', '.join(missing[:5])}")

        return ValidationResult(
            is_valid=(wrong_len == 0 and no_quant == 0 and not repeated and coverage >= 80),
            bullet_analyses=analyses,
            verb_counts=dict(verb_counts),
            repeated_verbs=repeated,
            bullets_wrong_length=wrong_len,
            bullets_no_quantification=no_quant,
            total_bullets=len(analyses),
            keyword_coverage=round(coverage, 1),
            matched_keywords=matched,
            missing_keywords=missing,
            suggestions=suggestions
        )

    @classmethod
    def estimate_pages(cls, text_or_data) -> int:
        """Fast page estimate."""
        if isinstance(text_or_data, dict):
            bullets = cls.extract_bullets_from_json(text_or_data)
            experiences = len(text_or_data.get("experience", []))
            skills = text_or_data.get("skills", [])
            skills_length = sum(len(s.get("items", "")) for s in skills)
        else:
            bullets = re.findall(r'\\resumeItem\{', text_or_data)
            experiences = len(re.findall(r'\\resumeSubheading\{', text_or_data))
            skills_section = re.search(r'\\section\{[Ss]kills\}(.*?)(?=\\section|\\end\{document\})', text_or_data, re.DOTALL)
            skills_length = len(skills_section.group(1)) if skills_section else 0

        n_bullets = len(bullets)
        score = n_bullets * 6 + experiences * 10 + skills_length / 50
        if score <= 110:
            return 1
        elif score <= 200:
            return 2
        return 3


class AIResumeTailorAsync:
    """Fast async resume tailor with robust prompting."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

        # Use OpenRouter client (routes to Claude via OpenRouter)
        try:
            from autoapply.llm import get_async_client
            self.client = get_async_client()
        except (ImportError, ValueError):
            if not self.api_key:
                raise ValueError("ANTHROPIC_API_KEY required (or install autoapply.llm for OpenRouter)")
            import anthropic
            self.client = anthropic.AsyncAnthropic(api_key=self.api_key)

    async def tailor_with_progress(
        self,
        resume_latex: str,
        job_description: str,
        max_experiences: int = 4,
        max_bullets: int = 4,
        max_passes: int = 3
    ) -> AsyncGenerator[Dict, None]:
        """Fast single-pass tailoring with comprehensive prompting."""

        yield {"step": "starting", "message": "Starting ATS optimization...", "progress": 5}

        if not resume_latex or not job_description:
            yield {"step": "error", "message": "Missing resume or job description", "progress": 0}
            return

        # SINGLE comprehensive API call
        yield {"step": "generating", "message": "Analyzing job & generating optimized resume...", "progress": 10}

        try:
            result_json = await self._generate_complete(
                resume_latex, job_description, max_experiences, max_bullets
            )
        except Exception as e:
            yield {"step": "error", "message": f"API error: {str(e)}", "progress": 0}
            return

        resume_data = result_json.get("resume_data", {})
        keywords = result_json.get("keywords", [])
        job_analysis = result_json.get("analysis", {})

        if not resume_data or not resume_data.get("experience"):
            yield {"step": "error", "message": "Invalid output generated — missing resume_data", "progress": 0}
            return

        yield {"step": "validating", "message": "Validating against ATS rules...", "progress": 60}

        # Fast local validation using JSON bullets
        validation = FastValidator.validate(resume_data, keywords)

        yield {
            "step": "validated",
            "message": f"Keyword coverage: {validation.keyword_coverage}%",
            "progress": 65,
            "data": {"coverage": validation.keyword_coverage, "keywords": len(keywords)}
        }

        # Fix pass if needed (uses Haiku for speed)
        if not validation.is_valid:
            issues = validation.bullets_wrong_length + validation.bullets_no_quantification + len(validation.repeated_verbs)
            yield {"step": "fixing", "message": f"Fixing {issues} rule violations...", "progress": 70}
            resume_data = await self._fix_violations_json(resume_data, validation)
            validation = FastValidator.validate(resume_data, keywords)

        # Page check
        pages = FastValidator.estimate_pages(resume_data)
        if pages > 1:
            yield {"step": "reducing", "message": "Enforcing 1-page limit...", "progress": 80}
            resume_data = await self._reduce_to_one_page_json(resume_data)
            validation = FastValidator.validate(resume_data, keywords)
            pages = FastValidator.estimate_pages(resume_data)

        yield {
            "step": "complete",
            "message": f"Done! {validation.keyword_coverage}% keyword coverage",
            "progress": 100,
            "data": {"pages": pages, "coverage": validation.keyword_coverage}
        }

        diff_data = self._create_diff_json(resume_latex, resume_data)

        result = TailoringResult(
            resume_data=resume_data,
            tailored_latex="",  # No longer generating LaTeX
            original_latex=resume_latex,
            match_analysis=job_analysis,
            keyword_coverage=validation.keyword_coverage,
            matched_keywords=validation.matched_keywords,
            missing_keywords=validation.missing_keywords,
            suggestions=validation.suggestions,
            validation=validation,
            bullet_suggestions=[],
            diff_data=diff_data,
            passes_completed=1
        )

        yield {"step": "result", "result": result}

    async def _generate_complete(
        self,
        resume: str,
        job: str,
        max_exp: int,
        max_bullets: int
    ) -> Dict:
        """ONE comprehensive API call with detailed prompting."""

        prompt = f"""You are an ELITE ATS (Applicant Tracking System) resume optimization specialist. Your task is to REPHRASE the candidate's EXISTING experiences with job-relevant keywords - NOT invent new ones.

═══════════════════════════════════════════════════════════════════════════════
                    ⚠️⚠️⚠️ CRITICAL: PRESERVE ORIGINAL EXPERIENCE ⚠️⚠️⚠️
═══════════════════════════════════════════════════════════════════════════════

YOU MUST PRESERVE THE CANDIDATE'S ACTUAL WORK HISTORY:

✅ KEEP EXACTLY AS-IS (NEVER CHANGE):
• Company names (e.g., "Google", "Amazon", "Startup XYZ")
• Job titles (e.g., "Software Engineer", "Security Analyst")
• Employment dates (e.g., "Jan 2022 - Present")
• Project names (e.g., "Project Apollo", "Internal Tool")
• Location information
• The CORE ACHIEVEMENT described in each bullet (what they actually did)

✅ YOU MAY ONLY REPHRASE:
• The WORDING of bullet points to incorporate keywords from the job description
• Add relevant technical terms that align with what they ACTUALLY did
• Adjust metrics formatting (wrap in \\textbf{{}})

❌ NEVER DO THIS:
• Invent new companies or experiences
• Change job titles to different roles
• Add projects that don't exist in the original
• Fabricate achievements or metrics that aren't implied by the original
• Replace their actual work with generic/made-up content

EXAMPLE OF CORRECT REPHRASING:
Original: "Built a web scraper using Python"
Rephrased: "Engineered automated Python-based web scraping solution using BeautifulSoup and Selenium, extracting \\textbf{{10,000+}} data points daily with \\textbf{{99\\%}} accuracy for business intelligence reporting"

The rephrased version:
• Keeps the SAME core work (Python web scraper)
• Adds relevant keywords (BeautifulSoup, Selenium, automated, data points)
• Adds reasonable metrics based on context
• Does NOT change it to a completely different project

═══════════════════════════════════════════════════════════════════════════════
                              ORIGINAL RESUME
═══════════════════════════════════════════════════════════════════════════════
{resume}

═══════════════════════════════════════════════════════════════════════════════
                            TARGET JOB DESCRIPTION
═══════════════════════════════════════════════════════════════════════════════
{job}

═══════════════════════════════════════════════════════════════════════════════
                    PHASE 1: EXHAUSTIVE KEYWORD EXTRACTION
═══════════════════════════════════════════════════════════════════════════════

Extract EVERY keyword an ATS would scan for:

TECHNICAL SKILLS:
• Programming languages (Python, Java, JavaScript, TypeScript, Go, Rust, etc.)
• Frameworks & libraries (React, Angular, Vue, Django, Flask, Spring, etc.)
• Databases (SQL, PostgreSQL, MySQL, MongoDB, Redis, DynamoDB, etc.)
• Cloud platforms (AWS, GCP, Azure) with specific services (EC2, S3, Lambda, etc.)
• DevOps tools (Docker, Kubernetes, Jenkins, Terraform, Ansible, etc.)
• Version control (Git, GitHub, GitLab, Bitbucket)

DOMAIN KEYWORDS:
• Methodologies (Agile, Scrum, Kanban, DevOps, CI/CD)
• Architecture patterns (Microservices, REST, GraphQL, Event-driven)
• Security terms (OAuth, JWT, HTTPS, encryption, compliance)
• Industry-specific terminology

SOFT SKILLS & ACTION VERBS:
• Leadership terms (led, managed, mentored, directed)
• Collaboration terms (collaborated, partnered, coordinated)
• Technical verbs (designed, implemented, deployed, architected, optimized)

INCLUDE BOTH:
• Acronyms AND full forms (AWS AND Amazon Web Services)
• Variations (CI/CD, CICD, continuous integration)

═══════════════════════════════════════════════════════════════════════════════
                    PHASE 2: RESUME GENERATION RULES
═══════════════════════════════════════════════════════════════════════════════

⚡ RULE 1: BULLET WORD COUNT (CRITICAL - COUNT EVERY WORD)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Each \\resumeItem MUST be EXACTLY 24-28 words
• Count method: Each space-separated token = 1 word
• Hyphenated terms = 1 word (cloud-native = 1)
• Numbers/metrics = 1 word (\\textbf{{40\\%}} = 1)
• VERIFY by counting before including each bullet

⚡ RULE 2: ACTION VERB DIVERSITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Every bullet STARTS with past-tense action verb
• NO verb used more than TWICE in entire resume
• VERB BANK (use max 2x each):
  - Impact: Spearheaded, Pioneered, Transformed, Revolutionized, Accelerated
  - Technical: Architected, Engineered, Implemented, Deployed, Configured
  - Leadership: Led, Directed, Managed, Mentored, Coordinated, Supervised
  - Creation: Designed, Developed, Built, Created, Established, Launched
  - Optimization: Optimized, Streamlined, Enhanced, Improved, Automated
  - Analysis: Analyzed, Investigated, Evaluated, Assessed, Researched
  - Security: Secured, Fortified, Hardened, Protected, Safeguarded
  - Collaboration: Collaborated, Partnered, Facilitated, Liaised

⚡ RULE 3: QUANTIFICATION (MANDATORY METRICS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• EVERY bullet MUST have at least ONE metric wrapped in \\textbf{{}}
• Formats:
  - Percentages: \\textbf{{40\\%}}, \\textbf{{95\\%}}
  - Numbers: \\textbf{{5}}, \\textbf{{100+}}, \\textbf{{10,000+}}
  - Money: \\textbf{{\\$2M}}, \\textbf{{\\$500K}}
  - Time: \\textbf{{6}} months, \\textbf{{3x}} faster
• If no metric exists, ADD a realistic one based on context

⚡ RULE 4: KEYWORD PLACEMENT PRIORITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRIORITY 1: EXPERIENCE section bullets (PRIMARY - put most keywords here)
PRIORITY 2: PROJECTS section bullets (SECONDARY)
PRIORITY 3: SKILLS section (OVERFLOW ONLY - keywords that couldn't fit above)

• Integrate keywords NATURALLY into bullet context
• Use EXACT phrases from job description
• DO NOT bloat skills section - keep it compact (max 2-3 lines)

⚡ RULE 5: PROTECTED SECTIONS AND EXPERIENCE INTEGRITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⛔ EDUCATION section: Copy EXACTLY as-is from original
⛔ CERTIFICATIONS section: Copy EXACTLY as-is from original
⛔ Contact/Header info: Copy EXACTLY as-is from original
⛔ Company names: NEVER change (keep "Amazon", "Google", etc. exactly)
⛔ Job titles: NEVER change (keep "Software Engineer", etc. exactly)
⛔ Dates: NEVER change employment dates
⛔ Project names: NEVER change project names
✅ EXPERIENCE bullets: REPHRASE with keywords (keep same core achievement)
✅ PROJECTS bullets: REPHRASE with keywords (keep same core project work)
✅ SKILLS: Add keywords NOT used in Experience/Projects bullets

⚡ RULE 6: SKILLS SECTION HANDLING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
After rephrasing Experience and Projects bullets:
1. Identify which job keywords were NOT used in any bullet
2. Add those unused keywords to the appropriate Skills subsection
3. Match the original Skills section structure (e.g., Languages, Frameworks, Tools)
4. Keep skills section compact - don't duplicate keywords already in bullets

⚡ RULE 7: ONE PAGE CONSTRAINT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Maximum {max_exp} experiences (from the original resume, not invented)
• Maximum {max_bullets} bullets per experience
• Keep skills section COMPACT - only add overflow keywords
• MUST fit on ONE page - this is NON-NEGOTIABLE

═══════════════════════════════════════════════════════════════════════════════
                         EXAMPLE CORRECT BULLETS
═══════════════════════════════════════════════════════════════════════════════

✅ (26 words) Architected cloud-native microservices platform on AWS using Docker and Kubernetes, processing \\textbf{{10M+}} daily transactions while reducing infrastructure costs by \\textbf{{35\\%}} through auto-scaling optimization.

✅ (25 words) Spearheaded implementation of CI/CD pipeline using Jenkins and Terraform, decreasing deployment time from \\textbf{{4}} hours to \\textbf{{15}} minutes while achieving \\textbf{{99.9\\%}} deployment success rate.

✅ (27 words) Led cross-functional team of \\textbf{{8}} engineers to migrate legacy monolith to microservices architecture, improving system reliability by \\textbf{{60\\%}} and reducing mean time to recovery by \\textbf{{75\\%}}.

═══════════════════════════════════════════════════════════════════════════════
                         SELF-VERIFICATION CHECKLIST
═══════════════════════════════════════════════════════════════════════════════

⚠️ CRITICAL - VERIFY EXPERIENCE PRESERVATION:
☐ All company names match EXACTLY from original resume
☐ All job titles match EXACTLY from original resume
☐ All employment dates match EXACTLY from original resume
☐ All project names match EXACTLY from original resume
☐ Each bullet describes the SAME core work as the original (just rephrased)
☐ NO new/invented experiences or projects were added

VERIFY for EACH bullet:
☐ Word count is 24-28 (count each word)
☐ Starts with strong action verb in past tense
☐ Contains \\textbf{{metric}}
☐ Verb not used more than 2x total
☐ Keywords integrated naturally into the EXISTING work description

VERIFY overall:
☐ Education section UNCHANGED from original
☐ Certifications section UNCHANGED from original
☐ Skills section contains keywords NOT used in bullets
☐ Total content fits ONE page

═══════════════════════════════════════════════════════════════════════════════
                              OUTPUT FORMAT
═══════════════════════════════════════════════════════════════════════════════

Return ONLY this JSON structure. Use **bold** markdown for metrics in bullets (NOT LaTeX \\textbf):
{{
  "analysis": {{
    "role_title": "extracted job title",
    "company": "company name if mentioned",
    "seniority": "junior/mid/senior/lead"
  }},
  "keywords": [
    "keyword1", "keyword2", "keyword3"
  ],
  "resume_data": {{
    "header": {{
      "name": "candidate full name",
      "location": "City, State",
      "phone": "phone number",
      "email": "email@example.com",
      "linkedin": "linkedin.com/in/profile",
      "website": "personal website or empty string",
      "github": "github.com/username or empty string"
    }},
    "education": [
      {{
        "school": "University Name",
        "location": "City, State",
        "degree": "Degree title exactly as in original",
        "date": "Graduation date",
        "bullets": ["Minor: Finance | GPA: 3.55", "Certifications: ..."]
      }}
    ],
    "experience": [
      {{
        "company": "Company Name EXACTLY as original",
        "location": "City, Country EXACTLY as original",
        "title": "Job Title EXACTLY as original",
        "dates": "Start -- End EXACTLY as original",
        "bullets": [
          "Architected application security deployment pipeline utilizing Docker containerization, CI/CD automation, and Prometheus monitoring, achieving **1000ms** P95 response times with comprehensive observability."
        ]
      }}
    ],
    "projects": [
      {{
        "name": "Project Name EXACTLY as original",
        "url": "https://github.com/... or empty string",
        "subtitle": "Short project description",
        "bullets": [
          "Built automated Python security scanning pipeline processing **100,000** enterprise files across documents, scripts, and spreadsheets, integrating adaptive keyword mapping across **25+** formats."
        ]
      }}
    ],
    "skills": [
      {{
        "category": "Application Security",
        "items": "Skill1, Skill2, Skill3"
      }}
    ]
  }}
}}

CRITICAL REQUIREMENTS:
• "keywords" array must contain ALL extracted keywords (30-50 typically)
• Use **bold** markdown for metrics in bullets (e.g., **40%**, **10,000+**, **5** endpoints)
• DO NOT use LaTeX syntax anywhere — this is pure JSON with markdown bold for metrics
• All header, education, company names, titles, dates must be EXACTLY from the original resume
• Only bullet text and skills items may be rephrased/augmented
• Return ONLY the JSON, no markdown code blocks, no explanations"""

        response = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text

        # Parse JSON response
        try:
            text = re.sub(r'```json\n?|```\n?', '', text).strip()
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON object
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except:
                    pass

            # Last resort: extract LaTeX directly
            latex_match = re.search(r'\\documentclass[\s\S]*\\end\{document\}', text)
            if latex_match:
                return {"latex": latex_match.group(), "keywords": [], "analysis": {}}

            return {"latex": "", "keywords": [], "analysis": {}}

    async def _fix_violations_json(self, resume_data: Dict, validation: ValidationResult) -> Dict:
        """Fix bullet violations in JSON resume_data using fast Haiku model."""

        issues = []
        for b in validation.bullet_analyses:
            if not b.is_valid_length:
                direction = "ADD words" if b.word_count < 24 else "REMOVE words"
                diff = abs(26 - b.word_count)
                issues.append(f"- [{b.word_count} words, need 24-28, {direction} ~{diff}]: \"{b.text[:80]}...\"")
            if not b.has_quantification:
                issues.append(f"- [NEEDS **bold metric**]: \"{b.text[:80]}...\"")
        if validation.repeated_verbs:
            issues.append(f"- [VERBS OVERUSED >2x]: {', '.join(validation.repeated_verbs)}")

        if not issues:
            return resume_data

        prompt = f"""Fix these SPECIFIC bullet issues in the resume JSON. Return the SAME JSON structure with ONLY the bullets fixed.

ISSUES:
{chr(10).join(issues[:10])}

FIX RULES:
- Adjust bullets to EXACTLY 24-28 words
- Add **bold metrics** to bullets missing quantification (e.g., **40%**, **10,000+**)
- Replace overused verbs with alternatives
- DO NOT modify education, header, company names, titles, or dates

RESUME JSON:
{json.dumps(resume_data, indent=2)}

Return ONLY the fixed JSON. No explanations, no code blocks."""

        try:
            response = await self.client.messages.create(
                model="claude-haiku-4-20250514",
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text
            text = re.sub(r'```json\n?|```\n?', '', text).strip()
            fixed = json.loads(text)
            if isinstance(fixed, dict) and fixed.get("experience"):
                return fixed
            return resume_data
        except:
            return resume_data

    async def _reduce_to_one_page_json(self, resume_data: Dict) -> Dict:
        """Reduce JSON resume content to fit one page using Haiku."""

        prompt = f"""This resume exceeds 1 page. Reduce it:

STRATEGIES (apply in order):
1. Remove least relevant skills (keep top 8-10)
2. Reduce each bullet to exactly 24 words (minimum of range)
3. If still too long, keep only 3 experiences with 3 bullets each

RULES:
- DO NOT modify education section
- Keep **bold metric** formatting
- Each bullet must still be 24-28 words
- Preserve important keywords

RESUME JSON:
{json.dumps(resume_data, indent=2)}

Return ONLY the reduced JSON. No explanations, no code blocks."""

        try:
            response = await self.client.messages.create(
                model="claude-haiku-4-20250514",
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text
            text = re.sub(r'```json\n?|```\n?', '', text).strip()
            reduced = json.loads(text)
            if isinstance(reduced, dict) and reduced.get("experience"):
                return reduced
            return resume_data
        except:
            return resume_data

    def _create_diff_json(self, original_latex: str, resume_data: Dict) -> Dict:
        """Create diff between original LaTeX bullets and new JSON bullets."""
        orig = re.findall(r'\\resumeItem\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', original_latex)
        new = FastValidator.extract_bullets_from_json(resume_data)
        return {
            "original_bullets": orig,
            "tailored_bullets": new,
            "added": [b for b in new if b not in orig],
            "removed": [b for b in orig if b not in new],
            "modified_count": len([b for b in new if b not in orig])
        }

    # Keep legacy methods for backward compatibility
    async def _fix_violations(self, latex: str, _validation: ValidationResult) -> str:
        """Legacy: fix violations in LaTeX string."""
        return latex

    async def _reduce_to_one_page(self, latex: str) -> str:
        """Legacy: reduce LaTeX to one page."""
        return latex

    def _create_diff(self, original: str, tailored: str) -> Dict:
        orig = re.findall(r'\\resumeItem\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', original)
        new = re.findall(r'\\resumeItem\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', tailored)
        return {
            "original_bullets": orig,
            "tailored_bullets": new,
            "added": [b for b in new if b not in orig],
            "removed": [b for b in orig if b not in new],
            "modified_count": len([b for b in new if b not in orig])
        }

    async def tailor(
        self,
        resume_latex: str,
        job_description: str,
        max_experiences: int = 4,
        max_bullets: int = 4,
        max_passes: int = 3
    ) -> TailoringResult:
        """Non-streaming version."""
        result = None
        async for update in self.tailor_with_progress(
            resume_latex, job_description, max_experiences, max_bullets, max_passes
        ):
            if update.get("step") == "result":
                result = update["result"]
        return result
