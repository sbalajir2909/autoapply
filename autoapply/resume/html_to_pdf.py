"""
HTML-to-PDF conversion using Playwright headless Chromium.

Renders a self-contained HTML string to a Letter-size PDF.
No LaTeX dependency required.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def html_to_pdf(html: str, output_path: str) -> str:
    """
    Render an HTML string to a PDF file using Playwright.

    Args:
        html:        Complete HTML document string.
        output_path: Destination path for the PDF file.

    Returns:
        The output_path on success, empty string on failure.
    """
    from playwright.async_api import async_playwright

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.set_content(html, wait_until="networkidle")

            await page.pdf(
                path=str(output),
                format="Letter",
                print_background=True,
                margin={
                    "top": "0in",
                    "bottom": "0in",
                    "left": "0in",
                    "right": "0in",
                },
            )

            await browser.close()

        if output.exists() and output.stat().st_size > 0:
            logger.info(f"PDF generated: {output_path} ({output.stat().st_size} bytes)")
            return str(output_path)

        logger.warning(f"PDF file empty or missing: {output_path}")
        return ""

    except Exception as e:
        logger.error(f"HTML-to-PDF failed: {e}")
        return ""
