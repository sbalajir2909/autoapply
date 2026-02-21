#!/usr/bin/env python3
"""
ATS-Optimized Resume Builder - CLI Entry Point

Takes a LaTeX resume and job description, outputs a tailored one-page
LaTeX resume with maximum ATS keyword match.

Usage:
    python src/main.py --resume input/master_resume.tex --job input/job_description.txt
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from .extractor import extract_keywords
from .analyzer import analyze_resume
from .generator import generate_tailored_resume


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="ATS-Optimized Resume Builder - Tailor your resume to job descriptions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m src.main --resume input/master_resume.tex --job input/job_description.txt
    python -m src.main -r resume.tex -j job.txt -o custom_output/
    python -m src.main --resume resume.tex --job job.txt --max-experiences 3 --max-bullets 3
        """
    )

    parser.add_argument(
        "-r", "--resume",
        type=str,
        required=True,
        help="Path to master LaTeX resume file"
    )

    parser.add_argument(
        "-j", "--job",
        type=str,
        required=True,
        help="Path to job description text file"
    )

    parser.add_argument(
        "-o", "--output",
        type=str,
        default="output",
        help="Output directory (default: output/)"
    )

    parser.add_argument(
        "--max-experiences",
        type=int,
        default=4,
        help="Maximum number of experience entries to include (default: 4)"
    )

    parser.add_argument(
        "--max-bullets",
        type=int,
        default=4,
        help="Maximum bullets per experience entry (default: 4)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )

    return parser.parse_args()


def load_file(path: str) -> str:
    """Load content from a file."""
    file_path = Path(path)
    if not file_path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        return file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        sys.exit(1)


def save_file(path: Path, content: str) -> None:
    """Save content to a file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"Error writing {path}: {e}", file=sys.stderr)
        sys.exit(1)


def print_summary(keywords, match_result, verbose: bool = False) -> None:
    """Print a summary of the matching results."""
    coverage = match_result.keyword_coverage * 100

    print("\n" + "=" * 60)
    print("ATS RESUME BUILDER - MATCH SUMMARY")
    print("=" * 60)

    # Coverage bar
    filled = int(coverage / 10)
    bar = "█" * filled + "░" * (10 - filled)
    print(f"\nKeyword Coverage: [{bar}] {coverage:.1f}%")

    # Quick stats
    print(f"\nMatched Keywords: {len(match_result.matched_keywords)}")
    print(f"Missing Keywords: {len(match_result.missing_keywords)}")

    if verbose:
        print("\n--- Matched Keywords ---")
        for category, skills in keywords.technical_skills.items():
            matched = [s for s in skills if s in match_result.matched_keywords]
            if matched:
                print(f"  {category}: {', '.join(matched)}")

        if match_result.missing_keywords:
            print("\n--- Missing Keywords ---")
            required_missing = match_result.missing_keywords & keywords.required_keywords
            if required_missing:
                print(f"  Required: {', '.join(required_missing)}")

    print("\n" + "=" * 60)


def main() -> int:
    """Main entry point."""
    args = parse_args()

    print("ATS-Optimized Resume Builder")
    print("-" * 40)

    # Load input files
    print(f"Loading resume: {args.resume}")
    resume_content = load_file(args.resume)

    print(f"Loading job description: {args.job}")
    job_content = load_file(args.job)

    # Extract keywords from job description
    print("\nExtracting keywords from job description...")
    keywords = extract_keywords(job_content)

    if args.verbose:
        print(f"  Found {len(keywords.all_keywords)} unique keywords")
        print(f"  Required: {len(keywords.required_keywords)}")
        print(f"  Preferred: {len(keywords.preferred_keywords)}")
        print(f"  Action verbs: {len(keywords.action_verbs)}")

    # Parse the resume
    print("Analyzing resume structure...")
    resume = analyze_resume(resume_content)

    if args.verbose:
        print(f"  Found {len(resume.sections)} sections")
        total_bullets = len(resume.get_all_bullets())
        print(f"  Total bullet points: {total_bullets}")

    # Generate tailored resume
    print("Generating tailored resume...")
    tailored_latex, match_report = generate_tailored_resume(
        resume=resume,
        keywords=keywords,
        max_experiences=args.max_experiences,
        max_bullets=args.max_bullets
    )

    # Get match result for summary
    from .matcher import ContentMatcher
    matcher = ContentMatcher(keywords, resume)
    match_result = matcher.match()

    # Save output files
    output_dir = Path(args.output)
    resume_path = output_dir / "tailored_resume.tex"
    report_path = output_dir / "match_report.md"

    print(f"\nSaving tailored resume: {resume_path}")
    save_file(resume_path, tailored_latex)

    print(f"Saving match report: {report_path}")
    save_file(report_path, match_report)

    # Print summary
    print_summary(keywords, match_result, args.verbose)

    print(f"\nOutput files saved to: {output_dir}/")
    print("  - tailored_resume.tex")
    print("  - match_report.md")

    # Return appropriate exit code based on coverage
    if match_result.keyword_coverage >= 0.8:
        print("\n✓ Excellent match! Resume is well-tailored for this position.")
        return 0
    elif match_result.keyword_coverage >= 0.6:
        print("\n⚠ Good match. Review match_report.md for improvement suggestions.")
        return 0
    else:
        print("\n⚠ Low match. Consider adding more relevant experience or skills.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
