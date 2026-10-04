"""Outbox -> Solana. Events are written to SQLite in the same transaction as the change
they describe; this worker anchors them in order and retries through outages."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

from .chain import ChainError, ProgramError

log = logging.getLogger("alexandria.anchoring")

DOC_EVENT = "DOCUMENT_APPROVED"


class Hasher:
    """Keyed hashes for everything on-chain except document text.

    Plain SHA-256 of a small value set (e.g. the action names) can be reversed by hashing
    every candidate, which would defeat clearance-based redaction. HMAC under a server-side
    key keeps them opaque to anyone without it.
    """

    def __init__(self, key: str) -> None:
        self._key = key.encode()

    def mac(self, value: str) -> bytes:
        return hmac.new(self._key, value.encode(), hashlib.sha256).digest()


def anchor_event(chain: Any, hasher: Hasher, event: Dict[str, Any]) -> Optional[str]:
    """Anchor one event; returns the tx signature (None if it was already on-chain)."""
    payload = json.loads(event["payload"])
    actor = hasher.mac(event["actor_id"] or "system")
    action = hasher.mac(event["kind"])
    dept, clearance = event["department"], event["min_clearance"]
    approver = payload.get("approver_pubkey")

    if event["kind"] == DOC_EVENT:
        doc_hash = bytes.fromhex(payload["doc_hash"])
        if chain.get_doc(doc_hash) is not None:
            return None  # a previous attempt landed but we crashed before recording it
        parent = bytes.fromhex(payload["parent_hash"]) if payload.get("parent_hash") else None
        return chain.anchor_document(doc_hash, parent, actor, action, dept, clearance, approver).signature

    payload_hash = hasher.mac(event["payload_hash"])
    return chain.log_event(actor, action, payload_hash, dept, clearance, approver).signature


def anchor_pending(store: Any, chain: Any, hasher: Hasher, limit: int = 50) -> Dict[str, int]:
    """Anchor queued events oldest-first. An outage stops the run (order is kept, nothing
    is lost: the rest stay queued). A permanent rejection is recorded and skipped so one bad
    event cannot block the ledger forever."""
    done = failed = 0
    for ev in store.pending_events(limit):
        try:
            sig = anchor_event(chain, hasher, ev)
        except ChainError as exc:
            log.warning("chain unavailable, %s event(s) left queued: %s", "remaining", exc)
            break
        except ProgramError as exc:
            store.mark_anchor_error(ev["id"], str(exc))
            failed += 1
            continue
        store.mark_anchored(ev["id"], sig)
        done += 1
    return {"anchored": done, "rejected": failed}
