"""Layer 1: Data Ingestion, Deduplication & Delta Governance Pipeline.

Flow:
    source -> extract text (pdfplumber / BeautifulSoup4)
           -> SHA-256 exact-duplicate check
           -> 128-perm MinHash Jaccard similarity vs. active documents
                >= 0.98        discard (near-exact duplicate)
                0.50 - 0.98    delta: senior auto-approves, junior is staged
                <  0.50        new standalone document
           -> RecursiveCharacterTextSplitter (512 / 50 tokens)
           -> deterministic chunk IDs + metadata payload

All parsing, hashing, splitting and similarity scoring runs locally. Nothing
is sent to a third-party parsing / OCR service. The only network call is the
HTTP GET that fetches a web source the caller asked for.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import ipaddress
import re
import socket
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
import pdfplumber
import requests
from bs4 import BeautifulSoup
from datasketch import MinHash
from langchain_text_splitters import RecursiveCharacterTextSplitter

DB_PATH = "doc_registry.db"

NUM_PERM = 128
MINHASH_SCHEME = "affine32"  # pinned so stored signatures stay comparable across upgrades
SHINGLE_SIZE = 3  # word n-grams used as MinHash shingles
EXACT_THRESHOLD = 0.98
DELTA_THRESHOLD = 0.50
FLAG_THRESHOLD = 3  # rejected submissions before an account / source is flagged

CHUNK_SIZE = 512  # tokens
CHUNK_OVERLAP = 50  # tokens
TIKTOKEN_ENCODING = "cl100k_base"

USER_AGENT = "Alexandria-Ingestion/1.0 (+internal document crawler)"
HTTP_TIMEOUT = 20
STRIP_TAGS = ("script", "style", "nav", "header", "footer", "noscript")

# Whole-word terms (optional plural). Matching "auth" or "port" as substrings
# would flag "author", "support", "important", "report", ...
SECURITY_WORDS = (
    "password", "passwd", "secret", "token", "credential", "auth", "oauth", "sso",
    "mfa", "2fa", "permission", "privilege", "role", "admin", "root", "sudo",
    "tls", "ssl", "certificate", "firewall", "port", "endpoint", "access",
    "allowlist", "whitelist", "denylist", "blacklist", "cve", "public",
    "api key", "apikey", "private key", "authentication", "authorization",
)
# Stems matched as word prefixes (encrypt -> encrypted / encryption).
SECURITY_STEMS = ("encrypt", "decrypt", "vulnerab")
_NUMBER_RE = re.compile(r"\d+(?:[.,:/-]\d+)*")
_SECURITY_RE = re.compile(
    r"\b(?:(?:" + "|".join(re.escape(t) for t in SECURITY_WORDS) + r")(?:s|es)?\b|"
    + "|".join(re.escape(t) for t in SECURITY_STEMS) + r")",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

def init_db(db_path: str = DB_PATH) -> None:
    """Create the registry tables if they do not exist."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_docs (
                doc_hash       TEXT PRIMARY KEY,
                source         TEXT NOT NULL,
                source_type    TEXT NOT NULL,
                min_role       TEXT NOT NULL,
                department     TEXT,
                uploader_id    TEXT,
                uploader_role  TEXT NOT NULL,
                status         TEXT NOT NULL CHECK (status IN ('active', 'deprecated')),
                raw_text       TEXT NOT NULL,
                minhash        BLOB NOT NULL,
                parent_hash    TEXT,
                superseded_by  TEXT,
                chunk_count    INTEGER NOT NULL DEFAULT 0,
                timestamp      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_processed_status ON processed_docs(status);

            CREATE TABLE IF NOT EXISTS staged_updates (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_hash       TEXT NOT NULL UNIQUE,
                parent_hash    TEXT NOT NULL,
                source         TEXT NOT NULL,
                source_type    TEXT NOT NULL,
                min_role       TEXT NOT NULL,
                department     TEXT,
                uploader_id    TEXT,
                uploader_role  TEXT NOT NULL,
                similarity     REAL NOT NULL,
                diff           TEXT NOT NULL,
                risk_level     TEXT NOT NULL CHECK (risk_level IN ('high', 'normal')),
                risk_reasons   TEXT NOT NULL,
                raw_text       TEXT NOT NULL,
                minhash        BLOB NOT NULL,
                status         TEXT NOT NULL
                               CHECK (status IN ('pending_approval', 'approved', 'rejected')),
                reviewed_by    TEXT,
                reviewed_at    TEXT,
                review_note    TEXT,
                timestamp      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_staged_status ON staged_updates(status);

            -- Rejected submissions per uploader account / source (data-poisoning signal).
            CREATE TABLE IF NOT EXISTS flags (
                kind           TEXT NOT NULL CHECK (kind IN ('account', 'source')),
                key            TEXT NOT NULL,
                rejections     INTEGER NOT NULL DEFAULT 0,
                last_reason    TEXT,
                last_at        TEXT NOT NULL,
                PRIMARY KEY (kind, key)
            );
            """
        )
        # Upgrade registries created before department / uploader_id existed.
        for table, wanted in (("processed_docs", ("department", "uploader_id")),
                              ("staged_updates", ("department", "uploader_id", "review_note"))):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col in wanted:
                if col not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        conn.commit()


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """One transaction per call: commit on success, roll back on error, always close."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Part A: extraction
# --------------------------------------------------------------------------- #

def _normalize(text: str) -> str:
    """Canonicalize whitespace so identical content always hashes identically."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _table_to_text(table: List[List[Optional[str]]]) -> str:
    rows = []
    for row in table:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def extract_pdf(path: str) -> Tuple[str, Dict[str, Any]]:
    """Extract text and tables page by page. Image-only pages are skipped (no OCR)."""
    pages_out: List[str] = []
    skipped: List[int] = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()
            bboxes = [t.bbox for t in tables]

            def outside_tables(obj: Dict[str, Any]) -> bool:
                if obj.get("object_type") != "char":
                    return True
                cx = (obj["x0"] + obj["x1"]) / 2
                cy = (obj["top"] + obj["bottom"]) / 2
                return not any(x0 <= cx <= x1 and top <= cy <= bottom
                               for x0, top, x1, bottom in bboxes)

            body_page = page.filter(outside_tables) if bboxes else page
            body = body_page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
            table_texts = [t for t in (_table_to_text(tb.extract()) for tb in tables) if t]

            parts = [body.strip()] + table_texts
            page_text = "\n\n".join(p for p in parts if p)
            if not page_text.strip():
                skipped.append(page_no)  # image-only scan or blank page
                continue
            pages_out.append(page_text)
        total_pages = len(pdf.pages)

    meta = {"pages_total": total_pages, "pages_skipped_no_text": skipped}
    return "\n\n".join(pages_out), meta


def _validate_url(url: str) -> None:
    """Only http(s) is allowed, and cloud-metadata / link-local targets are blocked
    so the crawler cannot be pointed at instance credentials. Internal hosts are
    otherwise allowed because this is an internal-docs tool."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"only http(s) URLs are allowed: {url!r}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return  # unresolvable here; the HTTP fetch will fail on its own
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_link_local:
            raise ValueError(f"blocked address {ip} for {parsed.hostname!r}")


def extract_web(url: str) -> Tuple[str, Dict[str, Any]]:
    """Fetch a static page and return only its main content text."""
    _validate_url(url)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup(list(STRIP_TAGS)):
        tag.decompose()

    root = soup.find("main") or soup.find("article") or soup.body or soup
    meta = {
        "http_status": resp.status_code,
        "title": soup.title.get_text(strip=True) if soup.title else None,
        "content_root": root.name if root is not soup else "document",
    }
    return root.get_text(separator="\n"), meta


# --------------------------------------------------------------------------- #
# Parts B & C: hashing and similarity
# --------------------------------------------------------------------------- #

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _shingles(text: str) -> Iterable[bytes]:
    words = re.findall(r"\w+", text.lower())
    if len(words) < SHINGLE_SIZE:
        return {" ".join(words).encode("utf-8")}
    return {" ".join(words[i:i + SHINGLE_SIZE]).encode("utf-8")
            for i in range(len(words) - SHINGLE_SIZE + 1)}


def compute_minhash(text: str) -> MinHash:
    mh = MinHash(num_perm=NUM_PERM, scheme=MINHASH_SCHEME)
    mh.update_batch(list(_shingles(text)))
    return mh


def _minhash_to_blob(mh: MinHash) -> bytes:
    return np.asarray(mh.hashvalues, dtype=np.uint64).tobytes()


def _minhash_from_blob(blob: bytes) -> MinHash:
    return MinHash(num_perm=NUM_PERM, scheme=MINHASH_SCHEME,
                   hashvalues=np.frombuffer(blob, dtype=np.uint64))


def _best_match(conn: sqlite3.Connection, mh: MinHash) -> Tuple[Optional[sqlite3.Row], float]:
    best_row, best_sim = None, 0.0
    for row in conn.execute(
        "SELECT doc_hash, source, raw_text, minhash FROM processed_docs WHERE status = 'active'"
    ):
        sim = mh.jaccard(_minhash_from_blob(row["minhash"]))
        if sim > best_sim:
            best_row, best_sim = row, sim
    return best_row, best_sim


def compute_diff(old_text: str, new_text: str, old_label: str, new_label: str) -> str:
    return "\n".join(difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile=old_label, tofile=new_label, lineterm="",
    ))


def assess_risk(diff: str) -> Tuple[str, List[str]]:
    """Flag a delta as high risk when numbers or security-related terms change."""
    removed, added = [], []
    for line in diff.splitlines():
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])

    reasons: List[str] = []
    old_nums = set(_NUMBER_RE.findall("\n".join(removed)))
    new_nums = set(_NUMBER_RE.findall("\n".join(added)))
    if old_nums != new_nums:
        changed = sorted(old_nums.symmetric_difference(new_nums))
        reasons.append(f"numeric values changed: {', '.join(changed[:20])}")

    sec_terms = sorted({m.group(0).lower() for m in _SECURITY_RE.finditer("\n".join(removed + added))})
    if sec_terms:
        reasons.append(f"security-related terms in changed lines: {', '.join(sec_terms)}")

    return ("high" if reasons else "normal"), reasons


# --------------------------------------------------------------------------- #
# Part D: chunking & metadata
# --------------------------------------------------------------------------- #

_splitter: Optional[RecursiveCharacterTextSplitter] = None


def _get_splitter() -> RecursiveCharacterTextSplitter:
    """Token-based splitter. tiktoken runs locally; it only needs its BPE
    vocabulary file (cached after first load). If unavailable, fall back to a
    ~4 chars/token approximation."""
    global _splitter
    if _splitter is None:
        try:
            _splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                encoding_name=TIKTOKEN_ENCODING,
                chunk_size=CHUNK_SIZE,
                chunk_overlap=CHUNK_OVERLAP,
            )
            _splitter.split_text("warmup")
        except Exception:
            _splitter = RecursiveCharacterTextSplitter(
                chunk_size=CHUNK_SIZE * 4, chunk_overlap=CHUNK_OVERLAP * 4,
            )
    return _splitter


def chunk_document(text: str, doc_hash: str, source: str, min_role: str,
                   timestamp: str, department: Optional[str] = None) -> List[Dict[str, Any]]:
    return [
        {
            "chunk_id": f"{doc_hash}_{i}",
            "doc_hash": doc_hash,
            "source": source,
            "department": department,
            "min_role": min_role,
            "status": "active",
            "timestamp": timestamp,
            "text": chunk,
        }
        for i, chunk in enumerate(_get_splitter().split_text(text))
    ]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _result(status_code: int, status: str, **fields: Any) -> Dict[str, Any]:
    return {"status_code": status_code, "status": status, **fields}


def _insert_active(conn: sqlite3.Connection, *, doc_hash: str, source: str, source_type: str,
                   min_role: str, uploader_role: str, raw_text: str, minhash_blob: bytes,
                   parent_hash: Optional[str], chunk_count: int, timestamp: str,
                   department: Optional[str] = None, uploader_id: Optional[str] = None) -> None:
    conn.execute(
        """INSERT INTO processed_docs
           (doc_hash, source, source_type, min_role, department, uploader_id, uploader_role,
            status, raw_text, minhash, parent_hash, chunk_count, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
        (doc_hash, source, source_type, min_role, department, uploader_id, uploader_role,
         raw_text, minhash_blob, parent_hash, chunk_count, timestamp),
    )
    if parent_hash:
        conn.execute(
            "UPDATE processed_docs SET status = 'deprecated', superseded_by = ? WHERE doc_hash = ?",
            (doc_hash, parent_hash),
        )


def process_incoming_source(source_type: str, path_or_url: str, user_role: str,
                            min_role: str, db_path: str = DB_PATH,
                            uploader_id: Optional[str] = None,
                            department: Optional[str] = None) -> Dict[str, Any]:
    """Run a PDF or web source through Layer 1.

    source_type: "pdf" or "web"
    user_role:   uploader's role; only "senior" may auto-approve deltas, anything
                 else is treated as "junior".
    min_role:    minimum role allowed to read the resulting chunks.
    uploader_id: who uploaded it; recorded for the audit trail.
    department:  owning department; stored in chunk metadata.

    Only seniors get back the diff / matched document hash: a delta is computed
    against a document the uploader may not be cleared to read.

    Returns a JSON-serializable dict with "status_code" and "status", plus
    chunks (ingested), a staging alert (pending_approval) or the reason the
    source was skipped.
    """
    source_type = source_type.lower().strip()
    uploader_role = "senior" if user_role.lower().strip() == "senior" else "junior"

    # Part A
    try:
        if source_type == "pdf":
            raw, extraction_meta = extract_pdf(path_or_url)
        elif source_type in ("web", "url", "html"):
            source_type = "web"
            raw, extraction_meta = extract_web(path_or_url)
        else:
            return _result(400, "error", error=f"unsupported source_type: {source_type!r}")
    except ValueError as exc:  # rejected URL
        if source_type != "pdf":
            return _result(400, "error", error=str(exc))
        return _result(422, "error", error=f"extraction failed: {exc}")
    except requests.RequestException as exc:
        return _result(502, "error", error=f"fetch failed: {exc}")
    except Exception as exc:  # malformed / unreadable file
        return _result(422, "error", error=f"extraction failed: {exc}")

    text = _normalize(raw)
    if not text:
        return _result(422, "error", error="no extractable text (image-only or empty source)",
                       extraction=extraction_meta)

    # Part B
    doc_hash = sha256_text(text)
    timestamp = _now()
    base = {"doc_hash": doc_hash, "source": path_or_url, "extraction": extraction_meta}

    with _connect(db_path) as conn:
        row = conn.execute("SELECT status FROM processed_docs WHERE doc_hash = ?", (doc_hash,)).fetchone()
        if row:
            return _result(200, "duplicate_skipped", existing_status=row["status"], **base)
        row = conn.execute("SELECT id, status FROM staged_updates WHERE doc_hash = ?", (doc_hash,)).fetchone()
        if row:
            return _result(200, "duplicate_skipped", existing_status=row["status"],
                           staged_id=row["id"], **base)

        # Part C
        mh = compute_minhash(text)
        mh_blob = _minhash_to_blob(mh)
        match, similarity = _best_match(conn, mh)
        similarity = round(similarity, 4)
        is_senior = uploader_role == "senior"
        # The matched document may be above the uploader's clearance, so its hash
        # (and any diff against it) is only revealed to seniors.
        match_info: Dict[str, Any] = {"similarity": similarity} if match else {}
        if match and is_senior:
            match_info["matched_doc_hash"] = match["doc_hash"]

        if similarity >= EXACT_THRESHOLD:
            return _result(200, "near_duplicate_discarded", **match_info, **base)

        parent_hash = None
        if similarity >= DELTA_THRESHOLD:
            diff = compute_diff(match["raw_text"], text, match["doc_hash"][:12], doc_hash[:12])
            risk_level, risk_reasons = assess_risk(diff)

            if not is_senior:
                cur = conn.execute(
                    """INSERT INTO staged_updates
                       (doc_hash, parent_hash, source, source_type, min_role, department,
                        uploader_id, uploader_role, similarity, diff, risk_level, risk_reasons,
                        raw_text, minhash, status, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)""",
                    (doc_hash, match["doc_hash"], path_or_url, source_type, min_role,
                     department, uploader_id, uploader_role, similarity, diff, risk_level,
                     json.dumps(risk_reasons), text, mh_blob, timestamp),
                )
                # No diff / risk reasons here: they quote the existing document.
                # Seniors read them through list_pending_updates().
                return _result(202, "pending_approval", staged_id=cur.lastrowid,
                               risk_level=risk_level, **match_info, **base)

            parent_hash = match["doc_hash"]
            match_info.update(risk_level=risk_level, risk_reasons=risk_reasons, diff=diff)

        # Part D
        chunks = chunk_document(text, doc_hash, path_or_url, min_role, timestamp, department)
        _insert_active(conn, doc_hash=doc_hash, source=path_or_url, source_type=source_type,
                       min_role=min_role, uploader_role=uploader_role, raw_text=text,
                       minhash_blob=mh_blob, parent_hash=parent_hash,
                       chunk_count=len(chunks), timestamp=timestamp,
                       department=department, uploader_id=uploader_id)

    status = "version_update_ingested" if parent_hash else "new_document_ingested"
    return _result(201, status, deprecated_doc_hash=parent_hash, chunk_count=len(chunks),
                   chunks=chunks, **match_info, **base)


# --------------------------------------------------------------------------- #
# Staging review
# --------------------------------------------------------------------------- #

def list_pending_updates(db_path: str = DB_PATH,
                         department: Optional[str] = None) -> List[Dict[str, Any]]:
    """The senior review queue (diff + risk flags), high-risk first. Pass
    ``department`` to show a reviewer only their own department's items."""
    sql = """SELECT id, doc_hash, parent_hash, source, department, uploader_id,
                    uploader_role, similarity, risk_level, risk_reasons, diff, timestamp
             FROM staged_updates WHERE status = 'pending_approval'"""
    args: Tuple[Any, ...] = ()
    if department is not None:
        sql += " AND department = ?"
        args = (department,)
    sql += " ORDER BY (risk_level = 'high') DESC, id"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [{**dict(r), "risk_reasons": json.loads(r["risk_reasons"])} for r in rows]


def _bump_flag(conn: sqlite3.Connection, kind: str, key: Optional[str],
               reason: Optional[str], timestamp: str) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    conn.execute(
        """INSERT INTO flags (kind, key, rejections, last_reason, last_at) VALUES (?, ?, 1, ?, ?)
           ON CONFLICT(kind, key) DO UPDATE SET rejections = rejections + 1,
               last_reason = excluded.last_reason, last_at = excluded.last_at""",
        (kind, key, reason, timestamp),
    )
    n = conn.execute("SELECT rejections FROM flags WHERE kind = ? AND key = ?", (kind, key)).fetchone()[0]
    return {"kind": kind, "key": key, "rejections": n, "flagged": n >= FLAG_THRESHOLD}


def list_flags(db_path: str = DB_PATH, only_flagged: bool = True) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT kind, key, rejections, last_reason, last_at FROM flags "
            "WHERE rejections >= ? ORDER BY rejections DESC",
            (FLAG_THRESHOLD if only_flagged else 1,),
        ).fetchall()
    return [dict(r) for r in rows]


def review_staged_update(staged_id: int, approver_role: str, approve: bool,
                         reviewer: Optional[str] = None,
                         db_path: str = DB_PATH,
                         note: Optional[str] = None) -> Dict[str, Any]:
    """Approve or reject a staged delta. Only a senior may review.

    Until this runs, the previous version stays active and the proposed text is
    neither chunked nor searchable. Approval deprecates the parent version and
    returns the new document's chunks. Rejection discards the proposal, records
    ``note``, and counts a strike against the uploader account and the source; the
    caller must write the audit alert for the returned ``flags``.
    """
    if approver_role.lower().strip() != "senior":
        return _result(403, "error", error="only senior users may review staged updates")

    timestamp = _now()
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM staged_updates WHERE id = ?", (staged_id,)).fetchone()
        if not row:
            return _result(404, "error", error=f"staged update {staged_id} not found")
        if row["status"] != "pending_approval":
            return _result(409, "error", error=f"staged update already {row['status']}")

        conn.execute(
            "UPDATE staged_updates SET status = ?, reviewed_by = ?, reviewed_at = ?, "
            "review_note = ? WHERE id = ?",
            ("approved" if approve else "rejected", reviewer or approver_role, timestamp,
             note, staged_id),
        )
        if not approve:
            flags = [f for f in (
                _bump_flag(conn, "account", row["uploader_id"], note, timestamp),
                _bump_flag(conn, "source", row["source"], note, timestamp),
            ) if f]
            return _result(200, "rejected", staged_id=staged_id, doc_hash=row["doc_hash"],
                           uploader_id=row["uploader_id"], flags=flags,
                           audit_alert=True)

        parent = conn.execute("SELECT status FROM processed_docs WHERE doc_hash = ?",
                              (row["parent_hash"],)).fetchone()
        if not parent or parent["status"] != "active":
            conn.rollback()
            return _result(409, "error", error="parent version is no longer active; "
                                               "resubmit the document against the current version")

        chunks = chunk_document(row["raw_text"], row["doc_hash"], row["source"],
                                row["min_role"], timestamp, row["department"])
        _insert_active(conn, doc_hash=row["doc_hash"], source=row["source"],
                       source_type=row["source_type"], min_role=row["min_role"],
                       uploader_role=row["uploader_role"], raw_text=row["raw_text"],
                       minhash_blob=row["minhash"], parent_hash=row["parent_hash"],
                       chunk_count=len(chunks), timestamp=timestamp,
                       department=row["department"], uploader_id=row["uploader_id"])

    return _result(201, "version_update_ingested", staged_id=staged_id, doc_hash=row["doc_hash"],
                   deprecated_doc_hash=row["parent_hash"], chunk_count=len(chunks), chunks=chunks)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Layer 1 ingestion")
    ap.add_argument("source_type", choices=["pdf", "web"])
    ap.add_argument("path_or_url")
    ap.add_argument("--user-role", default="junior")
    ap.add_argument("--min-role", default="employee")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--uploader-id")
    ap.add_argument("--department")
    args = ap.parse_args()
    print(json.dumps(process_incoming_source(args.source_type, args.path_or_url,
                                             args.user_role, args.min_role, args.db,
                                             args.uploader_id, args.department), indent=2))
