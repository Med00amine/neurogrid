"""Importing this package registers every table on Base.metadata."""

from app.models.auth import AppSetting, AuditLog, AuthSession, LoginAttempt, User  # noqa: F401
from app.models.base import Base  # noqa: F401
from app.models.contacts import (  # noqa: F401
    Company,
    Contact,
    EligibilityDecision,
    ImportBatch,
    ImportRowIssue,
    JurisdictionPolicy,
    Suppression,
)
from app.models.drafting import Draft, DraftVersion, EmailTemplate  # noqa: F401
from app.models.pipeline_jobs import Deal, Job, JobItem, Note, Task  # noqa: F401
from app.models.replies import InboundMessage, MailboxCursor  # noqa: F401
from app.models.research import (  # noqa: F401
    FindingEvidence,
    LeadScore,
    ResearchFinding,
    ResearchRun,
    ResearchSource,
    Segment,
    UsageRecord,
)
from app.models.sending import Campaign, CampaignRecipient, Message, OutreachEvent, SendAttempt  # noqa: F401
