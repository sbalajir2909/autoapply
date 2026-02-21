"""
Keyword extractor for job descriptions.
Extracts technical skills, tools, soft skills, and action verbs.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Set, Dict, List


# Common technical skill categories
TECH_PATTERNS = {
    'languages': r'\b(Python|Java|JavaScript|TypeScript|C\+\+|C#|Go|Rust|Ruby|PHP|Swift|Kotlin|Scala|R|SQL|Bash|Shell|Perl|MATLAB)\b',
    'frameworks': r'\b(React|Angular|Vue|Django|Flask|FastAPI|Spring|Node\.js|Express|Rails|Laravel|\.NET|TensorFlow|PyTorch|Keras|Scikit-learn|Pandas|NumPy)\b',
    'cloud': r'\b(AWS|Azure|GCP|Google Cloud|Amazon Web Services|EC2|S3|Lambda|ECS|EKS|Kubernetes|Docker|Terraform|CloudFormation|Ansible|Jenkins|CI/CD)\b',
    'databases': r'\b(PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|Cassandra|DynamoDB|Oracle|SQL Server|SQLite|GraphQL|NoSQL)\b',
    'tools': r'\b(Git|GitHub|GitLab|Bitbucket|JIRA|Confluence|Slack|VS Code|IntelliJ|Linux|Unix|Agile|Scrum|Kanban)\b',
    'data': r'\b(Machine Learning|ML|Deep Learning|NLP|Natural Language Processing|Computer Vision|Data Science|Data Engineering|ETL|Data Pipeline|Big Data|Spark|Hadoop|Kafka|Airflow)\b',
    'security': r'\b(Security|OAuth|JWT|Authentication|Authorization|Encryption|SSL|TLS|HTTPS|Penetration Testing|Vulnerability)\b',
    'testing': r'\b(Unit Testing|Integration Testing|E2E|End-to-End|TDD|BDD|Jest|Pytest|JUnit|Selenium|Cypress|Testing)\b',
}

# Action verbs commonly sought in resumes
ACTION_VERBS = {
    'leadership': ['led', 'managed', 'directed', 'coordinated', 'supervised', 'mentored', 'guided', 'oversaw', 'spearheaded'],
    'achievement': ['achieved', 'accomplished', 'delivered', 'exceeded', 'improved', 'increased', 'reduced', 'optimized', 'enhanced'],
    'technical': ['developed', 'designed', 'implemented', 'architected', 'built', 'created', 'engineered', 'integrated', 'deployed'],
    'collaboration': ['collaborated', 'partnered', 'contributed', 'facilitated', 'supported', 'assisted', 'consulted'],
    'analysis': ['analyzed', 'evaluated', 'assessed', 'researched', 'investigated', 'identified', 'diagnosed', 'resolved'],
    'communication': ['presented', 'documented', 'communicated', 'reported', 'translated', 'articulated'],
}

# Soft skills patterns
SOFT_SKILLS = r'\b(communication|leadership|teamwork|problem[- ]solving|critical thinking|analytical|detail[- ]oriented|self[- ]motivated|proactive|collaborative|adaptable|innovative|creative|organized|time management|multitasking|interpersonal|stakeholder|cross[- ]functional)\b'

# Requirement level indicators
REQUIRED_INDICATORS = r'\b(required|must have|essential|mandatory|need|necessary)\b'
PREFERRED_INDICATORS = r'\b(preferred|nice to have|bonus|plus|ideally|desired|optional)\b'


@dataclass
class KeywordProfile:
    """Structured profile of extracted keywords from a job description."""
    technical_skills: Dict[str, Set[str]] = field(default_factory=dict)
    action_verbs: Set[str] = field(default_factory=set)
    soft_skills: Set[str] = field(default_factory=set)
    required_keywords: Set[str] = field(default_factory=set)
    preferred_keywords: Set[str] = field(default_factory=set)
    all_keywords: Set[str] = field(default_factory=set)
    keyword_frequency: Counter = field(default_factory=Counter)
    raw_phrases: List[str] = field(default_factory=list)

    def get_priority_keywords(self) -> List[str]:
        """Return keywords sorted by priority (required first, then by frequency)."""
        required = [(k, self.keyword_frequency.get(k.lower(), 0) + 100) for k in self.required_keywords]
        preferred = [(k, self.keyword_frequency.get(k.lower(), 0) + 50) for k in self.preferred_keywords - self.required_keywords]
        others = [(k, self.keyword_frequency.get(k.lower(), 0)) for k in self.all_keywords - self.required_keywords - self.preferred_keywords]

        all_scored = required + preferred + others
        all_scored.sort(key=lambda x: x[1], reverse=True)
        return [k for k, _ in all_scored]


class JobDescriptionExtractor:
    """Extract and categorize keywords from job descriptions."""

    def __init__(self):
        self.tech_patterns = {k: re.compile(v, re.IGNORECASE) for k, v in TECH_PATTERNS.items()}
        self.soft_skills_pattern = re.compile(SOFT_SKILLS, re.IGNORECASE)
        self.required_pattern = re.compile(REQUIRED_INDICATORS, re.IGNORECASE)
        self.preferred_pattern = re.compile(PREFERRED_INDICATORS, re.IGNORECASE)

    def extract(self, job_description: str) -> KeywordProfile:
        """Extract all keywords from a job description."""
        profile = KeywordProfile()

        # Normalize text
        text = self._normalize_text(job_description)

        # Extract technical skills by category
        for category, pattern in self.tech_patterns.items():
            matches = pattern.findall(text)
            if matches:
                profile.technical_skills[category] = set(m if isinstance(m, str) else m[0] for m in matches)
                for match in matches:
                    skill = match if isinstance(match, str) else match[0]
                    profile.all_keywords.add(skill)
                    profile.keyword_frequency[skill.lower()] += 1

        # Extract soft skills
        soft_matches = self.soft_skills_pattern.findall(text)
        profile.soft_skills = set(s.lower() for s in soft_matches)
        for skill in profile.soft_skills:
            profile.all_keywords.add(skill)
            profile.keyword_frequency[skill.lower()] += 1

        # Extract action verbs used in the job description
        text_lower = text.lower()
        for category, verbs in ACTION_VERBS.items():
            for verb in verbs:
                if verb in text_lower:
                    profile.action_verbs.add(verb)

        # Identify required vs preferred keywords
        self._classify_requirement_level(job_description, profile)

        # Extract key phrases (2-3 word combinations)
        profile.raw_phrases = self._extract_phrases(text)

        # Add years of experience patterns
        self._extract_experience_requirements(text, profile)

        return profile

    def _normalize_text(self, text: str) -> str:
        """Normalize text for processing."""
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Keep important punctuation that affects meaning
        return text.strip()

    def _classify_requirement_level(self, text: str, profile: KeywordProfile):
        """Classify keywords as required or preferred based on context."""
        lines = text.split('\n')

        for i, line in enumerate(lines):
            line_lower = line.lower()
            is_required_section = bool(self.required_pattern.search(line_lower))
            is_preferred_section = bool(self.preferred_pattern.search(line_lower))

            # Look at next few lines after requirement indicators
            context_lines = lines[i:i+5]
            context = ' '.join(context_lines)

            # Extract skills from context
            for pattern in self.tech_patterns.values():
                matches = pattern.findall(context)
                for match in matches:
                    skill = match if isinstance(match, str) else match[0]
                    if is_required_section:
                        profile.required_keywords.add(skill)
                    elif is_preferred_section:
                        profile.preferred_keywords.add(skill)

    def _extract_phrases(self, text: str) -> List[str]:
        """Extract meaningful 2-3 word phrases."""
        # Remove common stop words and extract noun phrases
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'what', 'which', 'who', 'whom', 'when', 'where', 'why', 'how', 'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'also'}

        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        phrases = []

        # Extract bigrams and trigrams
        for i in range(len(words) - 1):
            if words[i] not in stop_words and words[i+1] not in stop_words:
                phrases.append(f"{words[i]} {words[i+1]}")

        for i in range(len(words) - 2):
            if words[i] not in stop_words and words[i+2] not in stop_words:
                phrases.append(f"{words[i]} {words[i+1]} {words[i+2]}")

        # Count and return most common
        phrase_counts = Counter(phrases)
        return [phrase for phrase, count in phrase_counts.most_common(20) if count >= 2]

    def _extract_experience_requirements(self, text: str, profile: KeywordProfile):
        """Extract years of experience requirements."""
        exp_pattern = r'(\d+)\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)'
        matches = re.findall(exp_pattern, text, re.IGNORECASE)
        if matches:
            # Store experience requirements as metadata
            profile.raw_phrases.append(f"{max(int(m) for m in matches)}+ years experience")


def extract_keywords(job_description: str) -> KeywordProfile:
    """Convenience function to extract keywords from a job description."""
    extractor = JobDescriptionExtractor()
    return extractor.extract(job_description)
