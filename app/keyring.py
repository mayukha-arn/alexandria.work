"""Versioned secret keys that can be rotated without downtime.

A keyring is an ordered list of keys, newest first, stored as a JSON file (owner-only, written
atomically). New data uses the newest key; old keys stay available to read old data until they
are explicitly retired. The file is re-read when it changes, so a rotation done from the CLI
takes effect in a running server.

Environment variables (e.g. ALEXANDRIA_JWT_SECRET) still work as a single fixed key, but a keyring
backed by an environment variable cannot be rotated.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


@dataclass(frozen=True)
class Key:
    kid: str
    secret: str
    created: float


class KeyringError(Exception):
    pass


def _new_kid() -> str:
    return time.strftime("k%Y%m%d%H%M%S", time.gmtime()) + "-" + secrets.token_hex(2)


class Keyring:
    def __init__(self, path: Path, make: Callable[[], str], env: Optional[str] = None,
                 legacy_file: Optional[Path] = None) -> None:
        self.path, self._make, self._env, self._legacy = Path(path), make, env, legacy_file
        self._lock = threading.RLock()
        self._mtime = -1.0
        self._keys: List[Key] = []
        if env and os.getenv(env):
            self._keys = [Key("env", os.environ[env], 0.0)]
        else:
            self._load_or_create()

    # ------------------------------------------------------------------ storage
    def _write(self, keys: List[Key]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump({"keys": [k.__dict__ for k in keys]}, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)       # atomic: readers never see a half-written file
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _load_or_create(self) -> None:
        if not self.path.exists():
            if self._legacy is not None and self._legacy.exists():     # one key from the pre-rotation layout
                keys = [Key("legacy", self._legacy.read_text().strip(), self._legacy.stat().st_mtime)]
                self._write(keys)
                self._legacy.unlink()                                   # moved, not copied
            else:
                self._write([Key(_new_kid(), self._make(), time.time())])
        self._reload()

    def _reload(self) -> None:
        raw = json.loads(self.path.read_text())
        self._keys = [Key(**k) for k in raw["keys"]]
        self._mtime = self.path.stat().st_mtime_ns
        if not self._keys:
            raise KeyringError(f"{self.path} contains no keys")

    def _fresh(self) -> None:
        if self._env and os.getenv(self._env):
            return
        with self._lock:
            if self.path.stat().st_mtime_ns != self._mtime:
                self._reload()

    # ---------------------------------------------------------------------- api
    @property
    def keys(self) -> List[Key]:
        self._fresh()
        return list(self._keys)

    @property
    def current(self) -> Key:
        return self.keys[0]

    def get(self, kid: str) -> Optional[Key]:
        return next((k for k in self.keys if k.kid == kid), None)

    @property
    def rotatable(self) -> bool:
        return not (self._env and os.getenv(self._env))

    def rotate(self) -> Key:
        """Add a fresh key as the new current one; the previous keys stay valid for reading."""
        if not self.rotatable:
            raise KeyringError("this key comes from an environment variable and cannot be rotated here")
        with self._lock:
            self._fresh()
            new = Key(_new_kid(), self._make(), time.time())
            self._write([new, *self._keys])
            self._reload()
            return new

    def retire_old(self) -> List[str]:
        """Drop every key except the current one. Callers must first make sure nothing still needs them."""
        if not self.rotatable:
            raise KeyringError("this key comes from an environment variable and cannot be retired here")
        with self._lock:
            self._fresh()
            removed = [k.kid for k in self._keys[1:]]
            self._write(self._keys[:1])
            self._reload()
            return removed


def make_fernet_key() -> str:
    return Fernet.generate_key().decode()


class Cipher:
    """Encrypts with the newest key, decrypts with any key in the ring."""

    def __init__(self, ring: Keyring) -> None:
        self.ring = ring

    def _multi(self) -> MultiFernet:
        return MultiFernet([Fernet(k.secret.encode()) for k in self.ring.keys])

    def encrypt(self, data: bytes) -> bytes:
        return self._multi().encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        return self._multi().decrypt(token)

    def rotate(self, token: bytes) -> bytes:
        """Re-encrypt ``token`` under the newest key."""
        return self._multi().rotate(token)

    def decryptable_with_current_only(self, token: bytes) -> bool:
        try:
            Fernet(self.ring.current.secret.encode()).decrypt(token)
            return True
        except InvalidToken:
            return False
