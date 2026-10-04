"""Document pipeline: Layer 1 -> vector store -> audit outbox, with signed senior approvals.

* Uploads run through Layer 1 (dedup, delta staging). Anything that goes live is indexed in
  the vector store and queued for on-chain anchoring.
* Staged items (updates, and new documents from juniors) wait in a review queue. Approving or
  rejecting needs the reviewer's *wallet signature* over the exact item and action, so "who
  approved what" is cryptographically attributable.
* Authorization is layered: capability, department, clearance to read the content, and
  never your own submission.
"""

from __future__ import annotations

import logging
import os
import secrets
import tempfile
from typing import Any, Dict, List, Optional

import ingestion_layer as il
import roles as R
from . import wallet
from .store import Store

log = logging.getLogger("alexandria.documents")

KNOWN_LABELS = set(R.CLASSIFICATIONS) | set(R.ROLES) | {"employee"}
CHALLENGE_TTL = 5 * 60


class DocError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


class DocumentService:
    def __init__(self, store: Store, vectors: Any, registry_path: str,
                 junior_new_requires_review: bool = True) -> None:
        self.store, self.vectors, self.registry = store, vectors, registry_path
        self.junior_review = junior_new_requires_review

    # ------------------------------------------------------------------ helpers
    def _doc_clearance(self, label: str) -> int:
        return R.min_clearance_for(label)

    def _event_kwargs(self, label: str, department: Optional[str]) -> Dict[str, Any]:
        return {"department": department, "min_clearance": self._doc_clearance(label)}

    def _sanitize(self, res: Dict[str, Any], user: R.User) -> Dict[str, Any]:
        """Never return chunk text, and never return a diff against (or the hash of) a document
        the caller is not cleared to read."""
        out = {k: v for k, v in res.items() if k != "chunks"}
        out["chunk_count"] = res.get("chunk_count", len(res.get("chunks", []) or []))
        matched = out.get("matched_doc_hash")
        if matched:
            doc = il.get_document(matched, self.registry)
            if not doc or not R.can_read(user, doc["min_role"]):
                for k in ("diff", "risk_reasons", "matched_doc_hash"):
                    out.pop(k, None)
        return out

    def _activate(self, res: Dict[str, Any], actor: R.User, wallet_pubkey: Optional[str], signature: Optional[str],
                  mode: str, staged_id: Optional[int] = None) -> int:
        """A document just went live: index it, retire its parent's vectors, queue the approval."""
        doc_hash = res["doc_hash"]
        doc = il.get_document(doc_hash, self.registry)
        parent = res.get("deprecated_doc_hash")
        flagged: List[Dict[str, Any]] = []
        try:
            out = self.vectors.add_chunks(res["chunks"])
            flagged = out["flagged"]
            if parent:
                self.vectors.deprecate_document(parent)
            il.mark_indexed(doc_hash, self.registry)
        except Exception:
            # Registry is already committed; reindex_unindexed() will finish the job.
            log.exception("indexing failed for %s; will retry", doc_hash)
        kw = self._event_kwargs(doc["min_role"], doc["department"])
        self.store.record_event("DOCUMENT_APPROVED", actor.id, None,
                                {"doc_hash": doc_hash, "parent_hash": parent, "source": doc["source"],
                                 "approver_pubkey": wallet_pubkey, "signature": signature, "mode": mode,
                                 "staged_id": staged_id}, **kw)
        if flagged:
            self.store.record_event("INJECTION_QUARANTINED", actor.id, None,
                                    {"doc_hash": doc_hash, "chunks": [f["chunk_id"] for f in flagged]},
                                    department=doc["department"], min_clearance=50)
        return len(flagged)

    # ------------------------------------------------------------------ upload
    def ingest(self, user: R.User, wallet_pubkey: Optional[str], source_type: str, path_or_url: str,
               label: str, min_role: str, department: Optional[str]) -> Dict[str, Any]:
        if not R.can(user, R.Cap.UPLOAD_DOC):
            raise DocError(403, "not permitted")
        if min_role not in KNOWN_LABELS:
            raise DocError(422, f"unknown access label {min_role!r}")
        if self._doc_clearance(min_role) > user.effective_clearance:
            raise DocError(403, "you cannot create content above your own clearance")
        department = department or user.department
        if department != user.department and user.role_def.level != "admin":
            raise DocError(403, "you can only upload for your own department")

        res = il.process_incoming_source(
            source_type, path_or_url, R.ingestion_role(user), min_role, db_path=self.registry,
            uploader_id=user.id, department=department,
            junior_new_requires_review=self.junior_review, source_label=label,
            # A senior may auto-approve an update only to documents they could approve and read.
            can_auto_approve=lambda doc: R.can_approve(user, doc["department"]) and R.can_read(user, doc["min_role"]))

        status = res["status"]
        if status in ("new_document_ingested", "version_update_ingested"):
            flagged = self._activate(res, user, wallet_pubkey, None, mode="auto_senior")
            res["quarantined_chunks"] = flagged
        elif status == "pending_approval":
            self.store.record_event("DOCUMENT_STAGED", user.id, None,
                                    {"doc_hash": res["doc_hash"], "staged_id": res["staged_id"],
                                     "new_document": bool(res.get("is_new_document")), "risk": res["risk_level"]},
                                    **self._event_kwargs(min_role, department))
        elif res["status_code"] >= 400:
            raise DocError(res["status_code"], res.get("error", "ingestion failed"))
        return self._sanitize(res, user)

    # ------------------------------------------------------------ review queue
    def _reviewable(self, user: R.User, item: Dict[str, Any]) -> bool:
        if item["uploader_id"] == user.id:
            return False                                    # never review your own submission
        if not R.can_approve(user, item["department"]) or not R.can_read(user, item["min_role"]):
            return False
        if item["parent_hash"]:                             # the diff quotes the current version too
            parent = il.get_document(item["parent_hash"], self.registry)
            if not parent or not R.can_read(user, parent["min_role"]):
                return False
        return True

    def pending(self, user: R.User) -> List[Dict[str, Any]]:
        if not R.can(user, R.Cap.APPROVE_DOC):
            raise DocError(403, "not permitted")
        return [i for i in il.list_pending_updates(self.registry) if self._reviewable(user, i)]

    def _item(self, user: R.User, staged_id: int) -> Dict[str, Any]:
        if not R.can(user, R.Cap.APPROVE_DOC):
            raise DocError(403, "not permitted")
        item = next((i for i in il.list_pending_updates(self.registry) if i["id"] == staged_id), None)
        if item is None or not self._reviewable(user, item):
            raise DocError(404, "no such pending item")      # same answer whether absent or off-limits
        return item

    def _message(self, action: str, item: Dict[str, Any], user: R.User, nonce: str) -> str:
        return wallet.approval_message(action, item["id"], item["doc_hash"], item["parent_hash"] or "none", user.id, nonce)

    def challenge(self, user: R.User, wallet_pubkey: Optional[str], staged_id: int, action: str) -> Dict[str, Any]:
        item = self._item(user, staged_id)
        if action not in ("approve", "reject"):
            raise DocError(422, "action must be 'approve' or 'reject'")
        if not wallet_pubkey:
            raise DocError(409, "link a Solana wallet before reviewing documents")
        nonce = secrets.token_urlsafe(24)
        message = self._message(action, item, user, nonce)
        import time
        self.store.create_challenge(nonce, user.id, message, time.time() + CHALLENGE_TTL)
        return {"nonce": nonce, "message": message}

    def decide(self, user: R.User, wallet_pubkey: Optional[str], staged_id: int, approve: bool,
               note: Optional[str], nonce: str, signature: str) -> Dict[str, Any]:
        item = self._item(user, staged_id)
        if not wallet_pubkey:
            raise DocError(409, "link a Solana wallet before reviewing documents")
        stored = self.store.consume_challenge(nonce, user.id)       # single use, even if the signature is bad
        expected = self._message("approve" if approve else "reject", item, user, nonce) if stored else None
        if stored is None or stored != expected or not wallet.verify_signature(wallet_pubkey, stored, signature):
            raise DocError(401, "wallet signature check failed")

        res = il.review_staged_update(staged_id, "senior", approve, reviewer=user.id,
                                      db_path=self.registry, note=note)
        if res["status_code"] >= 400:
            raise DocError(res["status_code"], res.get("error", "review failed"))
        kw = self._event_kwargs(item["min_role"], item["department"])
        if approve:
            self._activate(res, user, wallet_pubkey, signature, mode="signed", staged_id=staged_id)
            return {"status": "approved", "doc_hash": res["doc_hash"], "chunk_count": res["chunk_count"]}

        self.store.record_event("DOCUMENT_REJECTED", user.id, item["uploader_id"],
                                {"doc_hash": item["doc_hash"], "staged_id": staged_id, "note": note,
                                 "approver_pubkey": wallet_pubkey, "signature": signature}, **kw)
        for f in res.get("flags", []):
            if f["flagged"]:
                kind = "ACCOUNT_FLAGGED" if f["kind"] == "account" else "SOURCE_FLAGGED"
                self.store.record_event(kind, user.id, item["uploader_id"] if f["kind"] == "account" else None,
                                        {"key": f["key"], "rejections": f["rejections"]},
                                        department=item["department"], min_clearance=50)
        return {"status": "rejected", "flags": res.get("flags", [])}

    # --------------------------------------------------------------- listing / purge
    def list_documents(self, user: R.User) -> List[Dict[str, Any]]:
        return [d for d in il.list_documents(self.registry) if R.can_read(user, d["min_role"])]

    def purge(self, user: R.User, doc_hash: str) -> Dict[str, Any]:
        if not R.can(user, R.Cap.PURGE_DOCUMENT):
            raise DocError(403, "not permitted")
        doc = il.get_document(doc_hash, self.registry)
        if not doc:
            raise DocError(404, "document not found")
        removed = self.vectors.purge_document(doc_hash)
        il.purge_document(doc_hash, self.registry)
        self.store.record_event("DOCUMENT_PURGED", user.id, None, {"doc_hash": doc_hash},
                                department=doc["department"], min_clearance=100)
        return {"status": "purged", "vectors_removed": removed}

    def reindex_unindexed(self) -> int:
        """Finish indexing documents that went live while the embedding model was unavailable."""
        n = 0
        for d in il.list_documents(self.registry, status="active"):
            if d["indexed"]:
                continue
            try:
                self.vectors.add_chunks(il.chunks_for(d["doc_hash"], self.registry))
                il.mark_indexed(d["doc_hash"], self.registry)
                n += 1
            except Exception:
                log.warning("reindex of %s still failing", d["doc_hash"])
                break
        return n
