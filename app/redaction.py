"""Clearance-based field redaction for ledger rows (PRD 4.2).

Always visible: department, the (keyed) actor hash, the transaction signature.
At or above ``min_clearance``: action, target, payload and payload hash are resolved from
the encrypted local store. Below it they are replaced by a redaction marker.
Note this is enforced by the API: on-chain data is public, but it is only keyed hashes.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from .anchoring import Hasher


def redaction_marker(level: int) -> str:
    return f"[REDACTED - Level {level} Required]"


def render_event(event: Dict[str, Any], viewer_clearance: int, hasher: Hasher) -> Dict[str, Any]:
    need = event["min_clearance"]
    row: Dict[str, Any] = {
        "id": event["id"],
        "created_at": event["created_at"],
        "department": event["department"],
        "actor_hash": hasher.mac(event["actor_id"] or "system").hex(),
        "required_clearance": need,
        "anchored": event["anchored_at"] is not None,
        "tx_signature": event["tx_signature"],
        "redacted": viewer_clearance < need,
    }
    if row["redacted"]:
        mark = redaction_marker(need)
        row.update(action=mark, target=mark, payload=mark, payload_hash=mark)
    else:
        row.update(action=event["kind"], target=event["target_id"],
                   payload=json.loads(event["payload"]), payload_hash=event["payload_hash"])
    return row
