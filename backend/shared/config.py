"""
Centralized application configuration.

All environment variables are read exactly once, here, via python-dotenv.
Every other module must import the shared `settings` object instead of
calling `os.getenv` / `os.environ` directly.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load variables from backend/.env (shared by every service under
# backend/, one API-key set for all of them) rather than a cwd-relative
# .env, since diagnose_service/ and agent_service/ each run with a
# different working directory. No-op in production, where the
# variables are already injected by the platform.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the application configuration."""

    perplexity_api_key: str | None
    deepseek_api_key: str | None
    contact_email: str | None = None

    # --- Perplexity API ---------------------------------------------------
    perplexity_api_base_url: str = "https://api.perplexity.ai"
    perplexity_model: str = "sonar"

    # --- DeepSeek API -------------------------------------------------------
    deepseek_api_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # --- HTTP client tuning -------------------------------------------------
    request_timeout_seconds: int = 20
    http_user_agent: str = "Geora-Diagnostic-Bot/1.0 (+https://github.com/FFLY-tt/geora)"

    def has_perplexity_key(self) -> bool:
        """Return True if a Perplexity API key is configured."""
        return bool(self.perplexity_api_key)

    def has_deepseek_key(self) -> bool:
        """Return True if a DeepSeek API key is configured."""
        return bool(self.deepseek_api_key)


def _load_settings() -> Settings:
    """Build a Settings instance from the current process environment."""
    return Settings(
        perplexity_api_key=os.getenv("PERPLEXITY_API_KEY") or None,
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
        contact_email=os.getenv("CONTACT_EMAIL") or None,
    )


# Single shared instance imported by every other module.
settings = _load_settings()
