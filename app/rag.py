"""The ask pipeline (PRD section 4, steps 4-7):

    input guardrail -> clearance-filtered retrieval -> tamper check -> persona LLM
    -> grounding check -> output DLP -> audit event
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import roles as R
from . import guardrails
from .chain import ChainError
from .llm import INSUFFICIENT, build_messages, cited_sources, has_bad_citations
from .store import Store
from .verify import verify_document

log = logging.getLogger("alexandria.rag")


@dataclass
class Answer:
    answer: str
    sources: List[Dict[str, Any]] = field(default_factory=list)
    grounded: bool = True
    warnings: List[str] = field(default_factory=list)
    persona: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)


class InputBlocked(Exception):
    def __init__(self, reasons: List[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def ask(*, question: str, user: R.User, store: Store, vectors: Any, llm: Any, registry_path: str,
        chain: Any = None, k: int = 5) -> Answer:
    verdict = guardrails.check_input(question)
    if verdict.blocked:
        store.record_event("GUARDRAIL_BLOCKED", user.id, None,
                           {"question_hash": _sha(question), "reasons": verdict.reasons},
                           department=user.department, min_clearance=50)
        raise InputBlocked(verdict.reasons)

    clearance = user.effective_clearance
    persona = user.role_def.persona
    hits = vectors.query(question, clearance, k)          # access filter is applied inside the database
    warnings: List[str] = []
    sources: List[Dict[str, Any]] = []

    # Tamper check: never show the model text whose registered hash no longer matches, and flag
    # documents with no approval recorded on-chain yet.
    status_by_doc: Dict[str, str] = {}
    if chain is not None and hits:
        for dh in {h.doc_hash for h in hits}:
            try:
                status_by_doc[dh] = verify_document(registry_path, chain, dh)["status"]
            except ChainError:
                status_by_doc[dh] = "unchecked"
        tampered = {d for d, s in status_by_doc.items() if s == "tampered"}
        if tampered:
            store.record_event("TAMPER_DETECTED", user.id, None, {"docs": sorted(tampered)},
                               department=user.department, min_clearance=50)
            warnings.append("Tamper / Unverified Source: some matching documents failed integrity "
                            "verification and were excluded.")
            hits = [h for h in hits if h.doc_hash not in tampered]
        if any(status_by_doc.get(h.doc_hash) in ("unanchored", "unchecked") for h in hits):
            warnings.append("Some sources are not yet verified on-chain.")

    metrics: Dict[str, Any] = {"retrieved": len(hits)}
    if not hits:
        text, grounded = INSUFFICIENT, True               # nothing cleared and relevant: do not ask the model
    else:
        done = llm.complete(build_messages(persona, question, hits))
        metrics.update(ttft_ms=done.ttft_ms, total_ms=done.total_ms)
        text = done.text.strip()
        used = cited_sources(text, len(hits))
        refused = INSUFFICIENT.lower() in text.lower()
        grounded = refused or (bool(used) and not has_bad_citations(text, len(hits)))
        if not grounded:
            warnings.append("The answer could not be tied to the retrieved documents; treat it as unverified.")
        # Only documents the answer actually cites count as its sources; listing everything that
        # was retrieved would make an unverified answer look document-backed.
        sources = [{"n": i, "chunk_id": hits[i - 1].chunk_id, "doc_hash": hits[i - 1].doc_hash,
                    "source": hits[i - 1].source, "department": hits[i - 1].department,
                    "verification": status_by_doc.get(hits[i - 1].doc_hash)} for i in used]

    text = guardrails.mask_pii(text)
    store.record_event("QUERY", user.id, None,
                       {"question_hash": _sha(question), "answer_hash": _sha(text),
                        "docs": sorted({s["doc_hash"] for s in sources}), "grounded": grounded},
                       department=user.department, min_clearance=0)
    return Answer(text, sources, grounded, warnings, persona, metrics)
