"""Assemble the exact text that is sent: approved body + signature + required disclosures + opt-out.

The result is stored on the message row as an immutable snapshot, so what was sent is always auditable.
Nothing here is invented: every line comes from the Settings -> Sender identity fields the operator filled in.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.core.security import make_unsubscribe_token
from app.core.settings_store import SenderProfile


@dataclass
class Composed:
    subject: str
    body: str
    headers: dict[str, str]
    from_email: str
    from_header: str


def sender_from_email(sender: SenderProfile, env: Settings) -> str:
    return (sender.from_email or env.smtp_from_email or "").strip()


def signature_lines(sender: SenderProfile) -> list[str]:
    lines = []
    if sender.display_name:
        lines.append(sender.display_name)
    if sender.job_title:
        lines.append(sender.job_title)
    verified = {u.rstrip("/").lower() for u in sender.verified_urls}
    for label, url in (("Website", sender.website_url), ("GitHub", sender.github_url), ("LinkedIn", sender.linkedin_url), ("Portfolio", sender.portfolio_url)):
        if url and url.rstrip("/").lower() in verified:
            lines.append(f"{label}: {url}")
    return lines


def footer_lines(sender: SenderProfile, https_unsubscribe: str | None) -> list[str]:
    lines = []
    if sender.postal_address:
        lines.append(sender.postal_address.strip())
    if sender.data_source_statement:
        lines.append(sender.data_source_statement.strip())
    if sender.privacy_url:
        lines.append(f"How I handle your data: {sender.privacy_url}")
    opt = "If you would rather not hear from me, just reply with “unsubscribe” and I will not contact you again."
    if https_unsubscribe:
        opt += f" Or use this link: {https_unsubscribe}"
    lines.append(opt)
    return lines


def compose(*, sender: SenderProfile, env: Settings, subject: str, core_body: str, message_id: int, contact_id: int,
            recipient_email: str, rfc_message_id: str, public_base_url: str | None = None) -> Composed:
    from_email = sender_from_email(sender, env)
    base = (public_base_url if public_base_url is not None else env.public_base_url).rstrip("/")
    https_ok = base.startswith("https://")
    token = make_unsubscribe_token(message_id, contact_id)
    https_link = f"{base}/u/{token}" if https_ok else None
    sig = signature_lines(sender)
    body = core_body.strip()
    if sig:
        body += "\n" + "\n".join(sig)
    body += "\n\n-- \n" + "\n".join(footer_lines(sender, https_link))
    mailto = f"mailto:{sender.reply_to or from_email}?subject=unsubscribe"
    list_unsub = f"<{mailto}>" + (f", <{https_link}>" if https_link else "")
    headers = {"List-Unsubscribe": list_unsub}
    if https_link:
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if sender.reply_to:
        headers["Reply-To"] = sender.reply_to
    name = sender.display_name.replace('"', "").strip()
    from_header = f'"{name}" <{from_email}>' if name else from_email
    return Composed(subject=subject.strip(), body=body.replace("\r\n", "\n"), headers=headers, from_email=from_email, from_header=from_header)
