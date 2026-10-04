"""Expert routing: who, specifically, should answer this question, and are they working right now?

In a company of tens of thousands, "ask the channel" doesn't scale and time zones mean the first person
who sees a question is often asleep. For a question this module ranks:

* **departments**, from the documents that match it (hybrid search, filtered to what the asker may read),
  the department's past requests that match it, and a small vocabulary per department as a fallback;
* **people** in those departments who may answer requests, scored by what they demonstrably know:
  documents they wrote or approved that match, and matching requests they answered. Evidence the asker
  could not read is only ever counted, never quoted.

Each person's local time and working hours are included, and people who are working (or online) now are
ranked ahead, so a question goes to someone who can answer it today: "follow the sun".
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from contextlib import closing
from typing import Any, Callable, Dict, Iterable, List, Optional, Set
from zoneinfo import ZoneInfo

import roles as R
from .store import Store

WORK_START, WORK_END = 9, 18          # local working hours, Monday to Friday
STOP = frozenset("a an the and or of to in on for with is are was were be been it its this that these those how what "
                 "when where which who why do does did can could should would i you we they my our your from at by as "
                 "if not no me us any anyone know about get got has have need there their them than then so just "
                 "please help question ask".split())

# Fallback vocabulary when nothing in the knowledge base or request history matches.
VOCAB: Dict[str, Set[str]] = {
    "support": {"customer", "customers", "payroll", "deposit", "direct", "cutoff", "refund", "paid", "paycheck",
                "employee", "employer", "ticket", "funding", "returned", "calendar"},
    "engineering": {"bug", "error", "500", "outage", "api", "deploy", "release", "engine", "crash", "slow", "down",
                    "integration", "trace", "code", "build"},
    "legal": {"tax", "compliance", "penalty", "notice", "contract", "policy", "leave", "pto", "withholding",
              "state", "law", "expense", "reimbursement", "agency", "audit"},
    "security": {"access", "password", "breach", "phishing", "soc", "data", "permission", "incident", "vpn",
                 "laptop", "mfa", "2fa", "deletion", "retention"},
    "product": {"feature", "roadmap", "launch", "notes", "wage", "ewa", "design", "pricing", "request"},
    "executive": {"budget", "strategy", "headcount", "board", "compensation", "salary", "bands", "merit"},
}


def terms(text: str) -> Set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1 and t not in STOP}


def availability(tz: Optional[str], now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Local time and whether it is within working hours there."""
    try:
        zone = ZoneInfo(tz) if tz else None
    except Exception:
        zone = None
    if zone is None:
        return {"timezone": None, "local_time": None, "working": None}
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(zone)
    working = local.weekday() < 5 and WORK_START <= local.hour < WORK_END
    return {"timezone": tz, "local_time": local.strftime("%H:%M"), "working": working}


class Router:
    def __init__(self, store: Store, vectors: Any, registry_path: str,
                 online: Callable[[], Set[str]] = lambda: set(),
                 now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)) -> None:
        self.store, self.vectors, self.registry = store, vectors, registry_path
        self.online, self.now = online, now

    # ------------------------------------------------------------------ people
    def person(self, row: Dict[str, Any], online: Set[str]) -> Dict[str, Any]:
        u = Store.as_role_user(row)
        return {"id": row["id"], "username": row["username"], "department": u.department,
                "role": u.role_def.label if hasattr(u.role_def, "label") else row["role"],
                "online": row["id"] in online, **availability(row.get("timezone"), self.now())}

    def directory(self) -> List[Dict[str, Any]]:
        online = self.online()
        return [self.person(r, online) for r in self.store.list_users()]

    # ----------------------------------------------------------------- evidence
    def _doc_people(self, doc_hashes: Iterable[str]) -> Dict[str, Dict[str, str]]:
        """doc_hash -> {"uploader": id, "approver": id}"""
        hashes = list(dict.fromkeys(doc_hashes))
        if not hashes:
            return {}
        out: Dict[str, Dict[str, str]] = {h: {} for h in hashes}
        marks = ",".join("?" * len(hashes))
        with closing(sqlite3.connect(self.registry)) as c:
            for h, up in c.execute(f"SELECT doc_hash, uploader_id FROM processed_docs WHERE doc_hash IN ({marks})", hashes):
                if up:
                    out[h]["uploader"] = up
            for h, rv in c.execute(f"SELECT doc_hash, reviewed_by FROM staged_updates WHERE status = 'approved' "
                                   f"AND doc_hash IN ({marks})", hashes):
                if rv:
                    out[h]["approver"] = rv
        return out

    def _ping_matches(self, q: Set[str], exclude: str, limit: int = 600) -> List[Dict[str, Any]]:
        """Answered requests whose title or question shares words with ``q``: (department, answerers, overlap)."""
        with self.store.tx() as c:
            pings = [dict(r) for r in c.execute(
                "SELECT id, to_department, title FROM pings WHERE status IN ('answered','resolved') "
                "ORDER BY updated_at DESC LIMIT ?", (limit,))]
            out = []
            for p in pings:
                first = c.execute("SELECT body FROM ping_messages WHERE ping_id = ? AND kind = 'question' ORDER BY id LIMIT 1",
                                  (p["id"],)).fetchone()
                words = terms(self.store.unseal(p["title"]) + " " + (self.store.unseal(first["body"]) if first else ""))
                overlap = len(q & words)
                if overlap < 2 and not (overlap == 1 and len(q) <= 2):
                    continue
                answerers = {r["author_id"] for r in c.execute(
                    "SELECT DISTINCT author_id FROM ping_messages WHERE ping_id = ? AND kind = 'answer'", (p["id"],))}
                out.append({"department": p["to_department"], "answerers": answerers - {exclude}, "overlap": overlap})
        return out

    # ------------------------------------------------------------------ suggest
    def suggest(self, asker: R.User, question: str, limit: int = 4) -> Dict[str, Any]:
        q = terms(question)
        dept: Dict[str, float] = {d: 0.0 for d in R.DEPARTMENTS}
        score: Dict[str, float] = {}
        why: Dict[str, Dict[str, Any]] = {}

        def credit(uid: str, pts: float, kind: str, label: Optional[str] = None) -> None:
            if not uid or uid == asker.id:
                return
            score[uid] = score.get(uid, 0.0) + pts
            w = why.setdefault(uid, {"wrote": [], "approved": [], "answered": 0})
            if kind == "answered":
                w["answered"] += 1
            elif label and label not in w[kind]:
                w[kind].append(label)

        # 1. documents the asker may read (the search filters by clearance inside the index)
        hits = []
        if self.vectors is not None and q:
            try:
                hits = self.vectors.query(question, asker.effective_clearance, 8)
            except Exception:
                hits = []
        people = self._doc_people(h.doc_hash for h in hits)
        seen: Set[str] = set()
        for rank, h in enumerate(hits):
            w = 1.0 / (1 + rank)
            if h.department in dept:
                dept[h.department] += 2.0 * w
            if h.doc_hash in seen:
                continue
            seen.add(h.doc_hash)
            title = re.sub(r"^(file:|thread:\d+ )", "", h.source).removesuffix(".pdf")
            credit(people.get(h.doc_hash, {}).get("uploader", ""), 3.0 * w, "wrote", title)
            credit(people.get(h.doc_hash, {}).get("approver", ""), 2.0 * w, "approved", title)

        # 2. requests the department answered before (content never returned, only counted)
        for m in self._ping_matches(q, asker.id):
            if m["department"] in dept:
                dept[m["department"]] += 0.6 * m["overlap"]
            for uid in m["answerers"]:
                credit(uid, 1.2 * m["overlap"], "answered")

        # 3. vocabulary fallback
        for d, vocab in VOCAB.items():
            if d in dept:
                dept[d] += 0.5 * len(q & vocab)

        ranked = sorted((d for d in dept if dept[d] > 0), key=lambda d: -dept[d]) or []
        top = dept[ranked[0]] if ranked else 1.0
        online = self.online()
        users = {r["id"]: r for r in self.store.list_users()}

        def roster(d: str) -> List[Dict[str, Any]]:
            return [self.person(r, online) for r in users.values()
                    if Store.as_role_user(r).department == d and R.can(Store.as_role_user(r), R.Cap.ANSWER_PING)
                    and r["id"] != asker.id]

        departments = []
        for d in ranked[:3]:
            team = roster(d)
            departments.append({"department": d, "confidence": round(dept[d] / top, 2), "members": len(team),
                                "working_now": sum(1 for p in team if p["working"] or p["online"])})

        # people who may answer, in the suggested departments (plus anyone with direct evidence)
        cands: Dict[str, float] = {}
        for uid, s in score.items():
            r = users.get(uid)
            if r and R.can(Store.as_role_user(r), R.Cap.ANSWER_PING):
                cands[uid] = s
        for d in ranked[:2]:
            for p in roster(d):
                cands.setdefault(p["id"], 0.1 * dept[d] / top)       # a teammate with no direct evidence yet

        experts = []
        for uid, s in cands.items():
            p = self.person(users[uid], online)
            avail = 1.0 if (p["online"] or p["working"]) else 0.0
            w = why.get(uid, {"wrote": [], "approved": [], "answered": 0})
            reasons = [f"Wrote “{t}”" for t in w["wrote"][:2]] + [f"Approved “{t}”" for t in w["approved"][:1]]
            if w["answered"]:
                reasons.append(f"Answered {w['answered']} similar request{'s' if w['answered'] != 1 else ''}")
            experts.append({**p, "score": round(s, 3), "reasons": reasons, "_rank": s + 0.75 * avail * max(s, 0.5)})
        experts.sort(key=lambda e: -e["_rank"])
        for e in experts:
            e.pop("_rank")
        return {"departments": departments, "experts": experts[:limit]}
