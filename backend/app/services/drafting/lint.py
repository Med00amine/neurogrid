"""Checks every draft must pass, and the issues a human should see before approving.

A *blocker* makes approval impossible. A *warning* is shown next to the draft and must be acknowledged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.text import norm_for_match

BANNED_PHRASES = [
    "hope this email finds you well", "hope you're doing well", "hope you are doing well", "i came across your",
    "i was impressed", "impressed by", "love what you", "huge fan", "big fan", "game-changer", "game changer", "cutting-edge",
    "cutting edge", "revolutionize", "revolutionise", "revolutionary", "synergy", "synergies", "unlock", "supercharge", "skyrocket",
    "10x", "world-class", "best-in-class", "state-of-the-art", "touch base", "circle back", "limited time", "act now",
    "guarantee", "guaranteed", "as per my last", "dear sir", "to whom it may concern", "ai-powered", "ai-driven", "seamless",
    "holistic", "empower", "leverage", "disrupt", "next-level", "next level", "paradigm", "turnkey", "end-to-end solutions",
    "quick call", "quick chat", "just checking in", "i know you're busy", "i noticed that you are a leader",
]
EXPERIENCE_RE = re.compile(
    r"\b(\d+\+?\s*years?\b|years of experience|decades? of|we helped|i helped|i delivered|i have delivered|clients? (such as|like|include)|"
    r"customers? (such as|like|include)|worked with (fortune|companies like)|increased [^.]{0,30}\d+%|reduced [^.]{0,30}\d+%|saved [^.]{0,30}\$?\d+|"
    r"track record|proven (record|experience)|trusted by)\b",
    re.IGNORECASE,
)
UNCITED_COMPANY_RE = re.compile(r"\b(your (website|site|team|company|clients?|customers?|product|platform|recent|latest|roadmap|growth|funding|launch)|congratulations|congrats)\b", re.IGNORECASE)
SPECIFIC_CLAIM_RE = re.compile(r"[$€£]\s?\d|\b\d{2,}\b|\b(raised|funding|acquired|acquisition|launched|announced|award|awarded|won|ipo|series [a-d])\b", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{\{|\}\}|\[(name|first ?name|company|your name|insert)[^\]]*\]|<(name|company|first ?name)>|lorem ipsum|\bTODO\b|\bXXX\b|\bFIXME\b", re.IGNORECASE)
URL_RE = re.compile(r"(https?://[^\s<>\")]+|www\.[^\s<>\")]+)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
NUM_RE = re.compile(r"\d[\d.,]*")

WORD_LIMITS = {"short": (110, 200), "medium": (170, 260)}  # (warn above, block above) for the body written by the draft


@dataclass
class Issue:
    code: str
    text: str
    severity: str = "warning"  # warning | blocker

    def asdict(self) -> dict:
        return {"code": self.code, "text": self.text, "severity": self.severity}


def words(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def shingles(text: str, n: int = 4) -> set[tuple[str, ...]]:
    toks = re.findall(r"[a-z0-9']+", norm_for_match(text))
    return {tuple(toks[i : i + n]) for i in range(max(0, len(toks) - n + 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def lint(
    *,
    subject: str,
    body: str,
    paragraphs: list[dict],
    allowed_urls: list[str],
    sender_emails: list[str],
    usable_findings: dict[int, str],  # finding id -> statement + evidence text
    proof_point_refs: set[int],
    length: str = "short",
    other_bodies: list[tuple[str, str, bool]] | None = None,  # (label, body, same_company)
) -> list[Issue]:
    issues: list[Issue] = []
    low = norm_for_match(body + " " + subject)

    # structure -------------------------------------------------------------------------------------------------
    if not subject.strip():
        issues.append(Issue("subject_empty", "The subject is empty", "blocker"))
    if len(subject) > 70:
        issues.append(Issue("subject_long", f"Subject is {len(subject)} characters; keep it under about 70"))
    if re.match(r"^\s*(re|fwd?)\s*:", subject, re.I):
        issues.append(Issue("subject_fake_reply", "A subject starting with Re:/Fwd: pretends this is an existing conversation", "blocker"))
    if "!" in subject:
        issues.append(Issue("subject_exclaim", "Exclamation marks in the subject look like spam"))
    if sum(1 for w in re.findall(r"\b[A-Z]{3,}\b", subject)) >= 1:
        issues.append(Issue("subject_caps", "ALL-CAPS words in the subject look like spam"))
    if PLACEHOLDER_RE.search(subject + "\n" + body):
        issues.append(Issue("placeholder", "The text still contains a placeholder or template marker", "blocker"))
    if not body.strip():
        issues.append(Issue("body_empty", "The body is empty", "blocker"))

    # length ----------------------------------------------------------------------------------------------------
    warn_at, block_at = WORD_LIMITS.get(length, WORD_LIMITS["short"])
    n = words(body)
    if n > block_at:
        issues.append(Issue("too_long", f"{n} words is too long for a first cold email (limit {block_at})", "blocker"))
    elif n > warn_at:
        issues.append(Issue("long", f"{n} words; aim for under {warn_at}"))

    # tone ------------------------------------------------------------------------------------------------------
    for phrase in BANNED_PHRASES:
        if re.search(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", low):
            issues.append(Issue("buzzword", f"Avoid the phrase “{phrase}” (generic, hype or false familiarity)"))
    if body.count("!") > 1:
        issues.append(Issue("exclamation", "More than one exclamation mark"))

    # links & addresses ----------------------------------------------------------------------------------------------
    allowed_hosts = {(urlsplit(u if "//" in u else "https://" + u).hostname or "").removeprefix("www.") for u in allowed_urls}
    for m in URL_RE.findall(body):
        u = m.rstrip(".,;)")
        host = (urlsplit(u if "//" in u else "https://" + u).hostname or "").removeprefix("www.")
        if host not in allowed_hosts:
            issues.append(Issue("unverified_link", f"The link {u} is not one of your verified URLs (Settings → Sender identity)", "blocker"))
    for e in EMAIL_RE.findall(body):
        if e.lower() not in {x.lower() for x in sender_emails}:
            issues.append(Issue("foreign_email", f"The text contains an email address ({e}) that is not yours", "blocker"))

    # evidence ----------------------------------------------------------------------------------------------------------
    cited_any = False
    for i, p in enumerate(paragraphs, start=1):
        ptype, text = p.get("type", "other"), p.get("text", "")
        fids = [f for f in p.get("finding_ids", []) if isinstance(f, int)]
        refs = [r for r in p.get("sender_refs", []) if isinstance(r, int)]
        if ptype == "company_fact":
            valid = [f for f in fids if f in usable_findings]
            if not valid:
                issues.append(Issue("unsupported_claim", f"Paragraph {i} states something about the company without an approved research finding behind it", "blocker"))
            else:
                cited_any = True
                evidence_blob = norm_for_match(" ".join(usable_findings[f] for f in valid))
                for num in NUM_RE.findall(text):
                    digits = re.sub(r"\D", "", num)
                    if len(digits) >= 2 and digits not in re.sub(r"\D", "", evidence_blob) and num not in evidence_blob:
                        issues.append(Issue("number_not_in_evidence", f"Paragraph {i} uses the number {num}, which does not appear in the cited evidence", "blocker"))
            if fids and len(valid) < len(fids):
                issues.append(Issue("stale_citation", f"Paragraph {i} cites a finding that is no longer approved", "blocker"))
        elif ptype == "sender_fact":
            if EXPERIENCE_RE.search(text) and not (set(refs) & proof_point_refs):
                issues.append(Issue("unsubstantiated_experience", f"Paragraph {i} claims experience or results that are not in your proof points", "blocker"))
        else:
            if EXPERIENCE_RE.search(text) and not (set(refs) & proof_point_refs):
                issues.append(Issue("unsubstantiated_experience", f"Paragraph {i} claims experience or results that are not in your proof points", "blocker"))
            if UNCITED_COMPANY_RE.search(text) and ptype != "user_edit":
                issues.append(Issue("uncited_company_reference", f"Paragraph {i} refers to the company ({UNCITED_COMPANY_RE.search(text).group(0)}) without citing evidence"))
        if ptype == "user_edit":
            issues.append(Issue("user_text", f"Paragraph {i} was written or edited by you and has not been checked against the research"))
            if SPECIFIC_CLAIM_RE.search(text):
                issues.append(Issue("uncited_specific_claim", f"Paragraph {i} contains a number, amount or event ({SPECIFIC_CLAIM_RE.search(text).group(0)}) that is not tied to approved research. Make sure it is true and verifiable"))
            if EXPERIENCE_RE.search(text) and not (set(refs) & proof_point_refs):
                issues.append(Issue("unsubstantiated_experience", f"Paragraph {i} claims experience or results; make sure they are true and add them to your proof points", "warning"))
    if not cited_any and paragraphs and not any(i.code in ("unsupported_claim",) for i in issues):
        issues.append(Issue("no_company_evidence", "Nothing in this email is backed by research on the company, so it is not personalised", "blocker"))

    # near-duplicates ------------------------------------------------------------------------------------------------------
    for label, other, same_company in other_bodies or []:
        sim = similarity(body, other)
        if sim >= 0.85 and not same_company:
            issues.append(Issue("near_duplicate", f"{sim:.0%} identical to the draft for {label}. Unrelated recipients should not get near-identical emails", "blocker"))
            break
        if sim >= 0.6:
            issues.append(Issue("similar", f"{sim:.0%} similar to the draft for {label}"))
            break
    return issues
