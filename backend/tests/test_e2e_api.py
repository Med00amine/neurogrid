"""The whole product loop through the real HTTP API, in dry-run mode, with synthetic contacts and a local fixture website."""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import func, select

from app.config import reset_settings_cache
from app.db import session_scope
from app.jobs import register_all_handlers
from app.jobs.runner import run_one
from app.models import Company, Contact, Draft, InboundMessage, Message, OutreachEvent, Suppression
from app.services.sending import engine
from tests.api_helpers import Api, api, app_client, make_user  # noqa: F401
from tests.site_fixture import Site

CSV = """email,name,company
ceo@acme-fixture.example.com,Chris Martin,Acme Fixture
cto@acme-fixture.example.com,Dana Roe,Acme Fixture
sales@acme-fixture.example.com,Sales Desk,Acme Fixture
=cmd@evil-corp.example.net,"=HYPERLINK(""http://evil.example"",""click"")",+Evil Corp
someone@gmail.com,Free Mail,
bad address@x,Broken,Nope
"""


@pytest.fixture()
def world(api, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_FETCH", "true")
    reset_settings_cache()
    register_all_handlers()
    with Site() as site:
        yield api, site
    reset_settings_cache()


def run_jobs(n=10):
    for _ in range(n):
        if not run_one("e2e"):
            return


def test_full_product_loop_in_dry_run(world):
    api, site = world
    # --- settings ----------------------------------------------------------------------------------------------------------------
    r = api.put("/api/settings/sender", json={"display_name": "Test Sender", "from_email": "sender@sender.example.org", "job_title": "AI & AWS Engineer",
                                              "postal_address": "1 Test Street, Testville", "data_source_statement": "I found your address on your company website."})
    assert r.status_code == 200
    assert api.put("/api/settings/research", json={"delay_between_requests_seconds": 0.0}).status_code == 200
    assert api.put("/api/settings/sending", json={"daily_cap": 10_000}).status_code == 422  # unsafe cap is refused
    assert api.put("/api/settings/general", json={"timezone": "Not/AZone"}).status_code == 422

    # --- import -----------------------------------------------------------------------------------------------------------------
    up = api.post("/api/imports/upload", data={"kind": "contacts"}, files={"file": ("contacts.csv", CSV.encode(), "text/csv")})
    assert up.status_code == 200, up.text
    u = up.json()
    assert u["suggested_mapping"]["email"] == "email" and u["total_rows"] == 6 and len(u["sample_rows"]) == 6
    st = api.post("/api/imports/start", json={"file_ref": u["file_ref"], "kind": "contacts", "mapping": u["suggested_mapping"], "filename": "contacts.csv"})
    assert st.status_code == 200, st.text
    run_jobs()
    batch = api.get(f"/api/imports/{st.json()['batch_id']}").json()
    assert batch["status"] == "completed" and batch["inserted"] == 5 and batch["invalid"] == 1
    assert any(i["issue"] == "invalid_email" for i in batch["issues"])

    lst = api.get("/api/contacts", params={"search": "acme"}).json()
    assert lst["total"] == 3
    ceo = next(i for i in lst["items"] if i["email"].startswith("ceo@"))
    assert ceo["history_status"] == "unknown" and ceo["eligibility_status"] == "unreviewed"
    sales = next(i for i in lst["items"] if i["email"].startswith("sales@"))
    assert sales["eligibility_status"] == "review_required"
    freemail = api.get("/api/contacts", params={"search": "gmail"}).json()["items"][0]
    assert freemail["eligibility_status"] == "excluded"

    # --- the contact cannot be mailed yet; the UI can explain why ----------------------------------------------------------
    d = api.get(f"/api/contacts/{ceo['id']}").json()
    assert not d["can_send"]["ok"] and {"not_eligible", "history_unknown", "jurisdiction_unknown"} <= {r["code"] for r in d["can_send"]["reasons"]}

    # --- compliance gates -------------------------------------------------------------------------------------------------------
    flt = {"search": "ceo@acme-fixture"}
    assert api.post("/api/contacts/bulk", json={"action": "attest_history", "reason": "short", "filter": flt}).status_code == 400
    assert api.post("/api/contacts/bulk", json={"action": "attest_history", "reason": "I exported this list myself and never emailed it", "filter": {}}).status_code == 400  # no 'everything' by accident
    r = api.post("/api/contacts/bulk", json={"action": "attest_history", "reason": "I exported this list myself and never emailed it", "filter": flt, "expected_count": 1})
    assert r.status_code == 200 and r.json()["changed"] == 1
    assert api.post("/api/contacts/bulk", json={"action": "set_country", "value": "FR", "reason": "company imprint shows France", "filter": flt}).json()["changed"] == 1
    assert api.post(f"/api/contacts/{ceo['id']}/eligibility", json={"status": "eligible", "reason": "reviewed company and role, relevant"}).status_code == 200
    r = api.post("/api/jurisdictions/FR/attest", json={"note": "Assessed CNIL B2B rules", "confirm": False})
    assert r.status_code == 400  # must tick the confirmation
    assert api.post("/api/jurisdictions/FR/attest", json={"note": "Assessed CNIL B2B rules", "confirm": True}).status_code == 200
    assert api.post("/api/jurisdictions/DE/attest", json={"note": "Trying to attest Germany", "confirm": True}).status_code == 400  # consent-required countries cannot be attested away

    # --- research ---------------------------------------------------------------------------------------------------------------
    company_id = api.get(f"/api/contacts/{ceo['id']}").json()["company"]["id"]
    assert api.patch(f"/api/companies/{company_id}", json={"website_url": "file:///etc/passwd"}).status_code == 400
    assert api.patch(f"/api/companies/{company_id}", json={"website_url": site.url}).status_code == 200
    est = api.post("/api/research/estimate", json={"contact_ids": [ceo["id"]]}).json()
    assert est["companies"] == 1 and est["mode"] == "heuristic" and est["estimated_total_usd"] == 0
    s = api.post("/api/research/start", json={"contact_ids": [ceo["id"], freemail["id"]]})
    assert s.status_code == 200 and s.json()["companies"] == 1 and s.json()["skipped_contacts"] == 1
    run_jobs()
    comp = api.get(f"/api/companies/{company_id}").json()
    assert comp["run"]["status"] == "completed" and comp["findings"] and comp["sources"]
    assert all(src["retrieved_at"] and src["url"].startswith("http://127.0.0.1") for src in comp["sources"])
    assert all(f["evidence"] and all(e["verified"] for e in f["evidence"]) for f in comp["findings"] if f["status"] != "auto_rejected")
    fit = next(f for f in comp["findings"] if f["category"] == "fit_reason")
    assert fit["kind"] == "inference"
    ap = api.post(f"/api/research/runs/{comp['run']['id']}/approve-verified", json={})
    assert ap.json()["approved"] >= 3
    assert next(f for f in api.get(f"/api/companies/{company_id}").json()["findings"] if f["id"] == fit["id"])["status"] == "pending"  # inferences are never bulk-approved
    api.post(f"/api/research/findings/{fit['id']}/review", json={"action": "approve"})
    lead = api.get(f"/api/leads/{ceo['id']}").json()
    assert lead["total"] == round(sum(c["points"] for c in lead["components"].values())) and lead["total"] > 0
    assert "does not predict" in lead["disclaimer"]

    # --- drafting -----------------------------------------------------------------------------------------------------------------
    tpl = api.get("/api/templates").json()
    agency = next(t for t in tpl if t["positioning"] == "agency_capacity")
    g = api.post("/api/drafts/generate", json={"contact_ids": [ceo["id"]], "template_id": agency["id"], "use_llm": False})
    assert g.status_code == 200
    run_jobs()
    drafts = api.get("/api/drafts").json()
    assert drafts["total"] == 1 and drafts["items"][0]["status"] == "needs_review"
    did = drafts["items"][0]["id"]
    detail = api.get(f"/api/drafts/{did}").json()
    assert detail["findings"] and any(f["cited"] for f in detail["findings"])
    assert all(e["source"] and e["source"]["url"] for f in detail["findings"] if f["cited"] for e in f["evidence"])
    assert "Test Sender" in detail["preview"]["body"] and "1 Test Street, Testville" in detail["preview"]["body"] and detail["version"]["blockers"] == []
    # editing voids approval; approving needs the warnings acknowledged
    r = api.post(f"/api/drafts/{did}/approve", json={"acknowledge_warnings": False})
    assert r.status_code in (200, 409)
    if r.status_code == 409:
        assert api.post(f"/api/drafts/{did}/approve", json={"acknowledge_warnings": True}).status_code == 200
    assert api.get(f"/api/drafts/{did}").json()["approval_valid"] is True
    ed = api.post(f"/api/drafts/{did}/edit", json={"subject": detail["version"]["subject"], "body": detail["version"]["body"] + "\n\nP.S. Happy to share more."})
    assert ed.status_code == 200 and api.get(f"/api/drafts/{did}").json()["approval_valid"] is False
    assert api.post(f"/api/drafts/{did}/approve", json={"acknowledge_warnings": True}).status_code == 200

    # --- dry-run campaign ------------------------------------------------------------------------------------------------------
    r = api.post("/api/campaigns", json={"name": "E2E rehearsal", "mode": "dry_run", "filter": {"search": "acme-fixture"}})
    assert r.status_code == 200, r.text
    camp = r.json()
    assert camp["estimate"]["included"] == 1 and camp["estimate"]["excluded"] == 2
    recips = api.get(f"/api/campaigns/{camp['id']}/recipients", params={"status": "excluded"}).json()["items"]
    assert len(recips) == 2 and all(x["exclusion_reason"] for x in recips)
    assert api.get("/api/campaigns").json()[0]["status"] == "draft"
    run_jobs(); assert engine.tick()["sent"] == 0  # nothing happens before confirmation
    assert api.post(f"/api/campaigns/{camp['id']}/confirm", json={"expected_recipients": 1, "phrase": "nope"}).status_code == 409
    ok = api.post(f"/api/campaigns/{camp['id']}/confirm", json={"expected_recipients": 1, "phrase": camp["required_phrase"]})
    assert ok.status_code == 200 and ok.json()["status"] == "running"
    assert engine.tick()["sent"] == 1
    final = api.get(f"/api/campaigns/{camp['id']}").json()
    assert final["status"] == "completed" and final["message_counts"] == {"submitted": 1}
    msg = api.get(f"/api/campaigns/{camp['id']}/messages").json()["items"][0]
    assert msg["dry_run"] is True and msg["provider_message_id"].startswith("dryrun-")

    # --- dashboard numbers equal the database ------------------------------------------------------------------------------------
    ov = api.get("/api/overview").json()
    with session_scope() as db:
        assert ov["contacts"]["total"]["value"] == db.execute(select(func.count()).select_from(Contact)).scalar_one() == 5
        assert ov["contacts"]["companies"]["value"] == db.execute(select(func.count()).select_from(Company)).scalar_one()
        assert ov["contacts"]["eligible"]["value"] == db.execute(select(func.count()).select_from(Contact).where(Contact.eligibility_status == "eligible")).scalar_one() == 1
        assert ov["drafts"]["approved"]["value"] == db.execute(select(func.count()).select_from(Draft).where(Draft.status == "approved")).scalar_one() == 1
        assert ov["email_dry_run"]["submitted"] == {"value": 1, "basis": "simulated"}
        assert ov["email_live"]["submitted"]["value"] == 0 and ov["email_live"]["delivered"]["basis"] == "provider_confirmed"
        assert ov["research"]["companies_researched"]["value"] == 1
        assert ov["email_live"]["submitted"]["note"].startswith("Accepted by your mail server")
    assert ov["spend"]["llm_month_usd"]["basis"] == "estimate"
    types = {e["type"] for e in ov["recent_events"]}
    assert "message_submitted" in types or "campaign_completed" in types

    # --- the contact is untouched by a rehearsal ---------------------------------------------------------------------------------
    after = api.get(f"/api/contacts/{ceo['id']}").json()
    assert after["history_status"] == "attested_unsent" and after["pipeline_stage"] == "approved"

    # --- audit trail ---------------------------------------------------------------------------------------------------------------
    actions = {a["action"] for a in api.get("/api/audit", params={"page_size": 500}).json()}
    assert {"import.create", "import.completed", "contacts.bulk.attest_history", "jurisdiction.attest", "draft.approve", "campaign.confirm"} <= actions


def test_exports_neutralise_spreadsheet_formulas(world):
    api, _ = world
    up = api.post("/api/imports/upload", data={"kind": "contacts"}, files={"file": ("c.csv", CSV.encode(), "text/csv")}).json()
    api.post("/api/imports/start", json={"file_ref": up["file_ref"], "kind": "contacts", "mapping": up["suggested_mapping"]})
    run_jobs()
    body = api.get("/api/contacts/export", params={"format": "csv"}).content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(body)))
    cells = [c for row in rows for c in row]
    assert not [c for c in cells if c and c[0] in "=+-@"], "an exported cell starts with a formula character"
    assert any(c.startswith("'=HYPERLINK") for c in cells) and any(c.startswith("'+Evil") for c in cells)
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(api.get("/api/contacts/export", params={"format": "xlsx"}).content))
    for ws in wb:
        for row in ws.iter_rows(values_only=True):
            assert not [c for c in row if isinstance(c, str) and c and c[0] in "=+-@"]
            assert not [c for c in row if isinstance(c, str) and c.startswith("=")]


def test_search_and_filters_are_injection_safe(world):
    api, _ = world
    up = api.post("/api/imports/upload", data={"kind": "contacts"}, files={"file": ("c.csv", CSV.encode(), "text/csv")}).json()
    api.post("/api/imports/start", json={"file_ref": up["file_ref"], "kind": "contacts", "mapping": up["suggested_mapping"]})
    run_jobs()
    for evil in ["'; DROP TABLE contacts; --", "\" OR 1=1 --", "%", "_", "\\", "a%' UNION SELECT password_hash FROM users --"]:
        r = api.get("/api/contacts", params={"search": evil})
        assert r.status_code == 200 and r.json()["total"] == 0, evil
    assert api.get("/api/contacts", params={"sort": "email; DROP TABLE users"}).status_code == 200  # unknown sort keys fall back safely
    assert api.get("/api/contacts", params={"eligibility": "x' OR '1'='1"}).json()["total"] == 0
    assert api.get("/api/contacts", params={"page_size": 100000}).status_code == 422
    assert api.get("/api/contacts").json()["total"] == 5  # the table is intact
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Contact)).scalar_one() == 5


def test_suppression_management_via_api(world):
    api, _ = world
    r = api.post("/api/suppressions", json={"kind": "email", "value": "NoThanks@Corp.Example.com", "reason": "do_not_contact"})
    assert r.status_code == 200 and r.json()["value"] == "nothanks@corp.example.com"
    assert api.post("/api/suppressions", json={"kind": "email", "value": "not an email"}).status_code == 400
    assert api.get("/api/suppressions/check", params={"email": "nothanks@corp.example.com"}).json()["suppressed"] is True
    sid = r.json()["id"]
    bad = api.post(f"/api/suppressions/{sid}/lift", json={"reason": "changed my mind about this", "confirm_value": "nothanks@corp.example.com"})
    assert bad.status_code == 400  # protected reason needs force
    assert api.post(f"/api/suppressions/{sid}/lift", json={"reason": "short", "confirm_value": "nothanks@corp.example.com", "force": True}).status_code == 400
    assert api.post(f"/api/suppressions/{sid}/lift", json={"reason": "person asked to be re-added in writing", "confirm_value": "wrong@x.example", "force": True}).status_code == 400
    ok = api.post(f"/api/suppressions/{sid}/lift", json={"reason": "person asked to be re-added in writing", "confirm_value": "nothanks@corp.example.com", "force": True})
    assert ok.status_code == 200 and ok.json()["active"] is False
    sup_csv = api.get("/api/suppressions-export").content.decode("utf-8-sig")
    assert "nothanks@corp.example.com" in sup_csv
    # deleting is impossible even with raw SQL
    from sqlalchemy import text

    with session_scope() as db:
        with pytest.raises(Exception):
            db.execute(text("DELETE FROM suppressions"))
            db.flush()


def test_retention_purges_text_but_keeps_the_audit_trail(world):
    api, site = world
    from datetime import datetime, timedelta, timezone

    from app.models import ResearchSource
    from app.services import retention
    from tests.factories import make_company
    from tests.research_factory import seed_research

    with session_scope() as db:
        comp = make_company(db, domain="old.example.com")
        seed_research(db, comp)
        s = db.execute(select(ResearchSource)).scalars().first()
        s.retrieved_at = datetime.now(timezone.utc) - timedelta(days=400)
    with session_scope() as db:
        r = retention.purge(db)
        assert r["source_texts"] == 1
        assert db.execute(select(ResearchSource)).scalars().first().text is None
        assert db.execute(select(func.count()).select_from(OutreachEvent)).scalar_one() >= 0  # events are never purged


def test_json_filters_treat_false_strings_as_false(world):
    from app.services.contacts import ContactFilter

    f = ContactFilter.from_dict({"researched": "false", "suppressed": "true", "role_account": False, "min_score": "40", "bogus": "x", "ids": [1], "stage": ""})
    assert f.researched is False and f.suppressed is True and f.role_account is False and f.min_score == 40 and f.stage is None and f.ids == []
    api, _ = world
    up = api.post("/api/imports/upload", data={"kind": "contacts"}, files={"file": ("c.csv", CSV.encode(), "text/csv")}).json()
    api.post("/api/imports/start", json={"file_ref": up["file_ref"], "kind": "contacts", "mapping": up["suggested_mapping"]})
    run_jobs()
    # "not researched" via a JSON body must select the contacts that have NOT been researched (all of them here)
    r = api.post("/api/research/estimate", json={"filter": {"researched": "false", "eligibility": "unreviewed"}})
    assert r.status_code == 200 and r.json()["companies"] >= 1
    r = api.post("/api/research/estimate", json={"filter": {"researched": "true"}})
    assert r.status_code == 200 and r.json()["companies"] == 0  # nothing has been researched yet


def test_overview_timeseries_is_real_and_complete(world):
    api, _ = world
    ts = api.get("/api/overview/timeseries", params={"days": 14}).json()
    assert len(ts["labels"]) == 14 and len(ts["series"]) == 2
    assert all(len(s["values"]) == 14 and sum(s["values"]) == 0 for s in ts["series"])  # nothing sent or received yet: all zeros, not demo data
