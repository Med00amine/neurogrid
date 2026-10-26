"""Configurable segments. A segment is a saved filter plus the positioning used when drafting.

Filter keys (all optional, combined with AND):
  company_types  list[str]  research-assigned company type
  countries      list[str]  contact country codes
  stages         list[str]  pipeline stages
  keywords       list[str]  case-insensitive match on company name
  service_tags   list[str]  an APPROVED research finding is tagged with at least one of these
  min_score      int        lead score threshold
"""

from __future__ import annotations

from sqlalchemy import and_, exists, or_
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm import Session

from app.constants import COMPANY_TYPES, PIPELINE_STAGES, SERVICE_TAGS
from app.models import Company, Contact, LeadScore, ResearchFinding, Segment

ALLOWED_KEYS = {"company_types", "countries", "stages", "keywords", "service_tags", "min_score"}


class SegmentError(ValueError):
    pass


def validate_filter(f: dict) -> dict:
    unknown = set(f) - ALLOWED_KEYS
    if unknown:
        raise SegmentError(f"unknown filter keys: {sorted(unknown)}")
    out: dict = {}
    if f.get("company_types"):
        bad = [t for t in f["company_types"] if t not in COMPANY_TYPES]
        if bad:
            raise SegmentError(f"unknown company types: {bad}")
        out["company_types"] = list(f["company_types"])
    if f.get("countries"):
        out["countries"] = [str(c).upper()[:2] for c in f["countries"]]
    if f.get("stages"):
        bad = [s for s in f["stages"] if s not in PIPELINE_STAGES]
        if bad:
            raise SegmentError(f"unknown stages: {bad}")
        out["stages"] = list(f["stages"])
    if f.get("keywords"):
        out["keywords"] = [str(k).strip()[:60] for k in f["keywords"] if str(k).strip()]
    if f.get("service_tags"):
        bad = [t for t in f["service_tags"] if t not in SERVICE_TAGS]
        if bad:
            raise SegmentError(f"unknown service tags: {bad}")
        out["service_tags"] = list(f["service_tags"])
    if f.get("min_score") not in (None, ""):
        out["min_score"] = max(0, min(100, int(f["min_score"])))
    return out


def clause_from_filter(f: dict):
    parts = []
    if f.get("company_types"):
        parts.append(Company.company_type.in_(f["company_types"]))
    if f.get("countries"):
        parts.append(Contact.country_code.in_(f["countries"]))
    if f.get("stages"):
        parts.append(Contact.pipeline_stage.in_(f["stages"]))
    if f.get("keywords"):
        like = lambda k: "%" + k.replace("%", "").replace("_", "") + "%"  # noqa: E731
        parts.append(or_(*[or_(Company.name.ilike(like(k)), Contact.company_name_raw.ilike(like(k))) for k in f["keywords"]]))
    if f.get("service_tags"):
        parts.append(
            exists().where(
                and_(
                    ResearchFinding.company_id == Contact.company_id,
                    ResearchFinding.status.in_(("approved", "edited")),
                    ResearchFinding.service_tags.op("?|")(array(f["service_tags"])),
                )
            )
        )
    if f.get("min_score") is not None:
        parts.append(LeadScore.total >= f["min_score"])
    return and_(*parts) if parts else None


def segment_clause(db: Session, segment_id: int):
    seg = db.get(Segment, segment_id)
    if seg is None:
        raise SegmentError("segment not found")
    return clause_from_filter(seg.filter or {})
