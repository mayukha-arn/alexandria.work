"""Alexandria API: login (password + mandatory TOTP), sessions, wallet linking,
and delegated access management. Roles are re-read from the database on every
request, so a revoked right or lowered clearance takes effect immediately."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import asynccontextmanager, closing, contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import jwt
import requests
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

import roles as R
from . import security, wallet
from .anchoring import Hasher, anchor_pending
from .chain import ChainError
from .documents import DocError, DocumentService
from .messaging import Messaging, MsgError
from .realtime import Hub
from .rag import InputBlocked, ask as rag_ask, ask_stream, prepare as rag_prepare
from .redaction import render_event
from .verify import verify_document
from .config import Settings
from .store import Store


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class ClearanceBody(BaseModel):
    level: int


class RightBody(BaseModel):
    cap: R.Cap
    action: str = Field(pattern="^(grant|revoke)$")


class WalletLinkBody(BaseModel):
    pubkey: str = Field(max_length=64)
    nonce: str = Field(max_length=128)
    signature: str = Field(max_length=200)


class NewUserBody(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(max_length=256)
    role: str


@dataclass
class Auth:
    user: Dict[str, Any]
    claims: Dict[str, Any]

    @property
    def actor(self) -> R.User:
        return Store.as_role_user(self.user)


def _public_user(row: Dict[str, Any]) -> Dict[str, Any]:
    u = Store.as_role_user(row)
    return {
        "id": row["id"], "username": row["username"], "role": row["role"],
        "label": u.role_def.label, "department": u.department, "level": u.role_def.level,
        "persona": u.role_def.persona, "clearance": u.effective_clearance,
        "manager_id": row["manager_id"], "wallet_pubkey": row["wallet_pubkey"],
        "totp_enrolled": bool(row["totp_enrolled"]),
        "capabilities": sorted(c.value for c in u.capabilities),
    }


log = logging.getLogger("alexandria.api")


class ChallengeBody(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")


class DecisionBody(BaseModel):
    approve: bool
    note: Optional[str] = Field(default=None, max_length=1000)
    nonce: str = Field(max_length=128)
    signature: str = Field(max_length=200)


class PingBody(BaseModel):
    to_department: str = Field(max_length=64)
    title: str = Field(max_length=300)
    body: str = Field(max_length=8000)
    min_role: Optional[str] = Field(default=None, max_length=64)


class PingMessageBody(BaseModel):
    kind: str = Field(pattern="^(answer|comment)$")
    body: str = Field(max_length=8000)
    min_role: Optional[str] = Field(default=None, max_length=64)


class ChatBody(BaseModel):
    body: str = Field(max_length=8000)


class DraftBody(BaseModel):
    text: Optional[str] = Field(default=None, max_length=40000)


class AskBody(BaseModel):
    question: str = Field(max_length=4000)


def create_app(settings: Optional[Settings] = None, chain: Any = None,
               anchor_interval: float = 10.0, vectors: Any = None, llm: Any = None) -> FastAPI:
    """``chain`` is a SolanaChain (or MemoryChain in tests). With one, a background loop
    anchors the audit outbox; without one, events simply queue until a chain is configured."""
    settings = settings or Settings()
    store = Store(settings.db_path, settings.cipher)
    hasher = Hasher(settings.ledger_keys)
    stop = threading.Event()
    hub = Hub(lambda uid: (lambda row: Store.as_role_user(row) if row else None)(store.get_user(uid)))
    docs: Optional[DocumentService] = (
        DocumentService(store, vectors, settings.registry_path, settings.junior_new_requires_review,
                        settings.require_approval)
        if vectors is not None else None)

    def anchor_loop() -> None:
        while not stop.wait(anchor_interval):
            try:
                if chain is not None:
                    anchor_pending(store, chain, hasher)
                if docs is not None:
                    docs.reindex_unindexed()
            except Exception:  # never let the loop die
                log.exception("background run failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        thread = None
        if chain is not None or docs is not None:
            thread = threading.Thread(target=anchor_loop, name="anchor-loop", daemon=True)
            thread.start()
        yield
        stop.set()

    messaging = Messaging(store, hub, llm=llm, docs=docs)
    app = FastAPI(title="Alexandria", docs_url="/docs", lifespan=lifespan)
    # Bearer tokens, not cookies, so no credentialed cross-site requests: only the listed web origins may read responses.
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    app.state.settings, app.state.store, app.state.chain, app.state.hasher = settings, store, chain, hasher
    app.state.vectors, app.state.llm, app.state.docs = vectors, llm, docs
    app.state.hub, app.state.messaging = hub, messaging

    @contextmanager
    def msg_errors():
        try:
            yield
        except MsgError as exc:
            raise HTTPException(exc.status, exc.detail)

    @contextmanager
    def doc_errors():
        if docs is None:
            raise HTTPException(503, "the document pipeline is not configured")
        try:
            yield
        except DocError as exc:
            raise HTTPException(exc.status, exc.detail)
    bearer = HTTPBearer(auto_error=False)

    def unauthorized(detail: str = "not authenticated") -> HTTPException:
        return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})

    def token_user(scope: str):
        def dep(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Auth:
            if not creds:
                raise unauthorized()
            try:
                claims = security.decode_token(settings, creds.credentials, scope)
            except jwt.PyJWTError:
                raise unauthorized()
            user = store.get_user(claims["sub"])
            if not user:
                raise unauthorized()
            if scope == "access" and not store.session_active(claims["jti"], user["id"]):
                raise unauthorized("session ended")
            return Auth(user, claims)
        return dep

    def register_failure(user: Dict[str, Any]) -> None:
        n = user["failed_attempts"] + 1
        if n >= settings.lock_after:
            store.update_user(user["id"], failed_attempts=0, locked_until=time.time() + settings.lock_seconds)
            store.record_event("AUTH_ACCOUNT_LOCKED", None, user["id"], {"user": user["id"]}, min_clearance=50)
        else:
            store.update_user(user["id"], failed_attempts=n)

    def check_not_locked(user: Dict[str, Any]) -> None:
        if user["locked_until"] > time.time():
            raise HTTPException(429, "account temporarily locked; try again later")

    def start_session(request: Request, user: Dict[str, Any]) -> Dict[str, Any]:
        token, jti, exp = security.issue_token(settings, user["id"], "access", settings.access_ttl)
        store.create_session(jti, user["id"], request.client.host if request.client else None,
                             request.headers.get("user-agent"), exp)
        store.update_user(user["id"], failed_attempts=0, locked_until=0)
        return {"status": "ok", "access_token": token, "token_type": "bearer",
                "expires_in": settings.access_ttl, "user": _public_user(store.get_user(user["id"]))}

    # ---------------------------------------------------------------- auth
    @app.post("/auth/login")
    def login(body: LoginBody) -> Dict[str, Any]:
        user = store.get_user_by_name(body.username)
        if user:
            check_not_locked(user)
        ok = security.verify_password(user["password_hash"] if user else None, body.password)
        if not user or not ok:
            if user:
                register_failure(user)
            raise HTTPException(401, "invalid credentials")
        if security.needs_rehash(user["password_hash"]):
            store.update_user(user["id"], password_hash=security.hash_password(body.password))
        # A password alone never yields a session: 2FA is mandatory.
        if user["totp_enrolled"]:
            tok, _, _ = security.issue_token(settings, user["id"], "mfa", settings.mfa_ttl)
            return {"status": "mfa_required", "mfa_token": tok, "expires_in": settings.mfa_ttl}
        tok, _, _ = security.issue_token(settings, user["id"], "enroll", settings.enroll_ttl)
        return {"status": "enrollment_required", "enroll_token": tok, "expires_in": settings.enroll_ttl}

    @app.post("/auth/2fa/setup")
    def twofa_setup(auth: Auth = Depends(token_user("enroll"))) -> Dict[str, Any]:
        if auth.user["totp_enrolled"]:
            raise HTTPException(409, "2FA is already enabled")
        secret = security.new_totp_secret()
        store.update_user(auth.user["id"], totp_secret_enc=security.encrypt_secret(settings, secret),
                          totp_last_step=None)
        return {"otpauth_uri": security.totp_uri(settings, secret, auth.user["username"]), "secret": secret}

    @app.post("/auth/2fa/enable")
    def twofa_enable(body: CodeBody, request: Request,
                     auth: Auth = Depends(token_user("enroll"))) -> Dict[str, Any]:
        user = auth.user
        check_not_locked(user)
        if user["totp_enrolled"] or not user["totp_secret_enc"]:
            raise HTTPException(409, "start 2FA setup first")
        secret = security.decrypt_secret(settings, user["totp_secret_enc"])
        step = security.verify_totp(secret, body.code, user["totp_last_step"])
        if step is None:
            register_failure(user)
            raise HTTPException(401, "invalid code")
        store.update_user(user["id"], totp_enrolled=1, totp_last_step=step)
        store.record_event("AUTH_2FA_ENABLED", user["id"], user["id"], {"user": user["id"]})
        return start_session(request, user)

    @app.post("/auth/2fa/verify")
    def twofa_verify(body: CodeBody, request: Request,
                     auth: Auth = Depends(token_user("mfa"))) -> Dict[str, Any]:
        user = auth.user
        check_not_locked(user)
        if not user["totp_enrolled"] or not user["totp_secret_enc"]:
            raise HTTPException(409, "2FA is not set up")
        secret = security.decrypt_secret(settings, user["totp_secret_enc"])
        step = security.verify_totp(secret, body.code, user["totp_last_step"])
        if step is None:
            register_failure(user)
            raise HTTPException(401, "invalid code")
        store.update_user(user["id"], totp_last_step=step)
        return start_session(request, user)

    @app.post("/auth/refresh")
    def refresh(auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        """Keep a live session going without redoing 2FA: a fresh short-lived token for the SAME
        session (so logout/revocation still ends both), until the session's absolute limit."""
        jti, now = auth.claims["jti"], time.time()
        started = store.session_created_at(jti) or now
        remaining = started + settings.session_max_seconds - now
        if remaining <= 0:
            raise unauthorized("session too old; sign in again")
        ttl = int(min(settings.access_ttl, remaining))
        token, _, exp = security.issue_token(settings, auth.user["id"], "access", ttl, jti=jti)
        store.extend_session(jti, exp)
        return {"access_token": token, "token_type": "bearer", "expires_in": ttl}

    @app.get("/meta")
    def meta(_: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        """What forms need to offer choices: departments, classification levels, roles, rights."""
        return {"departments": R.DEPARTMENTS, "classifications": R.CLASSIFICATIONS,
                "roles": [{"name": r.name, "label": r.label, "department": r.department, "level": r.level,
                           "clearance": r.clearance, "persona": r.persona} for r in R.ROLES.values()],
                "capabilities": [c.value for c in R.Cap]}

    @app.get("/auth/me")
    def me(auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        return _public_user(auth.user)

    @app.post("/auth/logout")
    def logout(auth: Auth = Depends(token_user("access"))) -> Dict[str, str]:
        store.revoke_session(auth.claims["jti"], auth.user["id"])
        return {"status": "logged_out"}

    @app.get("/auth/sessions")
    def sessions(auth: Auth = Depends(token_user("access"))) -> List[Dict[str, Any]]:
        return [{**s, "current": s["jti"] == auth.claims["jti"]} for s in store.list_sessions(auth.user["id"])]

    @app.delete("/auth/sessions/{jti}")
    def revoke_session(jti: str, auth: Auth = Depends(token_user("access"))) -> Dict[str, str]:
        if not store.revoke_session(jti, auth.user["id"]):
            raise HTTPException(404, "no such active session")
        return {"status": "revoked"}

    # -------------------------------------------------------------- wallet
    @app.post("/wallet/challenge")
    def wallet_challenge(auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        nonce, message, expires = wallet.link_challenge(auth.user["id"], settings.challenge_ttl)
        store.create_challenge(nonce, auth.user["id"], message, expires)
        return {"nonce": nonce, "message": message, "expires_at": int(expires)}

    @app.post("/wallet/link")
    def wallet_link(body: WalletLinkBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        user = auth.user
        if user["wallet_pubkey"]:
            raise HTTPException(409, "a wallet is already linked; your manager or an admin must reset it")
        if not wallet.is_valid_pubkey(body.pubkey):
            raise HTTPException(422, "not a valid Solana public key")
        message = store.consume_challenge(body.nonce, user["id"])
        if message is None or not wallet.verify_signature(body.pubkey, message, body.signature):
            raise HTTPException(401, "wallet signature check failed")
        if any(u["wallet_pubkey"] == body.pubkey for u in store.list_users()):
            raise HTTPException(409, "that wallet is linked to another account")
        store.update_user(user["id"], wallet_pubkey=body.pubkey)
        store.record_event("WALLET_LINKED", user["id"], user["id"], {"user": user["id"], "pubkey": body.pubkey})
        return {"status": "linked", "wallet_pubkey": body.pubkey}

    # ------------------------------------------------------ org / access
    def load_target(user_id: str) -> Dict[str, Any]:
        row = store.get_user(user_id)
        if not row:
            raise HTTPException(404, "user not found")
        return row

    @app.get("/users")
    def list_users(auth: Auth = Depends(token_user("access"))) -> List[Dict[str, Any]]:
        """You see yourself and the people you manage (admins see everyone)."""
        actor = auth.actor
        return [_public_user(r) for r in store.list_users()
                if r["id"] == actor.id or R.manages(actor, Store.as_role_user(r))]

    @app.post("/users", status_code=201)
    def create_user(body: NewUserBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        actor = auth.actor
        if not R.can(actor, R.Cap.MANAGE_ACCESS):
            raise HTTPException(403, "not permitted")
        role = R.ROLES.get(body.role)
        if not role:
            raise HTTPException(422, "unknown role")
        if role.clearance > actor.effective_clearance or (role.level == "admin" and actor.role_def.level != "admin"):
            raise HTTPException(403, "cannot create a user more privileged than yourself")
        if len(body.password) < settings.min_password_length:
            raise HTTPException(422, f"password must be at least {settings.min_password_length} characters")
        if store.get_user_by_name(body.username):
            raise HTTPException(409, "username taken")
        new = store.create_user(body.username, security.hash_password(body.password), body.role, manager_id=actor.id)
        store.record_event("USER_CREATED", actor.id, new["id"], {"role": body.role, "manager": actor.id},
                           department=role.department, min_clearance=role.clearance)
        return _public_user(new)

    @app.put("/users/{user_id}/clearance")
    def set_clearance(user_id: str, body: ClearanceBody,
                      auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        target = load_target(user_id)
        actor, tgt = auth.actor, Store.as_role_user(target)
        if not R.can_set_clearance(actor, tgt, body.level):
            raise HTTPException(403, "not permitted")
        before = tgt.effective_clearance
        store.update_user(user_id, clearance=body.level)
        store.record_event("ACCESS_CLEARANCE_CHANGED", actor.id, user_id,
                           {"from": before, "to": body.level}, department=tgt.department,
                           min_clearance=max(before, body.level))
        return _public_user(store.get_user(user_id))

    @app.post("/users/{user_id}/rights")
    def change_right(user_id: str, body: RightBody,
                     auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        target = load_target(user_id)
        actor, tgt = auth.actor, Store.as_role_user(target)
        try:
            new = (R.grant_right if body.action == "grant" else R.revoke_right)(actor, tgt, body.cap)
        except PermissionError:
            raise HTTPException(403, "not permitted")
        store.update_user(user_id, granted=[c.value for c in new.granted], revoked=[c.value for c in new.revoked])
        store.record_event(f"ACCESS_RIGHT_{body.action.upper()}ED", actor.id, user_id,
                           {"cap": body.cap.value}, department=tgt.department,
                           min_clearance=tgt.effective_clearance)
        return _public_user(store.get_user(user_id))

    @app.delete("/users/{user_id}/wallet")
    def reset_wallet(user_id: str, auth: Auth = Depends(token_user("access"))) -> Dict[str, str]:
        target = load_target(user_id)
        actor, tgt = auth.actor, Store.as_role_user(target)
        if not (R.can(actor, R.Cap.MANAGE_ACCESS) and R.manages(actor, tgt)):
            raise HTTPException(403, "not permitted")
        store.update_user(user_id, wallet_pubkey=None)
        store.record_event("WALLET_RESET", actor.id, user_id, {"user": user_id}, department=tgt.department,
                           min_clearance=tgt.effective_clearance)
        return {"status": "reset"}

    # ----------------------------------------------------------- chat & pings
    @app.get("/channels")
    def channels(auth: Auth = Depends(token_user("access"))) -> List[Dict[str, Any]]:
        return messaging.channels_for(auth.actor)

    @app.get("/channels/{channel_id}/messages")
    def channel_messages(channel_id: str, limit: int = 50, before: Optional[int] = None,
                         auth: Auth = Depends(token_user("access"))) -> List[Dict[str, Any]]:
        with msg_errors():
            return messaging.chat_history(auth.actor, channel_id, limit, before)

    @app.post("/channels/{channel_id}/messages", status_code=201)
    def post_channel_message(channel_id: str, body: ChatBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with msg_errors():
            return messaging.post_chat(auth.actor, channel_id, body.body)

    @app.post("/pings", status_code=201)
    def create_ping(body: PingBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        """Ask a department, not a person. Any qualified member can pick it up."""
        with msg_errors():
            return messaging.create_ping(auth.actor, body.to_department, body.title, body.body, body.min_role)

    @app.get("/pings")
    def list_pings(box: str = "inbox", include_closed: bool = False, limit: int = 50,
                   auth: Auth = Depends(token_user("access"))) -> List[Dict[str, Any]]:
        with msg_errors():
            return messaging.list_pings(auth.actor, box, include_closed, limit)

    @app.get("/pings/{ping_id}")
    def get_ping(ping_id: int, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with msg_errors():
            return messaging.get_ping(auth.actor, ping_id)

    @app.post("/pings/{ping_id}/claim")
    def claim_ping(ping_id: int, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with msg_errors():
            return messaging.claim(auth.actor, ping_id)

    @app.post("/pings/{ping_id}/release")
    def release_ping(ping_id: int, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with msg_errors():
            return messaging.release(auth.actor, ping_id)

    @app.post("/pings/{ping_id}/messages", status_code=201)
    def post_ping_message(ping_id: int, body: PingMessageBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with msg_errors():
            return messaging.post_message(auth.actor, ping_id, body.kind, body.body, body.min_role)

    @app.post("/pings/{ping_id}/resolve")
    def resolve_ping(ping_id: int, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with msg_errors():
            return messaging.resolve(auth.actor, ping_id)

    @app.post("/pings/{ping_id}/close")
    def close_ping(ping_id: int, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with msg_errors():
            return messaging.close(auth.actor, ping_id)

    @app.get("/pings/{ping_id}/draft-preview")
    def ping_draft_preview(ping_id: int, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with msg_errors():
            return messaging.draft_preview(auth.actor, ping_id)

    @app.post("/pings/{ping_id}/draft", status_code=202)
    def ping_draft(ping_id: int, body: DraftBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        """Stage the (edited) draft for review: a different person must approve it."""
        with msg_errors():
            return messaging.stage_draft(auth.actor, auth.user["wallet_pubkey"], ping_id, body.text)

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        """Live updates. The first message must be {"type":"auth","token":"<access token>"}
        (a header cannot be set from a browser WebSocket, and a URL would end up in logs).
        The server then pushes events built for *this* user's current rights; it closes the
        socket when the session is revoked or its token expires."""
        await ws.accept()
        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=5)
            claims = security.decode_token(settings, first.get("token", ""), "access")
        except Exception:
            await ws.close(code=4401)
            return
        user = await run_in_threadpool(store.get_user, claims["sub"])
        if not user or not await run_in_threadpool(store.session_active, claims["jti"], user["id"]):
            await ws.close(code=4401)
            return
        conn = hub.register(user["id"], claims["jti"], float(claims["exp"]))
        await ws.send_json({"type": "ready", "user": _public_user(user)})

        async def drain_client() -> None:      # notices disconnects; clients do not send commands
            try:
                while True:
                    await ws.receive_text()
            except WebSocketDisconnect:
                pass

        async def push() -> None:
            while True:
                wait = min(30.0, conn.expires_at - time.time())
                if wait <= 0:
                    await ws.close(code=4401)
                    return
                try:
                    msg = await asyncio.wait_for(conn.queue.get(), timeout=wait)
                    # A logged-out or revoked session must not receive anything more, not even for 30s.
                    if not await run_in_threadpool(store.session_active, conn.jti, conn.user_id):
                        await ws.close(code=4401)
                        return
                    await ws.send_json(msg)
                except asyncio.TimeoutError:
                    if not await run_in_threadpool(store.session_active, conn.jti, conn.user_id):
                        await ws.close(code=4401)      # logged out or revoked elsewhere
                        return
                    await ws.send_json({"type": "heartbeat"})

        tasks = [asyncio.create_task(drain_client()), asyncio.create_task(push())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            pass
        finally:
            for t in tasks:
                t.cancel()
            hub.unregister(conn)

    # ------------------------------------------------------------ documents
    @app.post("/documents")
    def upload_document(file: Optional[UploadFile] = File(None), url: Optional[str] = Form(None),
                        min_role: str = Form("internal"), department: Optional[str] = Form(None),
                        auth: Auth = Depends(token_user("access"))) -> JSONResponse:
        """Upload a PDF or give a web URL. Returns 201 (live), 202 (waiting for senior review),
        or 200 (duplicate)."""
        with doc_errors():
            if bool(file) == bool(url):
                raise HTTPException(422, "provide exactly one of: file, url")
            if url:
                res = docs.ingest(auth.actor, auth.user["wallet_pubkey"], "web", url, url, min_role, department)
            else:
                head = file.file.read(5)
                if head != b"%PDF-":
                    raise HTTPException(422, "only PDF files are supported")
                fd, tmp = tempfile.mkstemp(suffix=".pdf")
                try:
                    with os.fdopen(fd, "wb") as out:
                        out.write(head)
                        size = len(head)
                        while chunk := file.file.read(1024 * 1024):
                            size += len(chunk)
                            if size > settings.max_upload_bytes:
                                raise HTTPException(413, "file too large")
                            out.write(chunk)
                    label = "file:" + os.path.basename(file.filename or "upload.pdf")
                    res = docs.ingest(auth.actor, auth.user["wallet_pubkey"], "pdf", tmp, label, min_role, department)
                finally:
                    os.unlink(tmp)
            return JSONResponse(res, status_code=res.get("status_code", 200))

    @app.get("/documents")
    def list_documents(auth: Auth = Depends(token_user("access"))) -> List[Dict[str, Any]]:
        with doc_errors():
            return docs.list_documents(auth.actor)

    @app.get("/documents/pending")
    def pending_documents(auth: Auth = Depends(token_user("access"))) -> List[Dict[str, Any]]:
        """The senior review queue: diffs and risk flags, limited to what you may approve."""
        with doc_errors():
            return docs.pending(auth.actor)

    @app.post("/documents/pending/{staged_id}/challenge")
    def review_challenge(staged_id: int, body: ChallengeBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with doc_errors():
            return docs.challenge(auth.actor, auth.user["wallet_pubkey"], staged_id, body.action)

    @app.post("/documents/pending/{staged_id}/decision")
    def review_decision(staged_id: int, body: DecisionBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with doc_errors():
            return docs.decide(auth.actor, auth.user["wallet_pubkey"], staged_id, body.approve,
                               body.note, body.nonce, body.signature)

    @app.delete("/documents/{doc_hash}")
    def purge_document(doc_hash: str, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        with doc_errors():
            return docs.purge(auth.actor, doc_hash)

    # ----------------------------------------------------------------- ask
    @app.post("/ask")
    def ask(body: AskBody, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        actor = auth.actor
        if not R.can(actor, R.Cap.ASK):
            raise HTTPException(403, "not permitted")
        if vectors is None or llm is None:
            raise HTTPException(503, "the knowledge engine is not configured")
        try:
            a = rag_ask(question=body.question, user=actor, store=store, vectors=vectors, llm=llm,
                        registry_path=settings.registry_path, chain=chain)
        except InputBlocked:
            # The reasons are audited but not echoed: telling an attacker which rule fired helps them iterate.
            raise HTTPException(400, "Your question was blocked by the security policy.")
        except requests.RequestException:
            raise HTTPException(503, "the language model is unavailable; try again shortly")
        return {"answer": a.answer, "sources": a.sources, "grounded": a.grounded, "warnings": a.warnings,
                "persona": a.persona, "metrics": a.metrics}

    @app.post("/ask/stream")
    def ask_streaming(body: AskBody, auth: Auth = Depends(token_user("access"))) -> StreamingResponse:
        """Same pipeline as /ask, delivered as server-sent events: ``meta``, ``token``*, ``done``.
        Read it with fetch() and a stream reader (EventSource cannot send the auth header)."""
        actor = auth.actor
        if not R.can(actor, R.Cap.ASK):
            raise HTTPException(403, "not permitted")
        if vectors is None or llm is None:
            raise HTTPException(503, "the knowledge engine is not configured")
        try:   # everything that can fail before the first byte is sent maps to a normal HTTP error
            prep = rag_prepare(question=body.question, user=actor, store=store, vectors=vectors,
                               registry_path=settings.registry_path, chain=chain)
        except InputBlocked:
            raise HTTPException(400, "Your question was blocked by the security policy.")

        def events():
            import json as _json
            for ev in ask_stream(prep, llm, store):
                yield f"event: {ev['event']}\ndata: {_json.dumps(ev['data'])}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # --------------------------------------------------------------- audit
    @app.get("/audit/ledger")
    def audit_ledger(limit: int = 100, auth: Auth = Depends(token_user("access"))) -> List[Dict[str, Any]]:
        """Newest first. Fields above the viewer's clearance come back as redaction markers."""
        if not R.can(auth.actor, R.Cap.VIEW_AUDIT):
            raise HTTPException(403, "not permitted")
        clearance = auth.actor.effective_clearance
        events = store.list_events()[-max(1, min(limit, 500)):]
        return [render_event(e, clearance, hasher) for e in reversed(events)]

    @app.get("/audit/status")
    def audit_status(auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        if not R.can(auth.actor, R.Cap.VIEW_AUDIT):
            raise HTTPException(403, "not permitted")
        evs = store.list_events()
        return {"chain_configured": chain is not None, "total": len(evs),
                "anchored": sum(e["anchored_at"] is not None for e in evs),
                "queued": sum(e["anchored_at"] is None and not e["anchor_error"] for e in evs),
                "rejected": sum(bool(e["anchor_error"]) for e in evs)}

    @app.get("/audit/verify/{doc_hash}")
    def audit_verify(doc_hash: str, auth: Auth = Depends(token_user("access"))) -> Dict[str, Any]:
        """Tamper check for a document the caller is cleared to read."""
        if chain is None:
            raise HTTPException(503, "blockchain verification is not configured")
        with closing(sqlite3.connect(settings.registry_path)) as conn:
            row = conn.execute("SELECT min_role FROM processed_docs WHERE doc_hash = ?", (doc_hash,)).fetchone()
        if not row or not R.can_read(auth.actor, row[0]):
            raise HTTPException(404, "document not found")  # same answer whether hidden or absent
        try:
            return verify_document(settings.registry_path, chain, doc_hash)
        except ChainError:
            raise HTTPException(503, "blockchain temporarily unreachable; try again")

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    return app


def app_factory() -> FastAPI:  # for `uvicorn app.main:app_factory --factory`
    return create_app()
