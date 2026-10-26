"""Operator-editable settings, stored as validated JSON groups in the `app_settings` table.

Secrets are NOT stored here (they live in the environment). Nothing in these groups is secret.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.pricing import DEFAULT_DRAFT_MODEL, DEFAULT_RESEARCH_MODEL, known_models
from app.models import AppSetting

DEFAULT_SERVICES = [
    "Python and FastAPI backend development",
    "AI/ML and LLM integrations",
    "RAG applications and document processing",
    "API and business workflow integrations",
    "Docker and AWS deployment",
    "AI application testing and productionization",
]

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


def _check_tz(v: str) -> str:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValueError(f"unknown timezone: {v}")
    return v


class GeneralSettings(_Base):
    timezone: str = "UTC"
    source_text_retention_days: int = Field(90, ge=7, le=3650)
    inbound_retention_days: int = Field(365, ge=30, le=3650)

    _tz = field_validator("timezone")(_check_tz)


class SenderProfile(_Base):
    """Who is sending. Everything here is typed by the operator; the app never invents any of it."""

    display_name: str = ""
    from_email: str = ""
    reply_to: str = ""
    job_title: str = ""
    website_url: str = ""
    github_url: str = ""
    linkedin_url: str = ""
    portfolio_url: str = ""
    # URLs the operator has explicitly confirmed are theirs. Drafts may link ONLY to these.
    verified_urls: list[str] = Field(default_factory=list)
    postal_address: str = ""
    # Honest statement of where the recipient's address came from (GDPR Art. 14 transparency).
    data_source_statement: str = ""
    privacy_url: str = ""
    services: list[str] = Field(default_factory=lambda: list(DEFAULT_SERVICES))
    # Claims about experience/results the operator can substantiate. Drafts may use ONLY these.
    proof_points: list[str] = Field(default_factory=list)


class SendingSettings(_Base):
    provider: Literal["dry_run", "smtp"] = "dry_run"
    live_mode_enabled: bool = False  # second of the three live-sending switches
    daily_cap: int = Field(20, ge=1, le=500)
    hourly_cap: int = Field(5, ge=1, le=100)
    min_interval_seconds: int = Field(120, ge=10, le=3600)
    # The limit your mailbox provider documents for your account. Caps may not exceed half of it.
    provider_documented_daily_limit: int = Field(500, ge=10, le=100000)
    window_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # Monday=0
    window_start: str = "09:00"
    window_end: str = "17:00"
    max_followups: int = Field(2, ge=0, le=3)
    followup_gap_days: int = Field(5, ge=2, le=60)
    max_consecutive_temp_failures: int = Field(3, ge=1, le=20)
    max_attempts_per_message: int = Field(3, ge=1, le=6)
    attempt_timeout_seconds: int = Field(90, ge=10, le=600)

    @field_validator("window_start", "window_end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("use HH:MM, 24-hour")
        return v

    @field_validator("window_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if not v or any(d < 0 or d > 6 for d in v):
            raise ValueError("window_days must be a non-empty list of 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @model_validator(mode="after")
    def _within_provider_limit(self):
        ceiling = max(1, self.provider_documented_daily_limit // 2)
        if self.daily_cap > ceiling:
            raise ValueError(
                f"daily_cap {self.daily_cap} is above the conservative ceiling of {ceiling} "
                "(half of the provider's documented daily limit)"
            )
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end")
        return self


class ResearchSettings(_Base):
    search_provider: Literal["none", "tavily"] = "none"
    max_pages_per_company: int = Field(5, ge=1, le=10)
    cache_days: int = Field(30, ge=1, le=365)
    use_llm: bool = True  # falls back to heuristic extraction when no LLM key is configured
    respect_robots: bool = True
    delay_between_requests_seconds: float = Field(1.0, ge=0.0, le=30.0)


class LLMSettings(_Base):
    provider: Literal["none", "anthropic"] = "anthropic"
    research_model: str = DEFAULT_RESEARCH_MODEL
    draft_model: str = DEFAULT_DRAFT_MODEL
    effort: Literal["low", "medium", "high"] = "low"

    @field_validator("research_model", "draft_model")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in known_models():
            raise ValueError(f"model {v!r} is not in the price table ({', '.join(known_models())}); cost could not be estimated")
        return v


class BudgetSettings(_Base):
    monthly_llm_usd: float = Field(15.0, ge=0, le=100000)
    monthly_search_usd: float = Field(5.0, ge=0, le=100000)
    per_job_usd: float = Field(5.0, ge=0, le=100000)
    warn_ratio: float = Field(0.8, ge=0.1, le=1.0)


class ScoringSettings(_Base):
    weights: dict[str, int] = Field(
        default_factory=lambda: {
            "service_match": 30,
            "problem_evidence": 25,
            "decision_relevance": 15,
            "evidence_quality": 15,
            "contact_reliability": 15,
        }
    )

    @field_validator("weights")
    @classmethod
    def _sum(cls, v: dict[str, int]) -> dict[str, int]:
        required = {"service_match", "problem_evidence", "decision_relevance", "evidence_quality", "contact_reliability"}
        if set(v) != required:
            raise ValueError(f"weights must have exactly these keys: {sorted(required)}")
        if any(w < 0 for w in v.values()) or sum(v.values()) != 100:
            raise ValueError("weights must be non-negative and sum to 100")
        return v


GROUPS: dict[str, type[_Base]] = {
    "general": GeneralSettings,
    "sender": SenderProfile,
    "sending": SendingSettings,
    "research": ResearchSettings,
    "llm": LLMSettings,
    "budget": BudgetSettings,
    "scoring": ScoringSettings,
}


def get_group(db: Session, name: str):
    model = GROUPS[name]
    row = db.get(AppSetting, name)
    if row is None:
        return model()
    try:
        return model(**(row.value or {}))
    except Exception:  # corrupted / outdated stored value: fall back to defaults rather than crash
        return model()


def set_group(db: Session, name: str, data: dict[str, Any], actor: str | None = None):
    """Validate then persist. Raises pydantic.ValidationError on bad input."""
    model = GROUPS[name]
    current = get_group(db, name).model_dump()
    merged = {**current, **data}
    validated = model(**merged)
    row = db.get(AppSetting, name)
    payload = validated.model_dump()
    if row is None:
        db.add(AppSetting(key=name, value=payload, updated_by=actor))
    else:
        row.value = payload
        row.updated_by = actor
    db.flush()
    return validated


def all_groups(db: Session) -> dict[str, dict]:
    return {name: get_group(db, name).model_dump() for name in GROUPS}
