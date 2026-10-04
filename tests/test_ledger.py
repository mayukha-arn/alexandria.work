import hashlib
import json
import sqlite3
import time

import pytest
import requests
from fastapi.testclient import TestClient

import ingestion_layer as il
from app import chain as C
from app.anchoring import DOC_EVENT, Hasher, anchor_pending
from app.config import Settings
from app.main import create_app
from app.redaction import redaction_marker, render_event
from app.verify import verify_document
from tests.test_api import H, enroll, make_user


@pytest.fixture
def parts(tmp_path):
    settings = Settings(db_path=str(tmp_path / "t.db"), secrets_dir=tmp_path / "sec",
                        registry_path=str(tmp_path / "reg.db"))
    chain = C.MemoryChain()
    app = create_app(settings, chain=chain, anchor_interval=3600)
    return TestClient(app), app.state.store, settings, chain, app.state.hasher


# ------------------------------------------------------------------ keyed hashing
def test_onchain_hashes_are_keyed_not_plain_sha256():
    h = Hasher("secret-a")
    assert h.mac("DOCUMENT_PURGE") != hashlib.sha256(b"DOCUMENT_PURGE").digest()   # not reversible by guessing
    assert h.mac("DOCUMENT_PURGE") == Hasher("secret-a").mac("DOCUMENT_PURGE")      # stable
    assert h.mac("DOCUMENT_PURGE") != Hasher("secret-b").mac("DOCUMENT_PURGE")      # key-dependent


# ------------------------------------------------------------------- the worker
def test_events_anchor_in_order_and_only_once(parts):
    _, store, _, chain, hasher = parts
    ids = [store.record_event("E", "u1", "t", {"n": n}, department="engineering", min_clearance=10) for n in range(3)]
    assert anchor_pending(store, chain, hasher) == {"anchored": 3, "rejected": 0}
    assert [e["index"] for e in chain.entries] == [0, 1, 2]
    assert [e["payload_hash"] for e in chain.entries] == [hasher.mac(ev["payload_hash"]).hex() for ev in store.list_events()]
    assert all(e["anchored_at"] and e["tx_signature"] for e in store.list_events())
    assert anchor_pending(store, chain, hasher) == {"anchored": 0, "rejected": 0}
    assert len(chain.entries) == 3 and ids == [1, 2, 3]


def test_outage_keeps_events_queued_and_ordered(parts):
    _, store, _, chain, hasher = parts
    for n in range(3):
        store.record_event("E", "u1", None, {"n": n})
    chain.fail_next = 1                                   # chain is down for the first attempt
    assert anchor_pending(store, chain, hasher) == {"anchored": 0, "rejected": 0}
    assert len(store.list_events(only_unanchored=True)) == 3, "nothing lost"
    assert anchor_pending(store, chain, hasher)["anchored"] == 3
    assert [json.loads(store.list_events()[i]["payload"])["n"] for i in range(3)] == [0, 1, 2]


def test_partial_outage_resumes_where_it_stopped(parts):
    _, store, _, chain, hasher = parts
    for n in range(4):
        store.record_event("E", "u1", None, {"n": n})
    assert anchor_pending(store, chain, hasher, limit=2)["anchored"] == 2
    chain.fail_next = 1
    assert anchor_pending(store, chain, hasher)["anchored"] == 0
    assert anchor_pending(store, chain, hasher)["anchored"] == 2
    assert len(chain.entries) == 4


def test_permanent_rejection_does_not_block_the_queue(parts):
    _, store, _, chain, hasher = parts
    store.record_event("BAD", "u1", None, {}, min_clearance=101)   # chain refuses clearance > 100
    store.record_event("GOOD", "u1", None, {}, min_clearance=10)
    assert anchor_pending(store, chain, hasher) == {"anchored": 1, "rejected": 1}
    bad, good = store.list_events()
    assert "InvalidClearance" in bad["anchor_error"] and bad["anchored_at"] is None
    assert good["anchored_at"] is not None
    assert anchor_pending(store, chain, hasher) == {"anchored": 0, "rejected": 0}   # the bad one is not retried forever


def test_document_event_anchors_document_record(parts):
    _, store, _, chain, hasher = parts
    doc, parent = hashlib.sha256(b"v2").hexdigest(), hashlib.sha256(b"v1").hexdigest()
    store.record_event(DOC_EVENT, "senior-1", None,
                       {"doc_hash": doc, "parent_hash": parent, "approver_pubkey": "WalletPubkey111"},
                       department="engineering", min_clearance=40)
    anchor_pending(store, chain, hasher)
    rec = chain.get_doc(bytes.fromhex(doc))
    assert rec["parent_hash"] == parent and rec["approver"] == "WalletPubkey111"
    assert chain.entries[rec["entry_index"]]["payload_hash"] == doc      # document hash stays plain SHA-256


def test_already_anchored_document_is_recovered_not_stuck(parts):
    """A previous run landed on-chain but crashed before recording it."""
    _, store, _, chain, hasher = parts
    doc = hashlib.sha256(b"v1").hexdigest()
    chain.anchor_document(bytes.fromhex(doc), None, b"a" * 32, b"b" * 32, "x", 0)
    store.record_event(DOC_EVENT, "s", None, {"doc_hash": doc, "parent_hash": None})
    assert anchor_pending(store, chain, hasher) == {"anchored": 1, "rejected": 0}
    assert store.list_events()[0]["anchor_error"] is None


# ------------------------------------------------------------------- redaction
def _event(**kw):
    base = {"id": 1, "created_at": 1.0, "department": "engineering", "actor_id": "u1", "target_id": "u2",
            "kind": "ACCESS_CLEARANCE_CHANGED", "payload": json.dumps({"from": 40, "to": 95}),
            "payload_hash": "ab" * 32, "min_clearance": 90, "anchored_at": 5.0, "tx_signature": "SIG"}
    return {**base, **kw}


def test_redaction_boundary():
    h = Hasher("k")
    low = render_event(_event(), 89, h)
    assert low["redacted"] and low["action"] == low["payload"] == low["payload_hash"] == low["target"] == redaction_marker(90)
    assert low["department"] == "engineering" and low["actor_hash"] == h.mac("u1").hex() and low["tx_signature"] == "SIG"
    assert "from" not in json.dumps(low) and "ACCESS_CLEARANCE" not in json.dumps(low)
    at = render_event(_event(), 90, h)
    assert not at["redacted"] and at["action"] == "ACCESS_CLEARANCE_CHANGED" and at["payload"] == {"from": 40, "to": 95}


# ------------------------------------------------------------------ verification
def _registry(path, text, doc_hash=None, min_role="developer"):
    il.init_db(path)
    h = doc_hash or hashlib.sha256(text.encode()).hexdigest()
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO processed_docs (doc_hash, source, source_type, min_role, uploader_role, status, "
                  "raw_text, minhash, chunk_count, timestamp) VALUES (?, 's', 'web', ?, 'senior', 'active', ?, x'00', 1, 't')",
                  (h, min_role, text))
    return h


def test_verify_statuses(tmp_path):
    reg, chain = str(tmp_path / "r.db"), C.MemoryChain()
    h = _registry(reg, "payroll policy v1")
    assert verify_document(reg, chain, "f" * 64)["status"] == "not_found"
    assert verify_document(reg, chain, h)["status"] == "unanchored"
    chain.anchor_document(bytes.fromhex(h), None, b"a" * 32, b"b" * 32, "x", 0, "Approver1")
    ok = verify_document(reg, chain, h)
    assert ok["status"] == "verified" and ok["chain"]["approver"] == "Approver1"
    with sqlite3.connect(reg) as c:                       # someone edits the DB behind our back
        c.execute("UPDATE processed_docs SET raw_text = 'payroll policy v1 (edited)' WHERE doc_hash = ?", (h,))
    assert verify_document(reg, chain, h)["status"] == "tampered"


# -------------------------------------------------------------------- the API
def org(client, store):
    admin = make_user(store, "root", "security_admin")
    boss = make_user(store, "boss", "senior_eng", manager=admin["id"])
    dev = make_user(store, "dev", "developer", manager=boss["id"])
    peer = make_user(store, "peer", "developer", manager=admin["id"])
    return admin, boss, dev, peer, {n: enroll(client, n)[0] for n in ("root", "boss", "dev", "peer")}


def test_ledger_endpoint_redacts_by_clearance(parts):
    client, store, _, chain, hasher = parts
    admin, boss, dev, peer, t = org(client, store)
    client.put(f"/users/{peer['id']}/clearance", headers=H(t["root"]), json={"level": 95})   # event needs 95
    client.put(f"/users/{dev['id']}/clearance", headers=H(t["boss"]), json={"level": 50})    # event needs 50

    as_boss = client.get("/audit/ledger", headers=H(t["boss"])).json()                       # clearance 70
    secret = next(r for r in as_boss if r["required_clearance"] == 95)
    visible = next(r for r in as_boss if r["required_clearance"] == 50)
    assert secret["redacted"] and secret["payload"] == redaction_marker(95)
    assert not visible["redacted"] and visible["payload"] == {"from": 40, "to": 50}
    as_root = client.get("/audit/ledger", headers=H(t["root"])).json()
    assert not next(r for r in as_root if r["required_clearance"] == 95)["redacted"]
    assert client.get("/audit/ledger", headers=H(t["dev"])).status_code == 403              # no VIEW_AUDIT right


def test_audit_status_counts(parts):
    client, store, _, chain, hasher = parts
    _, _, dev, _, t = org(client, store)
    client.put(f"/users/{dev['id']}/clearance", headers=H(t["boss"]), json={"level": 50})
    s = client.get("/audit/status", headers=H(t["boss"])).json()
    assert s["chain_configured"] and s["queued"] >= 1 and s["anchored"] == 0
    anchor_pending(store, chain, hasher)
    assert client.get("/audit/status", headers=H(t["boss"])).json()["queued"] == 0


def test_events_encrypted_at_rest(parts):
    client, store, settings, _, _ = parts
    _, _, dev, _, t = org(client, store)
    client.put(f"/users/{dev['id']}/clearance", headers=H(t["boss"]), json={"level": 55})
    with sqlite3.connect(settings.db_path) as c:
        raw = [r[0] for r in c.execute("SELECT payload FROM events WHERE kind = 'ACCESS_CLEARANCE_CHANGED'")]
    assert raw and all('"to"' not in r and "55" not in r for r in raw)
    assert json.loads(store.list_events()[-1]["payload"])["to"] == 55     # still readable through the store


def test_verify_endpoint_respects_clearance(parts):
    client, store, settings, chain, _ = parts
    _, _, _, _, t = org(client, store)
    secret_doc = _registry(settings.registry_path, "restricted design doc", min_role="senior_eng")
    chain.anchor_document(bytes.fromhex(secret_doc), None, b"a" * 32, b"b" * 32, "engineering", 70)
    ok = client.get(f"/audit/verify/{secret_doc}", headers=H(t["boss"]))                     # senior_eng may read it
    assert ok.status_code == 200 and ok.json()["status"] == "verified"
    hidden = client.get(f"/audit/verify/{secret_doc}", headers=H(t["dev"]))
    missing = client.get(f"/audit/verify/{'0' * 64}", headers=H(t["dev"]))
    assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()


def test_background_loop_anchors_automatically(tmp_path):
    settings = Settings(db_path=str(tmp_path / "t.db"), secrets_dir=tmp_path / "sec", registry_path=str(tmp_path / "r.db"))
    chain = C.MemoryChain()
    app = create_app(settings, chain=chain, anchor_interval=0.05)
    with TestClient(app):
        app.state.store.record_event("E", "u", None, {"x": 1})
        for _ in range(60):
            if chain.entries:
                break
            time.sleep(0.05)
    assert len(chain.entries) == 1


# ------------------------------------------------------------------- RPC failover
class FakeResp:
    def __init__(self, body=None, status=200):
        self._b, self.status_code = body or {}, status

    def json(self):
        return self._b


def test_rpc_backs_off_then_fails_over(monkeypatch):
    calls, sleeps = [], []

    def post(url, **kw):
        calls.append(url)
        if url == "http://primary":
            raise requests.ConnectionError("down")
        return FakeResp({"result": 42})

    monkeypatch.setattr(C.requests, "post", post)
    rpc = C.Rpc(["http://primary", "http://backup"], sleep=sleeps.append)
    assert rpc.call("getSlot", []) == 42
    assert calls == ["http://primary"] * 3 + ["http://backup"]
    assert sleeps == [2, 4]                                # 2s, 4s between the three attempts, none after the last


def test_rpc_rate_limit_is_retried_but_rejection_is_not(monkeypatch):
    seq = iter([FakeResp(status=429), FakeResp({"result": "ok"})])
    monkeypatch.setattr(C.requests, "post", lambda url, **kw: next(seq))
    assert C.Rpc(["http://a"], sleep=lambda s: None).call("m", []) == "ok"

    calls = []
    monkeypatch.setattr(C.requests, "post", lambda url, **kw: calls.append(url) or FakeResp({"error": {"code": -32002, "message": "rejected"}}))
    with pytest.raises(C.ProgramError):
        C.Rpc(["http://a", "http://b"], sleep=lambda s: None).call("m", [])
    assert calls == ["http://a"], "a rejected transaction is not retried or failed over"


def test_rpc_all_endpoints_down(monkeypatch):
    monkeypatch.setattr(C.requests, "post", lambda url, **kw: (_ for _ in ()).throw(requests.Timeout("t")))
    with pytest.raises(C.ChainError):
        C.Rpc(["http://a", "http://b"], sleep=lambda s: None).call("m", [])


def test_rpc_requires_an_endpoint():
    with pytest.raises(ValueError):
        C.Rpc([])
