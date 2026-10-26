"""LLM access behind a tiny interface, so research/drafting/classification are testable without a network.

* `AnthropicClient` calls the Messages API with structured JSON output (`output_config.format`).
* `FakeLLM` (tests) returns scripted answers and records the prompts it was given.
* If no key/provider is configured, `get_llm()` returns None and callers use the deterministic template mode.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import pricing
from app.core.settings_store import get_group
from app.models import UsageRecord
from app.services.metrics import month_spend

log = logging.getLogger("llm")


class LLMError(Exception):
    def __init__(self, message: str, category: str = "unexpected"):
        super().__init__(message)
        self.category = category


class BudgetExceeded(LLMError):
    def __init__(self, message: str):
        super().__init__(message, "budget_exceeded")


@dataclass
class LLMResult:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str = "end_turn"
    cost_usd: Decimal = Decimal(0)


class LLMClient(Protocol):
    name: str

    def complete_json(self, *, model: str, system: str, user: str, schema: dict, max_tokens: int = 8000, effort: str = "low") -> LLMResult: ...


class AnthropicClient:
    name = "anthropic"

    def __init__(self, api_key: str):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120.0)

    def complete_json(self, *, model: str, system: str, user: str, schema: dict, max_tokens: int = 8000, effort: str = "low") -> LLMResult:
        a = self._anthropic
        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except a.AuthenticationError as e:
            raise LLMError("The Anthropic API key was rejected", "auth") from e
        except a.RateLimitError as e:
            raise LLMError("Anthropic rate limit reached", "rate_limited") from e
        except a.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API", "network") from e
        except a.BadRequestError as e:
            raise LLMError(f"The Anthropic API rejected the request: {str(e)[:300]}", "bad_input") from e
        except a.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}", "network") from e
        stop = getattr(resp, "stop_reason", None) or "end_turn"
        if stop == "refusal":
            raise LLMError("The model declined this request", "bad_input")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        if stop == "max_tokens":
            raise LLMError("The model ran out of output tokens before finishing", "bad_input")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("The model returned invalid JSON", "bad_input") from e
        u = resp.usage
        return LLMResult(data=data, input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0, model=model, stop_reason=stop)


@dataclass
class FakeLLM:
    """Scripted LLM for tests. `responder(system, user, schema) -> dict` produces the JSON answer."""

    responder: Any
    name: str = "fake"
    calls: list[dict] = field(default_factory=list)
    input_tokens: int = 3000
    output_tokens: int = 600

    def complete_json(self, *, model: str, system: str, user: str, schema: dict, max_tokens: int = 8000, effort: str = "low") -> LLMResult:
        self.calls.append({"model": model, "system": system, "user": user, "schema": schema})
        data = self.responder(system, user, schema) if callable(self.responder) else self.responder
        return LLMResult(data=data, input_tokens=self.input_tokens, output_tokens=self.output_tokens, model=model)


_override: LLMClient | None = None


def set_llm_override(client: LLMClient | None) -> None:
    """Tests inject a FakeLLM here."""
    global _override
    _override = client


def get_llm(db: Session) -> LLMClient | None:
    if _override is not None:
        return _override
    cfg = get_group(db, "llm")
    key = get_settings().anthropic_api_key
    if cfg.provider != "anthropic" or not key:
        return None
    return AnthropicClient(key.get_secret_value())


# --- budget + accounting ----------------------------------------------------------------------------------------------


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    return pricing.llm_cost(model, input_tokens, output_tokens)


def check_budget(db: Session, kind: str, planned_usd: Decimal, job_spent: Decimal = Decimal(0)) -> None:
    b = get_group(db, "budget")
    cap = Decimal(str(b.monthly_llm_usd if kind == "llm" else b.monthly_search_usd))
    spent = month_spend(db, kind)
    if spent + planned_usd > cap:
        raise BudgetExceeded(f"Monthly {kind} budget of ${cap} would be exceeded (spent ${spent:.4f} so far)")
    if job_spent + planned_usd > Decimal(str(b.per_job_usd)):
        raise BudgetExceeded(f"Per-job budget of ${b.per_job_usd} would be exceeded")


def record_usage(db: Session, *, provider: str, kind: str, purpose: str, model: str | None = None, input_tokens: int = 0,
                 output_tokens: int = 0, units: float = 0, cost: Decimal = Decimal(0), job_id=None, company_id: int | None = None,
                 contact_id: int | None = None, campaign_id: int | None = None) -> UsageRecord:
    row = UsageRecord(provider=provider, kind=kind, purpose=purpose, model=model, input_tokens=input_tokens,
                      output_tokens=output_tokens, units=units, cost_usd=cost, job_id=job_id, company_id=company_id,
                      contact_id=contact_id, campaign_id=campaign_id)
    db.add(row)
    db.flush()
    return row


def call_llm(db: Session, client: LLMClient, *, model: str, system: str, user: str, schema: dict, purpose: str,
             max_tokens: int = 8000, effort: str = "low", job_id=None, job_spent: Decimal = Decimal(0),
             company_id: int | None = None, contact_id: int | None = None, campaign_id: int | None = None) -> LLMResult:
    """Budget check -> call -> usage record. The cost is an estimate from the price table; tokens are from the API."""
    # pessimistic pre-check: assume the whole max_tokens is output and the prompt is ~4 chars/token
    planned = estimate_cost(model, len(system + user) // 4, max_tokens // 2)
    check_budget(db, "llm", planned, job_spent)
    result = client.complete_json(model=model, system=system, user=user, schema=schema, max_tokens=max_tokens, effort=effort)
    result.cost_usd = estimate_cost(model, result.input_tokens, result.output_tokens)
    record_usage(db, provider=client.name, kind="llm", purpose=purpose, model=model, input_tokens=result.input_tokens,
                 output_tokens=result.output_tokens, cost=result.cost_usd, job_id=job_id, company_id=company_id,
                 contact_id=contact_id, campaign_id=campaign_id)
    return result
