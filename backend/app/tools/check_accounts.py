"""
Read-only check of how accounts are stored and how each one would sign in.
Use it when someone can't sign in or sees the wrong dashboard (for example an
admin shown as a survey person). It never changes anything and never prints
password hashes or tokens.

Run from the backend folder (with the virtual environment active):
    python -m app.tools.check_accounts                      every admin and organization account
    python -m app.tools.check_accounts --name "Vipin"       accounts whose name contains "Vipin"
    python -m app.tools.check_accounts --login vipin@brand.in
    python -m app.tools.check_accounts --login 9876543210   the account this sign-in would open

For each account it shows: id, name, role, which dashboard the app opens,
masked mobile/email, active, password set, must choose a new password, last
sign-in; and it warns about anything that stops a normal sign-in.
"""

from __future__ import annotations

import argparse
import sys

from fastapi import HTTPException

from app.api.auth import _parse_identifier
from app.database.database import SessionLocal
from app.database.models import User

# The one role contract shared by the backend, the web app and the mobile app.
DASHBOARD = {
    "admin": "Admin (survey app + History with product reports; web: admin dashboard)",
    "client": "Super Admin portal (mobile: Overview/Products/Catalog/Updates)",
    "executive": "Survey person (survey app)",
}


def _mask(value: str | None) -> str:
    if not value:
        return "—"
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:2]}…@{domain}"
    return f"{value[:2]}……{value[-2:]}" if len(value) > 4 else "…"


def _problems(user: User) -> list[str]:
    out = []
    if user.role not in DASHBOARD:
        out.append(f"role '{user.role}' is not one of admin/client/executive: the app refuses this account")
    if not user.is_active:
        out.append("account is disabled: sign-in is refused (403)")
    if not user.password_hash:
        out.append("no password set: sign-in can't succeed")
    if getattr(user, "must_change_password", 0):
        out.append("must choose a new password at the next sign-in (the app now shows that screen first)")
    return out


def _describe(user: User) -> str:
    lines = [
        f"  #{user.id}  {user.name}",
        f"      role            : {user.role}  ->  {DASHBOARD.get(user.role, 'UNKNOWN (refused by the app)')}",
        f"      mobile / email  : {_mask(user.mobile)} / {_mask(user.email)}",
        f"      active          : {'yes' if user.is_active else 'NO'}",
        f"      password set    : {'yes' if user.password_hash else 'NO'}",
        f"      new password due: {'YES' if getattr(user, 'must_change_password', 0) else 'no'}",
        f"      last sign-in    : {user.last_login_at.isoformat(sep=' ', timespec='minutes') if user.last_login_at else 'never'}",
    ]
    lines += [f"      ! {p}" for p in _problems(user)]
    return "\n".join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.tools.check_accounts", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--name", help="part of the account's name (case-insensitive)")
    group.add_argument("--login", help="what the person types to sign in (mobile number or email)")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.login:
            try:
                kind, value = _parse_identifier(args.login)
            except HTTPException as e:
                sys.exit(f"Not a valid sign-in: {e.detail}")
            column = User.mobile if kind == "mobile" else User.email
            users = db.query(User).filter(column == value).all()
            print(f"Sign-in '{_mask(value)}' ({kind}) matches {len(users)} account(s):")
            if not users:
                print("  none: this sign-in can't work (wrong mobile/email, or stored differently).")
        elif args.name:
            users = db.query(User).filter(User.name.ilike(f"%{args.name}%")).order_by(User.id).all()
            print(f"Accounts whose name contains '{args.name}': {len(users)}")
        else:
            users = db.query(User).filter(User.role.in_(["admin", "client"])).order_by(User.role, User.id).all()
            print(f"Admin and Super Admin accounts: {len(users)}")
        for u in users:
            print(_describe(u))

        if args.name and len(users) > 1:
            roles = sorted({u.role for u in users})
            print(f"\n  ! {len(users)} accounts share this name (roles: {', '.join(roles)}). "
                  "Each sign-in opens the account whose mobile/email was typed; check which one the person uses.")
        unknown = db.query(User.role).filter(User.role.notin_(list(DASHBOARD))).distinct().all()
        if unknown:
            print(f"\n  ! Roles in the database the apps don't know: {', '.join(r for (r,) in unknown)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()