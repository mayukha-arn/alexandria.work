import json
import os
import stat

import pytest

from app import azure_secrets as AS
from app.azure_secrets import KeyVault, VaultError, pull, push

URL = "https://alexandria-kv.vault.azure.net"


class FakeVaultHttp:
    """A tiny fake of the Key Vault REST API: records requests and holds secrets in memory."""

    def __init__(self, secrets=None, status=None):
        self.secrets, self.status, self.requests = dict(secrets or {}), status, []

    def request(self, method, url, params=None, headers=None, timeout=None, json=None):
        self.requests.append((method, url, dict(headers or {}), json))
        name = url.rsplit("/", 1)[1]
        R = lambda code, body=None: type("R", (), {"status_code": code, "json": lambda s: body})()
        if self.status:
            return R(self.status)
        assert params == {"api-version": "7.4"}
        if method == "GET":
            return R(200, {"value": self.secrets[name]}) if name in self.secrets else R(404)
        self.secrets[name] = json["value"]
        return R(200, {})


def vault(**kw):
    http = FakeVaultHttp(**kw)
    return KeyVault(URL, token=lambda: "TOKEN-VALUE", http=http), http


def test_get_set_and_missing():
    v, http = vault(secrets={"jwt-keys": '{"keys": []}'})
    assert v.get("jwt-keys") == '{"keys": []}' and v.get("nope") is None and v.exists("jwt-keys")
    v.set("fernet-keys", "abc")
    assert http.secrets["fernet-keys"] == "abc"
    assert all(h["Authorization"] == "Bearer TOKEN-VALUE" for _, _, h, _ in http.requests)


def test_errors_never_contain_tokens_or_secret_values():
    v, _ = vault(status=403)
    with pytest.raises(VaultError) as e1:
        v.get("jwt-keys")
    with pytest.raises(VaultError) as e2:
        v.set("jwt-keys", "SUPER-SECRET-VALUE")
    for e in (e1.value, e2.value):
        assert "TOKEN-VALUE" not in str(e) and "SUPER-SECRET-VALUE" not in str(e) and "403" in str(e)


def test_unreachable_vault_is_a_clear_error():
    class Boom:
        def request(self, *a, **k):
            raise ConnectionError("dns failure")
    with pytest.raises(VaultError, match="unreachable"):
        KeyVault(URL, token=lambda: "t", http=Boom()).get("x")


def test_pull_writes_private_files_and_skips_what_the_vault_lacks(tmp_path):
    v, _ = vault(secrets={"jwt-keys": '{"keys": [1]}', "authority-keypair": "[1,2,3]"})
    dest = tmp_path / "run"
    assert sorted(pull(v, dest)) == ["authority.json", "jwt.keys.json"]
    assert (dest / "jwt.keys.json").read_text() == '{"keys": [1]}'
    assert stat.S_IMODE(os.stat(dest / "authority.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o700
    assert not (dest / "fernet.keys.json").exists()


def test_push_sends_only_what_changed(tmp_path):
    v, http = vault(secrets={"jwt-keys": "same", "fernet-keys": "old"})
    (tmp_path / "jwt.keys.json").write_text("same")
    (tmp_path / "fernet.keys.json").write_text("new")
    (tmp_path / "ledger.keys.json").write_text("fresh")
    assert sorted(push(v, tmp_path)) == ["fernet-keys", "ledger-keys"]
    assert http.secrets == {"jwt-keys": "same", "fernet-keys": "new", "ledger-keys": "fresh"}
    assert push(v, tmp_path) == []                                       # idempotent: no new vault versions


def test_files_that_are_not_secrets_are_never_pushed(tmp_path):
    v, http = vault()
    (tmp_path / "demo-accounts.txt").write_text("admin  hunter2")
    (tmp_path / "program-keypair.json").write_text("[9,9,9]")
    assert push(v, tmp_path) == [] and http.secrets == {}


def test_round_trip_through_the_real_keyrings(tmp_path):
    """Keyring files survive vault -> disk intact, and a rotation made on the VM flows back to the vault."""
    from app.config import Settings
    src = Settings(db_path=str(tmp_path / "a.db"), secrets_dir=tmp_path / "src")
    v, _ = vault()
    assert sorted(push(v, tmp_path / "src")) == ["fernet-keys", "jwt-keys", "ledger-keys"]
    pull(v, tmp_path / "run")
    vm = Settings(db_path=str(tmp_path / "b.db"), secrets_dir=tmp_path / "run")
    assert vm.jwt_keys.current.kid == src.jwt_keys.current.kid                           # same keys on the VM
    assert vm.cipher.decrypt(src.cipher.encrypt(b"2fa-secret")) == b"2fa-secret"        # and they interoperate
    vm.jwt_keys.rotate()
    assert push(v, tmp_path / "run") == ["jwt-keys"]
    assert json.loads(v.get("jwt-keys"))["keys"][0]["kid"] == vm.jwt_keys.current.kid


def test_managed_identity_token_request_shape(monkeypatch):
    seen = {}
    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers)
        return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"access_token": "AT"}})()
    monkeypatch.setattr(AS.requests, "get", fake_get)
    monkeypatch.delenv("AZURE_ACCESS_TOKEN", raising=False)
    assert AS.managed_identity_token() == "AT"
    assert seen["url"].startswith("http://169.254.169.254/") and seen["headers"] == {"Metadata": "true"}
    assert seen["params"]["resource"] == "https://vault.azure.net"
    monkeypatch.setattr(AS.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("not on azure")))
    with pytest.raises(VaultError, match="managed-identity"):
        AS.managed_identity_token()


def test_cli_status_prints_presence_but_never_values(tmp_path, monkeypatch, capsys):
    v, _ = vault(secrets={"jwt-keys": "TOP-SECRET-VALUE"})
    monkeypatch.setattr(AS, "KeyVault", lambda url: v)
    assert AS.main(["status", "--vault", URL]) == 0
    out = capsys.readouterr().out
    assert "jwt-keys" in out and "present" in out and "missing" in out and "TOP-SECRET-VALUE" not in out
    assert AS.main(["pull"]) == 2                                             # no vault configured


def test_rotation_cli_updates_the_vault_when_one_is_configured(tmp_path, monkeypatch, capsys):
    from app import cli
    v, http = vault()
    monkeypatch.setenv("ALEXANDRIA_SECRETS", str(tmp_path / "sec"))
    monkeypatch.setenv("ALEXANDRIA_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("ALEXANDRIA_KEYVAULT_URL", URL)
    for n in ("ALEXANDRIA_JWT_SECRET", "ALEXANDRIA_FERNET_KEY", "ALEXANDRIA_LEDGER_KEY"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(AS, "KeyVault", lambda url: v)
    assert cli.main(["rotate-keys", "jwt"]) == 0
    assert "key vault updated" in capsys.readouterr().out and "jwt-keys" in http.secrets
