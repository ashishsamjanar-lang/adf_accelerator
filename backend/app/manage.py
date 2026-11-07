"""Backend-only admin CLI: no route triggers either command here from the API.

    docker compose run --rm api python -m app.manage create-platform-admin \\
        --email you@example.com --name "Ashish Kumar" [--username ashish] [--password '...']

    docker compose run --rm api python -m app.manage purge-audit-log \\
        [--retention-days 400]

``create-platform-admin`` (§27 extension — "platform admin added from
backend", never a public sign-up route) is idempotent: an existing user with
that email is simply promoted to PLATFORM_ADMIN (and reactivated) rather than
erroring; a brand-new row is created otherwise. ``--username`` is the login
name (derived from the email's local part when omitted, and left unchanged on
an existing account that already has one). If ``--password`` is omitted,
a random one-time password is generated and printed once (never logged
elsewhere, never stored except as its hash) with ``must_change_password``
forced.

``purge-audit-log`` (§30.1) is the "dedicated retention job (not a user
action)" that deletes `audit_log` rows past the retention window
(``AUDIT_RETENTION_DAYS``, default 400) — an operator schedules it, the same
way `alembic upgrade head` is an operator-run step, not something the API
exposes.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from app import models, repository
from app.config import settings
from app.db.base import new_id, utcnow
from app.db.session import session_scope
from app.errors import ApiError
from app.security import generate_temp_password, hash_password, normalize_username, username_from_email


def create_platform_admin(email: str, name: str, password: str | None, username: str | None = None) -> None:
    generated = password is None
    plaintext = password or generate_temp_password()

    with session_scope() as db:
        user = repository.get_user_by_email(db, email)
        wanted = normalize_username(username) if username else None
        if user is not None and user.username and (wanted is None or wanted == user.username):
            resolved = user.username
        else:
            # Reuses the API's own rule so the CLI can't create a username the login screen rejects.
            from app.api.users import validate_new_username

            candidate = wanted or username_from_email(email)
            try:
                resolved = validate_new_username(db, candidate)
            except ApiError as exc:
                raise SystemExit(f"Cannot use username {candidate!r}: {exc.message}") from exc
        if user is not None:
            user.name = name
            user.username = resolved
            user.role = "PLATFORM_ADMIN"
            user.active = True
            user.password_hash = hash_password(plaintext)
            user.must_change_password = generated
            action = "updated"
        else:
            user = models.User(
                id=new_id("user"),
                name=name,
                email=email,
                username=resolved,
                role="PLATFORM_ADMIN",
                team_id=None,
                active=True,
                password_hash=hash_password(plaintext),
                must_change_password=generated,
            )
            db.add(user)
            action = "created"
        repository.record_audit(db, actor="system", action=f"platform_admin.{action}", target=email)

    print(f"Platform admin {action}: {email} (username: {resolved})")
    if generated:
        print(f"Temporary password (shown once): {plaintext}")
        print("The account must change this password on first login.")


def purge_audit_log(retention_days: int | None) -> None:
    """§30.1 — the "dedicated retention job (not a user action)" that purges
    `audit_log` rows past the org's retention window. No API route triggers
    this; an operator schedules it (cron/Task Scheduler/CI) alongside
    `alembic upgrade head`. Append-only from the API's own perspective stays
    true — this is the one sanctioned exception, run out-of-band.
    """
    days = retention_days if retention_days is not None else settings.audit_retention_days
    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as db:
        removed = repository.purge_audit_older_than(db, cutoff)
    print(f"Purged {removed} audit_log row(s) older than {days} days (before {cutoff.isoformat()}).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="command", required=True)

    admin_parser = sub.add_parser(
        "create-platform-admin", help="Create (or promote) the platform admin account."
    )
    admin_parser.add_argument("--email", required=True)
    admin_parser.add_argument("--name", required=True)
    admin_parser.add_argument("--username", default=None, help="Login name; defaults to the email's local part.")
    admin_parser.add_argument("--password", default=None, help="Omit to auto-generate one, shown once.")

    purge_parser = sub.add_parser(
        "purge-audit-log", help="Delete audit_log rows older than the retention window (§30.1)."
    )
    purge_parser.add_argument(
        "--retention-days", type=int, default=None, help="Overrides AUDIT_RETENTION_DAYS for this run."
    )

    args = parser.parse_args(argv)
    if args.command == "create-platform-admin":
        create_platform_admin(args.email, args.name, args.password, args.username)
    elif args.command == "purge-audit-log":
        purge_audit_log(args.retention_days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
