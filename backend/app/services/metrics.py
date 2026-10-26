"""Overview metrics. Every number is a database query; nothing is demo data.

Each metric carries a `basis` label so the UI can distinguish:
  database            a count of rows in this application's database
  provider_confirmed  an outcome reported by the mail system itself (a bounce or delivery receipt we received)
  estimate            computed from a price table or a heuristic
  simulated           produced by a dry run (nothing was really sent)
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
    Campaign,
    Company,
    Contact,
    Deal,
    Draft,
    InboundMessage,
    Job,
    Message,
    OutreachEvent,
    ResearchRun,
    Task,
    UsageRecord,
)
from app.services.contacts import suppressed_expr
from app.services.suppression import count_suppressed_contacts


def _m(value, basis="database", note: str | None = None) -> dict:
    d = {"value": value, "basis": basis}
    if note:
        d["note"] = note
    return d


def month_start() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_spend(db: Session, kind: str) -> Decimal:
    v = db.execute(
        select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(UsageRecord.kind == kind, UsageRecord.at >= month_start())
    ).scalar_one()
    return Decimal(v)


def overview(db: Session) -> dict[str, Any]:
    by = lambda col, *where: dict(db.execute(select(col, func.count()).where(*where).group_by(col)).all())  # noqa: E731

    elig = by(Contact.eligibility_status)
    total_contacts = sum(elig.values())
    contacts = {
        "total": _m(total_contacts),
        "companies": _m(db.execute(select(func.count()).select_from(Company)).scalar_one()),
        "eligible": _m(elig.get("eligible", 0)),
        "excluded": _m(elig.get("excluded", 0)),
        "review_required": _m(elig.get("review_required", 0)),
        "unreviewed": _m(elig.get("unreviewed", 0)),
        "suppressed": _m(count_suppressed_contacts(db)),
        "history_unknown": _m(db.execute(select(func.count()).select_from(Contact).where(Contact.history_status == "unknown")).scalar_one(),
                              note="Blocked from sending until reviewed"),
        "sendable_now": _m(db.execute(
            select(func.count()).select_from(Contact).where(
                Contact.eligibility_status == "eligible", Contact.history_status == "attested_unsent", ~suppressed_expr(),
                Contact.is_freemail.is_(False), Contact.pipeline_stage.in_(("new", "researched", "qualified", "draft_ready", "approved")))
        ).scalar_one(), note="Eligible + history attested + not suppressed (jurisdiction rules are checked again at send time)"),
    }

    jobs = by(Job.status)
    research_jobs = dict(db.execute(select(Job.status, func.count()).where(Job.type == "research_companies").group_by(Job.status)).all())
    research = {
        "queued": _m(research_jobs.get("pending", 0)),
        "running": _m(research_jobs.get("running", 0)),
        "completed": _m(research_jobs.get("completed", 0) + research_jobs.get("partial", 0)),
        "failed": _m(research_jobs.get("failed", 0)),
        "companies_researched": _m(db.execute(select(func.count(func.distinct(ResearchRun.company_id))).where(ResearchRun.status.in_(("completed", "partial")))).scalar_one()),
    }

    drafts_by = by(Draft.status)
    drafts = {
        "awaiting_review": _m(drafts_by.get("needs_review", 0)),
        "approved": _m(drafts_by.get("approved", 0)),
        "rejected": _m(drafts_by.get("rejected", 0)),
    }

    def msg_counts(dry: bool) -> dict[str, int]:
        return dict(db.execute(select(Message.status, func.count()).where(Message.is_dry_run.is_(dry)).group_by(Message.status)).all())

    live, dry = msg_counts(False), msg_counts(True)
    email_live = {
        "queued": _m(live.get("queued", 0)),
        "submitting": _m(live.get("submitting", 0)),
        "submitted": _m(live.get("submitted", 0) + live.get("delivered", 0) + live.get("replied", 0) + live.get("bounced", 0),
                        note="Accepted by your mail server. This is NOT proof of delivery or of being read."),
        "delivered": _m(live.get("delivered", 0), "provider_confirmed",
                        "Only counted when the receiving system sent a delivery receipt. SMTP rarely does, so 0 is normal."),
        "bounced": _m(live.get("bounced", 0), "provider_confirmed"),
        "rejected": _m(live.get("rejected", 0), "provider_confirmed"),
        "failed": _m(live.get("failed", 0), note="Provably never handed to the mail server"),
        "unknown": _m(live.get("unknown", 0), note="Outcome unclear: needs manual investigation. Never auto-retried."),
    }
    email_dry = {k: _m(v, "simulated") for k, v in dry.items()}

    replies_q = select(func.count()).select_from(InboundMessage).where(InboundMessage.kind == "reply")
    replies = {
        "replies": _m(db.execute(replies_q).scalar_one()),
        "positive": _m(db.execute(replies_q.where(InboundMessage.classification.in_(("interested", "info_request", "referral")))).scalar_one(),
                       note="Based on the current classification, which includes your manual corrections"),
        "unsubscribes": _m(db.execute(replies_q.where(InboundMessage.classification == "unsubscribe")).scalar_one()),
    }
    stage = by(Contact.pipeline_stage)
    deals_won = db.execute(select(func.count(), func.coalesce(func.sum(Deal.value_amount), 0)).where(Deal.status == "won")).one()
    pipeline = {
        "meetings": _m(stage.get("meeting_scheduled", 0)),
        "proposals": _m(stage.get("proposal_sent", 0)),
        "won": _m(deals_won[0]),
        "won_value": _m(float(deals_won[1]), note="Sum of deal values you entered, in mixed currencies"),
        "by_stage": _m(stage),
    }

    spend = {
        "llm_month_usd": _m(float(month_spend(db, "llm")), "estimate", "Token counts come from the API; dollars use the price table in Settings"),
        "search_month_usd": _m(float(month_spend(db, "search")), "estimate"),
    }

    now = datetime.now(timezone.utc)
    attention = []
    unknown = db.execute(select(func.count()).select_from(Message).where(Message.status == "unknown", Message.is_dry_run.is_(False))).scalar_one()
    if unknown:
        attention.append({"kind": "unknown_outcome", "count": unknown, "text": f"{unknown} email(s) have an unknown outcome and need manual review", "link": "/campaigns"})
    due = db.execute(select(func.count()).select_from(Task).where(Task.status == "open", Task.due_at <= now)).scalar_one()
    if due:
        attention.append({"kind": "tasks_due", "count": due, "text": f"{due} task(s) are due", "link": "/pipeline"})
    failed_jobs = jobs.get("failed", 0) + jobs.get("partial", 0)
    if failed_jobs:
        attention.append({"kind": "jobs_failed", "count": failed_jobs, "text": f"{failed_jobs} job(s) failed or finished partially", "link": "/jobs"})
    unreviewed_replies = db.execute(select(func.count()).select_from(InboundMessage).where(InboundMessage.reviewed.is_(False), InboundMessage.matched_message_id.is_not(None))).scalar_one()
    if unreviewed_replies:
        attention.append({"kind": "replies_unreviewed", "count": unreviewed_replies, "text": f"{unreviewed_replies} repl(ies) to review", "link": "/replies"})
    paused = db.execute(select(func.count()).select_from(Campaign).where(Campaign.status == "paused")).scalar_one()
    if paused:
        attention.append({"kind": "campaign_paused", "count": paused, "text": f"{paused} campaign(s) paused", "link": "/campaigns"})
    if contacts["history_unknown"]["value"] and not live:
        attention.append({"kind": "history_unknown", "count": contacts["history_unknown"]["value"],
                          "text": "Previous-outreach history is unknown for these contacts. Import your sent log or attest before any live send.", "link": "/contacts"})

    recent = db.execute(select(OutreachEvent).order_by(OutreachEvent.id.desc()).limit(15)).scalars().all()
    return {
        "contacts": contacts, "research": research, "drafts": drafts, "email_live": email_live, "email_dry_run": email_dry,
        "replies": replies, "pipeline": pipeline, "spend": spend, "attention": attention,
        "recent_events": [
            {"id": e.id, "at": e.occurred_at.isoformat(), "type": e.event_type, "contact_id": e.contact_id, "message_id": e.message_id,
             "campaign_id": e.campaign_id, "source": e.source, "data": e.data}
            for e in recent
        ],
        "generated_at": now.isoformat(),
    }
