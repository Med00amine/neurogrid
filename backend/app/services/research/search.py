"""Optional web-search enrichment through the Tavily Search API (https://docs.tavily.com).

Request: POST https://api.tavily.com/search with `Authorization: Bearer <key>` and a JSON body
(query, search_depth, max_results). A basic search costs 1 credit (docs.tavily.com/documentation/api-credits).
Results are only used when they demonstrably concern this company (its domain or name appears).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.services.llm import LLMError

API_URL = "https://api.tavily.com/search"


@dataclass
class SearchHit:
    title: str
    url: str
    content: str
    score: float


class TavilySearch:
    name = "tavily"

    def __init__(self, api_key: str):
        self.key = api_key

    def search(self, query: str, *, max_results: int = 5) -> tuple[list[SearchHit], float]:
        try:
            r = httpx.post(
                API_URL, timeout=20.0,
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                json={"query": query[:380], "search_depth": "basic", "max_results": max_results, "include_answer": False},
            )
        except httpx.HTTPError as e:
            raise LLMError("Could not reach the search provider", "network") from e
        if r.status_code in (401, 403):
            raise LLMError("The search API key was rejected", "auth")
        if r.status_code == 429:
            raise LLMError("The search provider is rate limiting", "rate_limited")
        if r.status_code >= 400:
            # The raw status is reported as-is; specific plan-limit codes were not verified against the docs.
            raise LLMError(f"Search provider returned HTTP {r.status_code}", "network")
        data = r.json()
        hits = [SearchHit(str(x.get("title") or ""), str(x.get("url") or ""), str(x.get("content") or ""), float(x.get("score") or 0))
                for x in data.get("results", []) if x.get("url") and x.get("content")]
        credits = float((data.get("usage") or {}).get("credits") or 1)
        return hits, credits


_override = None


def set_search_override(client) -> None:
    global _override
    _override = client


def get_search(provider: str):
    if _override is not None:
        return _override
    key = get_settings().tavily_api_key
    if provider == "tavily" and key:
        return TavilySearch(key.get_secret_value())
    return None


def relevant(hit: SearchHit, domain: str, name: str | None) -> bool:
    host = (urlsplit(hit.url).hostname or "").lower()
    d = domain.lower().removeprefix("www.")
    if host == d or host.endswith("." + d):
        return True
    blob = (hit.title + " " + hit.content).lower()
    if d in blob:
        return True
    return bool(name and len(name) >= 4 and name.lower() in blob)
