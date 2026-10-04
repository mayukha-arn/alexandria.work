"""Key rotation operations (run by an operator from the CLI, never exposed over HTTP).

Each rotation is safe to interrupt and safe to repeat. Retiring an old key is a separate,
deliberate step that is refused while anything still depends on that key.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict

from solders.keypair import Keypair

from .config import Settings
from .store import Store


class RotationError(Exception):
    pass


def rotate_jwt(settings: Settings, retire: bool = False) -> Dict[str, Any]:
    """New tokens use the new key at once. Sessions signed by the old key keep working until it
    is retired (they last at most ``access_ttl``), so retire after that window to end them."""
    new = settings.jwt_keys.rotate()
    out: Dict[str, Any] = {"current": new.kid, "keys": [k.kid for k in settings.jwt_keys.keys]}
    if retire:
        out["retired"] = settings.jwt_keys.retire_old()
        out["keys"] = [k.kid for k in settings.jwt_keys.keys]
    return out


def rotate_fernet(settings: Settings, store: Store, retire: bool = False) -> Dict[str, Any]:
    """Rotate the at-rest encryption key and re-encrypt everything under it. The old key is
    retired only if the new key alone can now read every stored value."""
    new = settings.fernet_keys.rotate()
    out: Dict[str, Any] = {"current": new.kid, "reencrypted": store.reencrypt_all()}
    leftover = store.count_not_under_current_key()
    out["not_under_current_key"] = leftover
    if retire:
        if leftover:
            raise RotationError(f"{leftover} value(s) are still encrypted under an older key; "
                                "refusing to retire it (re-run the rotation to finish re-encrypting)")
        out["retired"] = settings.fernet_keys.retire_old()
    out["keys"] = [k.kid for k in settings.fernet_keys.keys]
    return out


def rotate_ledger(settings: Settings, retire: bool = False, confirm: bool = False) -> Dict[str, Any]:
    """New on-chain hashes use the new key. Old events remember their key id, so they stay
    verifiable until that key is retired; retiring it makes their on-chain hashes unrecomputable."""
    if retire and not confirm:
        raise RotationError("retiring a ledger key makes old on-chain hashes impossible to recompute; "
                            "pass --i-understand to do it anyway")
    new = settings.ledger_keys.rotate()
    out: Dict[str, Any] = {"current": new.kid}
    if retire:
        out["retired"] = settings.ledger_keys.retire_old()
    out["keys"] = [k.kid for k in settings.ledger_keys.keys]
    return out


# ---------------------------------------------------------------- Solana authority key
def _load(path: Path) -> Keypair:
    return Keypair.from_bytes(bytes(json.loads(path.read_text())))


def _write_keypair(path: Path, kp: Keypair) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(list(bytes(kp)), fh)
    os.chmod(path, 0o600)


def recover_pending_authority(chain: Any, key_path: Path) -> bool:
    """Finish a rotation that was interrupted after the on-chain handover but before the key file
    was swapped. Returns True if it promoted (or cleaned up) a pending key."""
    pending = key_path.with_name(key_path.name + ".next")
    if not pending.exists():
        return False
    nxt = _load(pending)
    if chain.ledger() and chain.ledger()["authority"] == str(nxt.pubkey()):
        _promote(key_path, pending)
    else:
        pending.unlink()                      # the handover never happened; the old key is still live
    return True


def _promote(key_path: Path, pending: Path) -> None:
    if key_path.exists():
        # Unique even within one second: a clash would silently overwrite (lose) an archived key.
        os.replace(key_path, key_path.with_name(f"{key_path.name}.retired-{int(time.time())}-{secrets.token_hex(3)}"))
    os.replace(pending, key_path)


def rotate_authority(chain: Any, key_path: Path) -> Dict[str, Any]:
    """Generate a new Solana authority key and hand the ledger to it (SOL moves with it).

    The new key is written to ``<key>.next`` BEFORE the on-chain handover, so a crash at any
    point leaves a recoverable state: either the old key is still the authority (the pending file
    is discarded) or the new one is (the pending file is promoted). The retired key is kept as
    ``<key>.retired-<time>``; delete it once you are sure.
    """
    key_path = Path(key_path)
    recover_pending_authority(chain, key_path)
    new = Keypair()
    pending = key_path.with_name(key_path.name + ".next")
    _write_keypair(pending, new)
    try:
        sig = chain.rotate_authority(new)
    except Exception:
        pending.unlink(missing_ok=True)       # handover failed: nothing changed on-chain
        raise
    _promote(key_path, pending)
    return {"new_authority": str(new.pubkey()), "signature": sig,
            "note": f"previous key archived next to {key_path.name}; delete it when you are sure. "
                    "The program's upgrade authority is separate: rotate it with `solana program set-upgrade-authority`."}
