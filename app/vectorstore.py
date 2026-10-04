"""Layer 2: vector storage with database-level access control.

Every chunk carries ``min_clearance`` (resolved from its ``min_role`` label at index time),
``status`` and ``flagged`` metadata. Queries pass a *where* filter to ChromaDB, so chunks
above the asker's clearance, deprecated versions and quarantined chunks are excluded by the
database before nearest-neighbour ranking, never fetched and filtered afterwards.

Embeddings come from a local Ollama model (nomic-embed-text): all-MiniLM-L6-v2 silently
truncates input at ~256 tokens, which would leave half of each 512-token chunk unsearchable.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence

import chromadb
import requests

import roles as R
from . import guardrails

COLLECTION = "alexandria_chunks"


class Embedder(Protocol):
    def embed(self, texts: Sequence[str], kind: str) -> List[List[float]]:
        """``kind`` is "document" or "query" (some models want different prefixes)."""


class OllamaEmbedder:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "nomic-embed-text",
                 timeout: float = 60.0) -> None:
        self.url, self.model, self.timeout = base_url.rstrip("/"), model, timeout

    def embed(self, texts: Sequence[str], kind: str) -> List[List[float]]:
        prefix = "search_query: " if kind == "query" else "search_document: "
        r = requests.post(f"{self.url}/api/embed", timeout=self.timeout,
                          json={"model": self.model, "input": [prefix + t for t in texts]})
        r.raise_for_status()
        return r.json()["embeddings"]


class HashingEmbedder:
    """Deterministic bag-of-words embedder for tests: no model, no network.
    Texts sharing words get closer vectors, enough to check ranking and filtering."""

    def __init__(self, dims: int = 256) -> None:
        self.dims = dims

    def embed(self, texts: Sequence[str], kind: str) -> List[List[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dims
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dims] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


@dataclass
class Hit:
    chunk_id: str
    text: str
    doc_hash: str
    source: str
    department: Optional[str]
    min_role: str
    distance: float


class VectorStore:
    def __init__(self, embedder: Embedder, path: Optional[str] = "./chroma_db",
                 collection: str = COLLECTION) -> None:
        self.embedder = embedder
        client = chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        # Cosine distance; we always supply our own embeddings.
        self.col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"},
                                                   embedding_function=None)

    # ------------------------------------------------------------------ writes
    def add_chunks(self, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Index Layer 1 chunks. Chunks whose text looks like an injection attempt are stored
        but ``flagged``: retrieval excludes them, so they never reach the LLM."""
        if not chunks:
            return {"indexed": 0, "flagged": []}
        flagged: List[Dict[str, Any]] = []
        metas, ids = [], []
        for c in chunks:
            findings = guardrails.scan_text(c["text"])
            if findings:
                flagged.append({"chunk_id": c["chunk_id"], "findings": findings})
            ids.append(c["chunk_id"])
            metas.append({
                "doc_hash": c["doc_hash"], "source": c["source"] or "",
                "department": c.get("department") or "", "min_role": c["min_role"],
                "min_clearance": R.min_clearance_for(c["min_role"]),
                "status": c.get("status", "active"), "flagged": bool(findings),
                "timestamp": c.get("timestamp") or "",
            })
        self.col.upsert(ids=ids, documents=[c["text"] for c in chunks], metadatas=metas,
                        embeddings=self.embedder.embed([c["text"] for c in chunks], "document"))
        return {"indexed": len(chunks), "flagged": flagged}

    def deprecate_document(self, doc_hash: str) -> int:
        """A newer approved version supersedes this one: keep the vectors, stop retrieving them."""
        got = self.col.get(where={"doc_hash": doc_hash}, include=["metadatas"])
        for i, m in zip(got["ids"], got["metadatas"]):
            self.col.update(ids=[i], metadatas=[{**m, "status": "deprecated"}])
        return len(got["ids"])

    def purge_document(self, doc_hash: str) -> int:
        """Right to be forgotten: remove every vector for the document."""
        got = self.col.get(where={"doc_hash": doc_hash})
        if got["ids"]:
            self.col.delete(ids=got["ids"])
        return len(got["ids"])

    # ------------------------------------------------------------------- reads
    def query(self, text: str, clearance: int, k: int = 5) -> List[Hit]:
        where = {"$and": [{"status": "active"}, {"flagged": False},
                          {"min_clearance": {"$lte": int(clearance)}}]}
        n = self.col.count()
        if n == 0:
            return []
        res = self.col.query(query_embeddings=self.embedder.embed([text], "query"),
                             n_results=min(k, n), where=where,
                             include=["documents", "metadatas", "distances"])
        hits = []
        for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            hits.append(Hit(cid, doc, meta["doc_hash"], meta["source"], meta["department"] or None,
                            meta["min_role"], dist))
        return hits

    def count(self) -> int:
        return self.col.count()
