"""Runtime settings. Secrets come from the environment, or are generated once into
a gitignored, owner-only ``.secrets/`` directory. They are never committed."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .keyring import Cipher, Keyring, make_fernet_key

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("ALEXANDRIA_DB", str(ROOT / "alexandria.db")))
    registry_path: str = field(default_factory=lambda: os.getenv("ALEXANDRIA_REGISTRY", str(ROOT / "doc_registry.db")))
    secrets_dir: Path = field(default_factory=lambda: Path(os.getenv("ALEXANDRIA_SECRETS", ROOT / ".secrets")))
    issuer: str = "alexandria"
    access_ttl: int = 15 * 60       # full session
    mfa_ttl: int = 5 * 60           # "password ok, now give me your code"
    enroll_ttl: int = 10 * 60       # "password ok, you must set up 2FA first"
    challenge_ttl: int = 5 * 60     # wallet signature challenge
    lock_after: int = 5             # consecutive failures
    lock_seconds: int = 5 * 60
    expose_docs: bool = field(default_factory=lambda: os.getenv("ALEXANDRIA_DOCS", "1") != "0")   # set 0 when public
    session_max_seconds: int = 8 * 3600   # a session can be refreshed, but never past this
    cors_origins: List[str] = field(default_factory=lambda: [o.strip() for o in os.getenv(
        "ALEXANDRIA_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()])
    # Prompt size controls (CPU-only hosts read prompts slowly: fewer / shorter excerpts mean a faster first word).
    top_k: int = field(default_factory=lambda: int(os.getenv("ALEXANDRIA_TOP_K", "5")))
    context_chars: int = field(default_factory=lambda: int(os.getenv("ALEXANDRIA_CONTEXT_CHARS", "0")))   # 0 = no cap
    min_password_length: int = field(default_factory=lambda: int(os.getenv("ALEXANDRIA_MIN_PASSWORD", "12")))
    # Four-eyes: every document, whoever uploads it, needs one approval from a different person
    # before it goes live. ALEXANDRIA_REQUIRE_APPROVAL=0 restores the PRD's senior auto-approve.
    require_approval: bool = field(default_factory=lambda: os.getenv("ALEXANDRIA_REQUIRE_APPROVAL", "1") != "0")
    # With the above off: a junior's brand-new document still waits for senior approval.
    junior_new_requires_review: bool = field(default_factory=lambda: os.getenv("ALEXANDRIA_JUNIOR_REVIEW", "1") != "0")
    max_upload_bytes: int = 25 * 1024 * 1024
    totp_issuer: str = "Alexandria"

    def __post_init__(self) -> None:
        d = Path(self.secrets_dir)
        # Rotatable keyrings (see app/keyring.py). Environment variables still give a single fixed key.
        self.jwt_keys = Keyring(d / "jwt.keys.json", lambda: secrets.token_urlsafe(64),
                                "ALEXANDRIA_JWT_SECRET", legacy_file=d / "jwt.key")
        # Key for the HMAC on actor/action/payload hashes that go on-chain: plain SHA-256 of a small
        # set of values (e.g. action names) could be reversed by anyone.
        self.ledger_keys = Keyring(d / "ledger.keys.json", lambda: secrets.token_urlsafe(48),
                                   "ALEXANDRIA_LEDGER_KEY", legacy_file=d / "ledger.key")
        self.fernet_keys = Keyring(d / "fernet.keys.json", make_fernet_key,
                                   "ALEXANDRIA_FERNET_KEY", legacy_file=d / "totp.key")
        self.cipher = Cipher(self.fernet_keys)   # 2FA secrets and audit payloads, encrypted at rest
