"""Research one company: fetch -> (search) -> extract -> verify evidence -> store with provenance."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import pricing
from app.core.events import record_event
from app.core.settings_store import get_group
from app.core.text import norm_for_match, quote_in_text
from app.models import (
    Company,
    Contact,
    FindingEvidence,
    ResearchFinding,
    ResearchRun,
    ResearchSource,
)
from app.services import llm as llm_mod
from app.services.research import extract, fetcher, heuristics, llm_extract, search
from app.services.research.heuristics import ExtractionOutput, FindingDraft

log = logging.getLogger("research")


class ResearchCancelled(Exception):
    pass


@dataclass
class Outcome:
    run_id: int | None
    status: str
    findings: int = 0
    pages: int = 0
    cost_usd: Decimal = Decimal(0)
    cached: bool = False
    notes: list[str] = field(default_factory=list)


def latest_run(db: Session, company_id: int, ok_only: bool = True) -> ResearchRun | None:
    stmt = select(ResearchRun).where(ResearchRun.company_id == company_id)
    if ok_only:
        stmt = stmt.where(ResearchRun.status.in_(("completed", "partial")))
    return db.execute(stmt.order_by(ResearchRun.id.desc()).limit(1)).scalar_one_or_none()


def _user_agent(db: Session) -> str:
    sender = get_group(db, "sender")
    contact = sender.from_email or get_settings().smtp_from_email
    return f"{fetcher.DEFAULT_UA} (research for a freelance engineer{'; contact: ' + contact if contact else ''})"


def verify_quote(quote: str, source_text: str | None) -> bool:
    return bool(source_text) and quote_in_text(quote, source_text or "")


def persist_findings(db: Session, run: ResearchRun, out: ExtractionOutput, source_texts: dict[int, str], flagged: set[int]) -> int:
    """Store findings; every evidence quote is re-verified against the stored page text."""
    previous = latest_run(db, run.company_id)
    carried: dict[tuple[str, str], ResearchFinding] = {}
    if previous and previous.id != run.id:
        for pf in db.execute(select(ResearchFinding).where(ResearchFinding.run_id == previous.id,
                                                          ResearchFinding.status.in_(("approved", "rejected", "edited")))).scalars():
            carried[(pf.category, norm_for_match(pf.statement))] = pf
    count = 0
    for d in out.findings:
        if d.category not in heuristics_categories() or not d.statement.strip() or not d.evidence:
            continue
        ev_rows, all_ok = [], True
        for sid, quote in d.evidence:
            ok = sid in source_texts and verify_quote(quote, source_texts.get(sid))
            all_ok = all_ok and ok
            ev_rows.append((sid if sid in source_texts else None, quote[:600], ok))
        confidence = d.confidence
        if any(sid in flagged for sid, _ in d.evidence):
            confidence = "low"
        status = "pending" if all_ok else "auto_rejected"
        f = ResearchFinding(run_id=run.id, company_id=run.company_id, category=d.category, statement=d.statement[:1500], kind=d.kind,
                            service_tags=list(d.service_tags), confidence=confidence, status=status, verified=all_ok,
                            review_note=None if all_ok else "Automatically rejected: a quoted passage was not found in the saved page text.")
        prev = carried.get((d.category, norm_for_match(d.statement)))
        if prev and all_ok:
            f.status, f.edited_statement = prev.status, prev.edited_statement
            f.review_note = f"Review carried over from run #{prev.run_id} (same statement, evidence re-verified)"
            f.reviewed_by, f.reviewed_at = prev.reviewed_by, prev.reviewed_at
        db.add(f)
        db.flush()
        for sid, quote, ok in ev_rows:
            db.add(FindingEvidence(finding_id=f.id, source_id=sid, excerpt=quote, excerpt_verified=ok))
        count += 1
    db.flush()
    return count


def heuristics_categories() -> set[str]:
    from app.constants import FINDING_CATEGORIES

    return set(FINDING_CATEGORIES)


def _candidate_homes(company: Company) -> list[str]:
    if company.website_url:
        return [company.website_url]
    d = company.domain
    return [f"https://{d}", f"https://www.{d}", f"http://{d}"]


def research_company(
    db: Session,
    company_id: int,
    *,
    force: bool = False,
    job_id=None,
    job_spent: Decimal = Decimal(0),
    should_cancel: Callable[[], bool] | None = None,
    llm_client=None,
) -> Outcome:
    company = db.get(Company, company_id)
    if company is None:
        return Outcome(None, "failed", notes=["company not found"])
    rcfg = get_group(db, "research")
    lcfg = get_group(db, "llm")
    now = datetime.now(timezone.utc)

    cached = latest_run(db, company_id)
    if cached and not force and cached.cache_expires_at and cached.cache_expires_at > now:
        return Outcome(cached.id, cached.status, cached=True, notes=[f"Reused research from {cached.finished_at:%Y-%m-%d} (cache valid until {cached.cache_expires_at:%Y-%m-%d})"])

    client = llm_client if llm_client is not None else (llm_mod.get_llm(db) if rcfg.use_llm else None)
    mode = "llm" if client is not None else "heuristic"
    run = ResearchRun(company_id=company_id, job_id=job_id, status="running", mode=mode, model=lcfg.research_model if client else None)
    db.add(run)
    db.flush()
    notes: list[str] = []
    ua = _user_agent(db)
    robots = fetcher.RobotsCache(ua, rcfg.respect_robots)
    pages: list[tuple[int, extract.Page, str]] = []  # (source_id, page, type)
    source_texts: dict[int, str] = {}
    flagged: set[int] = set()
    cost = Decimal(0)

    def save_source(page: extract.Page, res: fetcher.FetchResult | None, stype: str, note: str | None = None) -> int:
        src = ResearchSource(run_id=run.id, company_id=company_id, url=(res.url if res else page.url)[:1000], final_url=(res.final_url if res else page.url)[:1000],
                             title=page.title, source_type=stype, http_status=res.status if res else None,
                             content_type=res.content_type if res else "search/snippet", content_sha256=page.sha256, text=page.text,
                             injection_flag=page.injection_flag, note=note)
        db.add(src)
        db.flush()
        pages.append((src.id, page, stype))
        source_texts[src.id] = page.text
        if page.injection_flag:
            flagged.add(src.id)
        return src.id

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    # ---- 1. official website ----
    home_res = None
    for url in _candidate_homes(company):
        if cancelled():
            raise ResearchCancelled()
        try:
            if not robots.allowed(url):
                notes.append(f"robots.txt does not allow reading {url}; skipped")
                continue
            home_res = fetcher.fetch(url, user_agent=ua)
            if home_res.status >= 400:
                notes.append(f"{url} answered HTTP {home_res.status}")
                home_res = None
                continue
            break
        except fetcher.FetchError as e:
            notes.append(f"Could not read {url}: {e}")
    if home_res is not None:
        home = extract.parse_html(home_res.body, home_res.final_url)
        save_source(home, home_res, "official_site")
        if not company.website_url:
            p = urlsplit(home_res.final_url)
            company.website_url = f"{p.scheme}://{p.netloc}"[:600]
        for link in extract.discover_links(home, home_res.final_url, max(0, rcfg.max_pages_per_company - 1)):
            if cancelled():
                raise ResearchCancelled()
            time.sleep(rcfg.delay_between_requests_seconds)
            try:
                if not robots.allowed(link):
                    notes.append(f"robots.txt does not allow reading {link}; skipped")
                    continue
                r = fetcher.fetch(link, user_agent=ua)
                if r.status >= 400:
                    continue
                save_source(extract.parse_html(r.body, r.final_url), r, "official_site")
            except fetcher.FetchError as e:
                notes.append(f"Could not read {link}: {e}")
    else:
        notes.append("The company website could not be read.")

    # ---- 2. optional web search (for hiring / news signals) ----
    searches = 0
    if rcfg.search_provider != "none":
        sclient = search.get_search(rcfg.search_provider)
        if sclient is None:
            notes.append("Search provider selected but no API key is configured; skipped.")
        else:
            try:
                planned = pricing.search_cost("tavily", 1)
                llm_mod.check_budget(db, "search", planned, job_spent + cost)
                q = f"{company.name or company.domain} {company.domain} company engineering hiring AI cloud"
                hits, credits = sclient.search(q, max_results=5)
                c = pricing.search_cost("tavily", credits)
                llm_mod.record_usage(db, provider="tavily", kind="search", purpose="research", units=credits, cost=c, job_id=job_id, company_id=company_id)
                cost += c
                searches += 1
                for h in hits:
                    if search.relevant(h, company.domain, company.name):
                        txt = f"{h.title}\n{h.content}"[:4000]
                        pg = extract.Page(url=h.url, title=h.title[:300] or None, meta_description=None, lang=None, text=txt,
                                          injection_flag=bool(extract.INJECTION_RE.search(txt)), sha256=__import__("hashlib").sha256(txt.encode()).hexdigest())
                        save_source(pg, None, "search_result", note="search result snippet (third-party)")
            except llm_mod.BudgetExceeded as e:
                notes.append(f"Search skipped: {e}")
            except llm_mod.LLMError as e:
                notes.append(f"Search failed: {e}")

    # ---- 3. extraction ----
    out: ExtractionOutput | None = None
    status = "completed"
    input_tokens = output_tokens = 0
    people_names = [n for (n,) in db.execute(select(Contact.name).where(Contact.company_id == company_id, Contact.name.is_not(None)).limit(30)).all()]
    if pages and client is not None:
        sender = get_group(db, "sender")
        sources = [{"id": sid, "type": st, "url": p.url, "title": p.title, "text": p.text, "injection_flag": p.injection_flag} for sid, p, st in pages]
        try:
            res = llm_mod.call_llm(
                db, client, model=lcfg.research_model, system=llm_extract.SYSTEM,
                user=llm_extract.build_user_prompt(company.name, company.domain, sender.services, sources, people=people_names),
                schema=llm_extract.SCHEMA, purpose="research", max_tokens=6000, effort=lcfg.effort,
                job_id=job_id, job_spent=job_spent + cost, company_id=company_id,
            )
            out = llm_extract.to_output(res.data)
            input_tokens, output_tokens, cost = res.input_tokens, res.output_tokens, cost + res.cost_usd
        except llm_mod.BudgetExceeded as e:
            notes.append(f"AI extraction skipped ({e}); used rule-based extraction instead.")
            status = "partial"
            run.mode = "heuristic"
        except llm_mod.LLMError as e:
            if e.category in ("auth",):
                run.status, run.error, run.finished_at = "failed", str(e), datetime.now(timezone.utc)
                db.flush()
                raise
            notes.append(f"AI extraction failed ({e}); used rule-based extraction instead.")
            status = "partial"
            run.mode = "heuristic"
    if pages and out is None:
        people = [n for (n,) in db.execute(select(Contact.name).where(Contact.company_id == company_id, Contact.name.is_not(None)).limit(30)).all()]
        out = heuristics.run_heuristics(pages, people)
    if out is None:
        out = ExtractionOutput([], "unknown", None, [])
        status = "partial" if home_res is None else status

    n = persist_findings(db, run, out, source_texts, flagged)
    uncertainties = list(dict.fromkeys(out.uncertainties + notes))[:12]
    run.status = "failed" if (not pages) else status
    if not pages:
        run.error = "No readable page was found for this company."
    run.pages_fetched = len([1 for _, _, st in pages if st == "official_site"])
    run.search_queries = searches
    run.llm_input_tokens, run.llm_output_tokens, run.cost_usd_est = input_tokens, output_tokens, cost
    run.summary = out.summary
    run.company_type = out.company_type if out.company_type in ("ai_consultancy", "software_agency", "aws_consultancy", "product_company", "other") else None
    run.uncertainties = uncertainties
    run.finished_at = datetime.now(timezone.utc)
    run.cache_expires_at = run.finished_at + timedelta(days=rcfg.cache_days)
    db.flush()

    if pages:
        contacts = db.execute(select(Contact).where(Contact.company_id == company_id, Contact.pipeline_stage == "new")).scalars().all()
        for c in contacts:
            c.pipeline_stage = "researched"
            record_event(db, "stage_changed", contact_id=c.id, source="system", data={"from": "new", "to": "researched", "why": "research completed", "run_id": run.id})
        from app.services.scoring import recompute_company

        recompute_company(db, company_id)
    return Outcome(run.id, run.status, findings=n, pages=run.pages_fetched, cost_usd=cost, notes=uncertainties)


# --- review ------------------------------------------------------------------------------------------------------------------


def review_finding(db: Session, finding_id: int, action: str, actor: str, *, edited_statement: str | None = None, note: str | None = None) -> ResearchFinding:
    f = db.get(ResearchFinding, finding_id)
    if f is None:
        raise ValueError("finding not found")
    if action not in ("approve", "reject", "edit", "reset"):
        raise ValueError("unknown action")
    if action in ("approve", "edit") and f.status == "auto_rejected" and not f.verified:
        raise ValueError("This finding's quoted evidence could not be verified in the saved page text, so it cannot be approved. Reject it, or re-run the research.")
    if action == "approve":
        f.status = "approved"
    elif action == "reject":
        f.status = "rejected"
    elif action == "edit":
        text = (edited_statement or "").strip()
        if len(text) < 5:
            raise ValueError("Edited text is empty")
        f.status, f.edited_statement = "edited", text[:1500]
    else:
        f.status, f.edited_statement = "pending", None
    f.review_note = (note or None) if note else f.review_note
    f.reviewed_by, f.reviewed_at = actor, datetime.now(timezone.utc)
    db.flush()
    company = db.get(Company, f.company_id)
    run = db.get(ResearchRun, f.run_id)
    if f.category == "business_model" and f.status in ("approved", "edited") and run and run.company_type and company:
        company.company_type = run.company_type
    from app.services.scoring import recompute_company

    recompute_company(db, f.company_id)
    return f
