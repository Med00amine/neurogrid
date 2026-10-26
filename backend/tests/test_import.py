from __future__ import annotations

import csv
import io
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.models import Company, Contact, EligibilityDecision, ImportBatch, Message, OutreachEvent, Suppression
from app.services import importing, suppression
from app.services.eligibility import evaluate_contact
from app.services.seed import seed_defaults

REAL_FILE = Path(__file__).resolve().parents[2] / "data" / "contacts_clean.xlsx"


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


def run_import(path: Path, kind="contacts", mapping=None, history="unknown", provenance="synthetic test data", options=None):
    content = path.read_bytes()
    stored, sha = importing.save_upload(content, path.name)
    headers, _ = importing.read_table(stored)
    mapping = mapping or importing.suggest_mapping(kind, headers)
    with session_scope() as db:
        seed_defaults(db)
        b = importing.create_batch(
            db, path=stored, sha256=sha, filename=path.name, kind=kind, mapping=mapping, options=options or {},
            provenance_note=provenance, history_attestation=history, created_by="tester",
        )
        bid = b.id
    return bid, importing.process_batch(bid)


@pytest.fixture()
def uploads(tmp_path, monkeypatch, database_url):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.config import reset_settings_cache

    reset_settings_cache()
    return tmp_path


CONTACT_ROWS = [
    ["email", "name", "company"],
    ["alice@acme.example.com", "Alice A", "Acme"],
    ["ALICE@acme.example.com", "Alice Duplicate", "Acme"],  # case duplicate in the same file
    ["  bob@beta.example.org ", "Bob B", "Beta"],
    ["not-an-email", "Broken", "Nope"],
    ["carol@gmail.com", "Carol Freemail", ""],
    ["info@delta.example.net", "Info Box", "Delta"],
    ["jobs@delta.example.net", "Jobs Box", "Delta"],
    ["dave@echo.example.de", "-Ing Dave", "=HYPERLINK(\"http://evil\")"],
    ["zero​width@foxtrot.example.fr", "Zed", "Foxtrot"],
    ["erin@golf.example.com\r\nBcc: attacker@evil.example", "Header Injection", "Golf"],
]


def test_import_synthetic_contacts(uploads):
    p = write_csv(uploads / "contacts.csv", CONTACT_ROWS)
    _, result = run_import(p)
    assert result["status"] == "completed"
    assert result["inserted"] == 7  # alice, bob, carol, info, jobs, dave, zero-width
    with session_scope() as db:
        emails = set(db.execute(select(Contact.email_normalized)).scalars())
        assert "alice@acme.example.com" in emails
        assert "bob@beta.example.org" in emails  # trimmed
        assert "zerowidth@foxtrot.example.fr" in emails  # invisible char removed
        assert not any("evil" in e for e in emails)  # header-injection address rejected
        assert "not-an-email" not in emails
        carol = db.execute(select(Contact).where(Contact.email_normalized == "carol@gmail.com")).scalar_one()
        assert carol.eligibility_status == "excluded" and carol.is_freemail
        info = db.execute(select(Contact).where(Contact.email_normalized == "info@delta.example.net")).scalar_one()
        assert info.eligibility_status == "review_required" and info.is_role_account
        jobs = db.execute(select(Contact).where(Contact.email_normalized == "jobs@delta.example.net")).scalar_one()
        assert jobs.eligibility_status == "excluded"
        dave = db.execute(select(Contact).where(Contact.email_normalized == "dave@echo.example.de")).scalar_one()
        assert dave.country_code == "DE" and dave.name == "-Ing Dave"  # stored as inert text
        assert dave.company_name_raw.startswith("=HYPERLINK")  # not evaluated, not altered
        batch = db.execute(select(ImportBatch)).scalar_one()
        assert batch.duplicate_in_file == 1 and batch.invalid == 2


def test_imported_contacts_have_unknown_history_and_cannot_be_sent(uploads):
    p = write_csv(uploads / "c.csv", CONTACT_ROWS[:3])
    run_import(p)
    with session_scope() as db:
        c = db.execute(select(Contact).where(Contact.email_normalized == "alice@acme.example.com")).scalar_one()
        assert c.history_status == "unknown"
        c.eligibility_status = "eligible"  # even if a human marks it eligible …
        d = evaluate_contact(db, c)
        assert not d.ok and "history_unknown" in d.codes  # … unknown history still blocks sending


def test_attesting_history_requires_a_provenance_description(uploads):
    p = write_csv(uploads / "c.csv", CONTACT_ROWS[:3])
    with pytest.raises(importing.ImportError_):
        run_import(p, history="attested_unsent", provenance="")
    _, result = run_import(p, history="attested_unsent", provenance="Exported from LinkedIn Sales Navigator by me, never emailed")
    with session_scope() as db:
        c = db.execute(select(Contact).where(Contact.email_normalized == "alice@acme.example.com")).scalar_one()
        assert c.history_status == "attested_unsent"
        decisions = db.execute(select(EligibilityDecision).where(EligibilityDecision.contact_id == c.id)).scalars().all()
        assert any(d.field == "history_status" for d in decisions)  # audited


def test_reimport_is_idempotent_and_never_changes_existing_contacts(uploads):
    p = write_csv(uploads / "c.csv", CONTACT_ROWS[:4])
    run_import(p)
    with session_scope() as db:
        c = db.execute(select(Contact).where(Contact.email_normalized == "alice@acme.example.com")).scalar_one()
        c.eligibility_status = "eligible"
        c.history_status = "previously_contacted"
        c.name = "Hand edited"
    _, second = run_import(p, history="attested_unsent", provenance="trying to overwrite state, long enough")
    assert second["inserted"] == 0 and second["duplicate_existing"] >= 2
    with session_scope() as db:
        c = db.execute(select(Contact).where(Contact.email_normalized == "alice@acme.example.com")).scalar_one()
        assert c.eligibility_status == "eligible"
        assert c.history_status == "previously_contacted"  # a new import cannot reset history
        assert c.name == "Hand edited"
        assert db.execute(select(func.count()).select_from(Contact)).scalar_one() == 2


def test_reimport_cannot_reenable_a_suppressed_contact(uploads):
    p = write_csv(uploads / "c.csv", CONTACT_ROWS[:3])
    run_import(p)
    with session_scope() as db:
        suppression.suppress(db, value="alice@acme.example.com", reason="unsubscribe", actor="tester")
    run_import(p, history="attested_unsent", provenance="re-import attempt, long enough text")
    with session_scope() as db:
        s = db.execute(select(Suppression).where(Suppression.value == "alice@acme.example.com")).scalar_one()
        assert s.active
        c = db.execute(select(Contact).where(Contact.email_normalized == "alice@acme.example.com")).scalar_one()
        c.eligibility_status = "eligible"
        c.history_status = "attested_unsent"
        c.consent_basis = "test"
        assert "suppressed" in evaluate_contact(db, c).codes


def test_import_is_resumable_after_interruption(uploads, monkeypatch):
    rows = [["email", "name", "company"]] + [[f"user{i}@co{i}.example.com", f"User {i}", f"Co {i}"] for i in range(10)]
    p = write_csv(uploads / "many.csv", rows)
    monkeypatch.setattr(importing, "CHUNK", 3)
    stored, sha = importing.save_upload(p.read_bytes(), p.name)
    with session_scope() as db:
        b = importing.create_batch(db, path=stored, sha256=sha, filename=p.name, kind="contacts",
                                   mapping={"email": "email", "name": "name", "company": "company"}, created_by="t")
        bid = b.id
    calls = {"n": 0}

    def cancel_after_first_chunk():
        calls["n"] += 1
        return True

    first = importing.process_batch(bid, should_cancel=cancel_after_first_chunk)
    assert first["status"] == "cancelled" and first["rows"] == 3
    second = importing.process_batch(bid)  # resume
    assert second["status"] == "completed" and second["rows"] == 10
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Contact)).scalar_one() == 10
    third = importing.process_batch(bid)  # running a finished batch again is a no-op
    assert third.get("already")


def test_history_import_preserves_status_and_blocks_sending(uploads):
    c_rows = write_csv(uploads / "c.csv", CONTACT_ROWS[:3])
    run_import(c_rows, history="attested_unsent", provenance="exported by me and never used before, honest")
    hist = write_csv(
        uploads / "sent.csv",
        [
            ["email", "sent_at", "status"],
            ["alice@acme.example.com", "2026-03-01", "sent"],
            ["bob@beta.example.org", "2026-03-02", "bounced"],
            ["newperson@unseen.example.com", "2026-03-03", "unsubscribed"],
            ["mystery@unseen2.example.com", "", ""],
        ],
    )
    _, result = run_import(hist, kind="history")
    assert result["status"] == "completed"
    with session_scope() as db:
        alice = db.execute(select(Contact).where(Contact.email_normalized == "alice@acme.example.com")).scalar_one()
        assert alice.history_status == "previously_contacted"  # overrode the earlier attestation
        assert alice.pipeline_stage == "contacted"
        assert db.execute(select(Suppression).where(Suppression.value == "bob@beta.example.org", Suppression.reason == "bounce_hard")).scalar_one().active
        assert db.execute(select(Suppression).where(Suppression.value == "newperson@unseen.example.com", Suppression.reason == "unsubscribe")).scalar_one().active
        mystery = db.execute(select(Contact).where(Contact.email_normalized == "mystery@unseen2.example.com")).scalar_one()
        assert mystery.history_status == "previously_contacted"
        events = db.execute(select(OutreachEvent).where(OutreachEvent.event_type == "historical_send")).scalars().all()
        assert len(events) == 4
        d = evaluate_contact(db, alice)
        assert "previously_contacted" in d.codes


def test_suppression_import_adds_only(uploads):
    p = write_csv(uploads / "sup.csv", [["value", "reason"], ["blocked@x.example.com", "asked"], ["badcorp.example", "domain"], ["", "empty"]])
    _, result = run_import(p, kind="suppressions")
    assert result["status"] == "completed"
    with session_scope() as db:
        kinds = {(s.kind, s.value) for s in db.execute(select(Suppression)).scalars()}
        assert ("email", "blocked@x.example.com") in kinds and ("domain", "badcorp.example") in kinds


def test_hostile_uploads_are_rejected(uploads):
    with pytest.raises(importing.ImportError_):
        importing.save_upload(b"x", "payload.exe")
    with pytest.raises(importing.ImportError_):
        importing.save_upload(b"", "empty.csv")
    with pytest.raises(importing.ImportError_):
        importing.save_upload(b"not a zip", "fake.xlsx")
    stored, _ = importing.save_upload(b"email\na@b.example.com\n", "../../etc/passwd.csv")
    assert stored.parent == (uploads / "data" / "uploads")  # path traversal in the filename is neutralised


def test_mapping_must_reference_real_columns(uploads):
    p = write_csv(uploads / "c.csv", CONTACT_ROWS[:2])
    stored, sha = importing.save_upload(p.read_bytes(), p.name)
    with session_scope() as db:
        with pytest.raises(importing.ImportError_):
            importing.create_batch(db, path=stored, sha256=sha, filename="c.csv", kind="contacts", mapping={"email": "nope"})
        with pytest.raises(importing.ImportError_):
            importing.create_batch(db, path=stored, sha256=sha, filename="c.csv", kind="contacts", mapping={"name": "name"})


def test_xlsx_import(uploads):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Email Address", "Full Name", "Company Name", "Notes"])
    ws.append(["x@xx.example.com", "X Person", "XX", "ignored"])
    p = uploads / "c.xlsx"
    wb.save(p)
    _, result = run_import(p)
    assert result["inserted"] == 1


@pytest.mark.skipif(not REAL_FILE.exists(), reason="real contact file not present (it is git-ignored on purpose)")
def test_real_contact_file_imports_safely(uploads):
    """Imports the real file into a throw-away test database. No email is sent; nothing leaves this database."""
    preview = importing.preview(REAL_FILE, "contacts")
    assert preview["headers"][:2] == ["source_row", "email"]
    assert preview["suggested_mapping"]["email"] == "email"
    assert preview["suggested_mapping"]["name"] == "name"
    assert preview["suggested_mapping"]["company"] == "company"
    assert preview["total_rows"] == 21860
    bid, result = run_import(
        REAL_FILE,
        options={"extra_columns": ["data_quality_issue"]},
        provenance="real file import test in a disposable test database",
    )
    assert result["status"] == "completed"
    assert result["inserted"] == 21860 and result["invalid"] == 0 and result["duplicate_in_file"] == 0
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Contact)).scalar_one() == 21860
        assert db.execute(select(func.count()).select_from(Company)).scalar_one() <= 17315
        assert db.execute(select(func.count()).select_from(Message)).scalar_one() == 0  # nothing was sent
        unknown = db.execute(select(func.count()).select_from(Contact).where(Contact.history_status == "unknown")).scalar_one()
        assert unknown == 21860  # a fresh database never means "unsent"
        suspicious = db.execute(select(Contact).where(Contact.name == "-Ing")).scalar_one_or_none()
        assert suspicious is not None and suspicious.name == "-Ing"
    # importing the identical file again changes nothing
    _, again = run_import(REAL_FILE, options={"extra_columns": ["data_quality_issue"]}, provenance="second run of identical file")
    assert again["inserted"] == 0 and again["duplicate_existing"] == 21860
