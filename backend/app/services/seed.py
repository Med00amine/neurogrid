"""Idempotent defaults: jurisdiction policies, starter segments, starter email templates."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailTemplate, Segment
from app.services.jurisdictions import seed_jurisdictions

DEFAULT_SEGMENTS = [
    dict(
        name="AI consultancies",
        description="AI/ML consultancies that may need extra delivery capacity (Python, LLM integration, RAG).",
        positioning="agency_capacity",
        filter={"company_types": ["ai_consultancy"]},
    ),
    dict(
        name="Software agencies",
        description="Software development agencies/studios that might subcontract backend or AI work.",
        positioning="agency_capacity",
        filter={"company_types": ["software_agency"]},
    ),
    dict(
        name="AWS consultancies",
        description="AWS partners and cloud consultancies (deployment, productionisation).",
        positioning="agency_capacity",
        filter={"company_types": ["aws_consultancy"]},
    ),
    dict(
        name="Businesses with integration needs",
        description="Product companies where research found a documented integration, document-processing or AI need.",
        positioning="direct_solution",
        filter={"company_types": ["product_company", "other"], "service_tags": ["integrations", "rag_documents", "ai_llm"]},
    ),
]

DEFAULT_TEMPLATES = [
    dict(
        name="Agency: extra engineering capacity",
        positioning="agency_capacity",
        tone="direct",
        length="short",
        instructions=(
            "The recipient works at a consultancy or agency. Offer engineering capacity as a subcontractor or "
            "collaborator for client projects. Mention only services the sender actually offers."
        ),
        subject_hint="Backend / AI engineering capacity",
        cta_text="If it would help to have an extra engineer for client work, I'm happy to share a short summary of what I do and check fit.",
        is_default=True,
    ),
    dict(
        name="Direct: help with a specific need",
        positioning="direct_solution",
        tone="friendly",
        length="short",
        instructions=(
            "The recipient works at a company with a documented need. Offer help with that specific need. "
            "Do not imply knowledge of their internal problems beyond the cited evidence."
        ),
        subject_hint="A question about your engineering work",
        cta_text="If this is relevant, would a 20-minute call be useful? If not, no problem at all.",
        is_default=True,
    ),
]


def seed_defaults(db: Session) -> dict[str, int]:
    created = {"jurisdictions": seed_jurisdictions(db), "segments": 0, "templates": 0}
    existing_segments = set(db.execute(select(Segment.name)).scalars())
    for s in DEFAULT_SEGMENTS:
        if s["name"] not in existing_segments:
            db.add(Segment(**s))
            created["segments"] += 1
    existing_templates = set(db.execute(select(EmailTemplate.name)).scalars())
    for t in DEFAULT_TEMPLATES:
        if t["name"] not in existing_templates:
            db.add(EmailTemplate(**t))
            created["templates"] += 1
    db.flush()
    return created
