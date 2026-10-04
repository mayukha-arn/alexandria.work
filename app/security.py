"""Password hashing, JWTs, TOTP and at-rest encryption of TOTP secrets."""

from __future__ import annotations

import hmac
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet

from .config import Settings

_hasher = PasswordHasher()
# Verified against when a username does not exist, so unknown users cost the same time.
_DUMMY_HASH = _hasher.hash("alexandria-dummy-password")

SCOPES = ("access", "mfa", "enroll")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: Optional[str], password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def issue_token(settings: Settings, user_id: str, scope: str, ttl: int,
                jti: Optional[str] = None) -> Tuple[str, str, int]:
    assert scope in SCOPES
    now = int(time.time())
    jti = jti or uuid.uuid4().hex
    claims = {"iss": settings.issuer, "sub": user_id, "scope": scope, "jti": jti,
              "iat": now, "exp": now + ttl}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256"), jti, now + ttl


def decode_token(settings: Settings, token: str, scope: str) -> Dict[str, Any]:
    """Raises jwt.PyJWTError for any invalid, expired or wrong-scope token."""
    claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.issuer,
                        options={"require": ["exp", "iat", "sub", "jti", "scope"]})
    if claims["scope"] != scope:
        raise jwt.InvalidTokenError("wrong token scope")
    return claims


# --- TOTP -----------------------------------------------------------------------

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(settings: Settings, secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=settings.totp_issuer)


def verify_totp(secret: str, code: str, last_step: Optional[int], now: Optional[float] = None,
                window: int = 1) -> Optional[int]:
    """Return the matched 30s time-step, or None. A step at or before ``last_step``
    is rejected, so a code (e.g. one shoulder-surfed) cannot be replayed."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    now = time.time() if now is None else now
    totp = pyotp.TOTP(secret)
    step = int(now // 30)
    for s in range(step - window, step + window + 1):
        if hmac.compare_digest(totp.at(s * 30), code):
            return s if (last_step is None or s > last_step) else None
    return None


def encrypt_secret(settings: Settings, plaintext: str) -> str:
    return Fernet(settings.fernet_key.encode()).encrypt(plaintext.encode()).decode()


def decrypt_secret(settings: Settings, token: str) -> str:
    return Fernet(settings.fernet_key.encode()).decrypt(token.encode()).decode()
