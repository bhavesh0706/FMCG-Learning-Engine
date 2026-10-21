"""
The organization's login for its portal, managed on the server only (the admin
dashboard has no client options).

Run from the backend folder (with the virtual environment active):

    python -m app.tools.organization create --name "Acme Foods Pvt Ltd" --email insights@acmefoods.com
    python -m app.tools.organization reset-password --email insights@acmefoods.com
    python -m app.tools.organization list
    python -m app.tools.organization rename --email insights@acmefoods.com --name "Acme Foods"
    python -m app.tools.organization disable --email insights@acmefoods.com
    python -m app.tools.organization enable --email insights@acmefoods.com

create and reset-password print a starting password ONCE. Give it to the
organization; at its first sign-in it must choose its own password, after which
only the organization knows it. reset-password also signs it out everywhere.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from datetime import datetime

from app.api.auth import EMAIL_RE, hash_password, sign_out_everywhere
from app.database.database import SessionLocal
from app.database.migrations import run_migrations
from app.database.models import User

ROLE = "client"


def starting_password() -> str:
    """12 characters with letters and digits, without look-alikes (0/O, 1/l/I)."""
    letters = "".join(c for c in string.ascii_letters if c not in "OIl")
    digits = "23456789"
    while True:
        text = "".join(secrets.choice(letters + digits) for _ in range(12))
        if any(c in digits for c in text) and any(c in letters for c in text):
            return text


def _email(value: str) -> str:
    email = (value or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise SystemExit("Enter a valid email address with --email.")
    return email


def _find(db, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user is None or user.role != ROLE:
        raise SystemExit(f"No organization login with the email {email}. See: python -m app.tools.organization list")
    return user


def _show_password(user: User, password: str) -> None:
    print()
    print(f"  Organization : {user.name}")
    print(f"  Sign-in email: {user.email}")
    print(f"  Password     : {password}")
    print()
    print("  This password is shown only now. It works once: at the first sign-in")
    print("  the organization must choose its own password.")
    print()


def create(args) -> None:
    email = _email(args.email)
    name = " ".join((args.name or "").split())
    if len(name) < 2:
        raise SystemExit("Enter the organization's name with --name.")
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            raise SystemExit(f"{email} already has an account. Use reset-password, or another email.")
        password = starting_password()
        user = User(name=name, email=email, mobile=None, password_hash=hash_password(password), role=ROLE,
                    is_active=1, can_view_history=0, must_change_password=1, created_at=datetime.utcnow())
        db.add(user)
        db.commit()
        db.refresh(user)
        print("Organization login created.")
        _show_password(user, password)
    finally:
        db.close()


def reset_password(args) -> None:
    db = SessionLocal()
    try:
        user = _find(db, _email(args.email))
        password = starting_password()
        user.password_hash = hash_password(password)
        user.must_change_password = 1
        sign_out_everywhere(user)
        db.commit()
        print("New starting password set. Every current sign-in of the organization has ended.")
        _show_password(user, password)
    finally:
        db.close()


def list_logins(args) -> None:
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.role == ROLE).order_by(User.name).all()
        if not users:
            print("No organization login yet. Create one with: python -m app.tools.organization create --name ... --email ...")
            return
        for u in users:
            state = "active" if u.is_active else "disabled"
            first = " (must choose a password)" if u.must_change_password else ""
            last = u.last_login_at.strftime("%d %b %Y %H:%M UTC") if u.last_login_at else "never"
            print(f"  {u.name:<32} {u.email:<36} {state}{first}, last sign-in: {last}")
    finally:
        db.close()


def rename(args) -> None:
    name = " ".join((args.name or "").split())
    if len(name) < 2:
        raise SystemExit("Enter the new name with --name.")
    db = SessionLocal()
    try:
        user = _find(db, _email(args.email))
        user.name = name
        db.commit()
        print(f"Renamed to {name}.")
    finally:
        db.close()


def set_active(args, active: bool) -> None:
    db = SessionLocal()
    try:
        user = _find(db, _email(args.email))
        user.is_active = 1 if active else 0
        if not active:
            sign_out_everywhere(user)
        db.commit()
        print(f"{user.name} is now {'enabled' if active else 'disabled (signed out everywhere)'}.")
    finally:
        db.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.tools.organization", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create", help="create the organization's login and print a starting password")
    p.add_argument("--name", required=True)
    p.add_argument("--email", required=True)
    p = sub.add_parser("reset-password", help="print a new starting password (the organization chooses its own at sign-in)")
    p.add_argument("--email", required=True)
    sub.add_parser("list", help="show the organization logins")
    p = sub.add_parser("rename", help="change the organization's name")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    for cmd in ("disable", "enable"):
        p = sub.add_parser(cmd, help=f"{cmd} the organization's login")
        p.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    run_migrations()  # the columns this needs exist even before the API was started once
    {"create": create, "reset-password": reset_password, "list": list_logins, "rename": rename,
     "disable": lambda a: set_active(a, False), "enable": lambda a: set_active(a, True)}[args.command](args)


if __name__ == "__main__":
    main(sys.argv[1:])
