import importlib.util
import os
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("porkbun_dns", Path(__file__).resolve().parent.parent / "scripts" / "porkbun_dns.py")
pd = importlib.util.module_from_spec(spec)
sys.modules["porkbun_dns"] = pd
spec.loader.exec_module(pd)

D, T, TOKEN = "example.work", "site.azurestaticapps.net", "_tok123"


def rec(id_, name, type_, content):
    return {"id": str(id_), "name": name, "type": type_, "content": content, "ttl": "600"}


PARKING = [rec(1, D, "ALIAS", "pixie.porkbun.com"), rec(2, f"*.{D}", "CNAME", "pixie.porkbun.com")]


def test_plan_replaces_only_parking_records_and_creates_the_three_needed():
    delete, create = pd.plan(PARKING + [rec(3, D, "MX", "mail.example.org")], D, T, TOKEN)
    assert [r["id"] for r in delete] == ["1", "2"]
    assert create == [("TXT", "", TOKEN), ("ALIAS", "", T), ("CNAME", "www", T)]


def test_a_www_parking_cname_is_replaced_too():
    delete, _ = pd.plan([rec(9, f"www.{D}", "CNAME", "pixie.porkbun.com")], D, T, TOKEN)
    assert [r["id"] for r in delete] == ["9"]


def test_it_is_idempotent_once_the_records_exist():
    done = [rec(1, D, "TXT", TOKEN), rec(2, D, "ALIAS", T), rec(3, f"www.{D}", "CNAME", T)]
    assert pd.plan(done, D, T, TOKEN) == ([], [])


def test_unrelated_records_are_never_deleted():
    mine = [rec(1, D, "MX", "mail.example.org"), rec(2, f"api.{D}", "CNAME", "pixie.porkbun.com"), rec(3, D, "TXT", "v=spf1 -all")]
    delete, create = pd.plan(mine, D, T, TOKEN)
    assert delete == []                                    # a CNAME on another subdomain is not mine to delete
    assert ("TXT", "", TOKEN) in create


@pytest.mark.parametrize("existing", [
    rec(1, f"www.{D}", "CNAME", "somewhere-else.example.com"),     # someone's real www
    rec(2, f"www.{D}", "A", "203.0.113.7"),                        # an address record on www would clash with the CNAME
    rec(3, D, "ALIAS", "elsewhere.example.net"),                   # a real alias on the bare domain
])
def test_a_conflicting_non_parking_record_stops_the_plan(existing):
    with pytest.raises(SystemExit, match="clash"):
        pd.plan([existing], D, T, TOKEN)


def test_a_record_that_already_matches_is_not_a_clash():
    assert pd.plan([rec(1, f"www.{D}", "CNAME", T)], D, T, TOKEN)[0] == []


def test_relative_names():
    assert pd.rel(D, D) == "" and pd.rel(f"www.{D}", D) == "www" and pd.rel(f"*.{D}", D) == "*" and pd.rel(f"a.b.{D}", D) == "a.b"


def test_keys_file_must_exist_be_private_and_complete(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        pd.load_keys(tmp_path / "nope")
    f = tmp_path / "keys"
    f.write_text("apikey=a\nsecretapikey=b\n")
    os.chmod(f, 0o644)
    with pytest.raises(SystemExit, match="chmod 600"):
        pd.load_keys(f)
    os.chmod(f, 0o600)
    assert pd.load_keys(f) == {"apikey": "a", "secretapikey": "b"}
    f.write_text("apikey=a\n")
    with pytest.raises(SystemExit, match="secretapikey"):
        pd.load_keys(f)


class FakePorkbun:
    def __init__(self, records):
        self.records, self.calls = list(records), []

    def post(self, url, json=None, timeout=None):
        path = url.split("/v3/")[1]
        self.calls.append((path, {k: v for k, v in json.items() if k not in ("apikey", "secretapikey")}))
        body = {"status": "SUCCESS"}
        if path.startswith("dns/retrieve"):
            body["records"] = self.records
        resp = type("R", (), {"status_code": 200, "json": lambda self_: body})()
        return resp


def run(tmp_path, monkeypatch, records, *extra):
    keys = tmp_path / "keys"
    keys.write_text("apikey=pk_LEAKCHECK_aaa\nsecretapikey=sk_LEAKCHECK_bbb\n")
    os.chmod(keys, 0o600)
    fake = FakePorkbun(records)
    monkeypatch.setattr(pd.requests, "post", fake.post)
    code = pd.main(["--domain", D, "--target", T, "--txt-token", TOKEN, "--keys", str(keys), *extra])
    return code, fake


def test_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    code, fake = run(tmp_path, monkeypatch, PARKING)
    out = capsys.readouterr().out
    assert code == 0 and "Dry run" in out and "delete" in out and "create" in out
    assert [c[0] for c in fake.calls] == ["ping", f"dns/retrieve/{D}"]                  # only reads


def test_apply_deletes_parking_then_creates_the_records_and_never_prints_the_keys(tmp_path, monkeypatch, capsys):
    code, fake = run(tmp_path, monkeypatch, PARKING, "--apply")
    out = capsys.readouterr()
    assert [c[0] for c in fake.calls] == ["ping", f"dns/retrieve/{D}", f"dns/delete/{D}/1", f"dns/delete/{D}/2",
                                          f"dns/create/{D}", f"dns/create/{D}", f"dns/create/{D}"]
    created = [c[1] for c in fake.calls if c[0].startswith("dns/create")]
    assert created == [{"name": "", "type": "TXT", "content": TOKEN, "ttl": "600"},
                       {"name": "", "type": "ALIAS", "content": T, "ttl": "600"},
                       {"name": "www", "type": "CNAME", "content": T, "ttl": "600"}]
    assert "LEAKCHECK" not in out.out + out.err


def test_a_second_run_changes_nothing(tmp_path, monkeypatch, capsys):
    done = [rec(1, D, "TXT", TOKEN), rec(2, D, "ALIAS", T), rec(3, f"www.{D}", "CNAME", T)]
    code, fake = run(tmp_path, monkeypatch, done, "--apply")
    assert "nothing to change" in capsys.readouterr().out
    assert not any(c[0].startswith(("dns/create", "dns/delete")) for c in fake.calls)


def test_a_clash_stops_before_anything_is_written(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="clash"):
        run(tmp_path, monkeypatch, [rec(1, f"www.{D}", "CNAME", "elsewhere.example.com")] + PARKING, "--apply")
