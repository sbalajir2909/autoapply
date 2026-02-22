"""
LLM abstraction layer.

Provides a unified async client that routes through OpenRouter,
matching the Anthropic SDK interface so existing code works unchanged.

Usage:
    from autoapply.llm import get_async_client, get_sync_client

    client = get_async_client()
    response = await client.messages.create(
        model="anthropic/claude-sonnet-4",
        max_tokens=8000,
        messages=[{"role": "user", "content": "..."}],
    )
    text = response.content[0].text
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from .config import GENAI_STUDIO_API_KEY, GENAI_STUDIO_URL, GENAI_STUDIO_MODEL, MAX_API_CALLS_PER_RUN

# ---------------------------------------------------------------------------
# OpenRouter config
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Retry settings
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_DELAYS = [2, 5, 15]  # exponential backoff in seconds
_MAX_RETRIES = len(_RETRY_DELAYS)

# API call budget
_api_call_count = 0


def _track_api_call() -> None:
    """Increment call counter and raise if budget exceeded."""
    global _api_call_count
    _api_call_count += 1
    if _api_call_count > MAX_API_CALLS_PER_RUN:
        raise RuntimeError(
            f"API call budget exceeded ({MAX_API_CALLS_PER_RUN} calls). "
            "Increase MAX_API_CALLS_PER_RUN in config or reduce workload."
        )

# Model mapping: internal name → OpenRouter model ID
MODEL_MAP = {
    # Anthropic models via OpenRouter
    "claude-sonnet-4-20250514": "anthropic/claude-sonnet-4",
    "claude-haiku-4-20250514": "anthropic/claude-3.5-haiku",
    "claude-sonnet-4": "anthropic/claude-sonnet-4",
    "claude-haiku-4": "anthropic/claude-3.5-haiku",
    # Direct OpenRouter IDs pass through
}


def _resolve_model(model: str) -> str:
    """Map internal model name to OpenRouter model ID."""
    return MODEL_MAP.get(model, model)


# ---------------------------------------------------------------------------
# Response dataclasses (mimic Anthropic SDK)
# ---------------------------------------------------------------------------

@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class MessageResponse:
    content: List[TextBlock]
    model: str = ""
    role: str = "assistant"
    stop_reason: str = "end_turn"
    usage: Dict = None

    def __post_init__(self):
        if self.usage is None:
            self.usage = {}


# ---------------------------------------------------------------------------
# Async Messages API (mimics anthropic.AsyncAnthropic().messages)
# ---------------------------------------------------------------------------

class AsyncMessages:
    """Drop-in replacement for anthropic.AsyncAnthropic().messages"""

    def __init__(self, api_key: str, base_url: str = OPENROUTER_URL):
        self.api_key = api_key
        self.base_url = base_url

    async def create(
        self,
        model: str,
        max_tokens: int = 4096,
        messages: List[Dict] = None,
        system: str = None,
        **kwargs,
    ) -> MessageResponse:
        """Send a chat completion request via OpenRouter."""
        _track_api_call()
        resolved_model = _resolve_model(model)

        # Build OpenAI-compatible messages
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        for msg in (messages or []):
            # Anthropic format: content can be str or list of blocks
            content = msg.get("content", "")
            if isinstance(content, list):
                # Extract text from content blocks
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)
            oai_messages.append({"role": msg["role"], "content": content})

        payload = {
            "model": resolved_model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/autoapply",
            "X-Title": "AutoApply Pipeline",
        }

        last_exc = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(self.base_url, json=payload, headers=headers)
                    if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                        delay = _RETRY_DELAYS[attempt]
                        if resp.status_code == 429:
                            delay = int(resp.headers.get("Retry-After", delay))
                        print(f"[LLM] Retrying after {resp.status_code} (attempt {attempt + 1}, wait {delay}s)")
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.HTTPStatusError:
                raise
            except httpx.TransportError as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    print(f"[LLM] Transport error: {e} — retrying in {delay}s (attempt {attempt + 1})")
                    await asyncio.sleep(delay)
                    continue
                raise
            else:
                break
        else:
            raise last_exc or RuntimeError("LLM request failed after retries")

        # Parse OpenAI-format response → Anthropic-format
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"Empty response from OpenRouter: {data}")

        text = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})

        return MessageResponse(
            content=[TextBlock(text=text)],
            model=data.get("model", resolved_model),
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Sync Messages API (mimics anthropic.Anthropic().messages)
# ---------------------------------------------------------------------------

class SyncMessages:
    """Drop-in replacement for anthropic.Anthropic().messages"""

    def __init__(self, api_key: str, base_url: str = OPENROUTER_URL):
        self.api_key = api_key
        self.base_url = base_url

    def create(
        self,
        model: str,
        max_tokens: int = 4096,
        messages: List[Dict] = None,
        system: str = None,
        **kwargs,
    ) -> MessageResponse:
        """Send a chat completion request via OpenRouter (sync)."""
        _track_api_call()
        resolved_model = _resolve_model(model)

        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        for msg in (messages or []):
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)
            oai_messages.append({"role": msg["role"], "content": content})

        payload = {
            "model": resolved_model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/autoapply",
            "X-Title": "AutoApply Pipeline",
        }

        last_exc = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(self.base_url, json=payload, headers=headers)
                    if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                        delay = _RETRY_DELAYS[attempt]
                        if resp.status_code == 429:
                            delay = int(resp.headers.get("Retry-After", delay))
                        print(f"[LLM] Retrying after {resp.status_code} (attempt {attempt + 1}, wait {delay}s)")
                        time.sleep(delay)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.HTTPStatusError:
                raise
            except httpx.TransportError as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    print(f"[LLM] Transport error: {e} — retrying in {delay}s (attempt {attempt + 1})")
                    time.sleep(delay)
                    continue
                raise
            else:
                break
        else:
            raise last_exc or RuntimeError("LLM request failed after retries")

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"Empty response from OpenRouter: {data}")

        text = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})

        return MessageResponse(
            content=[TextBlock(text=text)],
            model=data.get("model", resolved_model),
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Client classes (mimic anthropic.AsyncAnthropic / anthropic.Anthropic)
# ---------------------------------------------------------------------------

class AsyncOpenRouterClient:
    """Drop-in replacement for anthropic.AsyncAnthropic"""

    def __init__(self, api_key: str = None, **kwargs):
        self.api_key = api_key or OPENROUTER_API_KEY
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY not set. "
                "Export it as an environment variable or pass api_key= explicitly."
            )
        self.messages = AsyncMessages(api_key=self.api_key)


class SyncOpenRouterClient:
    """Drop-in replacement for anthropic.Anthropic"""

    def __init__(self, api_key: str = None, **kwargs):
        self.api_key = api_key or OPENROUTER_API_KEY
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY not set. "
                "Export it as an environment variable or pass api_key= explicitly."
            )
        self.messages = SyncMessages(api_key=self.api_key)


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------

def get_async_client(api_key: str = None) -> AsyncOpenRouterClient:
    return AsyncOpenRouterClient(api_key=api_key)


def get_sync_client(api_key: str = None) -> SyncOpenRouterClient:
    return SyncOpenRouterClient(api_key=api_key)


def _call_genai_studio(system_prompt: str, user_prompt: str, max_tokens: int = 200) -> Optional[str]:
    """
    Call Purdue GenAI Studio (Llama) as a cheaper/free alternative.
    Returns the response text or None on failure.
    """
    from .config import GENAI_STUDIO_API_KEY, GENAI_STUDIO_URL, GENAI_STUDIO_MODEL

    if not GENAI_STUDIO_API_KEY or not GENAI_STUDIO_URL:
        return None

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                GENAI_STUDIO_URL,
                json={
                    "model": GENAI_STUDIO_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "stream": False,
                },
                headers={
                    "Authorization": f"Bearer {GENAI_STUDIO_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"[GenAI Studio] Call failed: {e}")
    return None


def synthesize_answer(
    question: str,
    profile: Dict,
    job_description: str = "",
    field_type: str = "text",
    options: Optional[List[str]] = None,
) -> str:
    """
    Use the LLM to dynamically synthesize an answer to an application question.

    Tries Purdue GenAI Studio first (free), falls back to OpenRouter.

    Args:
        question:        The form field label/question text.
        profile:         Candidate profile dict.
        job_description: JD context for better answers.
        field_type:      One of: text, textarea, select, radio, checkbox, date.
        options:         Available options for select/radio/checkbox fields.
    """
    system_prompt = (
        "You are an expert candidate filling out a job application. "
        "Your goal is to provide concise, accurate, and professional answers "
        "to application questions based strictly on the provided candidate profile. "
        "If a question asks for a number (like years of experience), provide just the number. "
        "If it asks for a yes/no, provide just Yes or No. "
        "For behavioral questions, provide a 1-3 sentence compelling answer. "
        "Never mention that you are an AI. Do not include any filler text or conversational openings. "
        "Answer the question directly."
    )

    # Add field-type-specific instructions
    if field_type in ("select", "radio") and options:
        system_prompt += (
            f"\n\nThis is a {field_type} field. You MUST pick EXACTLY ONE of these options "
            f"(return only the option text, nothing else): {options}"
        )
    elif field_type == "checkbox" and options:
        system_prompt += (
            f"\n\nThis is a checkbox field. Return comma-separated values from these options "
            f"that apply to the candidate: {options}"
        )
    elif field_type == "date":
        system_prompt += (
            "\n\nThis is a date field. Return the date in YYYY-MM-DD format."
        )

    user_prompt = f"""Candidate Profile Context:
{json.dumps(profile, indent=2)}

Job Description Context:
{job_description[:2000] if job_description else "Not provided"}

Question to Answer:
{question}"""

    # Try GenAI Studio first (free)
    result = _call_genai_studio(system_prompt, user_prompt, max_tokens=200)
    if result and result != "":
        return result

    # Fall back to OpenRouter
    try:
        client = get_sync_client()
        response = client.messages.create(
            model="claude-haiku-4",
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[LLM] Error synthesizing answer for '{question}': {e}")
        return "Please refer to my attached resume for more details."
