"""
ATS-Optimized Resume Builder

A tool that takes a LaTeX resume and job description, then outputs
a tailored one-page resume with maximum ATS keyword match.
"""

from .extractor import extract_keywords, KeywordProfile, JobDescriptionExtractor
from .analyzer import analyze_resume, ParsedResume, LaTeXResumeAnalyzer
from .matcher import match_resume_to_job, ContentMatcher, MatchResult
from .generator import generate_tailored_resume, ResumeGenerator, MatchReportGenerator

__version__ = "1.0.0"
__all__ = [
    "extract_keywords",
    "analyze_resume",
    "match_resume_to_job",
    "generate_tailored_resume",
    "KeywordProfile",
    "ParsedResume",
    "MatchResult",
    "JobDescriptionExtractor",
    "LaTeXResumeAnalyzer",
    "ContentMatcher",
    "ResumeGenerator",
    "MatchReportGenerator",
]
