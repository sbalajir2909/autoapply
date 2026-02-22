"""
Gmail IMAP monitor for OTP codes and verification emails.

Polls the inbox for recent emails matching known ATS sender patterns,
extracts OTP codes or verification links, and returns them for the
auth handler to use.

Uses Python built-in imaplib + email — no extra dependencies.
"""

import email
import imaplib
import re
import time
import logging
from email.header import decode_header
from typing import Optional

from ..config import IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD
from ..events import emit, EventType

logger = logging.getLogger(__name__)

# Sender patterns for known ATS platforms
ATS_SENDER_PATTERNS = [
    r"workday",
    r"greenhouse",
    r"lever",
    r"icims",
    r"taleo",
    r"smartrecruiters",
    r"noreply",
    r"no-reply",
    r"do-not-reply",
    r"verify",
    r"confirm",
    r"security",
    r"account",
]

# OTP extraction patterns (order matters — more specific first)
OTP_PATTERNS = [
    re.compile(r"(?:verification|security|confirmation)\s*code\s*(?:is|:)\s*(\d{4,8})", re.I),
    re.compile(r"(?:your|the)\s*(?:code|otp|pin)\s*(?:is|:)\s*(\d{4,8})", re.I),
    re.compile(r"(?:enter|use)\s*(?:the\s*)?(?:code|otp)\s*(\d{4,8})", re.I),
    re.compile(r"(\d{4,8})\s*(?:is your|as your)\s*(?:verification|security|confirmation)", re.I),
    re.compile(r">\s*(\d{4,8})\s*<", re.I),  # OTP in HTML tags
    re.compile(r"(?:code|otp|pin)\s*:\s*(\d{4,8})", re.I),
]

# Verification link patterns
VERIFY_LINK_PATTERNS = [
    re.compile(r'(https?://[^\s"<>]+(?:verify|confirm|activate|validate|token)[^\s"<>]*)', re.I),
    re.compile(r'href="(https?://[^"]+(?:verify|confirm|activate|validate)[^"]*)"', re.I),
]


class EmailMonitor:
    """Monitor Gmail inbox via IMAP for OTP codes and verification links."""

    def __init__(self):
        self._conn: Optional[imaplib.IMAP4_SSL] = None

    def _connect(self) -> bool:
        """Connect to Gmail IMAP. Returns True on success."""
        if not IMAP_USER or not IMAP_PASSWORD:
            logger.warning("[EmailMonitor] IMAP credentials not configured")
            return False
        try:
            self._conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            self._conn.login(IMAP_USER, IMAP_PASSWORD)
            return True
        except Exception as e:
            logger.error(f"[EmailMonitor] IMAP connection failed: {e}")
            self._conn = None
            return False

    def _disconnect(self):
        """Close IMAP connection."""
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def wait_for_otp(
        self,
        sender_filter: str = "",
        timeout: int = 120,
        poll_interval: int = 5,
    ) -> Optional[str]:
        """
        Poll inbox for an OTP code email.

        Args:
            sender_filter: Regex pattern to filter sender addresses (e.g. "workday|greenhouse").
            timeout: Max seconds to wait before giving up.
            poll_interval: Seconds between inbox checks.

        Returns:
            The extracted OTP code string, or None if not found within timeout.
        """
        if not self._connect():
            return None

        emit(EventType.AUTH_OTP_WAITING,
             f"Monitoring inbox for OTP code (timeout: {timeout}s)...",
             timeout=timeout)

        start_time = time.time()
        # Track emails we've already checked
        seen_ids = set()

        try:
            while time.time() - start_time < timeout:
                code = self._check_for_otp(sender_filter, seen_ids)
                if code:
                    emit(EventType.AUTH_SUCCESS,
                         f"OTP code received: {code}",
                         code=code)
                    return code
                time.sleep(poll_interval)

            emit(EventType.AUTH_TIMEOUT,
                 f"OTP not received within {timeout}s")
            return None
        finally:
            self._disconnect()

    def wait_for_verification_link(
        self,
        sender_filter: str = "",
        timeout: int = 120,
        poll_interval: int = 5,
    ) -> Optional[str]:
        """
        Poll inbox for a verification/confirmation link.

        Returns:
            The verification URL string, or None if not found within timeout.
        """
        if not self._connect():
            return None

        emit(EventType.AUTH_VERIFY_WAITING,
             f"Monitoring inbox for verification link (timeout: {timeout}s)...",
             timeout=timeout)

        start_time = time.time()
        seen_ids = set()

        try:
            while time.time() - start_time < timeout:
                link = self._check_for_verification_link(sender_filter, seen_ids)
                if link:
                    emit(EventType.AUTH_SUCCESS,
                         f"Verification link received",
                         link=link)
                    return link
                time.sleep(poll_interval)

            emit(EventType.AUTH_TIMEOUT,
                 f"Verification link not received within {timeout}s")
            return None
        finally:
            self._disconnect()

    def _check_for_otp(self, sender_filter: str, seen_ids: set) -> Optional[str]:
        """Check inbox for new emails containing OTP codes."""
        messages = self._fetch_recent_messages(sender_filter, seen_ids)
        for body in messages:
            code = self._extract_otp(body)
            if code:
                return code
        return None

    def _check_for_verification_link(self, sender_filter: str, seen_ids: set) -> Optional[str]:
        """Check inbox for new emails containing verification links."""
        messages = self._fetch_recent_messages(sender_filter, seen_ids)
        for body in messages:
            link = self._extract_verification_link(body)
            if link:
                return link
        return None

    def _fetch_recent_messages(self, sender_filter: str, seen_ids: set) -> list:
        """Fetch recent unseen email bodies from inbox."""
        bodies = []
        if not self._conn:
            return bodies

        try:
            self._conn.select("INBOX")
            # Search for unseen emails from the last few minutes
            _, msg_nums = self._conn.search(None, "UNSEEN")
            if not msg_nums[0]:
                return bodies

            msg_ids = msg_nums[0].split()
            # Only check the 10 most recent
            for msg_id in msg_ids[-10:]:
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                _, msg_data = self._conn.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Check sender filter
                from_addr = str(msg.get("From", "")).lower()
                if sender_filter:
                    if not re.search(sender_filter, from_addr, re.I):
                        # Also check against known ATS patterns
                        if not any(re.search(p, from_addr) for p in ATS_SENDER_PATTERNS):
                            continue

                # Extract body text
                body = self._get_email_body(msg)
                if body:
                    bodies.append(body)

        except Exception as e:
            logger.debug(f"[EmailMonitor] Fetch error: {e}")

        return bodies

    def _get_email_body(self, msg: email.message.Message) -> str:
        """Extract text body from an email message."""
        body_parts = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type in ("text/plain", "text/html"):
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        body_parts.append(payload.decode(charset, errors="replace"))
                    except Exception:
                        continue
        else:
            try:
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or "utf-8"
                body_parts.append(payload.decode(charset, errors="replace"))
            except Exception:
                pass

        return "\n".join(body_parts)

    @staticmethod
    def _extract_otp(text: str) -> Optional[str]:
        """Extract OTP code from email body text."""
        for pattern in OTP_PATTERNS:
            match = pattern.search(text)
            if match:
                code = match.group(1)
                # Sanity: must be 4-8 digits, not a year like 2025/2026
                if 4 <= len(code) <= 8 and code not in ("2024", "2025", "2026", "2027"):
                    return code
        return None

    @staticmethod
    def _extract_verification_link(text: str) -> Optional[str]:
        """Extract verification/confirmation link from email body."""
        for pattern in VERIFY_LINK_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
        return None
