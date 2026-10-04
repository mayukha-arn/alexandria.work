"""Solana client for the alexandria_audit program.

Only SHA-256 hashes are sent on-chain. The backend's authority key signs every
write (the program rejects anyone else), and every RPC call goes through a
failover/backoff wrapper so Devnet rate limits don't lose events: callers keep
unanchored events in the outbox and retry later.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from .config import ROOT

log = logging.getLogger("alexandria.chain")

PROGRAM_ID = "Cgnt5epauqrsHCF9BhstkLyUVLjCnGP865bLJEqs22Y6"
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
ZERO32 = bytes(32)
DEVNET = "https://api.devnet.solana.com"
BACKOFF_SECONDS = (2, 4, 8)  # per endpoint: attempt 1, 2, 3


class ChainError(Exception):
    """A transient failure (network, rate limit): worth retrying later."""


class ProgramError(Exception):
    """The chain refused the transaction (e.g. unauthorized, duplicate): retrying won't help."""


# ---------------------------------------------------------------- encoding helpers

def sha256(data: bytes | str) -> bytes:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).digest()


def dept_bytes(dept: Optional[str]) -> bytes:
    raw = (dept or "").encode()[:16]
    return raw.ljust(16, b"\0")


def _disc(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def _pubkey_or_zero(p: Optional[str]) -> bytes:
    return bytes(Pubkey.from_string(p)) if p else ZERO32


@dataclass(frozen=True)
class AnchorResult:
    signature: str
    index: int


# ---------------------------------------------------------------------- RPC layer

class Rpc:
    def __init__(self, urls: Sequence[str], sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 15.0) -> None:
        if not urls:
            raise ValueError("at least one RPC url is required")
        self.urls, self._sleep, self.timeout = list(urls), sleep, timeout

    def call(self, method: str, params: List[Any]) -> Any:
        """Try each endpoint with exponential backoff. JSON-RPC *errors* (a rejected
        transaction) are permanent; network failures and 429/5xx are retried."""
        last: Optional[Exception] = None
        for url in self.urls:
            for attempt, wait in enumerate(BACKOFF_SECONDS, start=1):
                try:
                    r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                      timeout=self.timeout)
                    if r.status_code == 429 or r.status_code >= 500:
                        raise ChainError(f"{url}: HTTP {r.status_code}")
                    body = r.json()
                except (requests.RequestException, ValueError, ChainError) as exc:
                    last = exc
                    log.warning("RPC %s on %s attempt %d failed: %s", method, url, attempt, exc)
                    if attempt < len(BACKOFF_SECONDS):
                        self._sleep(wait)
                    continue
                if "error" in body:
                    raise ProgramError(str(body["error"]))
                return body["result"]
        raise ChainError(f"all Solana RPC endpoints failed for {method}: {last}")

    def account(self, pubkey: Pubkey) -> Optional[bytes]:
        res = self.call("getAccountInfo", [str(pubkey), {"encoding": "base64", "commitment": "confirmed"}])
        val = res.get("value")
        return base64.b64decode(val["data"][0]) if val else None

    def send_and_confirm(self, tx: Transaction, timeout: float = 45.0) -> str:
        raw = base64.b64encode(bytes(tx)).decode()
        sig = self.call("sendTransaction", [raw, {"encoding": "base64", "preflightCommitment": "confirmed"}])
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.call("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])["value"][0]
            if st:
                if st.get("err"):
                    raise ProgramError(f"transaction {sig} failed: {st['err']}")
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return sig
            self._sleep(0.5)
        raise ChainError(f"transaction {sig} not confirmed within {timeout}s")


# ------------------------------------------------------------------ account decoding

def decode_ledger(data: bytes) -> Dict[str, Any]:
    return {"authority": str(Pubkey.from_bytes(data[8:40])), "next_index": struct.unpack_from("<Q", data, 40)[0]}


def decode_entry(data: bytes) -> Dict[str, Any]:
    o = 8
    index = struct.unpack_from("<Q", data, o)[0]; o += 8
    actor, action, payload = data[o:o + 32], data[o + 32:o + 64], data[o + 64:o + 96]; o += 96
    dept = data[o:o + 16].rstrip(b"\0").decode(); o += 16
    clearance = data[o]; o += 1
    approver = Pubkey.from_bytes(data[o:o + 32]); o += 32
    ts = struct.unpack_from("<q", data, o)[0]
    return {"index": index, "actor_hash": actor.hex(), "action_hash": action.hex(),
            "payload_hash": payload.hex(), "dept": dept, "required_clearance": clearance,
            "approver": None if bytes(approver) == ZERO32 else str(approver), "timestamp": ts}


def decode_doc(data: bytes) -> Dict[str, Any]:
    doc, parent = data[8:40], data[40:72]
    approver = Pubkey.from_bytes(data[72:104])
    entry_index, ts = struct.unpack_from("<Qq", data, 104)
    return {"doc_hash": doc.hex(), "parent_hash": None if parent == ZERO32 else parent.hex(),
            "approver": None if bytes(approver) == ZERO32 else str(approver),
            "entry_index": entry_index, "timestamp": ts}


# ------------------------------------------------------------------------ the client

class SolanaChain:
    def __init__(self, rpc: Rpc, authority: Keypair, program_id: str = PROGRAM_ID) -> None:
        self.rpc, self.authority = rpc, authority
        self.program = Pubkey.from_string(program_id)
        self.ledger_pda = Pubkey.find_program_address([b"ledger"], self.program)[0]
        self._lock = threading.Lock()  # entry PDAs are sequential: one writer at a time

    # -- derived addresses
    def entry_pda(self, index: int) -> Pubkey:
        return Pubkey.find_program_address([b"entry", struct.pack("<Q", index)], self.program)[0]

    def doc_pda(self, doc_hash: bytes) -> Pubkey:
        return Pubkey.find_program_address([b"doc", doc_hash], self.program)[0]

    # -- plumbing
    def _send(self, ix: Instruction) -> str:
        bh = self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
        tx = Transaction([self.authority], Message([ix], self.authority.pubkey()), Hash.from_string(bh))
        return self.rpc.send_and_confirm(tx)

    def _metas(self, *extra: tuple[Pubkey, bool]) -> List[AccountMeta]:
        auth = AccountMeta(self.authority.pubkey(), is_signer=True, is_writable=True)
        rest = [AccountMeta(k, is_signer=False, is_writable=w) for k, w in extra]
        return [auth, *rest, AccountMeta(SYSTEM_PROGRAM, is_signer=False, is_writable=False)]

    def ledger(self) -> Optional[Dict[str, Any]]:
        data = self.rpc.account(self.ledger_pda)
        return decode_ledger(data) if data else None

    def ensure_initialized(self) -> None:
        if self.ledger() is not None:
            return
        ix = Instruction(self.program, _disc("initialize"), self._metas((self.ledger_pda, True)))
        self._send(ix)

    # -- key rotation
    def rotate_authority(self, new_authority: Keypair, sweep: bool = True) -> str:
        """Hand the ledger to ``new_authority``, in one atomic transaction signed by both keys.

        With ``sweep`` the old key's remaining SOL moves to the new key in the same transaction,
        so the new key can pay for future entries and nothing is stranded on the retired key.
        On success this client switches to the new key. Returns the transaction signature.
        """
        with self._lock:
            ixs: List[Instruction] = []
            if sweep:
                bal = self.rpc.call("getBalance", [str(self.authority.pubkey()), {"commitment": "confirmed"}])["value"]
                fee = 10_000                                   # two signatures + headroom
                if bal > fee:
                    ixs.append(transfer(TransferParams(from_pubkey=self.authority.pubkey(),
                                                       to_pubkey=new_authority.pubkey(), lamports=bal - fee)))
            metas = [AccountMeta(self.authority.pubkey(), is_signer=True, is_writable=False),
                     AccountMeta(new_authority.pubkey(), is_signer=True, is_writable=False),
                     AccountMeta(self.ledger_pda, is_signer=False, is_writable=True)]
            ixs.append(Instruction(self.program, _disc("set_authority"), metas))
            bh = self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
            tx = Transaction([self.authority, new_authority], Message(ixs, self.authority.pubkey()), Hash.from_string(bh))
            sig = self.rpc.send_and_confirm(tx)
            self.authority = new_authority
            return sig

    # -- writes
    def log_event(self, actor_hash: bytes, action_hash: bytes, payload_hash: bytes, dept: Optional[str],
                  required_clearance: int, approver: Optional[str] = None) -> AnchorResult:
        with self._lock:
            index = self.ledger()["next_index"]  # type: ignore[index]
            data = (_disc("log_audit_event") + actor_hash + action_hash + payload_hash + dept_bytes(dept)
                    + bytes([required_clearance]) + _pubkey_or_zero(approver))
            ix = Instruction(self.program, data, self._metas((self.ledger_pda, True), (self.entry_pda(index), True)))
            return AnchorResult(self._send(ix), index)

    def anchor_document(self, doc_hash: bytes, parent_hash: Optional[bytes], actor_hash: bytes,
                        action_hash: bytes, dept: Optional[str], required_clearance: int,
                        approver: Optional[str] = None) -> AnchorResult:
        with self._lock:
            index = self.ledger()["next_index"]  # type: ignore[index]
            data = (_disc("anchor_document") + doc_hash + (parent_hash or ZERO32) + actor_hash + action_hash
                    + dept_bytes(dept) + bytes([required_clearance]) + _pubkey_or_zero(approver))
            ix = Instruction(self.program, data, self._metas(
                (self.ledger_pda, True), (self.entry_pda(index), True), (self.doc_pda(doc_hash), True)))
            return AnchorResult(self._send(ix), index)

    # -- reads
    def get_entry(self, index: int) -> Optional[Dict[str, Any]]:
        data = self.rpc.account(self.entry_pda(index))
        return decode_entry(data) if data else None

    def get_doc(self, doc_hash: bytes) -> Optional[Dict[str, Any]]:
        data = self.rpc.account(self.doc_pda(doc_hash))
        return decode_doc(data) if data else None


class MemoryChain:
    """In-process stand-in with the same interface and the same rules (write-once
    documents, clearance range). For unit tests; never used when a real chain is configured."""

    def __init__(self, authority: Optional[Keypair] = None) -> None:
        self.entries: List[Dict[str, Any]] = []
        self.docs: Dict[bytes, Dict[str, Any]] = {}
        self.fail_next = 0  # simulate an outage
        self.authority = authority

    def ledger(self) -> Optional[Dict[str, Any]]:
        return {"authority": str(self.authority.pubkey()) if self.authority else None,
                "next_index": len(self.entries)}

    def rotate_authority(self, new_authority: Keypair, sweep: bool = True) -> str:
        self._maybe_fail()
        self.authority = new_authority
        return "mem-rotate"

    def _maybe_fail(self) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise ChainError("simulated outage")

    def _entry(self, actor, action, payload, dept, clearance, approver) -> AnchorResult:
        if not 0 <= clearance <= 100:
            raise ProgramError("InvalidClearance")
        i = len(self.entries)
        self.entries.append({"index": i, "actor_hash": actor.hex(), "action_hash": action.hex(),
                             "payload_hash": payload.hex(), "dept": dept or "", "required_clearance": clearance,
                             "approver": approver, "timestamp": int(time.time())})
        return AnchorResult(f"mem-{i}", i)

    def log_event(self, actor_hash, action_hash, payload_hash, dept, required_clearance, approver=None):
        self._maybe_fail()
        return self._entry(actor_hash, action_hash, payload_hash, dept, required_clearance, approver)

    def anchor_document(self, doc_hash, parent_hash, actor_hash, action_hash, dept, required_clearance, approver=None):
        self._maybe_fail()
        if doc_hash in self.docs:
            raise ProgramError("document already anchored")
        res = self._entry(actor_hash, action_hash, doc_hash, dept, required_clearance, approver)
        self.docs[doc_hash] = {"doc_hash": doc_hash.hex(), "parent_hash": parent_hash.hex() if parent_hash else None,
                               "approver": approver, "entry_index": res.index, "timestamp": int(time.time())}
        return res

    def get_entry(self, index):
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def get_doc(self, doc_hash):
        return self.docs.get(doc_hash)


def chain_from_env() -> Optional[SolanaChain]:
    """``ALEXANDRIA_CHAIN=off`` disables anchoring (events just queue in the outbox).
    Otherwise connects with SOLANA_RPC_URLS (comma-separated; default: public Devnet)."""
    if os.getenv("ALEXANDRIA_CHAIN", "solana").lower() == "off":
        return None
    urls = [u.strip() for u in os.getenv("SOLANA_RPC_URLS", DEVNET).split(",") if u.strip()]
    key_path = Path(os.getenv("ALEXANDRIA_AUTHORITY_KEYPAIR", ROOT / ".secrets" / "authority.json"))
    import json
    authority = Keypair.from_bytes(bytes(json.loads(key_path.read_text())))
    return SolanaChain(Rpc(urls), authority, os.getenv("ALEXANDRIA_PROGRAM_ID", PROGRAM_ID))
