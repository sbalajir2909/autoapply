"""Shared URL detection utilities for the discovery module."""


def _detect_ats(url: str) -> str:
    """Guess the ATS platform from a job URL."""
    url_lower = url.lower()
    if "greenhouse" in url_lower:
        return "greenhouse"
    if "lever" in url_lower:
        return "lever"
    if "linkedin" in url_lower:
        return "linkedin"
    if "indeed" in url_lower:
        return "indeed"
    if "workday" in url_lower or "myworkdayjobs" in url_lower:
        return "workday"
    if "icims" in url_lower:
        return "icims"
    if "taleo" in url_lower:
        return "taleo"
    return "unknown"
