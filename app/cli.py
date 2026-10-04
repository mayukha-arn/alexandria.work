"""Bootstrap users: ``python -m app.cli create-user`` / ``seed-demo``."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import roles as R
from . import security
from .config import Settings
from .store import Store


# (username, role, manager's username): two seniors per department so the four-eyes rule can be exercised
DEMO_ORG = [
    ("admin", "security_admin", None),
    ("senior_eng", "senior_eng", "admin"), ("senior_eng2", "senior_eng", "admin"),
    ("support_lead", "support_lead", "admin"), ("support_lead2", "support_lead", "admin"),
    ("developer", "developer", "senior_eng"), ("support_rep", "support_rep", "support_lead"),
    ("pm", "product_manager", "admin"), ("legal", "legal_counsel", "admin"), ("exec", "executive", "admin"),
]


def _seed_generated(store: Store, settings: Settings, prefix: str, out: Path) -> int:
    """Create the demo organisation with a unique random password per account. The passwords go to a
    private file (owner-only) and are never printed, so they don't end up in a terminal or a log."""
    import secrets as _secrets
    if store.list_users():
        print("refusing to seed: users already exist", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    ids, lines = {}, []
    for name, role, mgr in DEMO_ORG:
        pw = _secrets.token_urlsafe(14)                              # ~19 characters, far above the 12 minimum
        row = store.create_user(prefix + name, security.hash_password(pw), role, manager_id=ids.get(mgr))
        ids[name] = row["id"]
        lines.append(f"{prefix + name:<16} {pw}   ({role})")
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("# Alexandria demo accounts: username, password, role. Keep private; share each password only with its user.\n"
                 "# Every account sets up two-factor authentication on its first sign-in, so sign in to each one\n"
                 "# yourself before handing the password to anyone else.\n" + "\n".join(lines) + "\n")
    print(f"created {len(DEMO_ORG)} accounts; passwords are in {out} (owner-only). Nothing was printed.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("--username", required=True)
    cu.add_argument("--role", required=True, choices=sorted(R.ROLES))
    cu.add_argument("--manager", help="username of this user's manager")
    sd = sub.add_parser("seed-demo", help="one user per role, reporting up a demo org chart")
    sd.add_argument("--prefix", default="")
    sd.add_argument("--generate", action="store_true",
                    help="give every account its own random password and write them to a private file "
                         "(nothing is printed, nothing is asked)")
    sd.add_argument("--out", help="where --generate writes the credentials (default: .secrets/demo-accounts.txt)")
    sub.add_parser("keys", help="show the key ids in each keyring")
    rk = sub.add_parser("rotate-keys", help="rotate a secret key without downtime")
    rk.add_argument("which", choices=["jwt", "fernet", "ledger", "authority"])
    rk.add_argument("--retire", action="store_true", help="also drop the old key(s) once nothing needs them")
    rk.add_argument("--i-understand", action="store_true", help="required to retire a ledger key")
    args = ap.parse_args(argv)

    settings = Settings()

    if args.cmd == "keys":
        for name, ring in (("jwt", settings.jwt_keys), ("fernet", settings.fernet_keys), ("ledger", settings.ledger_keys)):
            ids = ", ".join(f"{k.kid}{' (current)' if i == 0 else ''}" for i, k in enumerate(ring.keys))
            print(f"{name:7} {ids}{'' if ring.rotatable else '   [from environment: cannot be rotated here]'}")
        return 0

    if args.cmd == "rotate-keys":
        from . import rotation
        from .chain import chain_from_env
        try:
            if args.which == "jwt":
                out = rotation.rotate_jwt(settings, args.retire)
            elif args.which == "fernet":
                out = rotation.rotate_fernet(settings, Store(settings.db_path, settings.cipher), args.retire)
            elif args.which == "ledger":
                out = rotation.rotate_ledger(settings, args.retire, args.i_understand)
            else:
                chain = chain_from_env()
                if chain is None:
                    print("the blockchain is disabled (ALEXANDRIA_CHAIN=off)", file=sys.stderr)
                    return 1
                out = rotation.rotate_authority(chain, Path(os.getenv(
                    "ALEXANDRIA_AUTHORITY_KEYPAIR", settings.secrets_dir / "authority.json")))
        except (rotation.RotationError, Exception) as exc:
            print(f"rotation failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(out, indent=2))
        if os.getenv("ALEXANDRIA_KEYVAULT_URL"):          # keep the vault (the source of truth) in step with the rotation
            from . import azure_secrets
            try:
                names = azure_secrets.push(azure_secrets.KeyVault(os.environ["ALEXANDRIA_KEYVAULT_URL"]), settings.secrets_dir)
                print(f"key vault updated: {', '.join(names) or 'nothing changed'}")
            except azure_secrets.VaultError as exc:
                print(f"WARNING: rotated locally but could not update Key Vault: {exc}", file=sys.stderr)
                return 1
        return 0

    store = Store(settings.db_path, settings.cipher)

    if args.cmd == "seed-demo" and args.generate:
        return _seed_generated(store, settings, args.prefix, Path(args.out) if args.out else settings.secrets_dir / "demo-accounts.txt")

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
    store.create_user(f"{p}senior_eng2", ph, "senior_eng", manager_id=admin["id"])
    slead = store.create_user(f"{p}support_lead", ph, "support_lead", manager_id=admin["id"])
    store.create_user(f"{p}support_lead2", ph, "support_lead", manager_id=admin["id"])
    store.create_user(f"{p}developer", ph, "developer", manager_id=lead["id"])
    store.create_user(f"{p}support_rep", ph, "support_rep", manager_id=slead["id"])
    store.create_user(f"{p}pm", ph, "product_manager", manager_id=admin["id"])
    store.create_user(f"{p}legal", ph, "legal_counsel", manager_id=admin["id"])
    store.create_user(f"{p}exec", ph, "executive", manager_id=admin["id"])
    print("seeded 10 demo users (two seniors per department, so documents can get a second approver); "
          "each must enroll 2FA on first login")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
