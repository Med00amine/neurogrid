from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.core.settings_store import SenderProfile, get_group, set_group
from app.db import session_scope
from app.jobs import queue, register_all_handlers
from app.jobs.runner import run_one
from app.models import Contact, Draft, DraftVersion, EmailTemplate, Job, ResearchFinding
from app.services import llm as llm_mod
from app.services.drafting import compose, generator, lint, service
from app.services.llm import FakeLLM
from app.services.research import pipeline
from app.services.seed import seed_defaults
from tests.factories import make_company, make_contact
from tests.research_factory import seed_research

SENDER = dict(
    display_name="Test Sender", from_email="sender@sender.example.org", job_title="AI & AWS Engineer",
    website_url="https://sender.example.org", github_url="https://github.com/unverified-handle", verified_urls=["https://sender.example.org"],
    postal_address="1 Test Street, Testville 12345", data_source_statement="I found your work address on your company's public website.",
    proof_points=[],
)


@pytest.fixture(autouse=True)
def _reset_llm():
    llm_mod.set_llm_override(None)
    yield
    llm_mod.set_llm_override(None)


@pytest.fixture()
def world(database_url):
    with session_scope() as db:
        seed_defaults(db)
        set_group(db, "sender", SENDER)
        comp = make_company(db, domain="acme.example.com", name="Acme Data Labs")
        contact = make_contact(db, email="chris@acme.example.com", company=comp, name="Chris Martin")
        findings = seed_research(db, comp)
        template = db.execute(select(EmailTemplate).where(EmailTemplate.positioning == "agency_capacity")).scalar_one()
        return {"company": comp.id, "contact": contact.id, "finding": findings[0].id, "template": template.id}


def build(w, **kw) -> int:
    with session_scope() as db:
        d = service.create_draft(db, db.get(Contact, w["contact"]), db.get(EmailTemplate, w["template"]), "tester", **kw)
        return d.id


def current(db, did) -> tuple[Draft, DraftVersion]:
    d = db.get(Draft, did)
    return d, db.get(DraftVersion, d.current_version_id)


def test_template_draft_is_grounded_in_the_approved_finding(world):
    did = build(world, use_llm=False)
    with session_scope() as db:
        d, v = current(db, did)
        assert v.source == "template" and d.status == "needs_review"
        assert v.blockers == []
        assert v.body_text.startswith("Hi Chris,")
        assert "We are hiring a backend engineer to help us build LLM integrations for our clients." in v.body_text  # exact quote
        fact = [p for p in v.claims if p["type"] == "company_fact"]
        assert fact and fact[0]["finding_ids"] == [world["finding"]]
        assert lint.words(v.body_text) <= 110
        assert not [w for w in v.warnings if w["code"] == "buzzword"]
        assert db.get(Contact, world["contact"]).pipeline_stage == "draft_ready"


def test_draft_without_approved_research_has_blockers_and_cannot_be_approved(database_url):
    with session_scope() as db:
        seed_defaults(db)
        set_group(db, "sender", SENDER)
        comp = make_company(db, domain="norsearch.example.com")
        c = make_contact(db, email="a@norsearch.example.com", company=comp)
        seed_research(db, comp, status="pending")  # findings exist but nobody approved them
        t = db.execute(select(EmailTemplate)).scalars().first()
        d = service.create_draft(db, c, t, "tester", use_llm=False)
        did = d.id
        v = db.get(DraftVersion, d.current_version_id)
        assert any(b["code"] in ("no_company_evidence", "unsupported_claim") for b in v.blockers)
        with pytest.raises(service.DraftError):
            service.approve(db, did, "tester", acknowledge_warnings=True)


def test_approval_is_bound_to_exact_text_and_voided_by_any_edit(world):
    did = build(world, use_llm=False)
    with session_scope() as db:
        service.approve(db, did, "tester", acknowledge_warnings=True)
        d, v = current(db, did)
        assert d.status == "approved" and service.is_approval_valid(db, d)
        approved_v = v.version_no
        service.edit(db, did, "tester", subject=v.subject, body=v.body_text + "\n\nP.S. Happy to share a short summary.")
    with session_scope() as db:
        d, v = current(db, did)
        assert d.status == "needs_review" and d.approved_hash is None and v.version_no == approved_v + 1
        assert not service.is_approval_valid(db, d)
    # tampering with the stored text after approval is also detected
    with session_scope() as db:
        service.approve(db, did, "tester", acknowledge_warnings=True)
        d, v = current(db, did)
        assert service.is_approval_valid(db, d)
        from sqlalchemy import text as sqltext

        db.execute(sqltext("UPDATE draft_versions SET body_text = body_text || ' tampered' WHERE id = :i"), {"i": v.id})
    with session_scope() as db:
        d, _ = current(db, did)
        assert not service.is_approval_valid(db, d)


def test_rejecting_or_unapproving_research_invalidates_the_draft(world):
    did = build(world, use_llm=False)
    with session_scope() as db:
        pipeline.review_finding(db, world["finding"], "reject", "tester")
        with pytest.raises(service.DraftError) as e:
            service.approve(db, did, "tester", acknowledge_warnings=True)
        assert "approved research" in str(e.value) or "no longer approved" in str(e.value)


def fake_llm(paragraphs, subject="A question about your LLM hiring"):
    def responder(system, user, schema):
        return {"subject": subject, "greeting": "Hi Chris,", "sign_off": "Best regards,", "paragraphs": paragraphs}

    return FakeLLM(responder)


def test_llm_draft_unsupported_claims_are_blocked(world):
    fid = world["finding"]
    bad = fake_llm([
        {"text": "Congratulations on the funding round you just closed.", "type": "company_fact", "finding_ids": [99999], "sender_refs": []},
        {"text": "I have 10 years of experience and worked with clients like Siemens.", "type": "sender_fact", "finding_ids": [], "sender_refs": [0]},
        {"text": "See my portfolio at https://not-mine.example.net/work and write to someone@else.example.com.", "type": "offer", "finding_ids": [], "sender_refs": [0]},
        {"text": "I hope this email finds you well, our cutting-edge synergy is a game-changer.", "type": "offer", "finding_ids": [], "sender_refs": [0]},
    ])
    with session_scope() as db:
        d = service.create_draft(db, db.get(Contact, world["contact"]), db.get(EmailTemplate, world["template"]), "tester", llm_client=bad)
        did = d.id
        v = db.get(DraftVersion, d.current_version_id)
        codes = {b["code"] for b in v.blockers}
        assert {"unsupported_claim", "unsubstantiated_experience", "unverified_link", "foreign_email"} <= codes
        assert "buzzword" in {w["code"] for w in v.warnings}
        assert v.source == "llm" and v.cost_usd_est > 0
        with pytest.raises(service.DraftError):
            service.approve(db, did, "tester", acknowledge_warnings=True)
    # a clean, grounded LLM answer passes
    good = fake_llm([
        {"text": "I saw your opening: “We are hiring a backend engineer to help us build LLM integrations for our clients.”", "type": "company_fact", "finding_ids": [fid], "sender_refs": []},
        {"text": "I’m a freelance engineer working on AI/ML and LLM integrations. If you need extra capacity, I can help.", "type": "offer", "finding_ids": [], "sender_refs": [1]},
        {"text": "Would a short summary of what I do be useful?", "type": "cta", "finding_ids": [], "sender_refs": []},
    ])
    with session_scope() as db:
        v = service.regenerate(db, did, "tester", llm_client=good)
        assert v.blockers == []
        service.approve(db, did, "tester", acknowledge_warnings=True)


def test_numbers_must_appear_in_the_cited_evidence(world):
    fid = world["finding"]
    llm = fake_llm([
        {"text": "I saw you are hiring 25 engineers.", "type": "company_fact", "finding_ids": [fid], "sender_refs": []},
        {"text": "I work on AI/ML and LLM integrations.", "type": "offer", "finding_ids": [], "sender_refs": [1]},
    ])
    with session_scope() as db:
        d = service.create_draft(db, db.get(Contact, world["contact"]), db.get(EmailTemplate, world["template"]), "tester", llm_client=llm)
        assert "number_not_in_evidence" in {b["code"] for b in db.get(DraftVersion, d.current_version_id).blockers}


def test_user_edit_is_allowed_but_flagged_and_links_are_checked(world):
    did = build(world, use_llm=False)
    with session_scope() as db:
        d, v = current(db, did)
        body = v.body_text.replace("Best regards,", "You recently raised $10 million, which is great.\n\nBest regards,")
        v2 = service.edit(db, did, "tester", subject=v.subject, body=body)
        codes = {w["code"] for w in v2.warnings}
        assert {"user_text", "uncited_specific_claim"} <= codes and not v2.blockers
        v3 = service.edit(db, did, "tester", subject=v.subject, body=body + "\n\nSee https://evil.example.net/x")
        assert "unverified_link" in {b["code"] for b in v3.blockers}
        v4 = service.edit(db, did, "tester", subject="Re: our call", body=body)
        assert "subject_fake_reply" in {b["code"] for b in v4.blockers}
        v5 = service.edit(db, did, "tester", subject=v.subject, body="Hi {{first_name}}, " + body)
        assert "placeholder" in {b["code"] for b in v5.blockers}


def test_near_identical_drafts_for_unrelated_companies_are_blocked(database_url):
    quote = "We are hiring a backend engineer to help us build LLM integrations for our clients."
    with session_scope() as db:
        seed_defaults(db)
        set_group(db, "sender", SENDER)
        t = db.execute(select(EmailTemplate).where(EmailTemplate.positioning == "agency_capacity")).scalar_one()
        ids = []
        for n in range(2):
            comp = make_company(db, domain=f"same{n}.example.com", name="Same Name")  # identical name => identical text
            c = make_contact(db, email=f"x{n}@same{n}.example.com", company=comp, name="Chris Martin")
            seed_research(db, comp, [{"category": "hiring_signal", "quote": quote, "tags": ["ai_llm"]}])
            ids.append(service.create_draft(db, c, t, "tester", use_llm=False).id)
        v2 = db.get(DraftVersion, db.get(Draft, ids[1]).current_version_id)
        assert "near_duplicate" in {b["code"] for b in v2.blockers}


def test_tone_and_length_settings(world):
    with session_scope() as db:
        seed_research(db, db.get(generator.Company, world["company"]), [
            {"category": "hiring_signal", "quote": "We are hiring a backend engineer to help us build LLM integrations for our clients.", "tags": ["ai_llm"]},
            {"category": "technology", "quote": "Our engineers build RAG applications on AWS using Python and FastAPI.", "tags": ["rag_documents", "aws_devops"]},
        ])
        c, t = db.get(Contact, world["contact"]), db.get(EmailTemplate, world["template"])
        formal, _ = generator.generate(db, c, t, tone="formal", length="short", use_llm=False)
        assert formal.paragraphs[0]["text"] == "Dear Chris," and formal.paragraphs[-1]["text"] == "Kind regards,"
        short, _ = generator.generate(db, c, t, length="short", use_llm=False)
        medium, _ = generator.generate(db, c, t, length="medium", use_llm=False)
        n_facts = lambda g: len([p for p in g.paragraphs if p["type"] == "company_fact"])  # noqa: E731
        assert n_facts(short) == 1 and n_facts(medium) == 2


def test_composed_message_has_identity_disclosures_and_only_verified_links(world):
    sender = SenderProfile(**SENDER)
    env = get_settings()
    comp = compose.compose(sender=sender, env=env, subject="Hello", core_body="Hi Chris,\n\nBody.\n\nBest regards,", message_id=7, contact_id=3,
                           recipient_email="chris@acme.example.com", rfc_message_id="<x@sender.example.org>", public_base_url="http://localhost:8000")
    b = comp.body
    assert "Test Sender" in b and "AI & AWS Engineer" in b
    assert "Website: https://sender.example.org" in b
    assert "github.com" not in b  # the GitHub URL was typed but NOT verified, so it is never printed
    assert "1 Test Street, Testville 12345" in b and "I found your work address" in b
    assert "reply with “unsubscribe”" in b
    assert comp.headers["List-Unsubscribe"].startswith("<mailto:sender@sender.example.org?subject=unsubscribe>")
    assert "List-Unsubscribe-Post" not in comp.headers and "/u/" not in b  # http (not https) base: no one-click link
    https = compose.compose(sender=sender, env=env, subject="Hello", core_body="Hi,", message_id=7, contact_id=3, recipient_email="a@b.example.com",
                            rfc_message_id="<x@sender.example.org>", public_base_url="https://crm.sender.example.org")
    assert "List-Unsubscribe-Post" in https.headers and https.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "https://crm.sender.example.org/u/" in https.headers["List-Unsubscribe"]


def test_generate_drafts_job_skips_contacts_without_approved_research(world):
    with session_scope() as db:
        comp2 = make_company(db, domain="nofindings.example.com")
        c2 = make_contact(db, email="n@nofindings.example.com", company=comp2)
        job = queue.enqueue(db, "generate_drafts", {"contact_ids": [world["contact"], c2.id], "template_id": world["template"], "use_llm": False, "actor": "tester"}, total=2)
        jid = job.id
    register_all_handlers()
    run_one("t")
    with session_scope() as db:
        j = db.get(Job, jid)
        assert j.status == "completed" and j.result == {"created": 1, "skipped": 1, "failed": 0}
        assert len(db.execute(select(Draft)).scalars().all()) == 1
        assert queue.item_counts(db, jid) == {"done": 1, "skipped": 1}
    # a repeat run does not create duplicates
    with session_scope() as db:
        job = queue.enqueue(db, "generate_drafts", {"contact_ids": [world["contact"]], "template_id": world["template"], "use_llm": False}, total=1)
    run_one("t")
    with session_scope() as db:
        assert len(db.execute(select(Draft).where(Draft.status.in_(("needs_review", "approved")))).scalars().all()) == 1


def test_one_active_draft_per_contact_and_step(world):
    build(world, use_llm=False)
    with pytest.raises(service.DraftError):
        build(world, use_llm=False)
