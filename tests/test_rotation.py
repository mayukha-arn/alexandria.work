import json
import os
import stat
import time

import jwt
import pyotp
import pytest
from fastapi.testclient import TestClient

from app import chain as C
from app import security
from app.anchoring import Hasher, anchor_pending
from app.config import Settings
from app.keyring import Cipher, Keyring, KeyringError, make_fernet_key
from app.main import create_app
from app.redaction import render_event
from tests.test_api import H, PW, enroll, login, make_user


def ring(tmp_path, name="k.json", env=None):
    return Keyring(tmp_path / name, lambda: os.urandom(24).hex(), env)


# ------------------------------------------------------------------------- keyring
def test_rotate_adds_a_new_current_key_and_keeps_the_old_one(tmp_path):
    r = ring(tmp_path)
    first = r.current
    new = r.rotate()
    assert r.current.kid == new.kid != first.kid
    assert [k.kid for k in r.keys] == [new.kid, first.kid]
    assert r.get(first.kid).secret == first.secret
    assert r.retire_old() == [first.kid] and [k.kid for k in r.keys] == [new.kid] and r.get(first.kid) is None


def test_key_files_are_owner_only_and_survive_a_restart(tmp_path):
    r = ring(tmp_path / "secrets")
    r.rotate()
    assert stat.S_IMODE(os.stat(r.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(r.path.parent).st_mode) == 0o700
    again = Keyring(r.path, lambda: "x")
    assert [k.kid for k in again.keys] == [k.kid for k in r.keys]


def test_a_running_process_sees_a_rotation_made_elsewhere(tmp_path):
    server_view = ring(tmp_path)
    before = server_view.current.kid
    time.sleep(0.01)
    Keyring(server_view.path, lambda: os.urandom(24).hex()).rotate()      # e.g. the CLI
    assert server_view.current.kid != before


def test_legacy_single_key_files_are_migrated_not_copied(tmp_path):
    (tmp_path / "jwt.key").write_text("old-secret-value\n")
    r = Keyring(tmp_path / "jwt.keys.json", lambda: "new", legacy_file=tmp_path / "jwt.key")
    assert r.current.secret == "old-secret-value" and r.current.kid == "legacy"
    assert not (tmp_path / "jwt.key").exists()


def test_environment_keys_work_but_cannot_be_rotated(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY_ENV", "from-env")
    r = Keyring(tmp_path / "e.json", lambda: "x", env="TEST_KEY_ENV")
    assert r.current.secret == "from-env" and not r.rotatable
    with pytest.raises(KeyringError):
        r.rotate()
    with pytest.raises(KeyringError):
        r.retire_old()


def test_cipher_reads_old_data_after_rotation_and_rotate_moves_it_forward(tmp_path):
    r = Keyring(tmp_path / "f.json", make_fernet_key)
    c = Cipher(r)
    token = c.encrypt(b"secret")
    r.rotate()
    assert c.decrypt(token) == b"secret"                       # old ciphertext still readable
    assert not c.decryptable_with_current_only(token)
    moved = c.rotate(token)
    assert c.decryptable_with_current_only(moved) and c.decrypt(moved) == b"secret"


# ------------------------------------------------------------------------------ JWT
def mk_settings(tmp_path):
    return Settings(db_path=str(tmp_path / "t.db"), secrets_dir=tmp_path / "sec", registry_path=str(tmp_path / "r.db"))


def test_tokens_survive_rotation_until_the_old_key_is_retired(tmp_path):
    st = mk_settings(tmp_path)
    old, _, _ = security.issue_token(st, "u1", "access", 600)
    assert jwt.get_unverified_header(old)["kid"] == st.jwt_keys.current.kid
    old_kid = st.jwt_keys.current.kid
    st.jwt_keys.rotate()
    new, _, _ = security.issue_token(st, "u1", "access", 600)
    assert jwt.get_unverified_header(new)["kid"] != old_kid
    assert security.decode_token(st, old, "access")["sub"] == "u1"           # grace period
    assert security.decode_token(st, new, "access")["sub"] == "u1"
    st.jwt_keys.retire_old()
    with pytest.raises(jwt.PyJWTError):
        security.decode_token(st, old, "access")                              # now it is dead
    assert security.decode_token(st, new, "access")["sub"] == "u1"


def test_unknown_or_forged_key_ids_are_rejected(tmp_path):
    st = mk_settings(tmp_path)
    now = int(time.time())
    claims = {"iss": "alexandria", "sub": "u1", "scope": "access", "jti": "j", "iat": now, "exp": now + 600}
    for headers in ({"kid": "no-such-key"}, {"kid": st.jwt_keys.current.kid}):
        bad = jwt.encode(claims, "attacker-secret-attacker-secret-1234", algorithm="HS256", headers=headers)
        with pytest.raises(jwt.PyJWTError):
            security.decode_token(st, bad, "access")


def test_tokens_from_before_key_ids_existed_still_verify(tmp_path):
    st = mk_settings(tmp_path)
    now = int(time.time())
    claims = {"iss": "alexandria", "sub": "u1", "scope": "access", "jti": "j", "iat": now, "exp": now + 600}
    legacy = jwt.encode(claims, st.jwt_keys.current.secret, algorithm="HS256")   # no kid header
    assert security.decode_token(st, legacy, "access")["sub"] == "u1"


def test_live_sessions_continue_across_a_rotation_and_end_at_retirement(tmp_path):
    st = mk_settings(tmp_path)
    app = create_app(st)
    client, store = TestClient(app), app.state.store
    make_user(store, "alice", "developer")
    access, _, totp = enroll(client, "alice")
    st.jwt_keys.rotate()                                                       # CLI rotation, server keeps running
    assert client.get("/auth/me", headers=H(access)).status_code == 200
    fresh = login(client, "alice", totp)
    assert jwt.get_unverified_header(fresh)["kid"] == st.jwt_keys.current.kid
    st.jwt_keys.retire_old()
    assert client.get("/auth/me", headers=H(access)).status_code == 401        # signed by the retired key
    assert client.get("/auth/me", headers=H(fresh)).status_code == 200


# --------------------------------------------------------------- at-rest encryption
def test_data_stays_readable_through_reencryption_and_retirement(tmp_path):
    st = mk_settings(tmp_path)
    app = create_app(st)
    client, store = TestClient(app), app.state.store
    make_user(store, "alice", "developer")
    _, secret, totp = enroll(client, "alice")
    store.record_event("E", "u1", None, {"n": 1})
    n_events = len(store.list_events())                                       # includes the 2FA-enabled event
    assert store.count_not_under_current_key() == 0

    st.fernet_keys.rotate()
    assert store.count_not_under_current_key() == 1 + n_events                # the 2FA secret + every event
    assert json.loads(store.list_events()[-1]["payload"]) == {"n": 1}         # old data still readable
    assert login(client, "alice", totp)                                       # 2FA still works mid-rotation

    done = store.reencrypt_all()
    assert done == {"users.totp_secret_enc": 1, "events.payload": n_events}
    assert store.count_not_under_current_key() == 0
    assert store.reencrypt_all() == {"users.totp_secret_enc": 0, "events.payload": 0}      # idempotent
    st.fernet_keys.retire_old()
    assert json.loads(store.list_events()[-1]["payload"]) == {"n": 1}
    row = store.get_user_by_name("alice")
    assert security.decrypt_secret(st, row["totp_secret_enc"]) == secret


def test_retiring_too_early_is_detectable_before_it_loses_data(tmp_path):
    st = mk_settings(tmp_path)
    store = create_app(st).state.store
    store.record_event("E", "u1", None, {"n": 1})
    st.fernet_keys.rotate()
    assert store.count_not_under_current_key() > 0       # the CLI refuses to retire while this is non-zero


# --------------------------------------------------------------------- ledger hashes
def test_old_events_remain_verifiable_after_a_ledger_key_rotation(tmp_path):
    st = mk_settings(tmp_path)
    store = create_app(st).state.store
    hasher, chain = Hasher(st.ledger_keys), C.MemoryChain()
    k1 = hasher.current_kid
    store.record_event("A", "alice", None, {"x": 1}, department="engineering", min_clearance=0)
    anchor_pending(store, chain, hasher)
    st.ledger_keys.rotate()
    k2 = hasher.current_kid
    store.record_event("B", "alice", None, {"x": 2}, department="engineering", min_clearance=0)
    anchor_pending(store, chain, hasher)

    first, second = store.list_events()
    assert (first["anchor_kid"], second["anchor_kid"]) == (k1, k2) and k1 != k2
    # what the UI shows for each event equals what is actually on-chain, whichever key was used
    for ev, entry in zip((first, second), chain.entries):
        assert render_event(ev, 100, hasher)["actor_hash"] == entry["actor_hash"]
    assert chain.entries[0]["actor_hash"] != chain.entries[1]["actor_hash"]    # same actor, different key

    st.ledger_keys.retire_old()                                                 # old key gone: no crash, no wrong hash
    assert render_event(first, 100, hasher)["actor_hash"] is None
    assert render_event(second, 100, hasher)["actor_hash"] == chain.entries[1]["actor_hash"]
    with pytest.raises(KeyError):
        hasher.mac("alice", k1)


def test_a_queued_batch_uses_one_key_even_if_rotation_lands_mid_run(tmp_path):
    st = mk_settings(tmp_path)
    store = create_app(st).state.store
    hasher, chain = Hasher(st.ledger_keys), C.MemoryChain()
    for n in range(3):
        store.record_event("E", "alice", None, {"n": n})
    real = chain.log_event
    calls = []

    def log_and_rotate(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            st.ledger_keys.rotate()                   # rotation lands after the first event was anchored
        return real(*a, **kw)

    chain.log_event = log_and_rotate
    anchor_pending(store, chain, hasher)
    assert len({e["anchor_kid"] for e in store.list_events()}) == 1
