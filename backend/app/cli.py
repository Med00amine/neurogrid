"""Operator commands.

    python -m app.cli create-admin --email you@example.com      (prompts for the password)
    python -m app.cli set-password --email you@example.com
    python -m app.cli seed                                       (re-run default policies/segments/templates)
    python -m app.cli doctor                                     (configuration checklist, no secrets printed)
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.config import get_settings
from app.core import security
from app.db import session_scope
from app.models import User
from app.services.seed import seed_defaults


def _password(args) -> str:
    if args.password:
        return args.password
    p1 = getpass.getpass("Password (12+ characters): ")
    if p1 != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match")
    return p1


def create_admin(args) -> None:
    with session_scope() as db:
        if db.execute(select(User).where(User.email == args.email.lower())).scalar_one_or_none():
            sys.exit("A user with that email already exists (use set-password)")
        security.create_user(db, args.email, _password(args), args.role)
    print(f"Created {args.role} user {args.email}")


def set_password(args) -> None:
    with session_scope() as db:
        user = db.execute(select(User).where(User.email == args.email.lower())).scalar_one_or_none()
        if user is None:
            sys.exit("No such user")
        pw = _password(args)
        security.validate_new_password(pw)
        user.password_hash = security.hash_password(pw)
    print("Password updated")


def seed(_args) -> None:
    with session_scope() as db:
        print(seed_defaults(db))


def doctor(_args) -> None:
    s = get_settings()
    ok = lambda b: "OK     " if b else "MISSING"  # noqa: E731
    print(f"environment            : {s.environment}")
    for name, configured in s.secret_status().items():
        print(f"{ok(configured)} {name}")
    print(f"{ok(bool(s.smtp_host))} SMTP_HOST (sending)")
    print(f"{ok(bool(s.imap_host))} IMAP_HOST (reply + bounce tracking)")
    print(f"live sending in env    : {'ENABLED' if s.live_sending_enabled else 'off (safe default)'}")
    print(f"public base URL        : {s.public_base_url}")
    try:
        with session_scope() as db:
            n = db.execute(select(User)).scalars().all()
            print(f"database               : reachable, {len(n)} user(s)")
    except Exception as e:  # noqa: BLE001
        print(f"database               : ERROR {type(e).__name__}")


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--password")
    a.add_argument("--role", default="admin", choices=["admin", "viewer"])
    a.set_defaults(fn=create_admin)
    b = sub.add_parser("set-password")
    b.add_argument("--email", required=True)
    b.add_argument("--password")
    b.set_defaults(fn=set_password)
    sub.add_parser("seed").set_defaults(fn=seed)
    sub.add_parser("doctor").set_defaults(fn=doctor)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
