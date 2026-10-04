"""Runtime settings. Secrets come from the environment, or are generated once into
a gitignored, owner-only ``.secrets/`` directory. They are never committed."""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_secret(directory: Path, filename: str, env: str, make) -> str:
    if os.getenv(env):
        return os.environ[env]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / filename
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(make())
    return path.read_text().strip()


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
    min_password_length: int = 12
    totp_issuer: str = "Alexandria"

    def __post_init__(self) -> None:
        self.jwt_secret = _load_secret(self.secrets_dir, "jwt.key", "ALEXANDRIA_JWT_SECRET",
                                       lambda: secrets.token_urlsafe(64))
        # Key for the HMAC used on actor/action/payload hashes that go on-chain: plain SHA-256
        # of a small set of values (e.g. action names) could be reversed by anyone.
        self.ledger_key = _load_secret(self.secrets_dir, "ledger.key", "ALEXANDRIA_LEDGER_KEY",
                                       lambda: secrets.token_urlsafe(48))
        self.fernet_key = _load_secret(self.secrets_dir, "totp.key", "ALEXANDRIA_FERNET_KEY",
                                       lambda: base64.urlsafe_b64encode(os.urandom(32)).decode())
