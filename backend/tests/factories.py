"""Tiny helpers that insert synthetic rows. All data here is fake (example.com / example.org)."""

from __future__ import annotations

import itertools
import uuid

from sqlalchemy.orm import Session

from app.models import Campaign, Company, Contact, Message

_counter = itertools.count(1)


def make_company(db: Session, domain: str | None = None, name: str | None = None, **kw) -> Company:
    n = next(_counter)
    c = Company(domain=domain or f"company{n}.example.com", name=name or f"Company {n}", **kw)
    db.add(c)
    db.flush()
    return c


def make_contact(db: Session, email: str | None = None, company: Company | None = None, **kw) -> Contact:
    n = next(_counter)
    email = (email or f"person{n}@company{n}.example.com").lower()
    domain = email.split("@")[1]
    company = company or make_company(db, domain=domain)
    c = Contact(
        email_normalized=email,
        email_original=email,
        email_domain=domain,
        name=kw.pop("name", f"Person {n}"),
        company_id=company.id,
        **kw,
    )
    db.add(c)
    db.flush()
    return c


def make_campaign(db: Session, **kw) -> Campaign:
    n = next(_counter)
    data = dict(name=f"Campaign {n}", mode="dry_run", provider="dry_run", status="draft")
    data.update(kw)
    c = Campaign(**data)
    db.add(c)
    db.flush()
    return c


def make_message(db: Session, contact: Contact, campaign: Campaign, **kw) -> Message:
    live = campaign.mode == "live"
    data = dict(
        campaign_id=campaign.id,
        contact_id=contact.id,
        sequence_step=1,
        is_dry_run=not live,
        provider="smtp" if live else "dry_run",
        recipient_email=contact.email_normalized,
        from_email="sender@sender.example.org",
        subject="Hello",
        body_text="Body",
        rfc_message_id=f"<{uuid.uuid4().hex}@sender.example.org>",
        idempotency_key=uuid.uuid4().hex,
        status="queued",
    )
    data.update(kw)
    m = Message(**data)
    db.add(m)
    db.flush()
    return m
