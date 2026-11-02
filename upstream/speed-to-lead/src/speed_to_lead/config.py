"""Typed configuration — bring-your-own-key, validated on startup.

Every credential is optional. A missing key disables that one integration
(the app degrades gracefully) rather than crashing. `DEMO_MODE` forces the
local stub LLM + console adapters so the whole pipeline runs with no keys.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CrmProvider = Literal["console", "twenty", "hubspot"]
LlmProvider = Literal["gemini", "groq", "openai", "anthropic", "ollama"]


class Settings(BaseSettings):
    """Application settings loaded from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Runtime
    demo_mode: bool = True
    log_level: str = "INFO"
    environment: str = "local"

    # LLM (draft step)
    llm_provider: LlmProvider = "gemini"
    llm_model: str = "gemini/gemini-1.5-flash"
    llm_api_key: str | None = None

    # Webhook security
    webhook_signing_secret: str | None = None

    # CRM
    crm_provider: CrmProvider = "console"
    twenty_api_url: str | None = None
    twenty_api_key: str | None = None
    hubspot_api_key: str | None = None

    # Notifications
    slack_webhook_url: str | None = None
    resend_api_key: str | None = None
    email_from: str | None = None

    # ATS
    greenhouse_api_key: str | None = None
    greenhouse_on_behalf_of: str | None = None

    # Observability (Langfuse — LLM tracing)
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

    # Infra
    redis_url: str = "redis://localhost:6379/0"
    classifier_adapter_path: str = "artifacts/intent-classifier"

    # Routing
    auto_send_min_confidence: float = Field(default=0.75, ge=0.0, le=1.0)

    @property
    def llm_enabled(self) -> bool:
        """True when a real LLM can be called (key present and not demo)."""
        return not self.demo_mode and bool(self.llm_api_key)

    @property
    def slack_enabled(self) -> bool:
        return not self.demo_mode and bool(self.slack_webhook_url)

    @property
    def email_enabled(self) -> bool:
        return not self.demo_mode and bool(self.resend_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so settings are parsed once per process."""
    return Settings()
