"""Solana wallet ownership proofs (ed25519).

A wallet is linked to a user only after the user signs a server-issued, single-use
challenge with it. The same primitive later authorizes document approvals: a senior's
approval carries a wallet signature over the exact action, so "who approved what" is
cryptographically attributable, not just a database row.
"""

from __future__ import annotations

import secrets
import time
from typing import Tuple

import base58
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

PUBKEY_LEN, SIGNATURE_LEN = 32, 64


def is_valid_pubkey(pubkey_b58: str) -> bool:
    try:
        return len(base58.b58decode(pubkey_b58)) == PUBKEY_LEN
    except Exception:
        return False


def verify_signature(pubkey_b58: str, message: str, signature_b58: str) -> bool:
    """True only for a valid ed25519 signature of ``message`` by ``pubkey_b58``.
    Works with Phantom's ``signMessage`` output (base58-encode the returned bytes)."""
    try:
        pub = base58.b58decode(pubkey_b58)
        sig = base58.b58decode(signature_b58)
        if len(pub) != PUBKEY_LEN or len(sig) != SIGNATURE_LEN:
            return False
        VerifyKey(pub).verify(message.encode("utf-8"), sig)
        return True
    except (BadSignatureError, ValueError, Exception):
        return False


def link_challenge(user_id: str, ttl: int) -> Tuple[str, str, float]:
    nonce = secrets.token_urlsafe(24)
    expires = time.time() + ttl
    message = (f"Alexandria wallet link\nuser: {user_id}\nnonce: {nonce}\n"
               f"expires: {int(expires)}")
    return nonce, message, expires


def approval_message(action: str, staged_id: int, doc_hash: str, parent_hash: str,
                     approver_id: str, nonce: str) -> str:
    """Canonical text a senior signs to approve / reject a staged document update."""
    return (f"Alexandria document {action}\nstaged: {staged_id}\ndoc: {doc_hash}\n"
            f"parent: {parent_hash}\napprover: {approver_id}\nnonce: {nonce}")
