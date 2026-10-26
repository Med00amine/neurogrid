"""Price table used for COST ESTIMATES. Numbers are copied from the provider pages named below.

Each entry carries the source URL and the date it was read. The Settings page shows this so you can
see how old the numbers are. Providers can change prices at any time; actual billing is on their
dashboard. Token counts come from the API response, the dollar amount is an estimate from this table.
"""

from __future__ import annotations

from decimal import Decimal

PRICES_CHECKED_ON = "2026-10-26"

# USD per million tokens (input, output). Haiku 5.5 is cheaper for prompts up to 100K tokens.
LLM_PRICES: dict[str, dict] = {
    "claude-haiku-5-5": {
        "input": Decimal("0.10"),
        "output": Decimal("0.50"),
        "input_over_100k": Decimal("0.50"),
        "output_over_100k": Decimal("2.50"),
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "claude-sonnet-5-5": {
        "input": Decimal("2"),
        "output": Decimal("10"),
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "claude-opus-5-5": {
        "input": Decimal("4"),
        "output": Decimal("20"),
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
}

# Tavily: 1 credit per basic search, free 1,000 credits/month, pay-as-you-go $0.008/credit.
SEARCH_PRICES: dict[str, dict] = {
    "tavily": {
        "usd_per_credit": Decimal("0.008"),
        "free_credits_per_month": 1000,
        "credits_basic": 1,
        "credits_advanced": 2,
        "source": "https://www.tavily.com/pricing and https://docs.tavily.com/documentation/api-credits",
    }
}

DEFAULT_RESEARCH_MODEL = "claude-haiku-5-5"
DEFAULT_DRAFT_MODEL = "claude-sonnet-5-5"


def known_models() -> list[str]:
    return list(LLM_PRICES)


def llm_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    p = LLM_PRICES.get(model)
    if p is None:
        return Decimal(0)  # unknown model: cost cannot be estimated; the UI flags this
    over = "input_over_100k" in p and input_tokens > 100_000
    in_price = p["input_over_100k"] if over else p["input"]
    out_price = p["output_over_100k"] if over else p["output"]
    return (Decimal(input_tokens) * in_price + Decimal(output_tokens) * out_price) / Decimal(1_000_000)


def search_cost(provider: str, credits: float) -> Decimal:
    p = SEARCH_PRICES.get(provider)
    if not p:
        return Decimal(0)
    return Decimal(str(credits)) * p["usd_per_credit"]


def estimate_draft_cost(model: str, input_tokens: int = 4000, output_tokens: int = 700) -> Decimal:
    return llm_cost(model, input_tokens, output_tokens)
