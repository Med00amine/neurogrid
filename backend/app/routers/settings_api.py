from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import pricing
from app.core.deps import Auth, read_access, write_access
from app.core.events import audit
from app.core.settings_store import GROUPS, all_groups, get_group, set_group
from app.db import get_db
from app.services import metrics
from app.services.eligibility import sender_readiness

router = APIRouter(prefix="/api", tags=["settings"])


def env_status() -> dict:
    s = get_settings()
    return {
        "environment": s.environment,
        "secrets": s.secret_status(),
        "smtp": {"configured": bool(s.smtp_host and (not s.smtp_username or s.smtp_password)), "host": s.smtp_host, "port": s.smtp_port,
                 "security": s.smtp_security, "from_email": s.smtp_from_email},
        "imap": {"configured": bool(s.imap_host and s.imap_password), "host": s.imap_host, "port": s.imap_port, "mailbox": s.imap_mailbox},
        "llm": {"configured": bool(s.anthropic_api_key)},
        "search": {"configured": bool(s.tavily_api_key)},
        "live_sending_enabled_in_env": s.live_sending_enabled,
        "public_base_url": s.public_base_url,
        "https_unsubscribe_possible": s.public_base_url.startswith("https://"),
    }


@router.get("/settings")
def get_settings_all(db: Session = Depends(get_db), auth: Auth = Depends(read_access)):
    groups = all_groups(db)
    s = get_settings()
    spend = {"llm_month_usd": float(metrics.month_spend(db, "llm")), "search_month_usd": float(metrics.month_spend(db, "search"))}
    return {
        "groups": groups,
        "env": env_status(),
        "spend": spend,
        "prices": {"checked_on": pricing.PRICES_CHECKED_ON, "llm": {k: {kk: str(vv) for kk, vv in v.items()} for k, v in pricing.LLM_PRICES.items()},
                   "search": {k: {kk: str(vv) for kk, vv in v.items()} for k, v in pricing.SEARCH_PRICES.items()}},
        "known_models": pricing.known_models(),
        "readiness_dry_run": [r.__dict__ for r in sender_readiness(db, s, live=False)],
        "readiness_live": [r.__dict__ for r in sender_readiness(db, s, live=True)],
    }


@router.put("/settings/{group}")
def put_group(group: str, body: dict, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    if group not in GROUPS:
        raise HTTPException(404, "Unknown settings group")
    try:
        before = get_group(db, group).model_dump()
        new = set_group(db, group, body, auth.label)
    except ValidationError as e:
        raise HTTPException(422, [{"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"].replace("Value error, ", "")} for err in e.errors()])
    changed = {k: v for k, v in new.model_dump().items() if before.get(k) != v}
    # live-mode switch changes are always audited
    audit(db, f"settings.{group}", actor=auth.label, entity_type="settings", entity_id=group, details={"changed_keys": sorted(changed)})
    return new.model_dump()


@router.post("/settings/test-llm")
def test_llm(db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    """One tiny real call (a few hundred tokens, a fraction of a cent) to prove the key and model work."""
    from app.services import llm as llm_mod

    client = llm_mod.get_llm(db)
    if client is None:
        raise HTTPException(409, "No AI key is configured (ANTHROPIC_API_KEY in .env) or the LLM provider is set to 'none'. Template drafting still works.")
    cfg = get_group(db, "llm")
    schema = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    try:
        res = llm_mod.call_llm(db, client, model=cfg.research_model, system="Reply with the JSON object {\"ok\": true}.", user="ping",
                               schema=schema, purpose="connection_test", max_tokens=200, effort="low")
    except llm_mod.LLMError as e:
        return {"ok": False, "error": str(e), "category": e.category}
    return {"ok": True, "model": res.model, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "cost_usd_est": float(res.cost_usd)}


@router.post("/settings/test-search")
def test_search(db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    from app.services import llm as llm_mod
    from app.services.research import search

    provider = get_group(db, "research").search_provider
    client = search.get_search(provider) if provider != "none" else None
    if client is None:
        raise HTTPException(409, "Search is switched off or has no key (TAVILY_API_KEY in .env). Website-only research still works.")
    try:
        hits, credits = client.search("example domain", max_results=1)
    except llm_mod.LLMError as e:
        return {"ok": False, "error": str(e), "category": e.category}
    cost = pricing.search_cost(provider, credits)
    llm_mod.record_usage(db, provider=provider, kind="search", purpose="connection_test", units=credits, cost=cost)
    return {"ok": True, "results": len(hits), "credits": credits, "cost_usd_est": float(cost)}
