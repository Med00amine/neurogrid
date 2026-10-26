"""Public (no login) unsubscribe endpoints.

* GET  /u/{token}  shows a confirmation page (a GET must never change state: mail scanners follow links)
* POST /u/{token}  performs the unsubscribe. Accepts both the HTML form and the RFC 8058 one-click POST
                   (`List-Unsubscribe=One-Click`). No redirect, no cookies required.
The token is HMAC-signed and identifies the message; it contains no personal data.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core import security
from app.core.events import record_event
from app.db import get_db
from app.models import Contact, Message
from app.services import suppression

router = APIRouter(tags=["public"])

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Unsubscribe</title>
<style>body{{font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#1a1a1a;line-height:1.5}}
button{{font:inherit;padding:.6rem 1.1rem;border:0;border-radius:.4rem;background:#1a56db;color:#fff;cursor:pointer}}
@media(prefers-color-scheme:dark){{body{{background:#111;color:#eee}}}}</style></head><body>{body}</body></html>"""


def _resolve(db: Session, token: str) -> tuple[Message, Contact] | None:
    parsed = security.parse_unsubscribe_token(token)
    if not parsed:
        return None
    mid, cid = parsed
    msg = db.get(Message, mid)
    contact = db.get(Contact, cid)
    if msg is None or contact is None or msg.contact_id != contact.id:
        return None
    return msg, contact


@router.get("/u/{token}", response_class=HTMLResponse, include_in_schema=False)
def confirm_page(token: str, db: Session = Depends(get_db)):
    found = _resolve(db, token)
    if not found:
        return HTMLResponse(PAGE.format(body="<h1>Link not valid</h1><p>This unsubscribe link is invalid. You can also simply reply to the email with the word &ldquo;unsubscribe&rdquo;.</p>"), status_code=404)
    _, contact = found
    body = (f"<h1>Unsubscribe</h1><p>Stop all further emails to <strong>{html.escape(contact.email_normalized)}</strong>?</p>"
            f'<form method="post" action="/u/{html.escape(token)}"><button type="submit">Yes, unsubscribe me</button></form>')
    return HTMLResponse(PAGE.format(body=body))


@router.post("/u/{token}", response_class=HTMLResponse, include_in_schema=False)
async def do_unsubscribe(token: str, request: Request, db: Session = Depends(get_db)):
    found = _resolve(db, token)
    if not found:
        return HTMLResponse(PAGE.format(body="<h1>Link not valid</h1>"), status_code=404)
    msg, contact = found
    suppression.suppress(db, value=contact.email_normalized, reason="unsubscribe", source="unsubscribe link",
                         contact_id=contact.id, message_id=msg.id, actor="recipient")
    record_event(db, "unsubscribed", contact_id=contact.id, message_id=msg.id, campaign_id=msg.campaign_id, source="provider",
                 actor="recipient", data={"via": "https_link"})
    return HTMLResponse(PAGE.format(body="<h1>You are unsubscribed</h1><p>You will not receive further emails from me. Sorry for the interruption.</p>"))
