"""The ask pipeline (PRD section 4, steps 4-7):

    input guardrail -> clearance-filtered retrieval -> tamper check -> persona LLM
    -> grounding check -> output DLP -> audit event
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

import requests

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


@dataclass
class Prepared:
    """Everything decided before the model is called: who asked, what they may see, what to send."""
    question: str
    user: R.User
    persona: str
    hits: List[Any]
    warnings: List[str]
    status_by_doc: Dict[str, str]
    search_ms: float = 0.0
    telemetry: Any = None


def trim_context(hits: List[Any], max_chars: int) -> List[Any]:
    """Keep the best-ranked excerpts that fit a character budget (0 = no cap). Always keeps at least one,
    shortening it if it alone is over budget. A smaller prompt means a faster first word on a slow CPU."""
    if not max_chars or not hits:
        return hits
    kept, used = [], 0
    for h in hits:
        if used + len(h.text) <= max_chars:
            kept.append(h)
            used += len(h.text)
        elif not kept:
            kept.append(type(h)(**{**h.__dict__, "text": h.text[:max_chars]}))
            break
    return kept


def prepare(*, question: str, user: R.User, store: Store, vectors: Any, registry_path: str,
            chain: Any = None, k: int = 5, context_chars: int = 0, telemetry: Any = None) -> Prepared:
    """Input guardrail -> clearance-filtered retrieval -> tamper check. Raises InputBlocked."""
    verdict = guardrails.check_input(question)
    if verdict.blocked:
        store.record_event("GUARDRAIL_BLOCKED", user.id, None,
                           {"question_hash": _sha(question), "reasons": verdict.reasons},
                           department=user.department, min_clearance=50)
        raise InputBlocked(verdict.reasons)

    t0 = time.perf_counter()
    hits = vectors.query(question, user.effective_clearance, k)   # access filter applied inside the databases
    search_ms = (time.perf_counter() - t0) * 1000
    warnings: List[str] = []
    status_by_doc: Dict[str, str] = {}
    # Tamper check: never show the model text whose registered hash no longer matches, and flag
    # documents with no approval recorded on-chain yet.
    if chain is not None and hits:
        for dh in {h.doc_hash for h in hits}:
            try:
                status_by_doc[dh] = verify_document(registry_path, chain, dh)["status"]
            except ChainError:
                status_by_doc[dh] = "unchecked"
        tampered = {d for d, st in status_by_doc.items() if st == "tampered"}
        if tampered:
            store.record_event("TAMPER_DETECTED", user.id, None, {"docs": sorted(tampered)},
                               department=user.department, min_clearance=50)
            warnings.append("Tamper / Unverified Source: some matching documents failed integrity "
                            "verification and were excluded.")
            hits = [h for h in hits if h.doc_hash not in tampered]
        if any(status_by_doc.get(h.doc_hash) in ("unanchored", "unchecked") for h in hits):
            warnings.append("Some sources are not yet verified on-chain.")
    hits = trim_context(hits, context_chars)
    return Prepared(question, user, user.role_def.persona, hits, warnings, status_by_doc, search_ms, telemetry)


def messages_for(prep: Prepared) -> List[Dict[str, str]]:
    return build_messages(prep.persona, prep.question, prep.hits)


def finish(prep: Prepared, raw_text: str, store: Store, metrics: Optional[Dict[str, Any]] = None) -> Answer:
    """Grounding check -> output DLP -> audit event."""
    hits, warnings = prep.hits, list(prep.warnings)
    metrics = {"retrieved": len(hits), **(metrics or {})}
    sources: List[Dict[str, Any]] = []
    if not hits:
        text, grounded = INSUFFICIENT, True               # nothing cleared and relevant: no model call needed
    else:
        text = raw_text.strip()
        used = cited_sources(text, len(hits))
        refused = INSUFFICIENT.lower() in text.lower()
        grounded = refused or (bool(used) and not has_bad_citations(text, len(hits)))
        if not grounded:
            warnings.append("The answer could not be tied to the retrieved documents; treat it as unverified.")
        # Only documents the answer actually cites count as its sources; listing everything that
        # was retrieved would make an unverified answer look document-backed.
        sources = [{"n": i, "chunk_id": hits[i - 1].chunk_id, "doc_hash": hits[i - 1].doc_hash,
                    "source": hits[i - 1].source, "department": hits[i - 1].department,
                    "verification": prep.status_by_doc.get(hits[i - 1].doc_hash)} for i in used]

    text = guardrails.mask_pii(text)
    if prep.telemetry is not None:                      # numbers and the department name only: never the text
        m, dept = metrics, prep.user.department
        prep.telemetry.event("ANSWER", {"department": dept, "persona": prep.persona, "grounded": grounded},
                             {k: v for k, v in {"search_ms": prep.search_ms, "ttft_ms": m.get("ttft_ms"), "total_ms": m.get("total_ms"),
                                                "retrieved": m.get("retrieved"), "sources_cited": len(sources),
                                                "prompt_tokens": m.get("prompt_tokens"), "completion_tokens": m.get("completion_tokens")}.items() if v is not None})
        prep.telemetry.metric("vector_search_ms", prep.search_ms, {"department": dept})
        for key in ("ttft_ms", "total_ms"):
            if m.get(key) is not None:
                prep.telemetry.metric(f"answer_{key}", m[key], {"department": dept})
        for key in ("prompt_tokens", "completion_tokens"):
            if m.get(key) is not None:
                prep.telemetry.metric(f"tokens_{key}", m[key], {"department": dept})   # token consumption per department
    prep_user = prep.user
    store.record_event("QUERY", prep_user.id, None,
                       {"question_hash": _sha(prep.question), "answer_hash": _sha(text),
                        "docs": sorted({sc["doc_hash"] for sc in sources}), "grounded": grounded},
                       department=prep_user.department, min_clearance=0)
    return Answer(text, sources, grounded, warnings, prep.persona, metrics)


def ask(*, question: str, user: R.User, store: Store, vectors: Any, llm: Any, registry_path: str,
        chain: Any = None, k: int = 5, context_chars: int = 0, telemetry: Any = None) -> Answer:
    prep = prepare(question=question, user=user, store=store, vectors=vectors, registry_path=registry_path,
                   chain=chain, k=k, context_chars=context_chars, telemetry=telemetry)
    if not prep.hits:
        return finish(prep, "", store)
    done = llm.complete(messages_for(prep))
    return finish(prep, done.text, store, {"ttft_ms": done.ttft_ms, "total_ms": done.total_ms,
                                           "prompt_tokens": done.prompt_tokens, "completion_tokens": done.completion_tokens})


def ask_stream(prep: Prepared, llm: Any, store: Store) -> Iterator[Dict[str, Any]]:
    """Server-sent-event payloads: meta, then token deltas (already DLP-masked), then done.

    The model's tokens are masked on the fly by StreamMasker, so a secret is never sent to the
    client even if it arrives split across tokens. Grounding is judged once the full text is in;
    the final ``done`` event carries the verdict, the sources and the complete masked answer.
    """
    yield {"event": "meta", "data": {"persona": prep.persona, "retrieved": len(prep.hits),
                                     "warnings": prep.warnings}}
    if not prep.hits:
        ans = finish(prep, "", store)
        yield {"event": "token", "data": {"text": ans.answer}}
        yield {"event": "done", "data": _answer_json(ans)}
        return

    masker, raw, start, first, sent = guardrails.StreamMasker(), [], time.perf_counter(), None, False
    stream = llm.stream(messages_for(prep))
    try:
        for piece in stream:
            if first is None:
                first = (time.perf_counter() - start) * 1000
            raw.append(piece)
            out = masker.feed(piece)
            if not sent:
                out = out.lstrip()          # the final answer is stripped; keep the stream identical to it
            if out:
                sent = True
                yield {"event": "token", "data": {"text": out}}
        tail = masker.flush().rstrip() if sent else masker.flush().strip()
        if tail:
            yield {"event": "token", "data": {"text": tail}}
    except requests.RequestException:
        yield {"event": "error", "data": {"detail": "the language model is unavailable; try again shortly"}}
        return
    usage = getattr(stream, "usage", None) or {}
    ans = finish(prep, "".join(raw), store, {"ttft_ms": first, "total_ms": (time.perf_counter() - start) * 1000,
                                             "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")})
    yield {"event": "done", "data": _answer_json(ans)}


def _answer_json(a: Answer) -> Dict[str, Any]:
    return {"answer": a.answer, "sources": a.sources, "grounded": a.grounded, "warnings": a.warnings,
            "persona": a.persona, "metrics": a.metrics}
