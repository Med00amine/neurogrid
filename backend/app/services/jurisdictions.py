"""Per-country outreach rules, checked 2026-10-26. NOT legal advice.

Rule meanings
  consent_required    prior consent is needed even for B2B email -> blocked unless the contact has recorded consent
  opt_out_permitted   B2B outreach may be possible with transparency + a working opt-out -> still blocked until YOU attest
  unknown             not assessed -> blocked until you decide (set the rule, or record consent per contact)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.constants import UNKNOWN_COUNTRY
from app.core.events import audit
from app.models import JurisdictionPolicy

CHECKED = date(2026, 10, 26)

COUNTRY_NAMES = {
    "ZZ": "Unknown country", "DE": "Germany", "FR": "France", "GB": "United Kingdom", "IT": "Italy", "ES": "Spain",
    "NL": "Netherlands", "BE": "Belgium", "CH": "Switzerland", "AT": "Austria", "IE": "Ireland", "SE": "Sweden",
    "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia",
    "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "PT": "Portugal", "LU": "Luxembourg",
    "HR": "Croatia", "SI": "Slovenia", "RS": "Serbia", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
    "UA": "Ukraine", "TR": "Turkey", "IL": "Israel", "US": "United States", "CA": "Canada", "AU": "Australia",
    "NZ": "New Zealand", "IN": "India", "JP": "Japan", "SG": "Singapore", "BR": "Brazil", "ZA": "South Africa",
    "AE": "United Arab Emirates", "MA": "Morocco", "TN": "Tunisia", "IS": "Iceland", "MT": "Malta", "CY": "Cyprus",
}

SEED: dict[str, dict] = {
    "DE": dict(
        rule="consent_required",
        notes="Email advertising needs prior express consent, businesses included (§7(2) Nr. 2 UWG); narrow exception for "
        "your own existing customers (§7(3)). Competitors and associations can sue for injunctions.",
        source_urls=["https://www.gesetze-im-internet.de/uwg_2004/__7.html"],
    ),
    "AT": dict(
        rule="consent_required",
        notes="Reported as opt-in for all recipients (secondary source only; the RIS fetch failed). Verify before relying on this.",
        source_urls=["https://itsalesaas.com/blog/is-cold-email-legal-in-europe"],
    ),
    "CH": dict(
        rule="consent_required",
        notes="NOT VERIFIED (Fedlex requires JavaScript). Treated conservatively as consent required (UWG Art. 3(1)(o)).",
        source_urls=[],
    ),
    "IT": dict(
        rule="consent_required",
        notes="Garante decisions say consent is required even for companies; a working unsubscribe link does not make an unconsented send lawful (secondary sources).",
        source_urls=["https://www.garanteprivacy.it/garante/doc.jsp?ID=4207931"],
    ),
    "ES": dict(
        rule="consent_required",
        notes="LSSI art. 21 requires prior consent; whether legal persons are protected was not confirmed. Treated conservatively as consent required.",
        source_urls=["https://www.aepd.es/documento/a-00180-2014.pdf"],
    ),
    "NL": dict(
        rule="consent_required",
        notes="Reported opt-in with narrow exceptions (secondary source). Verify before relying on this.",
        source_urls=["https://itsalesaas.com/blog/is-cold-email-legal-in-europe"],
    ),
    "PL": dict(
        rule="consent_required",
        notes="Reported: since 10 Nov 2024 prior consent is required for companies too (secondary source).",
        source_urls=["https://itsalesaas.com/blog/is-cold-email-legal-in-europe"],
    ),
    "CZ": dict(
        rule="consent_required",
        notes="Reported opt-in for any address, own-customer exception (secondary source).",
        source_urls=["https://itsalesaas.com/blog/is-cold-email-legal-in-europe"],
    ),
    "FR": dict(
        rule="opt_out_permitted",
        notes="CNIL (page dated 2026-06-10): B2B prospecting can rest on legitimate interest if the offer relates to the person's "
        "professional role, they were informed, and can object. Generic mailboxes (info@) are outside these principles.",
        source_urls=["https://www.cnil.fr/fr/la-prospection-commerciale-par-courrier-electronique"],
    ),
    "GB": dict(
        rule="opt_out_permitted",
        notes="PECR's email rule does not apply to corporate subscribers (companies, LLPs), but sole traders and some partnerships "
        "need consent. UK GDPR still applies to named individuals. If unsure whether a recipient is a company, treat as an individual.",
        source_urls=["https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/"],
    ),
    "US": dict(
        rule="opt_out_permitted",
        notes="CAN-SPAM covers B2B email: accurate headers, non-deceptive subject, ad identification, valid postal address, "
        "working opt-out honoured within 10 business days.",
        source_urls=["https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business"],
    ),
}


def seed_jurisdictions(db: Session) -> int:
    """Insert missing policy rows. Never overwrites an existing row (your attestations stay)."""
    created = 0
    existing = {p.country_code for p in db.query(JurisdictionPolicy.country_code).all()}
    if UNKNOWN_COUNTRY not in existing:
        db.add(JurisdictionPolicy(
            country_code=UNKNOWN_COUNTRY, name=COUNTRY_NAMES[UNKNOWN_COUNTRY], rule="unknown",
            notes="Country could not be inferred (generic domains such as .com/.io/.ai). Set the contact's country or record consent.",
            source_urls=[], sources_checked_on=CHECKED))
        created += 1
    for code, data in SEED.items():
        if code not in existing:
            db.add(JurisdictionPolicy(country_code=code, name=COUNTRY_NAMES.get(code, code), sources_checked_on=CHECKED, **data))
            created += 1
    db.flush()
    return created


def ensure_policy_rows(db: Session, country_codes: set[str]) -> None:
    existing = {p.country_code for p in db.query(JurisdictionPolicy.country_code).all()}
    for code in sorted(country_codes - existing):
        db.add(JurisdictionPolicy(
            country_code=code, name=COUNTRY_NAMES.get(code, code), rule="unknown",
            notes="Not assessed. Blocked until you set a rule and attest, or record consent per contact.",
            source_urls=[]))
    db.flush()


def policy_for(db: Session, country_code: str | None) -> JurisdictionPolicy:
    code = country_code or UNKNOWN_COUNTRY
    p = db.get(JurisdictionPolicy, code)
    if p is None:
        p = JurisdictionPolicy(country_code=code, name=COUNTRY_NAMES.get(code, code), rule="unknown", source_urls=[])
    return p


def attest(db: Session, country_code: str, *, actor: str, note: str | None = None) -> JurisdictionPolicy:
    p = db.get(JurisdictionPolicy, country_code)
    if p is None:
        raise ValueError("unknown country policy")
    if p.rule == "consent_required":
        raise ValueError("this country requires consent: record consent per contact instead of attesting a rule")
    if p.rule == "unknown":
        raise ValueError("set the rule to 'opt_out_permitted' first (that is your assessment), then attest")
    p.attested_at = datetime.now(timezone.utc)
    p.attested_by = actor
    audit(db, "jurisdiction.attest", actor=actor, entity_type="jurisdiction", entity_id=country_code,
          details={"rule": p.rule, "note": note})
    db.flush()
    return p


def set_rule(db: Session, country_code: str, rule: str, *, actor: str, notes: str | None = None) -> JurisdictionPolicy:
    p = db.get(JurisdictionPolicy, country_code)
    if p is None:
        raise ValueError("unknown country policy")
    before = p.rule
    p.rule = rule
    p.attested_at = None  # any rule change voids the previous attestation
    p.attested_by = None
    if notes:
        p.notes = notes
    audit(db, "jurisdiction.set_rule", actor=actor, entity_type="jurisdiction", entity_id=country_code,
          details={"from": before, "to": rule})
    db.flush()
    return p
