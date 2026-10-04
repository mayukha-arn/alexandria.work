"""Integration tests: the real Anchor program on a local solana-test-validator.

Skipped unless a validator is reachable and the program is deployed (see solana/README.md):
    solana-test-validator --reset &
    cd solana && anchor build && solana --url localhost program deploy target/deploy/alexandria_audit.so \
        --program-id target/deploy/alexandria_audit-keypair.json
"""
import json
import os
from pathlib import Path

import pytest
import requests
from solders.keypair import Keypair

from app import chain as C

URL = os.getenv("TEST_VALIDATOR_URL", "http://127.0.0.1:8899")
ROOT = Path(__file__).resolve().parent.parent


def _validator_ready() -> bool:
    try:
        body = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                "params": [C.PROGRAM_ID, {"encoding": "base64"}]}
        v = requests.post(URL, json=body, timeout=2).json()["result"]["value"]
        return bool(v and v.get("executable"))
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _validator_ready(), reason="no local validator with the program deployed")


def _fund(kp: Keypair) -> None:
    requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "requestAirdrop",
                             "params": [str(kp.pubkey()), 5_000_000_000]}, timeout=10)
    rpc = C.Rpc([URL])
    import time
    for _ in range(40):
        if rpc.call("getBalance", [str(kp.pubkey())])["value"] > 0:
            return
        time.sleep(0.25)


@pytest.fixture(scope="module")
def authority_chain():
    kp = Keypair.from_bytes(bytes(json.loads((ROOT / ".secrets" / "authority.json").read_text())))
    ch = C.SolanaChain(C.Rpc([URL]), kp)
    ch.ensure_initialized()
    return ch


def h(n: int) -> bytes:
    return bytes([n]) * 32


def test_ledger_authority_is_the_backend_key(authority_chain):
    assert authority_chain.ledger()["authority"] == str(authority_chain.authority.pubkey())


def test_log_event_roundtrip(authority_chain):
    wallet = str(Keypair().pubkey())
    res = authority_chain.log_event(C.sha256("user-1"), C.sha256("ACCESS_CLEARANCE_CHANGED"), h(3), "engineering", 60, wallet)
    e = authority_chain.get_entry(res.index)
    assert e["actor_hash"] == C.sha256("user-1").hex()
    assert e["action_hash"] == C.sha256("ACCESS_CLEARANCE_CHANGED").hex()
    assert e["payload_hash"] == h(3).hex()
    assert (e["dept"], e["required_clearance"], e["approver"]) == ("engineering", 60, wallet)
    assert e["timestamp"] > 0


def test_entries_are_sequential(authority_chain):
    a = authority_chain.log_event(h(1), h(2), h(3), "legal", 0)
    b = authority_chain.log_event(h(1), h(2), h(4), "legal", 0)
    assert b.index == a.index + 1
    assert authority_chain.get_entry(b.index)["approver"] is None


def test_clearance_over_100_is_refused(authority_chain):
    with pytest.raises(C.ProgramError):
        authority_chain.log_event(h(1), h(2), h(3), "x", 101)


def test_non_authority_cannot_write(authority_chain):
    attacker_kp = Keypair()
    _fund(attacker_kp)
    attacker = C.SolanaChain(C.Rpc([URL]), attacker_kp)
    before = authority_chain.ledger()["next_index"]
    with pytest.raises(C.ProgramError):
        attacker.log_event(h(1), h(2), h(3), "x", 0)
    with pytest.raises(C.ProgramError):
        attacker.anchor_document(h(77), None, h(1), h(2), "x", 0)
    assert authority_chain.ledger()["next_index"] == before
    assert authority_chain.get_doc(h(77)) is None


def test_document_anchor_is_write_once(authority_chain):
    doc = C.sha256(os.urandom(16))
    parent = C.sha256(os.urandom(16))
    wallet = str(Keypair().pubkey())
    res = authority_chain.anchor_document(doc, parent, h(9), h(8), "support", 40, wallet)
    rec = authority_chain.get_doc(doc)
    assert rec["doc_hash"] == doc.hex() and rec["parent_hash"] == parent.hex()
    assert rec["approver"] == wallet and rec["entry_index"] == res.index
    assert authority_chain.get_entry(res.index)["payload_hash"] == doc.hex()
    before = authority_chain.ledger()["next_index"]
    with pytest.raises(C.ProgramError):
        authority_chain.anchor_document(doc, parent, h(9), h(8), "support", 40, wallet)
    assert authority_chain.ledger()["next_index"] == before, "a refused anchor leaves no entry"


def test_unanchored_hash_has_no_record(authority_chain):
    assert authority_chain.get_doc(C.sha256(os.urandom(16))) is None


def test_root_document_has_no_parent(authority_chain):
    doc = C.sha256(os.urandom(16))
    authority_chain.anchor_document(doc, None, h(9), h(8), "support", 0)
    assert authority_chain.get_doc(doc)["parent_hash"] is None


def test_outbox_to_real_chain_end_to_end(authority_chain, tmp_path):
    """Store events -> worker -> real program: docs verify, tampering is caught."""
    import hashlib
    import sqlite3

    import ingestion_layer as il
    from app.anchoring import DOC_EVENT, Hasher, anchor_pending
    from app.store import Store
    from app.verify import verify_document

    store = Store(str(tmp_path / "e.db"))
    hasher = Hasher("test-ledger-key")
    text = f"approved policy {os.urandom(8).hex()}"
    doc = hashlib.sha256(text.encode()).hexdigest()
    reg = str(tmp_path / "reg.db")
    il.init_db(reg)
    with sqlite3.connect(reg) as c:
        c.execute("INSERT INTO processed_docs (doc_hash, source, source_type, min_role, uploader_role, status, "
                  "raw_text, minhash, chunk_count, timestamp) VALUES (?, 's', 'web', 'developer', 'senior', 'active', ?, x'00', 1, 't')",
                  (doc, text))

    assert verify_document(reg, authority_chain, doc)["status"] == "unanchored"
    store.record_event("ACCESS_CLEARANCE_CHANGED", "u1", "u2", {"from": 40, "to": 60}, "engineering", 60)
    store.record_event(DOC_EVENT, "senior-1", None, {"doc_hash": doc, "parent_hash": None}, "engineering", 40)
    assert anchor_pending(store, authority_chain, hasher) == {"anchored": 2, "rejected": 0}
    assert verify_document(reg, authority_chain, doc)["status"] == "verified"

    first, second = store.list_events()
    assert authority_chain.get_entry(authority_chain.get_doc(bytes.fromhex(doc))["entry_index"])["payload_hash"] == doc
    assert authority_chain.get_doc(bytes.fromhex(doc))["entry_index"] == authority_chain.ledger()["next_index"] - 1
    assert second["tx_signature"] and first["tx_signature"] != second["tx_signature"]

    with sqlite3.connect(reg) as c:
        c.execute("UPDATE processed_docs SET raw_text = ? WHERE doc_hash = ?", (text + " (edited)", doc))
    assert verify_document(reg, authority_chain, doc)["status"] == "tampered"

    # the unmasked action name must not appear on-chain: only its keyed hash does
    e = authority_chain.get_entry(authority_chain.ledger()["next_index"] - 2)
    assert e["action_hash"] == hasher.mac("ACCESS_CLEARANCE_CHANGED").hex()
    assert e["action_hash"] != hashlib.sha256(b"ACCESS_CLEARANCE_CHANGED").hexdigest()


# ----------------------------------------------------------------- key rotation (on-chain)
def _balance(pubkey):
    # "confirmed", like the client itself: the RPC default (finalized) lags a few seconds behind
    return C.Rpc([URL]).call("getBalance", [str(pubkey), {"commitment": "confirmed"}])["value"]


def test_authority_rotation_hands_over_write_access_and_funds(authority_chain):
    old = authority_chain.authority
    new = Keypair()                                    # brand new, unfunded: the sweep funds it
    index_before = authority_chain.ledger()["next_index"]
    old_before = _balance(old.pubkey())
    assert old_before > 1_000_000
    try:
        authority_chain.rotate_authority(new)
        assert authority_chain.ledger()["authority"] == str(new.pubkey())
        assert _balance(old.pubkey()) < 10_000 and _balance(new.pubkey()) > old_before - 50_000   # funds moved

        stale = C.SolanaChain(C.Rpc([URL]), old)
        with pytest.raises(C.ProgramError):
            stale.log_event(h(1), h(2), h(3), "x", 0)                       # the retired key is locked out
        res = authority_chain.log_event(h(1), h(2), h(3), "x", 0)           # the client now signs with the new key
        assert res.index == index_before                                    # the entry counter carried on
    finally:
        authority_chain.rotate_authority(old)                               # hand it back for the other tests
    assert authority_chain.ledger()["authority"] == str(old.pubkey())
    authority_chain.log_event(h(4), h(5), h(6), "x", 0)


def test_an_attacker_cannot_rotate_the_authority(authority_chain):
    attacker = Keypair()
    _fund(attacker)
    evil = C.SolanaChain(C.Rpc([URL]), attacker)
    with pytest.raises(C.ProgramError):
        evil.rotate_authority(Keypair(), sweep=False)                       # both keys sign, but not the ledger's authority
    assert authority_chain.ledger()["authority"] == str(authority_chain.authority.pubkey())


def test_handover_without_the_new_keys_signature_is_refused(authority_chain):
    """A mistyped address cannot lock the ledger: the new key must prove it exists by signing."""
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import Message
    from solders.transaction import Transaction
    typo = Keypair().pubkey()
    metas = [AccountMeta(authority_chain.authority.pubkey(), is_signer=True, is_writable=False),
             AccountMeta(typo, is_signer=False, is_writable=False),                 # not a signer
             AccountMeta(authority_chain.ledger_pda, is_signer=False, is_writable=True)]
    ix = Instruction(authority_chain.program, C._disc("set_authority"), metas)
    bh = authority_chain.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
    tx = Transaction([authority_chain.authority], Message([ix], authority_chain.authority.pubkey()), Hash.from_string(bh))
    with pytest.raises(C.ProgramError):
        authority_chain.rpc.send_and_confirm(tx)
    assert authority_chain.ledger()["authority"] == str(authority_chain.authority.pubkey())
