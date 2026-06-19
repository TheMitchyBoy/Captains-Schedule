"""
Application configuration loaded from environment variables.

OpenAI is optional but enables AI-assisted XML recovery and prediction
forecasting when OPENAI_API_KEY is set.
Set OPENAI_API_KEY in your environment or in a `.env` file at the project root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

# Load .env from project root when present (does not override existing env vars).
load_dotenv()


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str | None
    model: str
    enabled: bool

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


@dataclass(frozen=True)
class AppSettings:
    openai: OpenAISettings


@lru_cache
def get_settings() -> AppSettings:
    api_key = os.environ.get("OPENAI_API_KEY")
    return AppSettings(
        openai=OpenAISettings(
            api_key=api_key.strip() if api_key else None,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            enabled=bool(api_key and api_key.strip()),
        )
    )


_ai_verification: tuple[bool, str] | None = None


def init_openai_verification() -> tuple[bool, str]:
    """Verify OpenAI once at startup; result is cached for health checks."""
    global _ai_verification
    settings = get_settings().openai
    if not settings.is_configured:
        _ai_verification = (False, "OPENAI_API_KEY is not set")
        return _ai_verification

    _ai_verification = verify_openai_connection()
    return _ai_verification


def get_openai_status() -> dict:
    """Return cached OpenAI integration status for API/UI display."""
    settings = get_settings().openai
    if not settings.is_configured:
        return {
            "enabled": False,
            "model": settings.model,
            "connected": False,
            "message": "OPENAI_API_KEY is not set",
        }

    global _ai_verification
    if _ai_verification is None:
        init_openai_verification()

    connected, message = _ai_verification or (False, "Not verified")
    return {
        "enabled": True,
        "model": settings.model,
        "connected": connected,
        "message": message,
    }


def verify_openai_connection() -> tuple[bool, str]:
    """
    Verify the OpenAI API key with a minimal request.

    Returns (success, message).
    """
    settings = get_settings().openai
    if not settings.is_configured:
        return False, "OPENAI_API_KEY is not set"

    import httpx

    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            json={
                "model": settings.model,
                "messages": [{"role": "user", "content": "Reply with OK"}],
                "max_tokens": 5,
                "temperature": 0,
            },
            timeout=20.0,
        )
        if response.status_code == 200:
            return True, f"Connected ({settings.model})"
        return False, f"API error {response.status_code}: {response.text[:200]}"
    except Exception as exc:
        return False, str(exc)
