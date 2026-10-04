"""Tamper check: does a stored document still match what a senior approved on-chain?"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any, Dict

from .chain import sha256


def verify_document(db_path: str, chain: Any, doc_hash: str) -> Dict[str, Any]:
    """Statuses:
        not_found   no such document in the registry
        tampered    stored text no longer hashes to its registered hash (DB was edited)
        unanchored  text is intact but no approval is recorded on-chain
        verified    text is intact and the approval exists on-chain
    """
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT raw_text, status FROM processed_docs WHERE doc_hash = ?",
                           (doc_hash,)).fetchone()
    if not row:
        return {"status": "not_found", "doc_hash": doc_hash}
    if sha256(row[0]).hex() != doc_hash:
        return {"status": "tampered", "doc_hash": doc_hash, "doc_status": row[1]}
    record = chain.get_doc(bytes.fromhex(doc_hash))
    if record is None:
        return {"status": "unanchored", "doc_hash": doc_hash, "doc_status": row[1]}
    return {"status": "verified", "doc_hash": doc_hash, "doc_status": row[1], "chain": record}
