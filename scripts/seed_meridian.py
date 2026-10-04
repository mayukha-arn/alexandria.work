"""Load the Meridian workspace (seed/meridian/content.py) into an EMPTY Alexandria database, through the same
services the API uses: every document is uploaded by one person and approved, with a wallet signature, by a
different person, then indexed and queued for the on-chain audit trail.

Run with the API's environment (database, secrets, Ollama for embeddings):
    sudo -u alexapi bash -c 'set -a; . /etc/alexandria/env; set +a; cd /opt/alexandria && .venv/bin/python scripts/seed_meridian.py'

Each account gets its own random password, written to an owner-only file (default <secrets>/meridian-accounts.txt)
and never printed. Everyone still sets up two-factor authentication on first sign-in.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import secrets as pysecrets
import sys
import time

import base58
from nacl.signing import SigningKey

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import roles as R  # noqa: E402
from app import security  # noqa: E402
from app.config import Settings  # noqa: E402
from app.documents import DocumentService  # noqa: E402
from app.messaging import Messaging, RateLimiter  # noqa: E402
from app.store import Store  # noqa: E402
from app.vectorstore import OllamaEmbedder, VectorStore  # noqa: E402
from seed.meridian.content import CHAT, DOCUMENTS, PEOPLE, PINGS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="credentials file (default: <secrets dir>/meridian-accounts.txt)")
    args = ap.parse_args()

    settings = Settings()
    store = Store(settings.db_path, settings.cipher)
    if store.list_users():
        print("refusing to seed: this database already has users (start from an empty one)", file=sys.stderr)
        return 1
    ollama = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    vectors = VectorStore(OllamaEmbedder(ollama, os.getenv("ALEXANDRIA_EMBED_MODEL", "nomic-embed-text")),
                          path=os.getenv("ALEXANDRIA_CHROMA", str(ROOT / "chroma_db")))
    docs = DocumentService(store, vectors, settings.registry_path, settings.junior_new_requires_review,
                           settings.require_approval)
    msg = Messaging(store, None, limiter=RateLimiter(10_000, 1.0))
    now = time.time()

    # people -------------------------------------------------------------------------------------------
    ids, lines = {}, []
    for name, role, mgr, timezone in PEOPLE:
        pw = pysecrets.token_urlsafe(12)
        ids[name] = store.create_user(name, security.hash_password(pw), role, manager_id=ids.get(mgr))["id"]
        store.update_user(ids[name], timezone=timezone)
        lines.append(f"{name:<16} {pw}   ({R.ROLES[role].label if hasattr(R.ROLES[role], 'label') else role})")
    out = pathlib.Path(args.out) if args.out else settings.secrets_dir / "meridian-accounts.txt"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("# Meridian accounts: username, password, role. Private. Each account sets up 2FA on first sign-in.\n"
                 + "\n".join(lines) + "\n")
    user = lambda name: Store.as_role_user(store.get_user(ids[name]))  # noqa: E731

    # signing wallets for the reviewers (a reviewer can link their own wallet later; that replaces this one)
    keys = {}
    for _, _, _, _, _, approver, _ in DOCUMENTS:
        if approver not in keys:
            keys[approver] = SigningKey.generate()
            store.update_user(ids[approver], wallet_pubkey=base58.b58encode(bytes(keys[approver].verify_key)).decode())

    # documents: upload by one person, signed approval by another ---------------------------------------
    for stem, title, level, dept, uploader, approver, _ in DOCUMENTS:
        path = ROOT / "seed/meridian/pdf" / f"{title.replace(':', ' -')}.pdf"
        res = docs.ingest(user(uploader), None, "pdf", str(path), f"file:{path.name}", level, dept)
        if res.get("status") != "pending_approval":
            print(f"  {title}: unexpected status {res.get('status')}", file=sys.stderr)
            continue
        rv = user(approver)
        pub = store.get_user(ids[approver])["wallet_pubkey"]
        ch = docs.challenge(rv, pub, res["staged_id"], "approve")
        sig = base58.b58encode(keys[approver].sign(ch["message"].encode()).signature).decode()
        done = docs.decide(rv, pub, res["staged_id"], True, "Reviewed and approved.", ch["nonce"], sig)
        print(f"  {title}: {done['status']} by {approver} ({done['chunk_count']} passages)")

    # channel history, back-dated ------------------------------------------------------------------------
    for channel, author, minutes, body in CHAT:
        m = msg.post_chat(user(author), channel, body)
        with store.tx() as c:
            c.execute("UPDATE chat_messages SET created_at = ? WHERE id = ?", (now - minutes * 60, m["id"]))

    # pings, back-dated ----------------------------------------------------------------------------------
    for asker, dept, title, body, minutes, answers, status in PINGS:
        p = msg.create_ping(user(asker), dept, title, body)
        t = now - minutes * 60
        for i, (author, kind, text) in enumerate(answers):
            if i == 0:
                msg.claim(user(author), p["id"])
            msg.post_message(user(author), p["id"], kind, text, p["min_role"])
        if status == "resolved":
            msg.resolve(user(asker), p["id"])
        with store.tx() as c:
            rows = c.execute("SELECT id FROM ping_messages WHERE ping_id = ? ORDER BY id", (p["id"],)).fetchall()
            for i, r in enumerate(rows):
                c.execute("UPDATE ping_messages SET created_at = ? WHERE id = ?", (t + i * 11 * 60, r["id"]))
            last = t + max(len(rows) - 1, 0) * 11 * 60
            c.execute("UPDATE pings SET created_at = ?, updated_at = ?, claimed_at = CASE WHEN claimed_at IS NULL THEN NULL ELSE ? END, "
                      "resolved_at = CASE WHEN resolved_at IS NULL THEN NULL ELSE ? END WHERE id = ?",
                      (t, last + 60, t + 5 * 60, last + 60, p["id"]))

    print(f"Meridian loaded: {len(PEOPLE)} people, {len(DOCUMENTS)} documents, {len(CHAT)} messages, {len(PINGS)} pings.")
    print(f"Passwords are in {out} (owner-only). Nothing secret was printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
