"""
Auth wall detection and handling for ATS portals.

Three-layer approach:
    1. Session reuse (cookies persisted via PlaywrightScraper)
    2. IMAP email monitoring for OTP/verification codes
    3. Pause-and-prompt via dashboard when automated methods fail

Handles: login walls, OTP challenges, email verification, account creation.
"""

import asyncio
import json
import logging
import random
import re
import string
from pathlib import Path
from typing import Optional

from .email_monitor import EmailMonitor
from ..config import (
    CANDIDATE_PROFILE,
    CREDENTIALS_PATH,
    SESSION_DIR,
    AUTH_PAUSE_TIMEOUT,
    IMAP_USER,
    IMAP_PASSWORD,
)
from ..events import emit, EventType, bus

logger = logging.getLogger(__name__)


# URL patterns that indicate an auth wall
AUTH_URL_PATTERNS = [
    r"/login",
    r"/signin",
    r"/sign-in",
    r"/auth",
    r"/sso",
    r"/account/create",
    r"/register",
    r"/createaccount",
    r"myworkdayjobs\.com.*/login",
    r"linkedin\.com/login",
    r"linkedin\.com/checkpoint",
]

# HTML content patterns that indicate auth walls
AUTH_HTML_PATTERNS = [
    r'<input[^>]*type=["\']password["\']',  # password field
    r'sign\s*in\s*to\s*(?:your|)\s*account',
    r'create\s*(?:an?\s*)?account',
    r'verify\s*your\s*(?:email|identity)',
    r'enter\s*(?:your\s*)?(?:verification|security)\s*code',
    r'two-factor|2fa|mfa',
    r'one-time\s*(?:password|code|passcode)',
]

# Patterns that indicate OTP/verification challenge (not just login)
OTP_HTML_PATTERNS = [
    r'enter\s*(?:the\s*)?(?:verification|security|confirmation)\s*code',
    r'(?:we|code)\s*sent\s*(?:a\s*)?(?:code|email|verification)',
    r'one-time\s*(?:password|code|passcode)',
    r'check\s*your\s*(?:email|inbox)',
    r'input[^>]*(?:otp|verification.?code|security.?code)',
]

# Patterns that indicate email verification required
EMAIL_VERIFY_PATTERNS = [
    r'verify\s*your\s*email',
    r'confirmation\s*(?:email|link)\s*(?:has been|was)\s*sent',
    r'check\s*your\s*(?:email|inbox)\s*(?:to\s*)?(?:verify|confirm|activate)',
    r'click\s*(?:the|on\s*the)\s*(?:link|button)\s*(?:in|we)',
]


def _detect_auth_type(url: str, html: str) -> str:
    """
    Classify what type of auth wall is present.

    Returns one of:
        "none"                 — no auth wall detected
        "login_required"       — standard login form
        "otp_required"         — OTP/verification code input
        "email_verify_required"— click verification link in email
        "account_creation"     — registration/sign-up form
    """
    url_lower = url.lower()
    html_lower = html.lower()

    # Check URL patterns first
    is_auth_url = any(re.search(p, url_lower) for p in AUTH_URL_PATTERNS)

    # Check for OTP challenge
    if any(re.search(p, html_lower) for p in OTP_HTML_PATTERNS):
        return "otp_required"

    # Check for email verification
    if any(re.search(p, html_lower) for p in EMAIL_VERIFY_PATTERNS):
        return "email_verify_required"

    # Check for account creation
    if is_auth_url and re.search(r"(?:create|register|sign.?up)", url_lower):
        return "account_creation"
    if re.search(r'create\s*(?:an?\s*)?account', html_lower) and re.search(
        r'<input[^>]*type=["\']password["\']', html_lower
    ):
        # Has both "create account" text and password fields
        if re.search(r'confirm\s*password|re.?enter\s*password', html_lower):
            return "account_creation"

    # Check for generic login
    if is_auth_url:
        return "login_required"
    if any(re.search(p, html_lower) for p in AUTH_HTML_PATTERNS):
        # Only flag as login if there's a password field and login-like text
        has_password = bool(re.search(r'<input[^>]*type=["\']password["\']', html_lower))
        has_login_text = bool(re.search(r'sign\s*in|log\s*in', html_lower))
        if has_password and has_login_text:
            return "login_required"

    return "none"


async def detect_auth_wall(page) -> str:
    """
    Check the current page for auth walls.

    Args:
        page: Playwright Page object.

    Returns:
        Auth type string: "none", "login_required", "otp_required",
        "email_verify_required", "account_creation".
    """
    url = page.url
    html = await page.content()
    auth_type = _detect_auth_type(url, html)

    if auth_type != "none":
        emit(EventType.AUTH_WALL_DETECTED,
             f"Auth wall detected: {auth_type} at {url}",
             auth_type=auth_type, url=url)
        logger.info(f"[Auth] Wall detected: {auth_type} at {url}")

    return auth_type


async def handle_auth(page, auth_type: str, original_url: str = "") -> bool:
    """
    Handle an auth wall. Tries automated methods first, falls back to pause-and-prompt.

    Args:
        page: Playwright Page object on the auth wall.
        auth_type: Type from detect_auth_wall().
        original_url: The job application URL we were trying to reach.

    Returns:
        True if auth was resolved, False if it failed/timed out.
    """
    emit(EventType.AUTH_LOGIN_ATTEMPT,
         f"Attempting to handle auth: {auth_type}",
         auth_type=auth_type, url=page.url)

    if auth_type == "login_required":
        result = await _handle_login(page)
    elif auth_type == "otp_required":
        result = await _handle_otp(page)
    elif auth_type == "email_verify_required":
        result = await _handle_email_verification(page, original_url)
    elif auth_type == "account_creation":
        result = await _handle_account_creation(page, original_url)
    else:
        result = False

    if not result:
        # Fallback: pause and ask user
        result = await _pause_for_user(page, auth_type)

    if result:
        emit(EventType.AUTH_SUCCESS,
             f"Auth resolved: {auth_type}",
             auth_type=auth_type, url=page.url)
    else:
        emit(EventType.AUTH_FAILURE,
             f"Auth failed: {auth_type}",
             auth_type=auth_type, url=page.url)

    return result


async def _handle_login(page) -> bool:
    """
    Try to log in using stored credentials.

    Looks up credentials in credentials.json by domain,
    fills email + password, and clicks sign in.
    """
    creds = _load_credentials(page.url)
    if not creds:
        # Try using candidate profile email + a stored password
        profile_email = CANDIDATE_PROFILE.get("email", "")
        if not profile_email:
            return False
        creds = {"email": profile_email, "password": ""}

    if not creds.get("password"):
        # No password stored — can't auto-login
        return False

    emit(EventType.AUTH_LOGIN_ATTEMPT,
         f"Attempting auto-login with stored credentials",
         url=page.url)

    try:
        # Fill email field
        email_selectors = [
            'input[type="email"]',
            'input[name="email"]',
            'input[name="username"]',
            'input[id*="email"]',
            'input[id*="username"]',
            'input[autocomplete="email"]',
            'input[autocomplete="username"]',
        ]
        for sel in email_selectors:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(creds["email"])
                break

        # Fill password field
        password_el = page.locator('input[type="password"]').first
        if await password_el.count() > 0 and await password_el.is_visible():
            await password_el.fill(creds["password"])

        # Click sign in button
        sign_in_selectors = [
            'button[type="submit"]',
            'button:has-text("Sign In")',
            'button:has-text("Log In")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'input[type="submit"]',
        ]
        for sel in sign_in_selectors:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                break

        await asyncio.sleep(3)

        # Check if we're past the login page
        new_auth = await detect_auth_wall(page)
        if new_auth == "none":
            return True
        elif new_auth == "otp_required":
            # Login triggered OTP — handle it
            return await _handle_otp(page)

        return False

    except Exception as e:
        logger.error(f"[Auth] Login error: {e}")
        return False


async def _handle_otp(page) -> bool:
    """
    Handle OTP challenge by monitoring email via IMAP.

    Falls back to dashboard pause if IMAP not configured or OTP not received.
    """
    # Try IMAP first
    if IMAP_USER and IMAP_PASSWORD:
        monitor = EmailMonitor()
        code = monitor.wait_for_otp(timeout=120)
        if code:
            return await _fill_otp_on_page(page, code)

    # IMAP failed or not configured — try pause-and-prompt
    return False  # caller will invoke _pause_for_user


async def _fill_otp_on_page(page, code: str) -> bool:
    """Fill an OTP code into the input field on the page."""
    try:
        otp_selectors = [
            'input[name*="otp"]',
            'input[name*="code"]',
            'input[name*="verification"]',
            'input[id*="otp"]',
            'input[id*="code"]',
            'input[id*="verification"]',
            'input[autocomplete="one-time-code"]',
            'input[type="tel"][maxlength]',
            'input[type="text"][maxlength="6"]',
            'input[type="number"][maxlength]',
        ]
        for sel in otp_selectors:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(code)
                await asyncio.sleep(0.5)

                # Try clicking verify/submit button
                verify_selectors = [
                    'button[type="submit"]',
                    'button:has-text("Verify")',
                    'button:has-text("Submit")',
                    'button:has-text("Confirm")',
                    'button:has-text("Continue")',
                ]
                for btn_sel in verify_selectors:
                    btn = page.locator(btn_sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        break

                await asyncio.sleep(3)

                # Check if OTP was accepted
                new_auth = await detect_auth_wall(page)
                return new_auth == "none"

        return False
    except Exception as e:
        logger.error(f"[Auth] OTP fill error: {e}")
        return False


async def _handle_email_verification(page, original_url: str) -> bool:
    """
    Handle email verification by monitoring inbox for verification link.

    Opens the verification link in a new tab, then navigates back
    to the original application URL.
    """
    if not IMAP_USER or not IMAP_PASSWORD:
        return False

    monitor = EmailMonitor()
    link = monitor.wait_for_verification_link(timeout=120)
    if not link:
        return False

    emit(EventType.AUTH_VERIFY_WAITING,
         f"Opening verification link...",
         link=link)

    try:
        # Open verification link in the same context (shares cookies)
        context = page.context
        verify_page = await context.new_page()
        await verify_page.goto(link, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3)
        await verify_page.close()

        # Navigate back to the original application
        if original_url:
            await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

        # Check if we're past the auth wall
        new_auth = await detect_auth_wall(page)
        return new_auth == "none"

    except Exception as e:
        logger.error(f"[Auth] Email verification error: {e}")
        return False


async def _handle_account_creation(page, original_url: str) -> bool:
    """
    Handle account creation by filling the registration form.

    Generates a password, fills the form, submits, then waits for
    email verification.
    """
    profile = CANDIDATE_PROFILE
    email_addr = profile.get("email", "")
    if not email_addr:
        return False

    # Generate a password
    password = _generate_password(email_addr)

    emit(EventType.AUTH_LOGIN_ATTEMPT,
         f"Creating account with {email_addr}",
         url=page.url)

    try:
        # Fill email
        email_selectors = [
            'input[type="email"]',
            'input[name="email"]',
            'input[name*="email"]',
            'input[id*="email"]',
            'input[autocomplete="email"]',
        ]
        for sel in email_selectors:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(email_addr)
                break

        # Fill password fields (main + confirm)
        pw_fields = page.locator('input[type="password"]')
        pw_count = await pw_fields.count()
        for i in range(pw_count):
            await pw_fields.nth(i).fill(password)

        # Fill name fields if present
        first_name_sels = ['input[name*="first"]', 'input[id*="first"]']
        for sel in first_name_sels:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(profile.get("first_name", ""))
                break

        last_name_sels = ['input[name*="last"]', 'input[id*="last"]']
        for sel in last_name_sels:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(profile.get("last_name", ""))
                break

        # Accept terms checkbox if present
        terms_sels = [
            'input[type="checkbox"][name*="terms"]',
            'input[type="checkbox"][name*="agree"]',
            'input[type="checkbox"][id*="terms"]',
        ]
        for sel in terms_sels:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.check()
                break

        # Click create/register button
        create_selectors = [
            'button[type="submit"]',
            'button:has-text("Create Account")',
            'button:has-text("Register")',
            'button:has-text("Sign Up")',
            'button:has-text("Create")',
            'input[type="submit"]',
        ]
        for sel in create_selectors:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                break

        await asyncio.sleep(3)

        # Save credentials for future logins
        _save_credentials(page.url, email_addr, password)

        # Check what happened after account creation
        new_auth = await detect_auth_wall(page)

        if new_auth == "email_verify_required":
            # Need to verify email — use IMAP
            return await _handle_email_verification(page, original_url)
        elif new_auth == "otp_required":
            return await _handle_otp(page)
        elif new_auth == "none":
            return True

        return False

    except Exception as e:
        logger.error(f"[Auth] Account creation error: {e}")
        return False


async def _pause_for_user(page, reason: str) -> bool:
    """
    Pause pipeline and wait for user action via dashboard.

    Takes a screenshot, emits AUTH_PAUSE_FOR_USER event,
    and blocks until the dashboard provides a response.
    """
    # Take screenshot for dashboard display
    screenshot_path = ""
    try:
        screenshot_dir = Path(SESSION_DIR)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshot_dir / "auth_screenshot.png")
        await page.screenshot(path=screenshot_path, full_page=False)
    except Exception:
        pass

    emit(EventType.AUTH_PAUSE_FOR_USER,
         f"Manual action required: {reason}. Check dashboard.",
         auth_type=reason, url=page.url,
         screenshot=screenshot_path)

    logger.info(f"[Auth] Pausing for user action: {reason}")
    print(f"[Auth] PAUSED — manual action required: {reason}")
    print(f"[Auth] Open the dashboard to provide OTP/code or handle verification.")

    # Block and wait for dashboard response
    response = bus.wait_for_auth_response(timeout=AUTH_PAUSE_TIMEOUT)

    if response:
        # Response could be an OTP code
        if response.strip().isdigit() and 4 <= len(response.strip()) <= 8:
            return await _fill_otp_on_page(page, response.strip())
        # Or it could be "done" meaning user handled it manually
        if response.strip().lower() in ("done", "ok", "continue", "resume"):
            await asyncio.sleep(2)
            new_auth = await detect_auth_wall(page)
            return new_auth == "none"

    emit(EventType.AUTH_TIMEOUT,
         f"User did not respond within {AUTH_PAUSE_TIMEOUT}s",
         auth_type=reason)
    return False


# ---------------------------------------------------------------------------
# Credential management helpers
# ---------------------------------------------------------------------------

def _generate_password(email_addr: str) -> str:
    """Generate a secure password for account creation."""
    prefix = email_addr.split("@")[0][:8]
    random_part = "".join(random.choices(string.digits, k=4))
    return f"{prefix}Aa1!{random_part}"


def _load_credentials(url: str) -> Optional[dict]:
    """Load stored credentials for a domain."""
    creds_file = Path(CREDENTIALS_PATH)
    if not creds_file.exists():
        return None

    try:
        all_creds = json.loads(creds_file.read_text(encoding="utf-8"))
        domain = _extract_domain(url)
        return all_creds.get(domain)
    except Exception:
        return None


def _save_credentials(url: str, email_addr: str, password: str):
    """Save credentials for a domain."""
    creds_file = Path(CREDENTIALS_PATH)
    creds_file.parent.mkdir(parents=True, exist_ok=True)

    all_creds = {}
    if creds_file.exists():
        try:
            all_creds = json.loads(creds_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    domain = _extract_domain(url)
    all_creds[domain] = {"email": email_addr, "password": password}
    creds_file.write_text(json.dumps(all_creds, indent=2), encoding="utf-8")

    logger.info(f"[Auth] Saved credentials for domain: {domain}")


def _extract_domain(url: str) -> str:
    """Extract base domain from a URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    # Get the main domain (e.g., "myworkdayjobs.com" from "company.myworkdayjobs.com")
    parts = parsed.netloc.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return parsed.netloc
