"""Runtime configuration. Everything comes from environment variables (or a local .env file).

Secrets are `SecretStr` so they never show up in repr()/logs. The UI only ever sees whether a
secret is configured, never its value.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-insecure-secret-change-me-dev-insecure-secret"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://neurogrid:neurogrid_dev_password@127.0.0.1:55432/neurogrid"
    secret_key: SecretStr = SecretStr(DEV_SECRET)

    # bootstrap admin (only used by `python -m app.cli create-admin` / first start)
    admin_email: str | None = None
    admin_password: SecretStr | None = None

    public_base_url: str = "http://localhost:8000"
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    cookie_secure: bool | None = None
    session_hours: int = 12

    # --- safety switches -------------------------------------------------------------------
    # Live sending needs this AND the live-mode setting AND a typed per-campaign confirmation.
    live_sending_enabled: bool = False
    # Test-only escape hatch so the research fetcher can reach a local fixture server.
    allow_private_fetch: bool = False

    # --- SMTP / IMAP (your own mailbox) -----------------------------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_timeout_seconds: int = 30

    imap_host: str | None = None
    imap_port: int = 993
    imap_ssl: bool = True
    imap_username: str | None = None
    imap_password: SecretStr | None = None
    imap_mailbox: str = "INBOX"
    imap_sent_mailbox: str | None = None

    # --- research / LLM -------------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None

    data_dir: Path = PROJECT_ROOT / "data"
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json

                return json.loads(s)
            return [o.strip() for o in s.split(",") if o.strip()]
        return v

    @field_validator("admin_email", "smtp_host", "imap_host", "smtp_username", "imap_username", "smtp_from_email", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("smtp_password", "imap_password", "anthropic_api_key", "tavily_api_key", "admin_password", mode="before")
    @classmethod
    def _blank_secret_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _production_guards(self):
        if self.environment == "production":
            key = self.secret_key.get_secret_value()
            if key == DEV_SECRET or len(key) < 32:
                raise ValueError("SECRET_KEY must be set to a random value of at least 32 characters in production")
            if self.allow_private_fetch:
                raise ValueError("ALLOW_PRIVATE_FETCH must never be enabled in production")
        return self

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment == "production"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    def secret_status(self) -> dict[str, bool]:
        """Which secrets are configured. Never returns values."""
        return {
            "SECRET_KEY": self.secret_key.get_secret_value() != DEV_SECRET,
            "SMTP_PASSWORD": bool(self.smtp_password),
            "IMAP_PASSWORD": bool(self.imap_password),
            "ANTHROPIC_API_KEY": bool(self.anthropic_api_key),
            "TAVILY_API_KEY": bool(self.tavily_api_key),
        }

    def secret_values(self) -> list[str]:
        vals = [self.secret_key, self.smtp_password, self.imap_password, self.anthropic_api_key,
                self.tavily_api_key, self.admin_password]
        return [v.get_secret_value() for v in vals if v is not None and len(v.get_secret_value()) >= 6]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
