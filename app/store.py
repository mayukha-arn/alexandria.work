"""SQLite persistence for users, sessions, wallet challenges and the audit outbox."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from typing import Any, Dict, Iterator, List, Optional

from cryptography.fernet import Fernet

import roles as R

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               TEXT PRIMARY KEY,
    username         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash    TEXT NOT NULL,
    role             TEXT NOT NULL,
    clearance        INTEGER,
    manager_id       TEXT REFERENCES users(id),
    granted          TEXT NOT NULL DEFAULT '[]',
    revoked          TEXT NOT NULL DEFAULT '[]',
    totp_secret_enc  TEXT,
    totp_enrolled    INTEGER NOT NULL DEFAULT 0,
    totp_last_step   INTEGER,
    wallet_pubkey    TEXT UNIQUE,
    failed_attempts  INTEGER NOT NULL DEFAULT 0,
    locked_until     REAL NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    jti        TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id),
    ip         TEXT,
    user_agent TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS wallet_challenges (
    nonce      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id),
    message    TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);
-- Audit outbox: every governance-relevant event is written here in the same
-- transaction as the change, then anchored to Solana by Layer 4 (retried until it lands).
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    actor_id      TEXT,
    target_id     TEXT,
    department    TEXT,
    payload       TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    min_clearance INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    tx_signature  TEXT,
    anchored_at   REAL,
    anchor_error  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_unanchored ON events(anchored_at) WHERE anchored_at IS NULL;
"""


def _row_user(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["granted"], d["revoked"] = json.loads(d["granted"]), json.loads(d["revoked"])
    return d


class Store:
    def __init__(self, db_path: str, fernet_key: Optional[str] = None) -> None:
        self.db_path = db_path
        # Event payloads hold the plaintext behind on-chain hashes: encrypt them at rest.
        self._fernet = Fernet(fernet_key.encode()) if fernet_key else None
        with closing(sqlite3.connect(db_path)) as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ---- users -------------------------------------------------------------------
    def create_user(self, username: str, password_hash: str, role: str,
                    manager_id: Optional[str] = None, clearance: Optional[int] = None) -> Dict[str, Any]:
        if role not in R.ROLES:
            raise ValueError(f"unknown role {role!r}")
        uid = uuid.uuid4().hex
        with self.tx() as c:
            c.execute("INSERT INTO users (id, username, password_hash, role, clearance, manager_id, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (uid, username, password_hash, role, clearance, manager_id, time.time()))
        return self.get_user(uid)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.tx() as c:
            row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_user(row) if row else None

    def get_user_by_name(self, username: str) -> Optional[Dict[str, Any]]:
        with self.tx() as c:
            row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _row_user(row) if row else None

    def list_users(self) -> List[Dict[str, Any]]:
        with self.tx() as c:
            return [_row_user(r) for r in c.execute("SELECT * FROM users ORDER BY created_at")]

    def update_user(self, user_id: str, **fields: Any) -> None:
        allowed = {"password_hash", "role", "clearance", "manager_id", "granted", "revoked",
                   "totp_secret_enc", "totp_enrolled", "totp_last_step", "wallet_pubkey",
                   "failed_attempts", "locked_until"}
        assert set(fields) <= allowed, set(fields) - allowed
        for k in ("granted", "revoked"):
            if k in fields:
                fields[k] = json.dumps(sorted(fields[k]))
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user_id))

    @staticmethod
    def as_role_user(row: Dict[str, Any]) -> R.User:
        return R.User(id=row["id"], role=row["role"], clearance=row["clearance"],
                      manager_id=row["manager_id"],
                      granted=frozenset(R.Cap(c) for c in row["granted"]),
                      revoked=frozenset(R.Cap(c) for c in row["revoked"]))

    # ---- sessions ----------------------------------------------------------------
    def create_session(self, jti: str, user_id: str, ip: Optional[str], ua: Optional[str],
                       expires_at: float) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO sessions (jti, user_id, ip, user_agent, created_at, expires_at) "
                      "VALUES (?, ?, ?, ?, ?, ?)", (jti, user_id, ip, ua, time.time(), expires_at))

    def session_active(self, jti: str, user_id: str) -> bool:
        with self.tx() as c:
            row = c.execute("SELECT revoked, expires_at FROM sessions WHERE jti = ? AND user_id = ?",
                            (jti, user_id)).fetchone()
        return bool(row) and not row["revoked"] and row["expires_at"] > time.time()

    def list_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        with self.tx() as c:
            rows = c.execute("SELECT jti, ip, user_agent, created_at, expires_at FROM sessions "
                             "WHERE user_id = ? AND revoked = 0 AND expires_at > ? ORDER BY created_at DESC",
                             (user_id, time.time())).fetchall()
        return [dict(r) for r in rows]

    def revoke_session(self, jti: str, user_id: str) -> bool:
        with self.tx() as c:
            return c.execute("UPDATE sessions SET revoked = 1 WHERE jti = ? AND user_id = ? AND revoked = 0",
                             (jti, user_id)).rowcount > 0

    def revoke_all_sessions(self, user_id: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))

    # ---- wallet challenges -------------------------------------------------------
    def create_challenge(self, nonce: str, user_id: str, message: str, expires_at: float) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM wallet_challenges WHERE expires_at < ? OR user_id = ?",
                      (time.time(), user_id))
            c.execute("INSERT INTO wallet_challenges (nonce, user_id, message, expires_at) VALUES (?, ?, ?, ?)",
                      (nonce, user_id, message, expires_at))

    def consume_challenge(self, nonce: str, user_id: str) -> Optional[str]:
        """Single-use: returns the stored message once, then never again."""
        with self.tx() as c:
            row = c.execute("SELECT message, expires_at, used FROM wallet_challenges "
                            "WHERE nonce = ? AND user_id = ?", (nonce, user_id)).fetchone()
            if not row or row["used"] or row["expires_at"] < time.time():
                return None
            c.execute("UPDATE wallet_challenges SET used = 1 WHERE nonce = ?", (nonce,))
            return row["message"]

    # ---- audit outbox ------------------------------------------------------------
    def record_event(self, kind: str, actor_id: Optional[str], target_id: Optional[str],
                     payload: Dict[str, Any], department: Optional[str] = None,
                     min_clearance: int = 0, conn: Optional[sqlite3.Connection] = None) -> int:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(body.encode()).hexdigest()

        def _do(c: sqlite3.Connection) -> int:
            return c.execute(
                "INSERT INTO events (kind, actor_id, target_id, department, payload, payload_hash, "
                "min_clearance, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, actor_id, target_id, department, self._seal(body), digest, min_clearance, time.time()),
            ).lastrowid

        if conn is not None:
            return _do(conn)
        with self.tx() as c:
            return _do(c)

    def _seal(self, body: str) -> str:
        return self._fernet.encrypt(body.encode()).decode() if self._fernet else body

    def _unseal(self, stored: str) -> str:
        return self._fernet.decrypt(stored.encode()).decode() if self._fernet else stored

    def _event(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["payload"] = self._unseal(d["payload"])
        return d

    def list_events(self, only_unanchored: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM events" + (" WHERE anchored_at IS NULL" if only_unanchored else "") + " ORDER BY id"
        with self.tx() as c:
            return [self._event(r) for r in c.execute(sql)]

    def pending_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Not yet on-chain and not permanently rejected, oldest first."""
        with self.tx() as c:
            rows = c.execute("SELECT * FROM events WHERE anchored_at IS NULL AND anchor_error IS NULL "
                             "ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [self._event(r) for r in rows]

    def mark_anchored(self, event_id: int, signature: Optional[str]) -> None:
        with self.tx() as c:
            c.execute("UPDATE events SET tx_signature = ?, anchored_at = ? WHERE id = ?",
                      (signature, time.time(), event_id))

    def mark_anchor_error(self, event_id: int, error: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE events SET anchor_error = ? WHERE id = ?", (error[:500], event_id))
