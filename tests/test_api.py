import time

import base58
import jwt
import pyotp
import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app import security
from app.config import Settings
from app.main import create_app

PW = "correct horse battery"


@pytest.fixture
def env(tmp_path):
    settings = Settings(db_path=str(tmp_path / "t.db"), secrets_dir=tmp_path / "sec")
    app = create_app(settings)
    return TestClient(app), app.state.store, settings


def make_user(store, name, role, manager=None, clearance=None):
    return store.create_user(name, security.hash_password(PW), role, manager_id=manager, clearance=clearance)


def H(token):
    return {"Authorization": f"Bearer {token}"}


def enroll(client, name, password=PW):
    """First login: password -> enroll token -> setup -> enable. Returns (access, secret, totp)."""
    r = client.post("/auth/login", json={"username": name, "password": password})
    assert r.json()["status"] == "enrollment_required", r.text
    et = r.json()["enroll_token"]
    secret = client.post("/auth/2fa/setup", headers=H(et)).json()["secret"]
    totp = pyotp.TOTP(secret)
    r = client.post("/auth/2fa/enable", headers=H(et), json={"code": totp.now()})
    assert r.status_code == 200, r.text
    return r.json()["access_token"], secret, totp


def login(client, name, totp, step_offset=1):
    r = client.post("/auth/login", json={"username": name, "password": PW})
    assert r.json()["status"] == "mfa_required"
    # a later 30s step than the one already used (replay protection forbids reuse)
    code = totp.at(time.time() + 30 * step_offset)
    r = client.post("/auth/2fa/verify", headers=H(r.json()["mfa_token"]), json={"code": code})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_password_alone_never_gives_a_session(env):
    client, store, _ = env
    make_user(store, "alice", "developer")
    r = client.post("/auth/login", json={"username": "alice", "password": PW})
    assert r.json()["status"] == "enrollment_required"
    assert "access_token" not in r.json()
    assert client.get("/auth/me", headers=H(r.json()["enroll_token"])).status_code == 401


def test_enroll_then_login_with_totp(env):
    client, store, _ = env
    make_user(store, "alice", "developer")
    access, _, totp = enroll(client, "alice")
    me = client.get("/auth/me", headers=H(access)).json()
    assert me["role"] == "developer" and me["clearance"] == 40 and me["department"] == "engineering"
    assert me["totp_enrolled"] is True and "ask" in me["capabilities"] and "approve_doc" not in me["capabilities"]
    assert client.get("/auth/me", headers=H(login(client, "alice", totp))).status_code == 200


def test_totp_secret_encrypted_at_rest(env):
    client, store, _ = env
    make_user(store, "alice", "developer")
    _, secret, _ = enroll(client, "alice")
    stored = store.get_user_by_name("alice")["totp_secret_enc"]
    assert stored and secret not in stored


def test_totp_code_cannot_be_replayed(env):
    client, store, _ = env
    make_user(store, "alice", "developer")
    _, _, totp = enroll(client, "alice")
    r = client.post("/auth/login", json={"username": "alice", "password": PW})
    # the code used to enable 2FA is the current step: reusing it must fail
    bad = client.post("/auth/2fa/verify", headers=H(r.json()["mfa_token"]), json={"code": totp.now()})
    assert bad.status_code == 401
    ok = client.post("/auth/2fa/verify", headers=H(r.json()["mfa_token"]), json={"code": totp.at(time.time() + 30)})
    assert ok.status_code == 200
    again = client.post("/auth/2fa/verify", headers=H(r.json()["mfa_token"]), json={"code": totp.at(time.time() + 30)})
    assert again.status_code == 401


def test_bad_credentials_are_indistinguishable(env):
    client, store, _ = env
    make_user(store, "alice", "developer")
    a = client.post("/auth/login", json={"username": "alice", "password": "wrong-password-1"})
    b = client.post("/auth/login", json={"username": "nobody", "password": "wrong-password-1"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_lockout_after_repeated_failures(env):
    client, store, settings = env
    make_user(store, "alice", "developer")
    for _ in range(settings.lock_after):
        assert client.post("/auth/login", json={"username": "alice", "password": "nope-nope-nope"}).status_code == 401
    r = client.post("/auth/login", json={"username": "alice", "password": PW})
    assert r.status_code == 429                      # correct password still refused while locked
    assert any(e["kind"] == "AUTH_ACCOUNT_LOCKED" for e in store.list_events())


def test_wrong_totp_counts_toward_lockout(env):
    client, store, settings = env
    make_user(store, "alice", "developer")
    _, _, totp = enroll(client, "alice")
    mt = client.post("/auth/login", json={"username": "alice", "password": PW}).json()["mfa_token"]
    for _ in range(settings.lock_after):
        assert client.post("/auth/2fa/verify", headers=H(mt), json={"code": "000000"}).status_code == 401
    assert client.post("/auth/2fa/verify", headers=H(mt), json={"code": totp.at(time.time() + 30)}).status_code == 429


def test_token_scopes_are_enforced(env):
    client, store, settings = env
    u = make_user(store, "alice", "developer")
    access, _, totp = enroll(client, "alice")
    mfa = client.post("/auth/login", json={"username": "alice", "password": PW}).json()["mfa_token"]
    assert client.get("/auth/me", headers=H(mfa)).status_code == 401           # mfa token is not a session
    assert client.post("/auth/2fa/verify", headers=H(access), json={"code": "123456"}).status_code == 401
    forged = jwt.encode({"iss": "alexandria", "sub": u["id"], "scope": "access", "jti": "x",
                         "iat": int(time.time()), "exp": int(time.time()) + 600}, "wrong-secret", algorithm="HS256")
    assert client.get("/auth/me", headers=H(forged)).status_code == 401
    expired, _, _ = security.issue_token(settings, u["id"], "access", -10)
    assert client.get("/auth/me", headers=H(expired)).status_code == 401
    assert client.get("/auth/me").status_code == 401


def test_logout_and_session_manager(env):
    client, store, _ = env
    make_user(store, "alice", "developer")
    first, _, totp = enroll(client, "alice")
    second = login(client, "alice", totp)
    sessions = client.get("/auth/sessions", headers=H(second)).json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert client.delete(f"/auth/sessions/{other['jti']}", headers=H(second)).status_code == 200
    assert client.get("/auth/me", headers=H(first)).status_code == 401         # revoked remotely
    assert client.post("/auth/logout", headers=H(second)).status_code == 200
    assert client.get("/auth/me", headers=H(second)).status_code == 401


def sign(sk, message):
    return base58.b58encode(sk.sign(message.encode()).signature).decode()


def pub(sk):
    return base58.b58encode(bytes(sk.verify_key)).decode()


def test_wallet_link_requires_a_valid_signature(env):
    client, store, _ = env
    make_user(store, "alice", "developer")
    access, _, _ = enroll(client, "alice")
    sk, other = SigningKey.generate(), SigningKey.generate()

    ch = client.post("/wallet/challenge", headers=H(access)).json()
    wrong = client.post("/wallet/link", headers=H(access), json={
        "pubkey": pub(sk), "nonce": ch["nonce"], "signature": sign(other, ch["message"])})
    assert wrong.status_code == 401                      # signed by a different key
    # the challenge is single use, even after a failed attempt
    again = client.post("/wallet/link", headers=H(access), json={
        "pubkey": pub(sk), "nonce": ch["nonce"], "signature": sign(sk, ch["message"])})
    assert again.status_code == 401

    ch = client.post("/wallet/challenge", headers=H(access)).json()
    ok = client.post("/wallet/link", headers=H(access), json={
        "pubkey": pub(sk), "nonce": ch["nonce"], "signature": sign(sk, ch["message"])})
    assert ok.status_code == 200 and client.get("/auth/me", headers=H(access)).json()["wallet_pubkey"] == pub(sk)
    assert any(e["kind"] == "WALLET_LINKED" for e in store.list_events())

    ch = client.post("/wallet/challenge", headers=H(access)).json()
    swap = client.post("/wallet/link", headers=H(access), json={
        "pubkey": pub(other), "nonce": ch["nonce"], "signature": sign(other, ch["message"])})
    assert swap.status_code == 409                       # no silent wallet swap


def test_wallet_garbage_and_duplicate_rejected(env):
    client, store, _ = env
    make_user(store, "alice", "developer")
    make_user(store, "bob", "developer")
    a, _, _ = enroll(client, "alice")
    b, _, _ = enroll(client, "bob")
    sk = SigningKey.generate()
    ch = client.post("/wallet/challenge", headers=H(a)).json()
    assert client.post("/wallet/link", headers=H(a), json={
        "pubkey": "not-a-key", "nonce": ch["nonce"], "signature": "x"}).status_code == 422
    ch = client.post("/wallet/challenge", headers=H(a)).json()
    client.post("/wallet/link", headers=H(a), json={"pubkey": pub(sk), "nonce": ch["nonce"], "signature": sign(sk, ch["message"])})
    ch = client.post("/wallet/challenge", headers=H(b)).json()
    dup = client.post("/wallet/link", headers=H(b), json={"pubkey": pub(sk), "nonce": ch["nonce"], "signature": sign(sk, ch["message"])})
    assert dup.status_code == 409
    # alice's challenge cannot be used by bob
    ch = client.post("/wallet/challenge", headers=H(a)).json()
    sk2 = SigningKey.generate()
    steal = client.post("/wallet/link", headers=H(b), json={"pubkey": pub(sk2), "nonce": ch["nonce"], "signature": sign(sk2, ch["message"])})
    assert steal.status_code == 401


def org(client, store):
    admin = make_user(store, "root", "security_admin")
    boss = make_user(store, "boss", "senior_eng", manager=admin["id"])
    dev = make_user(store, "dev", "developer", manager=boss["id"])
    peer = make_user(store, "peer", "developer", manager=admin["id"])
    tokens = {n: enroll(client, n)[0] for n in ("root", "boss", "dev", "peer")}
    return admin, boss, dev, peer, tokens


def test_manager_sets_direct_report_clearance(env):
    client, store, _ = env
    _, boss, dev, peer, t = org(client, store)
    r = client.put(f"/users/{dev['id']}/clearance", headers=H(t["boss"]), json={"level": 55})
    assert r.status_code == 200 and r.json()["clearance"] == 55
    assert client.get("/auth/me", headers=H(t["dev"])).json()["clearance"] == 55   # live, no re-login
    assert client.put(f"/users/{peer['id']}/clearance", headers=H(t["boss"]), json={"level": 55}).status_code == 403
    assert client.put(f"/users/{dev['id']}/clearance", headers=H(t["boss"]), json={"level": 71}).status_code == 403
    assert client.put(f"/users/{boss['id']}/clearance", headers=H(t["dev"]), json={"level": 0}).status_code == 403
    assert client.put(f"/users/{dev['id']}/clearance", headers=H(t["dev"]), json={"level": 100}).status_code == 403
    assert client.put(f"/users/{peer['id']}/clearance", headers=H(t["root"]), json={"level": 10}).status_code == 200
    kinds = [e["kind"] for e in store.list_events()]
    assert kinds.count("ACCESS_CLEARANCE_CHANGED") == 2


def test_grant_and_revoke_take_effect_immediately(env):
    client, store, _ = env
    _, boss, dev, _, t = org(client, store)
    assert "approve_doc" not in client.get("/auth/me", headers=H(t["dev"])).json()["capabilities"]
    r = client.post(f"/users/{dev['id']}/rights", headers=H(t["boss"]), json={"cap": "approve_doc", "action": "grant"})
    assert r.status_code == 200
    assert "approve_doc" in client.get("/auth/me", headers=H(t["dev"])).json()["capabilities"]
    client.post(f"/users/{dev['id']}/rights", headers=H(t["boss"]), json={"cap": "approve_doc", "action": "revoke"})
    assert "approve_doc" not in client.get("/auth/me", headers=H(t["dev"])).json()["capabilities"]
    # cannot hand out a right you do not hold
    assert client.post(f"/users/{dev['id']}/rights", headers=H(t["boss"]),
                       json={"cap": "purge_document", "action": "grant"}).status_code == 403


def test_users_list_is_scoped_to_your_reports(env):
    client, store, _ = env
    _, _, _, _, t = org(client, store)
    names = lambda tok: {u["username"] for u in client.get("/users", headers=H(tok)).json()}
    assert names(t["boss"]) == {"boss", "dev"}
    assert names(t["dev"]) == {"dev"}
    assert names(t["root"]) == {"root", "boss", "dev", "peer"}


def test_create_user_cannot_exceed_creator(env):
    client, store, _ = env
    _, _, _, _, t = org(client, store)
    ok = client.post("/users", headers=H(t["boss"]), json={"username": "newdev", "password": PW, "role": "developer"})
    assert ok.status_code == 201 and ok.json()["manager_id"] is not None
    assert client.post("/users", headers=H(t["boss"]), json={"username": "x1x", "password": PW, "role": "security_admin"}).status_code == 403
    assert client.post("/users", headers=H(t["boss"]), json={"username": "x2x", "password": PW, "role": "legal_counsel"}).status_code == 403
    assert client.post("/users", headers=H(t["dev"]), json={"username": "x3x", "password": PW, "role": "support_rep"}).status_code == 403
    assert client.post("/users", headers=H(t["boss"]), json={"username": "x4x", "password": "short", "role": "developer"}).status_code == 422


def test_manager_can_reset_report_wallet(env):
    client, store, _ = env
    _, _, dev, peer, t = org(client, store)
    sk = SigningKey.generate()
    ch = client.post("/wallet/challenge", headers=H(t["dev"])).json()
    client.post("/wallet/link", headers=H(t["dev"]), json={"pubkey": pub(sk), "nonce": ch["nonce"], "signature": sign(sk, ch["message"])})
    assert client.delete(f"/users/{dev['id']}/wallet", headers=H(t["peer"])).status_code == 403
    assert client.delete(f"/users/{dev['id']}/wallet", headers=H(t["boss"])).status_code == 200
    assert client.get("/auth/me", headers=H(t["dev"])).json()["wallet_pubkey"] is None


def test_events_have_hashes_for_anchoring(env):
    client, store, _ = env
    _, _, dev, _, t = org(client, store)
    client.put(f"/users/{dev['id']}/clearance", headers=H(t["boss"]), json={"level": 60})
    ev = [e for e in store.list_events(only_unanchored=True) if e["kind"] == "ACCESS_CLEARANCE_CHANGED"][0]
    import hashlib
    assert ev["payload_hash"] == hashlib.sha256(ev["payload"].encode()).hexdigest()
    assert ev["min_clearance"] == 60
