"""Bootstrap users: ``python -m app.cli create-user`` / ``seed-demo``."""

from __future__ import annotations

import argparse
import getpass
import sys

import roles as R
from . import security
from .config import Settings
from .store import Store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("--username", required=True)
    cu.add_argument("--role", required=True, choices=sorted(R.ROLES))
    cu.add_argument("--manager", help="username of this user's manager")
    sd = sub.add_parser("seed-demo", help="one user per role, reporting up a demo org chart")
    sd.add_argument("--prefix", default="")
    args = ap.parse_args(argv)

    settings = Settings()
    store = Store(settings.db_path)
    pw = getpass.getpass(f"Password (min {settings.min_password_length} chars): ")
    if len(pw) < settings.min_password_length:
        print("password too short", file=sys.stderr)
        return 1
    ph = security.hash_password(pw)

    if args.cmd == "create-user":
        mgr = store.get_user_by_name(args.manager) if args.manager else None
        if args.manager and not mgr:
            print(f"no such manager {args.manager!r}", file=sys.stderr)
            return 1
        u = store.create_user(args.username, ph, args.role, manager_id=mgr["id"] if mgr else None)
        print(f"created {u['username']} ({u['role']})")
        return 0

    if store.list_users():
        print("refusing to seed: users already exist", file=sys.stderr)
        return 1
    p = args.prefix
    admin = store.create_user(f"{p}admin", ph, "security_admin")
    lead = store.create_user(f"{p}senior_eng", ph, "senior_eng", manager_id=admin["id"])
    slead = store.create_user(f"{p}support_lead", ph, "support_lead", manager_id=admin["id"])
    store.create_user(f"{p}developer", ph, "developer", manager_id=lead["id"])
    store.create_user(f"{p}support_rep", ph, "support_rep", manager_id=slead["id"])
    store.create_user(f"{p}pm", ph, "product_manager", manager_id=admin["id"])
    store.create_user(f"{p}legal", ph, "legal_counsel", manager_id=admin["id"])
    store.create_user(f"{p}exec", ph, "executive", manager_id=admin["id"])
    print("seeded 8 demo users; each must enroll 2FA on first login")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
