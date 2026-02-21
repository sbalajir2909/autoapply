"""
Content matcher and scorer.
Scores resume content against job description keywords and selects optimal content.
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional
from collections import Counter

from .extractor import KeywordProfile
from .analyzer import ParsedResume, BulletPoint, Experience, SectionType


@dataclass
class MatchResult:
    """Result of matching resume to job description."""
    matched_keywords: Set[str] = field(default_factory=set)
    missing_keywords: Set[str] = field(default_factory=set)
    keyword_coverage: float = 0.0
    selected_experiences: List[Tuple[Experience, float]] = field(default_factory=list)
    selected_bullets: List[Tuple[BulletPoint, float]] = field(default_factory=list)
    skill_coverage: Dict[str, List[str]] = field(default_factory=dict)
    action_verbs_used: Set[str] = field(default_factory=set)
    recommendations: List[str] = field(default_factory=list)


class ContentMatcher:
    """Match and score resume content against job requirements."""

    # Weights for scoring
    REQUIRED_KEYWORD_WEIGHT = 3.0
    PREFERRED_KEYWORD_WEIGHT = 2.0
    REGULAR_KEYWORD_WEIGHT = 1.0
    METRIC_BONUS = 1.5
    ACTION_VERB_MATCH_BONUS = 0.5

    def __init__(self, keyword_profile: KeywordProfile, resume: ParsedResume):
        self.keywords = keyword_profile
        self.resume = resume
        self.used_verbs: Set[str] = set()
        self.used_keywords: Counter = Counter()

    def match(self) -> MatchResult:
        """Perform full matching analysis."""
        result = MatchResult()

        # Score all bullets
        all_bullets = self.resume.get_all_bullets()
        scored_bullets = [(b, self._score_bullet(b)) for b in all_bullets]
        scored_bullets.sort(key=lambda x: x[1], reverse=True)
        result.selected_bullets = scored_bullets

        # Score experiences
        for section in self.resume.sections:
            if section.section_type in [SectionType.EXPERIENCE, SectionType.PROJECTS]:
                for exp in section.experiences:
                    score = self._score_experience(exp)
                    result.selected_experiences.append((exp, score))

        result.selected_experiences.sort(key=lambda x: x[1], reverse=True)

        # Calculate keyword coverage
        all_target_keywords = self.keywords.all_keywords
        resume_text = self._get_full_resume_text()
        resume_text_lower = resume_text.lower()

        for keyword in all_target_keywords:
            if keyword.lower() in resume_text_lower:
                result.matched_keywords.add(keyword)
            else:
                result.missing_keywords.add(keyword)

        if all_target_keywords:
            result.keyword_coverage = len(result.matched_keywords) / len(all_target_keywords)

        # Track skill coverage
        result.skill_coverage = self._analyze_skill_coverage()

        # Track used action verbs
        result.action_verbs_used = self.used_verbs

        # Generate recommendations
        result.recommendations = self._generate_recommendations(result)

        return result

    def _score_bullet(self, bullet: BulletPoint) -> float:
        """Score a single bullet point against job keywords."""
        score = 0.0
        text_lower = bullet.text.lower()

        # Score keyword matches
        for keyword in self.keywords.required_keywords:
            if keyword.lower() in text_lower:
                score += self.REQUIRED_KEYWORD_WEIGHT
                self.used_keywords[keyword] += 1

        for keyword in self.keywords.preferred_keywords:
            if keyword.lower() in text_lower and keyword not in self.keywords.required_keywords:
                score += self.PREFERRED_KEYWORD_WEIGHT
                self.used_keywords[keyword] += 1

        for keyword in self.keywords.all_keywords:
            if keyword.lower() in text_lower and keyword not in self.keywords.required_keywords and keyword not in self.keywords.preferred_keywords:
                score += self.REGULAR_KEYWORD_WEIGHT
                self.used_keywords[keyword] += 1

        # Bonus for metrics
        if bullet.metrics:
            score += self.METRIC_BONUS * len(bullet.metrics)

        # Bonus for matching action verbs
        if bullet.action_verb:
            verb_lower = bullet.action_verb.lower()
            for verb in self.keywords.action_verbs:
                if verb in verb_lower or verb_lower.startswith(verb):
                    score += self.ACTION_VERB_MATCH_BONUS
                    self.used_verbs.add(verb)
                    break

        bullet.score = score
        return score

    def _score_experience(self, exp: Experience) -> float:
        """Score an experience entry."""
        score = 0.0

        # Score based on title/company keyword matches
        title_text = f"{exp.title} {exp.company}".lower()
        for keyword in self.keywords.all_keywords:
            if keyword.lower() in title_text:
                score += self.REQUIRED_KEYWORD_WEIGHT

        # Sum bullet scores
        for bullet in exp.bullets:
            score += bullet.score

        return score

    def _get_full_resume_text(self) -> str:
        """Get all text content from the resume."""
        text_parts = []

        for section in self.resume.sections:
            text_parts.append(section.content)
            for exp in section.experiences:
                text_parts.append(exp.get_plain_text())
            for skill_list in section.skills.values():
                text_parts.extend(skill_list)

        return ' '.join(text_parts)

    def _analyze_skill_coverage(self) -> Dict[str, List[str]]:
        """Analyze which skill categories are covered."""
        coverage = {}

        resume_skills = self.resume.get_all_skills()
        resume_skills_lower = {s.lower() for s in resume_skills}

        for category, skills in self.keywords.technical_skills.items():
            matched = []
            for skill in skills:
                if skill.lower() in resume_skills_lower:
                    matched.append(skill)
            if matched:
                coverage[category] = matched

        return coverage

    def _generate_recommendations(self, result: MatchResult) -> List[str]:
        """Generate recommendations for improving the resume."""
        recommendations = []

        # Missing required keywords
        missing_required = result.missing_keywords & self.keywords.required_keywords
        if missing_required:
            recommendations.append(
                f"Consider adding these required skills: {', '.join(list(missing_required)[:5])}"
            )

        # Low coverage warning
        if result.keyword_coverage < 0.6:
            recommendations.append(
                f"Keyword coverage is {result.keyword_coverage:.0%}. Consider tailoring content more."
            )

        # Missing action verbs
        unused_verbs = self.keywords.action_verbs - result.action_verbs_used
        if unused_verbs:
            recommendations.append(
                f"Consider using these action verbs: {', '.join(list(unused_verbs)[:3])}"
            )

        return recommendations

    def select_best_content(self, max_bullets_per_exp: int = 4, max_experiences: int = 4) -> Dict:
        """Select optimal content for the tailored resume."""
        result = self.match()

        selected = {
            'experiences': [],
            'bullets_by_exp': {},
            'matched_keywords': result.matched_keywords,
            'coverage': result.keyword_coverage,
        }

        # Select top experiences
        seen_keywords: Counter = Counter()
        used_verbs: Set[str] = set()

        for exp, score in result.selected_experiences[:max_experiences]:
            # Score and select bullets avoiding repetition
            bullet_scores = []
            for bullet in exp.bullets:
                # Penalize if keywords already heavily used
                adjusted_score = bullet.score
                for keyword in self._get_bullet_keywords(bullet):
                    if seen_keywords[keyword] > 0:
                        adjusted_score -= 0.5 * seen_keywords[keyword]

                # Penalize repeated action verbs
                if bullet.action_verb and bullet.action_verb.lower() in used_verbs:
                    adjusted_score -= 1.0

                bullet_scores.append((bullet, adjusted_score))

            # Sort and select top bullets
            bullet_scores.sort(key=lambda x: x[1], reverse=True)
            selected_bullets = []

            for bullet, score in bullet_scores[:max_bullets_per_exp]:
                if score > 0 or len(selected_bullets) < 2:  # Keep at least 2 bullets
                    selected_bullets.append(bullet)
                    # Track used keywords and verbs
                    for keyword in self._get_bullet_keywords(bullet):
                        seen_keywords[keyword] += 1
                    if bullet.action_verb:
                        used_verbs.add(bullet.action_verb.lower())

            selected['experiences'].append(exp)
            selected['bullets_by_exp'][id(exp)] = selected_bullets

        return selected

    def _get_bullet_keywords(self, bullet: BulletPoint) -> List[str]:
        """Get keywords found in a bullet."""
        text_lower = bullet.text.lower()
        return [k for k in self.keywords.all_keywords if k.lower() in text_lower]


class DuplicationChecker:
    """Check for and prevent content duplication."""

    def __init__(self):
        self.seen_phrases: Set[str] = set()
        self.verb_counts: Counter = Counter()
        self.skill_mentions: Counter = Counter()

    def is_duplicate(self, text: str, threshold: float = 0.7) -> bool:
        """Check if text is too similar to previously seen content."""
        normalized = self._normalize(text)

        for seen in self.seen_phrases:
            similarity = self._jaccard_similarity(normalized, seen)
            if similarity > threshold:
                return True

        self.seen_phrases.add(normalized)
        return False

    def check_verb_repetition(self, verb: str, max_uses: int = 2) -> bool:
        """Check if a verb has been used too many times."""
        verb_lower = verb.lower()
        if self.verb_counts[verb_lower] >= max_uses:
            return True
        self.verb_counts[verb_lower] += 1
        return False

    def check_skill_repetition(self, skill: str) -> bool:
        """Track skill mentions and warn if repeated."""
        skill_lower = skill.lower()
        self.skill_mentions[skill_lower] += 1
        return self.skill_mentions[skill_lower] > 1

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        # Remove punctuation and extra whitespace
        text = re.sub(r'[^\w\s]', '', text.lower())
        return ' '.join(text.split())

    def _jaccard_similarity(self, text1: str, text2: str) -> float:
        """Calculate Jaccard similarity between two texts."""
        words1 = set(text1.split())
        words2 = set(text2.split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union)


def match_resume_to_job(keyword_profile: KeywordProfile, resume: ParsedResume) -> MatchResult:
    """Convenience function to match resume to job description."""
    matcher = ContentMatcher(keyword_profile, resume)
    return matcher.match()
