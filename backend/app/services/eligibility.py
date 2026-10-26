"""The single place that decides whether a contact may be emailed.

`evaluate_contact` is used (a) when a campaign is built, (b) at confirmation, and (c) AGAIN, inside the
sending transaction, immediately before every send. Every reason is a stable machine code plus a
human sentence, so the UI can explain *why* someone is blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.constants import (
    EXCLUDED_LOCAL_PARTS,
    FREEMAIL_DOMAINS,
    MESSAGE_NON_BLOCKING_STATUSES,
    ROLE_LOCAL_PARTS,
)
from app.core.settings_store import get_group
from app.models import Contact, Message
from app.services import jurisdictions, suppression

BLOCKED_STAGES = {"do_not_contact", "won", "lost"}
CONVERSATION_STAGES = {"replied", "interested", "meeting_scheduled", "proposal_sent"}


@dataclass
class Reason:
    code: str
    text: str


@dataclass
class Decision:
    ok: bool
    reasons: list[Reason] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [r.code for r in self.reasons]

    def summary(self) -> str:
        return "; ".join(r.text for r in self.reasons) if self.reasons else "eligible"


def classify_new_contact(email: str, domain: str) -> tuple[str, str | None, bool, bool]:
    """Initial status for a freshly imported address -> (status, reason, is_freemail, is_role_account)."""
    local = email.split("@")[0]
    freemail = domain in FREEMAIL_DOMAINS
    base = local.split("+")[0].replace(".", "").replace("-", "").replace("_", "")
    role = local in ROLE_LOCAL_PARTS or base in ROLE_LOCAL_PARTS
    if freemail:
        return "excluded", "Personal free-mail address (not a business mailbox)", True, role
    if local in EXCLUDED_LOCAL_PARTS or base in EXCLUDED_LOCAL_PARTS:
        return "excluded", "Recruiting / HR mailbox is not a buying contact", False, True
    if role:
        return "review_required", "Shared role mailbox (info@, sales@ …): confirm it is appropriate", False, True
    return "unreviewed", None, False, False


def _jurisdiction_reasons(db: Session, contact: Contact) -> list[Reason]:
    if contact.consent_basis:
        return []
    cc = contact.country_code
    policy = jurisdictions.policy_for(db, cc)
    label = f"{policy.name} ({policy.country_code})"
    if policy.rule == "consent_required":
        return [Reason("consent_required", f"{label} requires prior consent for email outreach and none is recorded")]
    if policy.rule == "opt_out_permitted":
        if policy.attested_at is None:
            return [Reason("jurisdiction_not_attested",
                           f"You have not yet attested that you meet the {label} B2B email requirements")]
        return []
    return [Reason("jurisdiction_unknown", f"{label}: outreach rules not assessed and no consent recorded")]


def evaluate_contact(db: Session, contact: Contact, *, step: int = 1, now: datetime | None = None) -> Decision:
    now = now or datetime.now(timezone.utc)
    reasons: list[Reason] = []

    sup = suppression.find_active(db, contact.email_normalized)
    if sup is not None:
        reasons.append(Reason("suppressed", f"On the suppression list ({sup.kind}: {sup.reason})"))
    if contact.is_freemail:
        reasons.append(Reason("freemail", "Personal free-mail address"))
    if contact.eligibility_status != "eligible":
        reasons.append(Reason("not_eligible", f"Eligibility status is '{contact.eligibility_status}', not 'eligible'"))
    if contact.pipeline_stage in BLOCKED_STAGES:
        reasons.append(Reason("stage_blocked", f"Pipeline stage is '{contact.pipeline_stage}'"))
    if contact.pipeline_stage in CONVERSATION_STAGES:
        reasons.append(Reason("in_conversation", f"Contact has already replied (stage '{contact.pipeline_stage}')"))

    existing = db.execute(
        select(Message)
        .where(Message.contact_id == contact.id, Message.sequence_step == step, Message.is_dry_run.is_(False),
               Message.status.notin_(MESSAGE_NON_BLOCKING_STATUSES))
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        reasons.append(Reason("already_messaged", f"A live message for step {step} already exists (status {existing.status})"))

    if step == 1:
        if contact.history_status == "unknown":
            reasons.append(Reason("history_unknown", "Previous outreach history is unknown: review and attest, or import your sent log"))
        elif contact.history_status == "previously_contacted":
            reasons.append(Reason("previously_contacted", "This person was already contacted before"))
    else:
        reasons.extend(_followup_reasons(db, contact, step, now))

    reasons.extend(_jurisdiction_reasons(db, contact))
    return Decision(ok=not reasons, reasons=reasons)


def _followup_reasons(db: Session, contact: Contact, step: int, now: datetime) -> list[Reason]:
    sending = get_group(db, "sending")
    out: list[Reason] = []
    if step - 1 > sending.max_followups:
        out.append(Reason("max_followups", f"Maximum of {sending.max_followups} follow-ups reached"))
    if contact.pipeline_stage != "contacted":
        out.append(Reason("not_in_contacted_stage", f"Follow-ups only go to contacts still in 'contacted' (is '{contact.pipeline_stage}')"))
    prev = db.execute(
        select(Message).where(Message.contact_id == contact.id, Message.sequence_step == step - 1,
                              Message.is_dry_run.is_(False)).limit(1)
    ).scalar_one_or_none()
    if prev is None or prev.status not in ("submitted", "delivered"):
        out.append(Reason("previous_not_sent", "The previous step was not successfully submitted"))
    elif prev.submitted_at and now - prev.submitted_at < timedelta(days=sending.followup_gap_days):
        out.append(Reason("followup_too_soon", f"Wait at least {sending.followup_gap_days} days after the previous email"))
    return out


# --- sender / provider readiness ------------------------------------------------------------------------


def sender_readiness(db: Session, env: Settings, *, live: bool) -> list[Reason]:
    """Things that must be true before a campaign can be confirmed. For dry runs only identity gaps are reported."""
    sender = get_group(db, "sender")
    sending = get_group(db, "sending")
    problems: list[Reason] = []
    from_email = sender.from_email or env.smtp_from_email
    if not sender.display_name.strip():
        problems.append(Reason("sender_name", "Sender name is empty (Settings → Sender identity)"))
    if not from_email:
        problems.append(Reason("sender_email", "No From address (Settings or SMTP_FROM_EMAIL)"))
    if not sender.postal_address.strip():
        problems.append(Reason("postal_address", "A postal address is required in commercial email (e.g. CAN-SPAM); add it in Settings"))
    if not sender.data_source_statement.strip():
        problems.append(Reason("data_source", "Add a truthful 'where I got your address' sentence (GDPR Art. 14 transparency)"))
    if live:
        if not env.live_sending_enabled:
            problems.append(Reason("env_live_off", "LIVE_SENDING_ENABLED is not set to true in the server environment"))
        if not sending.live_mode_enabled:
            problems.append(Reason("setting_live_off", "Live mode is switched off in Settings → Sending"))
        if sending.provider != "smtp":
            problems.append(Reason("provider_not_smtp", "The sending provider is 'dry_run'; choose SMTP in Settings"))
        if not (env.smtp_host and env.smtp_password and (env.smtp_username or from_email)):
            problems.append(Reason("smtp_not_configured", "SMTP_HOST / SMTP_PASSWORD are not configured"))
    return problems
