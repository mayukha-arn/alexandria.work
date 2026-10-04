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
import logging
import math
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence

import chromadb
import requests

import roles as R
from . import guardrails

log = logging.getLogger("alexandria.vectorstore")

COLLECTION = "alexandria_chunks"
RRF_K = 60  # reciprocal-rank-fusion constant
_STOPWORDS = frozenset("a an the and or of to in on for with is are was were be been it its this that these "
                       "those how what when where which who why do does did can could should would i you we "
                       "they my our your from at by as if not no".split())


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
    distance: Optional[float] = None   # cosine distance; None when only the keyword index matched
    score: float = 0.0                 # fused ranking score (higher is better)
    via: str = ""                      # "vector", "keyword" or "both"


class VectorStore:
    """Hybrid retrieval: ChromaDB (semantic) + SQLite FTS5 (keyword, BM25), fused with reciprocal
    rank fusion. Both sides apply the access filter inside their own database."""

    def __init__(self, embedder: Embedder, path: Optional[str] = "./chroma_db",
                 collection: str = COLLECTION) -> None:
        self.embedder = embedder
        client = chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        # Cosine distance; we always supply our own embeddings.
        self.col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"},
                                                   embedding_function=None)
        kw_path = os.path.join(path, f"{collection}.keyword.sqlite") if path else ":memory:"
        self._kw = sqlite3.connect(kw_path, check_same_thread=False)
        self._kw.row_factory = sqlite3.Row
        self._kw_lock = threading.RLock()
        with self._kw_lock, self._kw:
            self._kw.executescript("""
                CREATE TABLE IF NOT EXISTS chunk_meta (
                    chunk_id TEXT PRIMARY KEY, doc_hash TEXT NOT NULL, source TEXT, department TEXT,
                    min_role TEXT NOT NULL, min_clearance INTEGER NOT NULL, status TEXT NOT NULL,
                    flagged INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunk_meta(doc_hash);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    text, chunk_id UNINDEXED, tokenize='porter unicode61');
            """)

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
        with self._kw_lock, self._kw:
            for c, m in zip(chunks, metas):
                self._kw.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (c["chunk_id"],))
                self._kw.execute("INSERT INTO chunks_fts (text, chunk_id) VALUES (?, ?)", (c["text"], c["chunk_id"]))
                self._kw.execute(
                    "INSERT OR REPLACE INTO chunk_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (c["chunk_id"], m["doc_hash"], m["source"], m["department"], m["min_role"],
                     m["min_clearance"], m["status"], int(m["flagged"])))
        return {"indexed": len(chunks), "flagged": flagged}

    def deprecate_document(self, doc_hash: str) -> int:
        """A newer approved version supersedes this one: keep the vectors, stop retrieving them."""
        got = self.col.get(where={"doc_hash": doc_hash}, include=["metadatas"])
        for i, m in zip(got["ids"], got["metadatas"]):
            self.col.update(ids=[i], metadatas=[{**m, "status": "deprecated"}])
        with self._kw_lock, self._kw:
            self._kw.execute("UPDATE chunk_meta SET status = 'deprecated' WHERE doc_hash = ?", (doc_hash,))
        return len(got["ids"])

    def purge_document(self, doc_hash: str) -> int:
        """Right to be forgotten: remove every vector for the document."""
        got = self.col.get(where={"doc_hash": doc_hash})
        if got["ids"]:
            self.col.delete(ids=got["ids"])
        with self._kw_lock, self._kw:
            ids = [r[0] for r in self._kw.execute("SELECT chunk_id FROM chunk_meta WHERE doc_hash = ?", (doc_hash,))]
            for cid in ids:
                self._kw.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (cid,))
            self._kw.execute("DELETE FROM chunk_meta WHERE doc_hash = ?", (doc_hash,))
        return max(len(got["ids"]), len(ids))

    # ------------------------------------------------------------------- reads
    def _vector_hits(self, text: str, clearance: int, k: int) -> List[Hit]:
        where = {"$and": [{"status": "active"}, {"flagged": False},
                          {"min_clearance": {"$lte": int(clearance)}}]}
        n = self.col.count()
        if n == 0:
            return []
        res = self.col.query(query_embeddings=self.embedder.embed([text], "query"),
                             n_results=min(k, n), where=where,
                             include=["documents", "metadatas", "distances"])
        return [Hit(cid, doc, meta["doc_hash"], meta["source"], meta["department"] or None,
                    meta["min_role"], distance=dist, via="vector")
                for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                                res["metadatas"][0], res["distances"][0])]

    @staticmethod
    def _terms(text: str) -> List[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        terms = [w for w in words if w not in _STOPWORDS and (len(w) > 1 or w.isdigit())]
        return list(dict.fromkeys(terms))[:24]

    def _keyword_hits(self, text: str, clearance: int, k: int) -> List[Hit]:
        terms = self._terms(text)
        if not terms:
            return []
        # Every term is quoted, so user input can never inject FTS5 query syntax.
        match = " OR ".join(f'"{t}"' for t in terms)
        with self._kw_lock:
            rows = self._kw.execute(
                """SELECT m.chunk_id, f.text, m.doc_hash, m.source, m.department, m.min_role
                   FROM chunks_fts f JOIN chunk_meta m ON m.chunk_id = f.chunk_id
                   WHERE chunks_fts MATCH ? AND m.status = 'active' AND m.flagged = 0
                         AND m.min_clearance <= ?
                   ORDER BY bm25(chunks_fts) LIMIT ?""", (match, int(clearance), k)).fetchall()
        return [Hit(r["chunk_id"], r["text"], r["doc_hash"], r["source"] or "", r["department"] or None,
                    r["min_role"], via="keyword") for r in rows]

    def query(self, text: str, clearance: int, k: int = 5, mode: str = "hybrid") -> List[Hit]:
        """``mode``: "hybrid" (default), "vector" or "keyword". If the embedding model is
        unreachable, hybrid degrades to keyword-only instead of failing."""
        fetch = max(2 * k, 10)
        vec: List[Hit] = []
        kw: List[Hit] = []
        if mode in ("hybrid", "vector"):
            try:
                vec = self._vector_hits(text, clearance, fetch)
            except Exception as exc:
                if mode == "vector":
                    raise
                log.warning("vector search unavailable, using keyword only: %s", exc)
        if mode in ("hybrid", "keyword"):
            kw = self._keyword_hits(text, clearance, fetch)

        fused: Dict[str, Hit] = {}
        for hits in (vec, kw):
            for rank, h in enumerate(hits, start=1):
                cur = fused.get(h.chunk_id)
                if cur is None:
                    fused[h.chunk_id] = Hit(**{**h.__dict__, "score": 1.0 / (RRF_K + rank)})
                else:
                    cur.score += 1.0 / (RRF_K + rank)
                    cur.via = "both"
                    cur.distance = cur.distance if cur.distance is not None else h.distance
        return sorted(fused.values(), key=lambda h: -h.score)[:k]

    def count(self) -> int:
        return self.col.count()
