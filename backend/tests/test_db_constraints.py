"""The database itself must refuse duplicate / conflicting sends, whatever the application code does."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import AuditLog, Contact, OutreachEvent, Suppression
from tests.factories import make_campaign, make_contact, make_message

# --- contact identity -------------------------------------------------------------------------------


def test_duplicate_contact_email_is_rejected(db):
    make_contact(db, email="alice@acme.example.com")
    db.commit()
    with pytest.raises(IntegrityError):
        make_contact(db, email="alice@acme.example.com")
        db.commit()


# --- one live message per contact+step ----------------------------------------------------------------


def test_second_live_message_for_same_contact_and_step_is_rejected_across_campaigns(db):
    contact = make_contact(db)
    c1 = make_campaign(db, mode="live", provider="smtp")
    c2 = make_campaign(db, mode="live", provider="smtp")
    make_message(db, contact, c1)
    db.commit()
    with pytest.raises(IntegrityError):
        make_message(db, contact, c2)  # different campaign, same contact+step
        db.commit()


def test_live_message_in_second_sequence_step_is_allowed(db):
    contact = make_contact(db)
    c1 = make_campaign(db, mode="live", provider="smtp")
    make_message(db, contact, c1, sequence_step=1)
    make_message(db, contact, c1, sequence_step=2)
    db.commit()


def test_dry_run_messages_do_not_block_anything(db):
    contact = make_contact(db)
    dry1 = make_campaign(db, mode="dry_run")
    dry2 = make_campaign(db, mode="dry_run")
    live = make_campaign(db, mode="live", provider="smtp")
    make_message(db, contact, dry1)
    make_message(db, contact, dry2)
    make_message(db, contact, live)  # a real send is still possible after rehearsals
    db.commit()


def test_cancelled_message_does_not_block_a_new_live_message(db):
    contact = make_contact(db)
    c1 = make_campaign(db, mode="live", provider="smtp")
    c2 = make_campaign(db, mode="live", provider="smtp")
    m = make_message(db, contact, c1)
    m.status = "cancelled"
    db.commit()
    make_message(db, contact, c2)
    db.commit()


@pytest.mark.parametrize("blocking_status", ["submitted", "unknown", "bounced", "rejected"])
def test_message_that_may_have_been_sent_keeps_blocking(db, blocking_status):
    contact = make_contact(db)
    c1 = make_campaign(db, mode="live", provider="smtp")
    c2 = make_campaign(db, mode="live", provider="smtp")
    m = make_message(db, contact, c1)
    legal_path = {
        "submitted": ["submitting", "submitted"],
        "unknown": ["submitting", "unknown"],
        "rejected": ["submitting", "rejected"],
        "bounced": ["submitting", "submitted", "bounced"],  # a bounce can only follow acceptance
    }[blocking_status]
    _move(db, m, *legal_path)
    db.commit()
    with pytest.raises(IntegrityError):
        make_message(db, contact, c2)
        db.commit()


def test_idempotency_key_and_rfc_message_id_are_unique(db):
    contact_a, contact_b = make_contact(db), make_contact(db)
    camp = make_campaign(db)
    make_message(db, contact_a, camp, idempotency_key="same-key")
    db.commit()
    with pytest.raises(IntegrityError):
        make_message(db, contact_b, camp, idempotency_key="same-key")
        db.commit()


def test_dry_run_flag_and_provider_must_agree(db):
    contact = make_contact(db)
    camp = make_campaign(db)
    with pytest.raises(IntegrityError):
        make_message(db, contact, camp, is_dry_run=False, provider="dry_run")
        db.commit()
    db.rollback()
    with pytest.raises(IntegrityError):
        make_message(db, contact, camp, is_dry_run=True, provider="smtp")
        db.commit()


# --- message state machine ---------------------------------------------------------------------------------


def _move(db, message, *path):
    for status in path:
        message.status = status
        db.flush()


def test_normal_lifecycle_is_allowed(db):
    contact = make_contact(db)
    m = make_message(db, contact, make_campaign(db))
    _move(db, m, "submitting", "submitted", "delivered", "replied")
    db.commit()


@pytest.mark.parametrize(
    "path",
    [
        ("submitting", "submitted", "queued"),  # a submitted message must never be re-queued
        ("submitting", "unknown", "queued"),  # an ambiguous outcome must never be blindly retried
        ("submitting", "rejected", "queued"),
        ("cancelled", "queued"),
        ("submitting", "failed", "queued"),
    ],
)
def test_illegal_status_transitions_are_rejected(db, path):
    contact = make_contact(db)
    m = make_message(db, contact, make_campaign(db))
    with pytest.raises((DBAPIError, IntegrityError)):
        _move(db, m, *path)
        db.commit()


def test_cannot_skip_the_submitting_step(db):
    contact = make_contact(db)
    m = make_message(db, contact, make_campaign(db))
    with pytest.raises((DBAPIError, IntegrityError)):
        _move(db, m, "submitted")
        db.commit()


def test_message_snapshot_is_immutable(db):
    contact = make_contact(db)
    m = make_message(db, contact, make_campaign(db))
    db.commit()
    for column, value in [("body_text", "changed"), ("subject", "changed"), ("recipient_email", "other@x.example.com")]:
        with pytest.raises((DBAPIError, IntegrityError)):
            db.execute(text(f"UPDATE messages SET {column} = :v WHERE id = :id"), {"v": value, "id": m.id})
            db.commit()
        db.rollback()


# --- suppression ---------------------------------------------------------------------------------------------


def test_live_message_to_suppressed_email_is_rejected_by_the_database(db):
    contact = make_contact(db, email="blocked@acme.example.com")
    db.add(Suppression(kind="email", value="blocked@acme.example.com", reason="unsubscribe"))
    db.commit()
    with pytest.raises((DBAPIError, IntegrityError)):
        make_message(db, contact, make_campaign(db, mode="live", provider="smtp"))
        db.commit()


def test_live_message_to_suppressed_domain_is_rejected(db):
    contact = make_contact(db, email="someone@blocked.example.net")
    db.add(Suppression(kind="domain", value="blocked.example.net", reason="manual"))
    db.commit()
    with pytest.raises((DBAPIError, IntegrityError)):
        make_message(db, contact, make_campaign(db, mode="live", provider="smtp"))
        db.commit()


def test_suppression_does_not_stop_dry_run_rehearsal(db):
    contact = make_contact(db, email="blocked2@acme.example.com")
    db.add(Suppression(kind="email", value="blocked2@acme.example.com", reason="unsubscribe"))
    db.commit()
    make_message(db, contact, make_campaign(db, mode="dry_run"))
    db.commit()


def test_inactive_suppression_does_not_block(db):
    contact = make_contact(db, email="lifted@acme.example.com")
    db.add(Suppression(kind="email", value="lifted@acme.example.com", reason="manual", active=False))
    db.commit()
    make_message(db, contact, make_campaign(db, mode="live", provider="smtp"))
    db.commit()


def test_moving_a_queued_message_to_submitting_rechecks_suppression(db):
    contact = make_contact(db, email="late@acme.example.com")
    m = make_message(db, contact, make_campaign(db, mode="live", provider="smtp"))
    db.commit()
    db.add(Suppression(kind="email", value="late@acme.example.com", reason="unsubscribe"))
    db.commit()
    with pytest.raises((DBAPIError, IntegrityError)):
        m.status = "submitting"
        db.commit()


def test_suppressions_cannot_be_deleted(db):
    db.add(Suppression(kind="email", value="x@acme.example.com", reason="manual"))
    db.commit()
    with pytest.raises((DBAPIError, IntegrityError)):
        db.execute(text("DELETE FROM suppressions"))
        db.commit()
    db.rollback()
    with pytest.raises((DBAPIError, IntegrityError)):
        db.execute(text("TRUNCATE suppressions"))
        db.commit()


def test_suppression_value_is_unique_per_kind(db):
    db.add(Suppression(kind="email", value="dup@acme.example.com", reason="manual"))
    db.commit()
    with pytest.raises(IntegrityError):
        db.add(Suppression(kind="email", value="dup@acme.example.com", reason="unsubscribe"))
        db.commit()


# --- append-only history ------------------------------------------------------------------------------------


@pytest.mark.parametrize("model,kwargs", [(OutreachEvent, {"event_type": "t"}), (AuditLog, {"action": "t"})])
def test_history_tables_are_append_only(db, model, kwargs):
    row = model(**kwargs)
    db.add(row)
    db.commit()
    table = model.__tablename__
    for sql in (f"UPDATE {table} SET id = id", f"DELETE FROM {table}", f"TRUNCATE {table}"):
        with pytest.raises((DBAPIError, IntegrityError)):
            db.execute(text(sql))
            db.commit()
        db.rollback()
    assert db.query(model).count() == 1


def test_campaign_limits_have_safe_bounds(db):
    with pytest.raises(IntegrityError):
        make_campaign(db, daily_cap=100000)
        db.commit()
    db.rollback()
    with pytest.raises(IntegrityError):
        make_campaign(db, min_interval_seconds=1)
        db.commit()
