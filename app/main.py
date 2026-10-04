"""Alexandria API: login (password + mandatory TOTP), sessions, wallet linking,
and delegated access management. Roles are re-read from the database on every
request, so a revoked right or lowered clearance takes effect immediately."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

import roles as R
from . import security, wallet
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


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.db_path)
    app = FastAPI(title="Alexandria", docs_url="/docs")
    app.state.settings, app.state.store = settings, store
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

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    return app


def app_factory() -> FastAPI:  # for `uvicorn app.main:app_factory --factory`
    return create_app()
