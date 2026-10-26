"""Explainable lead score (0-100). It ranks how well the *evidence* supports a conversation.
It is NOT a probability of a sale and says nothing about whether the person will reply.

Components (weights are editable in Settings; default 30/25/15/15/15):
  service_match        approved findings tied to the sender's services
  problem_evidence     approved findings about hiring, needs, projects or technology work
  decision_relevance   the contact's job title, if known (the supplied file has none, so this is usually 'unknown')
  evidence_quality     number of sources, share of verified quotes, freshness of the research
  contact_reliability   address quality, outreach eligibility, history and jurisdiction readiness
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings_store import get_group
from app.models import (
    Company,
    Contact,
    FindingEvidence,
    LeadScore,
    ResearchFinding,
    ResearchRun,
    ResearchSource,
)

USABLE = ("approved", "edited")
SENIOR = re.compile(r"\b(ceo|cto|cio|coo|chief|founder|co-?founder|owner|managing director|president|vp|vice president|head of|director|principal)\b", re.I)
MID = re.compile(r"\b(lead|senior|staff|manager|architect|engineer|developer|partner|consultant|product)\b", re.I)
DISCLAIMER = "This score ranks how well the saved evidence supports reaching out. It does not predict whether anyone will reply or buy."


def band(total: int) -> str:
    return "strong evidence" if total >= 70 else "some evidence" if total >= 40 else "weak evidence"


def _component(score: float, weight: int, rationale: str, finding_ids: list[int] | None = None, **extra) -> dict:
    score = max(0.0, min(1.0, score))
    return {"score": round(score, 2), "points": round(score * weight, 1), "max": weight, "rationale": rationale,
            "finding_ids": finding_ids or [], **extra}


def compute(db: Session, contact: Contact) -> dict:
    weights = get_group(db, "scoring").weights
    run = None
    findings: list[ResearchFinding] = []
    if contact.company_id:
        run = db.execute(select(ResearchRun).where(ResearchRun.company_id == contact.company_id, ResearchRun.status.in_(("completed", "partial")))
                         .order_by(ResearchRun.id.desc()).limit(1)).scalar_one_or_none()
        if run:
            findings = list(db.execute(select(ResearchFinding).where(ResearchFinding.run_id == run.id, ResearchFinding.status.in_(USABLE))).scalars())
    comps: dict[str, dict] = {}

    # service match
    tagged = [f for f in findings if f.service_tags and f.kind in ("fact", "inference")]
    tags = sorted({t for f in tagged for t in f.service_tags})
    facts_with_tags = {t for f in tagged if f.kind == "fact" for t in f.service_tags}
    if tags:
        s = min(1.0, (len(facts_with_tags) + 0.5 * (len(tags) - len(facts_with_tags))) / 3)
        comps["service_match"] = _component(s, weights["service_match"], f"Approved findings relate to: {', '.join(tags)}", [f.id for f in tagged])
    else:
        why = "No research yet" if run is None else "No approved finding connects this company to your services"
        comps["service_match"] = _component(0, weights["service_match"], why)

    # problem evidence
    cat = {f.category for f in findings if f.kind == "fact"}
    fids = lambda cats: [f.id for f in findings if f.category in cats]  # noqa: E731
    if {"need", "hiring_signal"} <= cat:
        comps["problem_evidence"] = _component(1.0, weights["problem_evidence"], "A documented need and a hiring signal are both approved", fids({"need", "hiring_signal"}))
    elif "need" in cat:
        comps["problem_evidence"] = _component(0.9, weights["problem_evidence"], "A documented need is approved", fids({"need"}))
    elif "hiring_signal" in cat:
        comps["problem_evidence"] = _component(0.7, weights["problem_evidence"], "A hiring signal is approved", fids({"hiring_signal"}))
    elif "project" in cat:
        comps["problem_evidence"] = _component(0.5, weights["problem_evidence"], "A relevant public project is approved", fids({"project"}))
    elif "technology" in cat:
        comps["problem_evidence"] = _component(0.3, weights["problem_evidence"], "Only technology mentions are approved (no stated need)", fids({"technology"}))
    else:
        comps["problem_evidence"] = _component(0, weights["problem_evidence"], "No approved evidence of a business problem")

    # decision relevance
    title = (contact.role_title or "").strip()
    role_finding = None
    if not title:
        from app.services.research.heuristics import _name_tokens

        toks = _name_tokens(contact.name or "")
        for f in findings:
            if f.category == "contact_role" and len(toks) >= 2 and all(t in (f.edited_statement or f.statement).casefold() for t in toks):
                role_finding, title = f, (f.edited_statement or f.statement)
                break
    if role_finding is not None:
        strength = 1.0 if SENIOR.search(title.split("\u201c")[-1]) else 0.5 if MID.search(title.split("\u201c")[-1]) else 0.3
        comps["decision_relevance"] = _component(strength, weights["decision_relevance"], "An approved research finding shows this person's title on the company site", [role_finding.id])
    elif not title:
        comps["decision_relevance"] = _component(0.3, weights["decision_relevance"], "Job title unknown (the contact file has no title column); treated as a low neutral value", unknown=True)
    elif SENIOR.search(title):
        comps["decision_relevance"] = _component(1.0, weights["decision_relevance"], f"Senior title: {title}")
    elif MID.search(title):
        comps["decision_relevance"] = _component(0.5, weights["decision_relevance"], f"Hands-on title: {title}")
    else:
        comps["decision_relevance"] = _component(0.2, weights["decision_relevance"], f"Title not clearly related to buying decisions: {title}")

    # evidence quality
    if run is None or not findings:
        comps["evidence_quality"] = _component(0, weights["evidence_quality"], "No approved evidence")
    else:
        fid_list = [f.id for f in findings]
        evs = list(db.execute(select(FindingEvidence).where(FindingEvidence.finding_id.in_(fid_list))).scalars())
        src_ids = {e.source_id for e in evs if e.source_id}
        verified = sum(1 for e in evs if e.excerpt_verified)
        ratio = verified / len(evs) if evs else 0
        n_sources = len(src_ids)
        age_days = (datetime.now(timezone.utc) - (run.finished_at or run.started_at)).days
        fresh = 1.0 if age_days <= 30 else 0.6 if age_days <= 90 else 0.2
        official = db.execute(select(ResearchSource).where(ResearchSource.id.in_(src_ids), ResearchSource.source_type == "official_site")).scalars().all() if src_ids else []
        s = 0.35 * min(1.0, n_sources / 3) + 0.35 * ratio + 0.2 * fresh + 0.1 * (1.0 if official else 0.0)
        comps["evidence_quality"] = _component(s, weights["evidence_quality"],
                                               f"{n_sources} source(s), {verified}/{len(evs)} quotes verified, research is {age_days} day(s) old" + (", includes official pages" if official else ""))

    # contact reliability
    pts, notes = 0.0, []
    pts += 0.25  # syntactically valid, normalised address (guaranteed by import)
    if not contact.is_role_account:
        pts += 0.2; notes.append("personal mailbox")
    else:
        notes.append("shared/role mailbox")
    if not contact.is_freemail:
        pts += 0.15; notes.append("company domain")
    if run is not None:
        pts += 0.15; notes.append("company website reachable")
    if contact.history_status != "unknown":
        pts += 0.1; notes.append("send history known")
    else:
        notes.append("send history unknown")
    if contact.eligibility_status == "eligible":
        pts += 0.15; notes.append("marked eligible")
    comps["contact_reliability"] = _component(pts, weights["contact_reliability"], ", ".join(notes))

    total = int(round(sum(c["points"] for c in comps.values())))
    return {"total": max(0, min(100, total)), "components": comps, "weights": weights, "run_id": run.id if run else None, "band": band(total), "disclaimer": DISCLAIMER}


def recompute_contact(db: Session, contact: Contact) -> LeadScore:
    r = compute(db, contact)
    row = db.get(LeadScore, contact.id)
    comps = {**r["components"], "_meta": {"band": r["band"], "disclaimer": r["disclaimer"]}}
    if row is None:
        row = LeadScore(contact_id=contact.id, total=r["total"], components=comps, weights=r["weights"], research_run_id=r["run_id"])
        db.add(row)
    else:
        row.total, row.components, row.weights, row.research_run_id = r["total"], comps, r["weights"], r["run_id"]
        row.computed_at = datetime.now(timezone.utc)
    db.flush()
    return row


def recompute_company(db: Session, company_id: int) -> int:
    contacts = db.execute(select(Contact).where(Contact.company_id == company_id)).scalars().all()
    for c in contacts:
        recompute_contact(db, c)
    # promote 'researched' -> 'qualified' is a human decision; we only keep stages consistent
    return len(contacts)
