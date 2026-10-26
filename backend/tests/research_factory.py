"""Create approved, verified research directly (for drafting/campaign tests that don't need to crawl)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Company, FindingEvidence, ResearchFinding, ResearchRun, ResearchSource


def seed_research(db: Session, company: Company, items: list[dict] | None = None, status: str = "approved") -> list[ResearchFinding]:
    """items: [{"category","statement","quote","kind","tags","confidence"}]"""
    items = items or [
        {"category": "hiring_signal", "quote": "We are hiring a backend engineer to help us build LLM integrations for our clients.", "tags": ["ai_llm", "python_fastapi"]},
    ]
    now = datetime.now(timezone.utc)
    run = ResearchRun(company_id=company.id, status="completed", mode="heuristic", started_at=now, finished_at=now, cache_expires_at=now + timedelta(days=30), uncertainties=[])
    db.add(run)
    db.flush()
    text = "\n".join(i["quote"] for i in items)
    src = ResearchSource(run_id=run.id, company_id=company.id, url=f"https://{company.domain}/careers", final_url=f"https://{company.domain}/careers",
                         title="Careers", source_type="official_site", http_status=200, content_type="text/html", text=text, content_sha256="x" * 64)
    db.add(src)
    db.flush()
    out = []
    for i in items:
        f = ResearchFinding(run_id=run.id, company_id=company.id, category=i["category"], statement=i.get("statement") or f"The website says: “{i['quote']}”",
                            kind=i.get("kind", "fact"), service_tags=i.get("tags", []), confidence=i.get("confidence", "medium"), status=status, verified=True)
        db.add(f)
        db.flush()
        db.add(FindingEvidence(finding_id=f.id, source_id=src.id, excerpt=i["quote"], excerpt_verified=True))
        out.append(f)
    db.flush()
    return out
