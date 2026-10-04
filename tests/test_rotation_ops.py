import json
import os
import stat

import pytest
from solders.keypair import Keypair

from app import chain as C
from app import cli, rotation
from app.config import Settings
from app.main import create_app
from app.store import Store


def settings_in(tmp_path):
    return Settings(db_path=str(tmp_path / "t.db"), secrets_dir=tmp_path / "sec", registry_path=str(tmp_path / "r.db"))


def test_rotate_jwt_and_retire(tmp_path):
    st = settings_in(tmp_path)
    first = st.jwt_keys.current.kid
    out = rotation.rotate_jwt(st)
    assert out["current"] != first and out["keys"] == [out["current"], first]
    out = rotation.rotate_jwt(st, retire=True)
    assert len(out["keys"]) == 1 and len(out["retired"]) == 2


def test_rotate_fernet_reencrypts_then_retires_safely(tmp_path):
    st = settings_in(tmp_path)
    store = create_app(st).state.store
    store.record_event("E", "u", None, {"n": 1})
    out = rotation.rotate_fernet(st, store, retire=True)
    assert out["reencrypted"]["events.payload"] == 1 and out["not_under_current_key"] == 0
    assert len(out["keys"]) == 1 and json.loads(store.list_events()[-1]["payload"]) == {"n": 1}


def test_fernet_retire_is_refused_while_data_still_depends_on_the_old_key(tmp_path, monkeypatch):
    st = settings_in(tmp_path)
    store = create_app(st).state.store
    store.record_event("E", "u", None, {"n": 1})
    monkeypatch.setattr(store, "reencrypt_all", lambda: {})                   # simulate a failed / partial re-encryption
    with pytest.raises(rotation.RotationError, match="still encrypted under an older key"):
        rotation.rotate_fernet(st, store, retire=True)
    assert len(st.fernet_keys.keys) == 2                                      # the old key was NOT dropped
    assert json.loads(store.list_events()[-1]["payload"]) == {"n": 1}         # so nothing was lost


def test_ledger_retire_needs_explicit_confirmation(tmp_path):
    st = settings_in(tmp_path)
    with pytest.raises(rotation.RotationError, match="i-understand"):
        rotation.rotate_ledger(st, retire=True)
    assert len(st.ledger_keys.keys) == 1                                      # refused before rotating anything
    assert len(rotation.rotate_ledger(st)["keys"]) == 2
    assert len(rotation.rotate_ledger(st, retire=True, confirm=True)["keys"]) == 1


# ------------------------------------------------------------- authority key file
def write_key(path, kp):
    path.write_text(json.dumps(list(bytes(kp))))
    os.chmod(path, 0o600)


def test_authority_rotation_swaps_the_key_file_and_archives_the_old_one(tmp_path):
    old = Keypair()
    chain = C.MemoryChain(old)
    path = tmp_path / "authority.json"
    write_key(path, old)
    out = rotation.rotate_authority(chain, path)
    new = Keypair.from_bytes(bytes(json.loads(path.read_text())))
    assert str(new.pubkey()) == out["new_authority"] == chain.ledger()["authority"] != str(old.pubkey())
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    archived = [p for p in tmp_path.iterdir() if ".retired-" in p.name]
    assert len(archived) == 1 and Keypair.from_bytes(bytes(json.loads(archived[0].read_text()))).pubkey() == old.pubkey()
    assert not (tmp_path / "authority.json.next").exists()


def test_failed_handover_changes_nothing(tmp_path):
    old = Keypair()
    chain = C.MemoryChain(old)
    path = tmp_path / "authority.json"
    write_key(path, old)
    chain.fail_next = 1                                                       # chain unreachable
    with pytest.raises(C.ChainError):
        rotation.rotate_authority(chain, path)
    assert Keypair.from_bytes(bytes(json.loads(path.read_text()))).pubkey() == old.pubkey()
    assert not (tmp_path / "authority.json.next").exists() and chain.ledger()["authority"] == str(old.pubkey())


def test_a_crash_after_the_handover_is_recovered_without_losing_the_new_key(tmp_path):
    old, new = Keypair(), Keypair()
    chain = C.MemoryChain(new)                                                # the handover already happened on-chain ...
    path = tmp_path / "authority.json"
    write_key(path, old)
    write_key(tmp_path / "authority.json.next", new)                          # ... but the process died before the file swap
    assert rotation.recover_pending_authority(chain, path) is True
    assert Keypair.from_bytes(bytes(json.loads(path.read_text()))).pubkey() == new.pubkey()
    assert not (tmp_path / "authority.json.next").exists()
    assert rotation.recover_pending_authority(chain, path) is False           # nothing left to do


def test_a_crash_before_the_handover_discards_the_unused_pending_key(tmp_path):
    old, unused = Keypair(), Keypair()
    chain = C.MemoryChain(old)                                                # handover never happened
    path = tmp_path / "authority.json"
    write_key(path, old)
    write_key(tmp_path / "authority.json.next", unused)
    assert rotation.recover_pending_authority(chain, path) is True
    assert Keypair.from_bytes(bytes(json.loads(path.read_text()))).pubkey() == old.pubkey()
    assert not (tmp_path / "authority.json.next").exists()


def test_rotation_recovers_a_pending_key_before_starting_a_new_one(tmp_path):
    old, mid = Keypair(), Keypair()
    chain = C.MemoryChain(mid)
    path = tmp_path / "authority.json"
    write_key(path, old)
    write_key(tmp_path / "authority.json.next", mid)
    out = rotation.rotate_authority(chain, path)                              # first completes the earlier rotation, then rotates again
    assert chain.ledger()["authority"] == out["new_authority"] != str(mid.pubkey())
    assert len([p for p in tmp_path.iterdir() if ".retired-" in p.name]) == 2


# ----------------------------------------------------------------------------- CLI
def test_cli_keys_and_rotate(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALEXANDRIA_SECRETS", str(tmp_path / "sec"))
    monkeypatch.setenv("ALEXANDRIA_DB", str(tmp_path / "t.db"))
    for name in ("ALEXANDRIA_JWT_SECRET", "ALEXANDRIA_FERNET_KEY", "ALEXANDRIA_LEDGER_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["keys"]) == 0
    assert capsys.readouterr().out.count("(current)") == 3
    assert cli.main(["rotate-keys", "jwt"]) == 0
    assert json.loads(capsys.readouterr().out)["keys"].__len__() == 2
    assert cli.main(["rotate-keys", "ledger", "--retire"]) == 1               # needs --i-understand
    assert "i-understand" in capsys.readouterr().err
    assert cli.main(["rotate-keys", "fernet", "--retire"]) == 0
    assert len(json.loads(capsys.readouterr().out)["keys"]) == 1


def test_cli_reports_that_environment_keys_cannot_be_rotated(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALEXANDRIA_SECRETS", str(tmp_path / "sec"))
    monkeypatch.setenv("ALEXANDRIA_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("ALEXANDRIA_JWT_SECRET", "fixed-from-environment-value-1234567890")
    assert cli.main(["rotate-keys", "jwt"]) == 1
    assert "environment variable" in capsys.readouterr().err
    cli.main(["keys"])
    assert "from environment" in capsys.readouterr().out


# ------------------------------------------------------------ generated demo accounts
def test_seed_demo_generate_creates_unique_private_passwords_and_prints_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALEXANDRIA_SECRETS", str(tmp_path / "sec"))
    monkeypatch.setenv("ALEXANDRIA_DB", str(tmp_path / "t.db"))
    for name in ("ALEXANDRIA_JWT_SECRET", "ALEXANDRIA_FERNET_KEY", "ALEXANDRIA_LEDGER_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["seed-demo", "--generate"]) == 0
    out = capsys.readouterr()
    creds = (tmp_path / "sec" / "demo-accounts.txt")
    assert stat.S_IMODE(os.stat(creds).st_mode) == 0o600
    rows = [l.split()[:2] for l in creds.read_text().splitlines() if l and not l.startswith("#")]
    assert len(rows) == 10 and len({pw for _, pw in rows}) == 10 and all(len(pw) >= 16 for _, pw in rows)
    for _, pw in rows:
        assert pw not in out.out and pw not in out.err                      # never printed

    from app import security
    from app.store import Store
    st = Settings()
    store = Store(st.db_path, st.cipher)
    users = {u["username"]: u for u in store.list_users()}
    assert len(users) == 10 and users["developer"]["manager_id"] == users["senior_eng"]["id"]
    assert users["senior_eng"]["manager_id"] == users["admin"]["id"] and users["admin"]["manager_id"] is None
    for name, pw in rows:                                                    # each password opens exactly its own account
        assert security.verify_password(users[name]["password_hash"], pw)
    other = next(n for n, _ in rows if n != rows[0][0])
    assert not security.verify_password(users[other]["password_hash"], rows[0][1])
    assert cli.main(["seed-demo", "--generate"]) == 1                        # never re-seeds an existing organisation
