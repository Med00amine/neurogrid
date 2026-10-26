"""Shared vocabulary. Statuses are stored as text with CHECK constraints built from these tuples."""

ROLES = ("admin", "viewer")

# --- contacts -----------------------------------------------------------------------------
ELIGIBILITY_STATUSES = ("unreviewed", "eligible", "excluded", "review_required")
# History = has this person been emailed before (by this app or an older tool)?
#   unknown          -> imported with no history; sending is BLOCKED until reviewed
#   attested_unsent  -> operator attested (audited) that none of these were emailed before
#   previously_contacted -> a history import / a send by this app says they were emailed
HISTORY_STATUSES = ("unknown", "attested_unsent", "previously_contacted")

PIPELINE_STAGES = (
    "new",
    "researched",
    "qualified",
    "draft_ready",
    "approved",
    "contacted",
    "replied",
    "interested",
    "meeting_scheduled",
    "proposal_sent",
    "won",
    "lost",
    "do_not_contact",
)

IMPORT_KINDS = ("contacts", "history", "suppressions")
IMPORT_STATUSES = ("uploaded", "queued", "running", "completed", "failed", "cancelled")

# --- suppression ---------------------------------------------------------------------------
SUPPRESSION_KINDS = ("email", "domain")
SUPPRESSION_REASONS = (
    "unsubscribe",
    "bounce_hard",
    "complaint",
    "manual",
    "replied_stop",
    "legal",
    "import",
    "do_not_contact",
)

# --- jurisdictions ---------------------------------------------------------------------------
JURISDICTION_RULES = ("consent_required", "opt_out_permitted", "unknown")
UNKNOWN_COUNTRY = "ZZ"

# --- research ---------------------------------------------------------------------------------
RESEARCH_RUN_STATUSES = ("running", "completed", "partial", "failed")
FINDING_CATEGORIES = (
    "description",
    "product_service",
    "industry",
    "business_model",
    "project",
    "hiring_signal",
    "technology",
    "need",
    "contact_role",
    "location",
    "fit_reason",
)
FINDING_KINDS = ("fact", "inference")
FINDING_STATUSES = ("pending", "approved", "rejected", "edited", "auto_rejected")
CONFIDENCE_LEVELS = ("high", "medium", "low")
# Fixed taxonomy used for fit scoring; maps to the operator's real services.
SERVICE_TAGS = (
    "python_fastapi",
    "ai_llm",
    "rag_documents",
    "integrations",
    "aws_devops",
    "ai_testing",
)
COMPANY_TYPES = (
    "ai_consultancy",
    "software_agency",
    "aws_consultancy",
    "product_company",
    "other",
    "unknown",
)

# --- drafting ---------------------------------------------------------------------------------
DRAFT_STATUSES = ("generating", "needs_review", "approved", "rejected", "superseded")
POSITIONINGS = ("agency_capacity", "direct_solution")
TONES = ("direct", "friendly", "formal")
LENGTHS = ("short", "medium")

# --- campaigns / messages ---------------------------------------------------------------------
CAMPAIGN_MODES = ("dry_run", "live")
CAMPAIGN_STATUSES = ("draft", "confirmed", "running", "paused", "cancelled", "completed")

MESSAGE_STATUSES = (
    "queued",
    "submitting",
    "submitted",
    "delivered",
    "bounced",
    "rejected",
    "failed",
    "unknown",
    "replied",
    "suppressed",
    "cancelled",
)
# Statuses that do NOT count as "an email may exist" for the live-message uniqueness index.
# `failed` is only ever set when we can prove nothing was handed to the provider.
MESSAGE_NON_BLOCKING_STATUSES = ("cancelled", "failed")

# --- replies ---------------------------------------------------------------------------------
REPLY_CLASSES = (
    "interested",
    "referral",
    "info_request",
    "not_interested",
    "unsubscribe",
    "auto_response",
    "uncertain",
)
INBOUND_KINDS = ("reply", "bounce", "auto_reply", "other")

# --- jobs ---------------------------------------------------------------------------------------
JOB_STATUSES = ("pending", "running", "completed", "partial", "cancelled", "failed")
JOB_ITEM_STATUSES = ("pending", "running", "done", "failed", "skipped")

TASK_STATUSES = ("open", "done", "cancelled")
DEAL_STATUSES = ("open", "won", "lost")

FREEMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.fr", "yahoo.de", "yahoo.co.uk",
        "hotmail.com", "hotmail.fr", "hotmail.de", "hotmail.co.uk", "outlook.com", "outlook.fr",
        "live.com", "live.fr", "msn.com", "icloud.com", "me.com", "aol.com", "proton.me",
        "protonmail.com", "pm.me", "gmx.de", "gmx.net", "gmx.com", "web.de", "t-online.de",
        "free.fr", "orange.fr", "wanadoo.fr", "sfr.fr", "laposte.net", "libero.it", "mail.com",
        "yandex.com", "yandex.ru", "mail.ru", "qq.com", "163.com", "zoho.com",
    }
)
# Mailboxes that are not a person (or are clearly not a buying contact).
ROLE_LOCAL_PARTS = frozenset(
    {
        "info", "contact", "hello", "admin", "office", "team", "mail", "press", "support",
        "sales", "marketing", "service", "enquiries", "inquiries", "webmaster", "postmaster",
        "noreply", "no-reply", "donotreply", "abuse", "billing", "accounts", "legal", "privacy",
        "gdpr", "dpo",
    }
)
# Recruiting mailboxes are never appropriate for B2B services outreach.
EXCLUDED_LOCAL_PARTS = frozenset({"jobs", "careers", "career", "hr", "recruiting", "recruitment", "talent", "apply"})
