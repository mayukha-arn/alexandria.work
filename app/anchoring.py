"""Outbox -> Solana. Events are written to SQLite in the same transaction as the change
they describe; this worker anchors them in order and retries through outages."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional, Union

from .chain import ChainError, ProgramError
from .keyring import Keyring

log = logging.getLogger("alexandria.anchoring")

DOC_EVENT = "DOCUMENT_APPROVED"


class Hasher:
    """Keyed hashes for everything on-chain except document text.

    Plain SHA-256 of a small value set (e.g. the action names) can be reversed by hashing
    every candidate, which would defeat clearance-based redaction. HMAC under a server-side
    key keeps them opaque to anyone without it.

    Keys rotate. Each anchored event records which key made its hashes (``anchor_kid``), so old
    events can still be recomputed and matched against the chain until that key is retired.
    ``key`` is a Keyring, or a plain string for a single fixed key (tests).
    """

    def __init__(self, key: Union[str, Keyring]) -> None:
        self._ring = key if isinstance(key, Keyring) else None
        self._static = None if self._ring else key.encode()

    @property
    def current_kid(self) -> str:
        return self._ring.current.kid if self._ring else "static"

    def mac(self, value: str, kid: Optional[str] = None) -> bytes:
        if self._ring is None:
            secret = self._static
        else:
            key = self._ring.get(kid) if kid else self._ring.current
            if key is None:
                raise KeyError(f"ledger key {kid!r} has been retired")
            secret = key.secret.encode()
        return hmac.new(secret, value.encode(), hashlib.sha256).digest()


def anchor_event(chain: Any, hasher: Hasher, event: Dict[str, Any], kid: Optional[str] = None) -> Optional[str]:
    """Anchor one event with the ledger key ``kid`` (default: current); returns the tx signature
    (None if it was already on-chain)."""
    payload = json.loads(event["payload"])
    actor = hasher.mac(event["actor_id"] or "system", kid)
    action = hasher.mac(event["kind"], kid)
    dept, clearance = event["department"], event["min_clearance"]
    approver = payload.get("approver_pubkey")

    if event["kind"] == DOC_EVENT:
        doc_hash = bytes.fromhex(payload["doc_hash"])
        if chain.get_doc(doc_hash) is not None:
            return None  # a previous attempt landed but we crashed before recording it
        parent = bytes.fromhex(payload["parent_hash"]) if payload.get("parent_hash") else None
        return chain.anchor_document(doc_hash, parent, actor, action, dept, clearance, approver).signature

    payload_hash = hasher.mac(event["payload_hash"], kid)
    return chain.log_event(actor, action, payload_hash, dept, clearance, approver).signature


def anchor_pending(store: Any, chain: Any, hasher: Hasher, limit: int = 50) -> Dict[str, int]:
    """Anchor queued events oldest-first. An outage stops the run (order is kept, nothing
    is lost: the rest stay queued). A permanent rejection is recorded and skipped so one bad
    event cannot block the ledger forever."""
    done = failed = 0
    kid = hasher.current_kid                        # fixed for the whole run, even if a rotation lands mid-run
    for ev in store.pending_events(limit):
        try:
            sig = anchor_event(chain, hasher, ev, kid)
        except ChainError as exc:
            log.warning("chain unavailable, %s event(s) left queued: %s", "remaining", exc)
            break
        except ProgramError as exc:
            store.mark_anchor_error(ev["id"], str(exc))
            failed += 1
            continue
        store.mark_anchored(ev["id"], sig, kid)
        done += 1
    return {"anchored": done, "rejected": failed}
