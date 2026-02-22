import asyncio
from unittest.mock import patch
from playwright.async_api import async_playwright
from autoapply.apply.classifier import FormField
from autoapply.apply.filler import fill_form

# We define a custom question that will NOT fuzzy match the candidate profile
# meaning it should trigger the LLM fallback.
FIELDS = [
    FormField(name="why_work_here", label="Why do you want to work at our company? (Behavioral)", field_type="textarea", required=True, selector="#why_work_here")
]

# Mock profile
PROFILE = {
     "first_name": "Test",
     "last_name": "User",
     "experience": "5 years in software engineering building AI tools.",
     "current_job_description": "We are looking for a passionate software engineer to build autonomous AI agents."
}

MOCK_HTML = """
<!DOCTYPE html>
<html>
<body>
    <label for="why_work_here">Why do you want to work at our company? (Behavioral)</label>
    <textarea id="why_work_here" name="why_work_here" required></textarea>
</body>
</html>
"""

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_content(MOCK_HTML)
        
        print("Running form filler...")
        # Patch synthesize_answer where it's actually imported in filler.py using the side effect approach
        # Note: in Python 3.11 we just patch the actual import location or use `with patch("autoapply.apply.filler.synthesize_answer")` if imported directly.
        # But wait, filler.py DOES NOT import synthesize_answer at the top of the file, it imports it INSIDE the function `fill_form`
        # Because it imports inside the function, we can just patch `autoapply.llm.synthesize_answer` directly!
        with patch("autoapply.llm.synthesize_answer") as mock_synth:
            mock_synth.return_value = "Because I am very passionate about this company."
            filled = await fill_form(page, FIELDS, "dummy.pdf", PROFILE)
            
        print(f"Fill output dict: {filled}")
        
        # Check what was actually typed into the DOM
        result_text = await page.locator("#why_work_here").input_value()
        print(f"Final Input Value:\n{result_text}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
