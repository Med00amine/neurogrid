"""The send path against a real (local) SMTP server that can misbehave. No real mail is ever sent."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import reset_settings_cache
from app.core.settings_store import set_group
from app.db import session_scope
from app.models import (
    Campaign,
    Contact,
    Draft,
    EligibilityDecision,
    Message,
    OutreachEvent,
    SendAttempt,
    Suppression,
    Task,
)
from app.services import jurisdictions, suppression
from app.services.drafting import service as draft_service
from app.services.sending import campaigns as camp
from app.services.sending import engine
from app.services.sending.providers import OutgoingMessage, SMTPProvider
from app.services.seed import seed_defaults
from tests.factories import make_company, make_contact
from tests.research_factory import seed_research
from tests.smtp_sink import Sink

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)  # a Tuesday, noon UTC


def at(seconds: float = 0) -> datetime:
    return T0 + timedelta(seconds=seconds)


SENDER = dict(
    display_name="Test Sender", from_email="sender@sender.example.org", job_title="AI & AWS Engineer",
    postal_address="1 Test Street, Testville 12345", data_source_statement="I found your address on your company website.",
    verified_urls=["https://sender.example.org"], website_url="https://sender.example.org",
)


@pytest.fixture()
def sink():
    with Sink() as s:
        yield s


@pytest.fixture()
def live(database_url, sink, monkeypatch):
    monkeypatch.setenv("LIVE_SENDING_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(sink.port))
    monkeypatch.setenv("SMTP_SECURITY", "none")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    reset_settings_cache()
    with session_scope() as db:
        seed_defaults(db)
        set_group(db, "sender", SENDER)
        set_group(db, "general", {"timezone": "UTC"})
        set_group(db, "sending", {"provider": "smtp", "live_mode_enabled": True, "daily_cap": 50, "hourly_cap": 50, "min_interval_seconds": 10,
                                   "window_days": [0, 1, 2, 3, 4, 5, 6], "window_start": "00:00", "window_end": "23:59",
                                   "max_attempts_per_message": 3, "max_consecutive_temp_failures": 3, "followup_gap_days": 5, "max_followups": 2})
        jurisdictions.attest(db, "FR", actor="tester", note="assessed FR requirements for the test")
    yield sink
    reset_settings_cache()


def ready_contacts(n: int, country: str = "FR", start: int = 0) -> list[int]:
    """n contacts that pass every gate and have an approved draft."""
    from app.models import EmailTemplate

    ids = []
    with session_scope() as db:
        t = db.execute(select(EmailTemplate).where(EmailTemplate.positioning == "agency_capacity")).scalar_one()
        for i in range(start, start + n):
            comp = make_company(db, domain=f"co{i}.example.fr", name=f"Company {i}")
            c = make_contact(db, email=f"pat{i}@co{i}.example.fr", company=comp, name=f"Pat{i} Smith", country_code=country,
                             eligibility_status="eligible", history_status="attested_unsent")
            seed_research(db, comp, [{"category": "hiring_signal", "tags": ["ai_llm"],
                                      "quote": f"Company {i} is hiring a senior engineer number {i} to build document pipelines for logistics customers."}])
            d = draft_service.create_draft(db, c, t, "tester", use_llm=False)
            draft_service.approve(db, d.id, "tester", acknowledge_warnings=True)
            ids.append(c.id)
    return ids


def new_campaign(ids: list[int], mode: str = "live", **kw) -> tuple[int, int]:
    with session_scope() as db:
        c = camp.build(db, name="Test campaign", mode=mode, actor="tester", contact_ids=ids, **kw)
        return c.id, c.estimate["included"]


def confirm(cid: int, n: int, mode: str = "live"):
    with session_scope() as db:
        c = db.get(Campaign, cid)
        return camp.confirm(db, cid, "tester", phrase=camp.required_phrase(c, n), expected_recipients=n)


def statuses(cid: int | None = None) -> dict[int, str]:
    with session_scope() as db:
        q = select(Message.contact_id, Message.status)
        if cid:
            q = q.where(Message.campaign_id == cid)
        return dict(db.execute(q).all())


# ---------------------------------------------------------------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------------------------------------------------------------


def test_live_send_end_to_end(live):
    (cid_contact,) = ready_contacts(1)
    cid, n = new_campaign([cid_contact])
    assert n == 1
    assert live.messages == []  # building a campaign sends nothing
    assert engine.tick(at(0))["sent"] == 0  # a draft campaign is never sent
    confirm(cid, n)
    assert live.messages == []  # confirming only queues
    assert engine.tick(at(1))["sent"] == 1
    assert len(live.messages) == 1
    sent = live.messages[0]["msg"]
    assert sent["To"] == "pat0@co0.example.fr" and "sender@sender.example.org" in sent["From"] and "Test Sender" in sent["From"]
    assert sent["Message-ID"].startswith("<") and sent["Message-ID"].endswith("@sender.example.org>")
    assert sent["List-Unsubscribe"].startswith("<mailto:sender@sender.example.org?subject=unsubscribe>")
    body = sent.get_content()
    assert "1 Test Street, Testville 12345" in body and "I found your address on your company website." in body
    assert "unsubscribe" in body.lower() and "Test Sender" in body
    assert "document pipelines" in body  # the personalised, evidence-backed sentence
    with session_scope() as db:
        m = db.execute(select(Message)).scalar_one()
        assert m.status == "submitted" and m.submitted_at and m.attempt_count == 1 and m.provider_message_id
        assert m.rfc_message_id == sent["Message-ID"]
        assert m.body_text.strip() == body.replace("\r\n", "\n").strip()  # what was sent is exactly what was stored (SMTP uses CRLF line ends)
        c = db.get(Contact, m.contact_id)
        assert c.pipeline_stage == "contacted" and c.history_status == "previously_contacted"
        follow = db.execute(select(Task).where(Task.kind == "followup")).scalar_one()
        assert follow.followup_step == 2 and follow.due_at.date() == (at(1) + timedelta(days=5)).date()
        types = [e.event_type for e in db.execute(select(OutreachEvent).where(OutreachEvent.message_id == m.id).order_by(OutreachEvent.id)).scalars()]
        assert types == ["message_queued", "submission_started", "message_submitted"]
        a = db.execute(select(SendAttempt)).scalar_one()
        assert a.outcome == "accepted" and a.smtp_code == 250
        assert db.get(Campaign, cid).status == "completed"
    assert engine.tick(at(100))["sent"] == 0 and len(live.messages) == 1  # nothing is ever sent twice


def test_confirmation_needs_the_exact_phrase_and_count(live):
    ids = ready_contacts(2)
    cid, n = new_campaign(ids)
    with session_scope() as db:
        with pytest.raises(camp.CampaignError):
            camp.confirm(db, cid, "tester", phrase="yes", expected_recipients=n)
    with session_scope() as db:
        with pytest.raises(camp.CampaignError):
            camp.confirm(db, cid, "tester", phrase=f"SEND {n} EMAILS", expected_recipients=n + 5)
    assert statuses() == {}  # nothing was queued by the failed attempts
    confirm(cid, n)
    assert len(statuses()) == 2


def test_live_needs_all_three_switches(live, monkeypatch):
    ids = ready_contacts(1)
    cid, n = new_campaign(ids)
    # 1) environment switch
    monkeypatch.setenv("LIVE_SENDING_ENABLED", "false")
    reset_settings_cache()
    with pytest.raises(camp.CampaignError) as e:
        confirm(cid, n)
    assert "LIVE_SENDING_ENABLED" in str(e.value)
    monkeypatch.setenv("LIVE_SENDING_ENABLED", "true")
    reset_settings_cache()
    # 2) settings switch
    with session_scope() as db:
        set_group(db, "sending", {"live_mode_enabled": False})
    with pytest.raises(camp.CampaignError):
        confirm(cid, n)
    with session_scope() as db:
        set_group(db, "sending", {"live_mode_enabled": True})
    # 3) incomplete sender identity
    with session_scope() as db:
        set_group(db, "sender", {"postal_address": ""})
    with pytest.raises(camp.CampaignError) as e:
        confirm(cid, n)
    assert "postal address" in str(e.value).lower()
    assert statuses() == {} and live.messages == []


def test_a_running_live_campaign_stops_if_the_switch_is_turned_off(live, monkeypatch):
    ids = ready_contacts(2)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    monkeypatch.setenv("LIVE_SENDING_ENABLED", "false")
    reset_settings_cache()
    stats = engine.tick(at(1))
    assert stats["sent"] == 0 and live.messages == []
    with session_scope() as db:
        c = db.get(Campaign, cid)
        assert c.status == "paused" and "LIVE_SENDING_ENABLED" in c.paused_reason


# ---------------------------------------------------------------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------------------------------------------------------------


def test_dry_run_never_touches_smtp_and_does_not_change_contacts(live):
    ids = ready_contacts(3)
    cid, n = new_campaign(ids, mode="dry_run")
    confirm(cid, n)
    stats = engine.tick(at(1))
    assert stats["sent"] == 3  # rehearsal ignores pacing and finishes at once
    assert live.messages == []  # SMTP is configured, but a dry run never opens a connection
    with session_scope() as db:
        msgs = db.execute(select(Message)).scalars().all()
        assert all(m.is_dry_run and m.provider == "dry_run" and m.status == "submitted" and m.provider_message_id.startswith("dryrun-") for m in msgs)
        for c in db.execute(select(Contact)).scalars():
            assert c.history_status == "attested_unsent" and c.pipeline_stage == "approved"  # a rehearsal changes nothing about the contact
        assert db.execute(select(Task).where(Task.kind == "followup")).first() is None
        assert db.get(Campaign, cid).status == "completed"
    # and a real campaign to the same people is still possible afterwards
    cid2, n2 = new_campaign(ids)
    assert n2 == 3


# ---------------------------------------------------------------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------------------------------------------------------------


def test_a_contact_cannot_be_in_two_live_campaigns(live):
    ids = ready_contacts(1)
    c1, n1 = new_campaign(ids)
    c2, n2 = new_campaign(ids)
    confirm(c1, n1)
    with session_scope() as db:
        with pytest.raises(camp.CampaignError) as e:
            camp.confirm(db, c2, "tester", phrase=camp.required_phrase(db.get(Campaign, c2), 1), expected_recipients=1)
        assert "No recipient is eligible" in str(e.value)
    engine.tick(at(1))
    assert len(live.messages) == 1


def test_parallel_workers_never_double_send(live):
    ids = ready_contacts(5)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    counter = {"k": 0}
    lock = threading.Lock()

    def worker():
        for _ in range(30):
            with lock:
                counter["k"] += 1
                now = at(20 * counter["k"])
            engine.tick(now)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    tos = [m["to"][0] for m in live.messages]
    assert sorted(tos) == sorted(f"pat{i}@co{i}.example.fr" for i in range(5))  # every recipient exactly once
    assert set(statuses().values()) == {"submitted"}


def test_queued_message_is_dropped_if_the_person_unsubscribes_before_it_goes_out(live):
    ids = ready_contacts(2)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    with session_scope() as db:
        suppression.suppress(db, value="pat0@co0.example.fr", reason="unsubscribe", actor="recipient")
        m = db.execute(select(Message).where(Message.recipient_email == "pat0@co0.example.fr")).scalar_one()
        assert m.status == "suppressed"  # cancelled the instant the suppression was written
    for k in range(1, 6):
        engine.tick(at(30 * k))
    assert [m["to"][0] for m in live.messages] == ["pat1@co1.example.fr"]


def test_eligibility_is_rechecked_at_send_time(live):
    ids = ready_contacts(3)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    with session_scope() as db:
        db.get(Contact, ids[0]).eligibility_status = "excluded"          # reviewer changed their mind
        db.get(Contact, ids[1]).history_status = "previously_contacted"  # a history import discovered an earlier email
    for k in range(1, 8):
        engine.tick(at(30 * k))
    assert [m["to"][0] for m in live.messages] == ["pat2@co2.example.fr"]
    st = statuses()
    assert st[ids[0]] == "cancelled" and st[ids[1]] == "cancelled" and st[ids[2]] == "submitted"
    with session_scope() as db:
        ev = db.execute(select(OutreachEvent).where(OutreachEvent.event_type == "send_blocked")).scalars().all()
        assert len(ev) == 2 and any("previously_contacted" in e.data["reasons"] for e in ev)


def test_withdrawing_the_draft_approval_after_confirmation_stops_the_send(live):
    ids = ready_contacts(1)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    with session_scope() as db:
        d = db.execute(select(Draft)).scalar_one()
        draft_service.reject(db, d.id, "tester", "changed my mind")
    engine.tick(at(5))
    assert live.messages == [] and list(statuses().values()) == ["cancelled"]


def test_jurisdiction_gates(live):
    de = ready_contacts(1, country="DE")
    zz = ready_contacts(1, country=None, start=10)
    with session_scope() as db:
        db.get(Contact, zz[0]).country_code = None
    cid, n = new_campaign(de + zz + ready_contacts(1, start=20))
    with session_scope() as db:
        recs = {r.contact_id: r for r in camp.CampaignRecipient and db.execute(select(camp.CampaignRecipient)).scalars()}
    assert n == 1  # only the French contact passes
    with session_scope() as db:
        rows = db.execute(select(camp.CampaignRecipient).where(camp.CampaignRecipient.status == "excluded")).scalars().all()
        reasons = " ".join(r.exclusion_reason for r in rows)
        assert "requires prior consent" in reasons and "not assessed" in reasons
    # recording consent for the German contact makes him sendable (and is audited)
    with session_scope() as db:
        d = db.get(Contact, de[0])
        d.consent_basis = "Opted in via signed form on 2026-02-01"
    with session_scope() as db:
        camp.refresh(db, cid, "tester")
        assert db.get(Campaign, cid).estimate["included"] == 2


# ---------------------------------------------------------------------------------------------------------------------------------
# pacing
# ---------------------------------------------------------------------------------------------------------------------------------


def test_minimum_interval_between_sends(live):
    ids = ready_contacts(3)
    cid, n = new_campaign(ids, min_interval_seconds=60)
    confirm(cid, n)
    assert engine.tick(at(0))["sent"] == 1
    assert engine.tick(at(30))["sent"] == 0  # too soon
    assert engine.tick(at(59))["sent"] == 0
    assert engine.tick(at(61))["sent"] == 1
    assert len(live.messages) == 2


def test_daily_cap_and_window(live):
    with session_scope() as db:
        set_group(db, "sending", {"daily_cap": 2, "min_interval_seconds": 10})
    ids = ready_contacts(4)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    for k in range(10):
        engine.tick(at(20 * k))
    assert len(live.messages) == 2  # cap reached for today
    with session_scope() as db:
        assert db.get(Campaign, cid).status == "running"
    for k in range(10):
        engine.tick(at(86400 + 20 * k))  # next day
    assert len(live.messages) == 4


def test_outside_sending_window_nothing_is_sent(live):
    with session_scope() as db:
        set_group(db, "sending", {"window_days": [0, 1, 2, 3, 4], "window_start": "09:00", "window_end": "17:00"})
    ids = ready_contacts(1)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    night = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    assert engine.tick(night)["waiting"] == 1 and engine.tick(saturday)["waiting"] == 1
    assert live.messages == []
    assert engine.tick(datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc))["sent"] == 1  # Monday morning


def test_hourly_cap(live):
    with session_scope() as db:
        set_group(db, "sending", {"hourly_cap": 2})
    ids = ready_contacts(4)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    for k in range(8):
        engine.tick(at(30 * k))
    assert len(live.messages) == 2
    for k in range(8):
        engine.tick(at(3700 + 30 * k))
    assert len(live.messages) == 4


# ---------------------------------------------------------------------------------------------------------------------------------
# failures: only provably-unsent failures are retried
# ---------------------------------------------------------------------------------------------------------------------------------


def test_unknown_mailbox_is_rejected_suppressed_and_not_retried(live):
    ids = ready_contacts(2)
    live.handler.rcpt_modes["pat0@co0.example.fr"] = "rcpt_550"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    for k in range(1, 6):
        engine.tick(at(30 * k))
    st = statuses()
    assert st[ids[0]] == "rejected" and st[ids[1]] == "submitted"
    with session_scope() as db:
        s = db.execute(select(Suppression).where(Suppression.value == "pat0@co0.example.fr")).scalar_one()
        assert s.reason == "bounce_hard" and s.active
        assert db.execute(select(Message).where(Message.contact_id == ids[0])).scalar_one().attempt_count == 1


def test_temporary_failure_is_retried_later_with_backoff(live):
    ids = ready_contacts(1)
    live.handler.rcpt_modes["pat0@co0.example.fr"] = "rcpt_450"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    engine.tick(at(0))
    with session_scope() as db:
        m = db.execute(select(Message)).scalar_one()
        assert m.status == "queued" and m.attempt_count == 1 and m.last_error_category == "network"
        assert m.not_before == at(0) + timedelta(minutes=5)
    engine.tick(at(120))  # too early to retry
    assert statuses()[ids[0]] == "queued"
    live.handler.rcpt_modes.clear()
    engine.tick(at(5 * 60 + 5))
    assert statuses()[ids[0]] == "submitted" and len(live.messages) == 1


def test_data_phase_temporary_refusal_is_safe_to_retry_and_never_duplicates(live):
    ids = ready_contacts(1)
    live.handler.mode = "data_451"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    engine.tick(at(0))
    assert statuses()[ids[0]] == "queued" and live.messages == []
    live.handler.mode = "ok"
    engine.tick(at(400))
    assert len(live.messages) == 1 and statuses()[ids[0]] == "submitted"


def test_giving_up_marks_failed_and_allows_a_deliberate_new_message(live):
    ids = ready_contacts(1)
    live.handler.mode = "rcpt_450"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    t = 0
    for _ in range(3):
        engine.tick(at(t))
        t += 4000
    with session_scope() as db:
        m = db.execute(select(Message)).scalar_one()
        assert m.status == "failed" and m.attempt_count == 3 and "never accepted" in m.status_reason
    # provably not sent -> the operator may include the contact in a new campaign
    live.handler.mode = "ok"
    cid2, n2 = new_campaign(ids)
    assert n2 == 1
    confirm(cid2, n2)
    engine.tick(at(t + 100))
    assert len(live.messages) == 1


def test_repeated_provider_errors_pause_the_campaign(live):
    ids = ready_contacts(5)
    live.handler.mode = "data_451"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    for k in range(0, 6):
        engine.tick(at(30 * k))
    with session_scope() as db:
        c = db.get(Campaign, cid)
        assert c.status == "paused" and "in a row" in c.paused_reason
    sent_before = len(live.messages)
    engine.tick(at(10000))
    assert len(live.messages) == sent_before


def test_rate_limit_response_sets_a_backoff(live):
    ids = ready_contacts(2)
    live.handler.mode = "rate"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    engine.tick(at(0))
    with session_scope() as db:
        c = db.get(Campaign, cid)
        assert c.backoff_until == at(0) + timedelta(hours=1)
    live.handler.mode = "ok"
    assert engine.tick(at(600))["sent"] == 0  # still backing off, even though the server would accept now
    assert engine.tick(at(3700))["sent"] == 1


def test_policy_rejections_are_recorded_but_do_not_suppress_the_person(live):
    ids = ready_contacts(4)
    live.handler.mode = "data_554"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    for k in range(0, 5):
        engine.tick(at(30 * k))
    with session_scope() as db:
        assert db.execute(select(Suppression)).first() is None
        assert db.get(Campaign, cid).status == "paused"
        m = db.execute(select(Message).where(Message.status == "rejected")).scalars().first()
        assert m.last_error_category == "policy_rejected"


def test_wrong_password_pauses_the_campaign_and_keeps_the_message_queued(database_url, monkeypatch):
    with Sink(auth=("user", "right-password")) as sink:
        monkeypatch.setenv("LIVE_SENDING_ENABLED", "true")
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(sink.port))
        monkeypatch.setenv("SMTP_SECURITY", "none")
        monkeypatch.setenv("SMTP_USERNAME", "user")
        monkeypatch.setenv("SMTP_PASSWORD", "wrong-password-xyz")
        reset_settings_cache()
        with session_scope() as db:
            seed_defaults(db)
            set_group(db, "sender", SENDER)
            set_group(db, "general", {"timezone": "UTC"})
            set_group(db, "sending", {"provider": "smtp", "live_mode_enabled": True, "min_interval_seconds": 10, "window_days": [0, 1, 2, 3, 4, 5, 6],
                                       "window_start": "00:00", "window_end": "23:59"})
            jurisdictions.attest(db, "FR", actor="t", note="assessed for the test run")
        ids = ready_contacts(1)
        cid, n = new_campaign(ids)
        confirm(cid, n)
        engine.tick(at(0))
        with session_scope() as db:
            c, m = db.get(Campaign, cid), db.execute(select(Message)).scalar_one()
            assert c.status == "paused" and "login" in c.paused_reason.lower()
            assert m.status == "queued"  # not failed, not suppressed: simply waiting for the operator to fix the password
            assert db.execute(select(Suppression)).first() is None
            a = db.execute(select(SendAttempt)).scalar_one()
            assert "wrong-password-xyz" not in (a.response or "")
        assert sink.messages == []


def test_connection_refused_is_a_safe_retry(database_url, monkeypatch):
    from tests.smtp_sink import free_port

    monkeypatch.setenv("LIVE_SENDING_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(free_port()))  # nothing listens here
    monkeypatch.setenv("SMTP_SECURITY", "none")
    reset_settings_cache()
    with session_scope() as db:
        seed_defaults(db)
        set_group(db, "sender", SENDER)
        set_group(db, "general", {"timezone": "UTC"})
        set_group(db, "sending", {"provider": "smtp", "live_mode_enabled": True, "min_interval_seconds": 10, "window_days": [0, 1, 2, 3, 4, 5, 6],
                                   "window_start": "00:00", "window_end": "23:59"})
        jurisdictions.attest(db, "FR", actor="t", note="assessed for the test run")
    ids = ready_contacts(1)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    engine.tick(at(0))
    with session_scope() as db:
        m = db.execute(select(Message)).scalar_one()
        assert m.status == "queued" and m.last_error_category == "network"


# ---------------------------------------------------------------------------------------------------------------------------------
# ambiguity
# ---------------------------------------------------------------------------------------------------------------------------------


def test_connection_lost_after_data_is_unknown_and_never_retried(live):
    ids = ready_contacts(2)
    live.handler.rcpt_modes["pat0@co0.example.fr"] = "drop"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    engine.tick(at(0))
    with session_scope() as db:
        m = db.execute(select(Message).where(Message.contact_id == ids[0])).scalar_one()
        assert m.status == "unknown" and m.attempt_count == 1
        c = db.get(Campaign, cid)
        assert c.status == "paused" and "unknown outcome" in c.paused_reason
        task = db.execute(select(Task).where(Task.kind == "investigate")).scalar_one()
        assert task.status == "open" and task.source_message_id == m.id
    received = len(live.messages)
    for k in range(1, 6):
        engine.tick(at(1000 * k))
    assert len(live.messages) == received  # no automatic retry, and the paused campaign sends nothing else
    with session_scope() as db:
        assert db.execute(select(Message).where(Message.contact_id == ids[0])).scalar_one().attempt_count == 1
        with pytest.raises(camp.CampaignError):
            camp.resume(db, cid, "tester")  # cannot resume until the unknown message is resolved


def test_reconcile_was_not_sent_then_resume(live):
    ids = ready_contacts(2)
    live.handler.rcpt_modes["pat0@co0.example.fr"] = "drop"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    engine.tick(at(0))
    live.handler.rcpt_modes.clear()
    with session_scope() as db:
        mid = db.execute(select(Message.id).where(Message.status == "unknown")).scalar_one()
        with pytest.raises(ValueError):
            engine.reconcile_unknown(db, mid, "was_not_sent", "tester", "x")  # needs a real note
        engine.reconcile_unknown(db, mid, "was_not_sent", "tester", "checked the Sent folder: absent")
        assert db.execute(select(Task).where(Task.kind == "investigate")).scalar_one().status == "done"
        camp.resume(db, cid, "tester")
    for k in range(1, 5):
        engine.tick(at(100 * k))
    st = statuses()
    assert st[ids[0]] == "failed" and st[ids[1]] == "submitted"


def test_reconcile_was_sent_marks_the_contact_contacted(live):
    ids = ready_contacts(1)
    live.handler.mode = "drop"
    cid, n = new_campaign(ids)
    confirm(cid, n)
    engine.tick(at(0))
    with session_scope() as db:
        mid = db.execute(select(Message.id).where(Message.status == "unknown")).scalar_one()
        engine.reconcile_unknown(db, mid, "was_sent", "tester", "found it in the Sent folder")
        c = db.get(Contact, ids[0])
        assert c.history_status == "previously_contacted" and c.pipeline_stage == "contacted"
    # a person whose email may have been sent can never be emailed again by a new campaign
    new_id, n2 = new_campaign(ids)
    assert n2 == 0


def test_crash_between_commit_and_provider_answer_is_recovered_as_unknown(live):
    ids = ready_contacts(1)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    # simulate a worker that wrote `submitting` and then died
    with session_scope() as db:
        m = db.execute(select(Message)).scalar_one()
        m.status, m.attempt_count, m.submission_started_at = "submitting", 1, at(-3600)
        db.add(SendAttempt(message_id=m.id, attempt_no=1, started_at=at(-3600), stage="started"))
    stats = engine.tick(at(0))
    assert stats["recovered"] == 1 and stats["sent"] == 0
    assert live.messages == []  # NOT re-sent
    with session_scope() as db:
        assert db.execute(select(Message)).scalar_one().status == "unknown"
        assert db.get(Campaign, cid).status == "paused"


def test_fresh_submitting_message_is_not_touched_by_recovery(live):
    ids = ready_contacts(1)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    with session_scope() as db:
        m = db.execute(select(Message)).scalar_one()
        m.status, m.attempt_count, m.submission_started_at = "submitting", 1, at(-5)
    assert engine.tick(at(0))["recovered"] == 0  # another worker may legitimately still be talking to the server


# ---------------------------------------------------------------------------------------------------------------------------------
# lifecycle controls
# ---------------------------------------------------------------------------------------------------------------------------------


def test_pause_resume_cancel(live):
    ids = ready_contacts(4)
    cid, n = new_campaign(ids)
    confirm(cid, n)
    engine.tick(at(0))
    with session_scope() as db:
        camp.pause(db, cid, "tester", "lunch")
    assert engine.tick(at(100))["sent"] == 0 and len(live.messages) == 1
    with session_scope() as db:
        camp.resume(db, cid, "tester")
    assert engine.tick(at(200))["sent"] == 1 and len(live.messages) == 2
    with session_scope() as db:
        camp.cancel(db, cid, "tester")
    assert engine.tick(at(300))["sent"] == 0 and len(live.messages) == 2
    st = statuses(cid)
    assert sorted(st.values()) == ["cancelled", "cancelled", "submitted", "submitted"]
    with session_scope() as db:
        assert db.get(Campaign, cid).status == "cancelled"
        # follow-ups for cancelled campaigns are cancelled too
        open_follow = db.execute(select(Task).where(Task.kind == "followup", Task.status == "open")).scalars().all()
        assert open_follow == []


# ---------------------------------------------------------------------------------------------------------------------------------
# provider guard + hygiene
# ---------------------------------------------------------------------------------------------------------------------------------


def test_smtp_provider_refuses_non_local_hosts_in_tests(database_url, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    reset_settings_cache()
    out = SMTPProvider().send(OutgoingMessage("a <a@b.example>", "a@b.example", "c@d.example", "s", "b", "<x@b.example>"))
    assert out.kind == "pre_send_failure" and out.error_category == "config" and "non-local" in out.response


def test_secrets_never_appear_in_logs(live, caplog, monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "super-secret-smtp-password-1")
    reset_settings_cache()
    from app.core.logging_setup import RedactingFilter

    f = RedactingFilter()
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "login failed for password=super-secret-smtp-password-1 !", None, None)
    f.filter(rec)
    assert "super-secret" not in rec.getMessage()


@pytest.mark.parametrize("code,text,expected", [
    (550, "5.1.1 <x@y>: User unknown", ("rejected", "mailbox_unavailable")),
    (550, "5.7.1 Message blocked by spam policy", ("rejected", "policy_rejected")),
    (554, "5.7.1 Rejected as spam", ("rejected", "policy_rejected")),
    (554, "Transaction failed", ("rejected", "content_rejected")),
    (421, "4.7.0 Too many connections from your IP", ("temporary_failure", "rate_limited")),
    (450, "4.2.0 Mailbox busy, try again later", ("temporary_failure", "network")),
    (451, "Requested action aborted: local error in processing", ("temporary_failure", "network")),
    (550, "5.4.5 Daily user sending quota exceeded", ("temporary_failure", "rate_limited")),
    (535, "5.7.8 Username and Password not accepted", ("auth_failure", "auth")),
])
def test_smtp_reply_classification(code, text, expected):
    from app.services.sending.providers import classify_reply

    assert classify_reply(code, text) == expected
