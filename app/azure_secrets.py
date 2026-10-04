"""Azure Key Vault as the home of Alexandria's secrets.

On the Azure VM the service has a *managed identity*: Azure hands it short-lived tokens, so there is no
password or key to configure. At start-up ``pull`` copies the secrets into a RAM-backed directory
(systemd's RuntimeDirectory, /run/alexandria), so nothing secret is ever written to the VM's disk and it all
disappears when the service stops. ``push`` sends the current files back (used after a key rotation).

    python -m app.azure_secrets pull      # vault -> $ALEXANDRIA_SECRETS
    python -m app.azure_secrets push      # $ALEXANDRIA_SECRETS -> vault (only what changed)
    python -m app.azure_secrets status    # what the vault holds, without printing any value

Secret names allow only letters, digits and dashes, hence the mapping below. Needs ALEXANDRIA_KEYVAULT_URL.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

API_VERSION = "7.4"
IMDS = "http://169.254.169.254/metadata/identity/oauth2/token"

# Key Vault secret name  ->  file in the secrets directory
SECRET_FILES: Dict[str, str] = {
    "jwt-keys": "jwt.keys.json",
    "fernet-keys": "fernet.keys.json",
    "ledger-keys": "ledger.keys.json",
    "authority-keypair": "authority.json",
    "appinsights-env": "appinsights.env",       # one line: APPLICATIONINSIGHTS_CONNECTION_STRING=...
    "llm-env": "llm.env",                       # one line: ANTHROPIC_API_KEY=... (hosted model key)
}


class VaultError(Exception):
    """A Key Vault problem. Messages never contain secret values or tokens."""


def managed_identity_token(resource: str = "https://vault.azure.net", timeout: float = 5.0) -> str:
    env = os.getenv("AZURE_ACCESS_TOKEN")           # for development / tests; on Azure the identity is used
    if env:
        return env
    try:
        r = requests.get(IMDS, params={"api-version": "2018-02-01", "resource": resource},
                         headers={"Metadata": "true"}, timeout=timeout)
        r.raise_for_status()
        return r.json()["access_token"]
    except Exception as exc:
        raise VaultError("could not get a managed-identity token (is this running on an Azure VM with an identity?)") from exc


class KeyVault:
    def __init__(self, vault_url: str, token: Optional[Callable[[], str]] = None, http=requests) -> None:
        self.url = vault_url.rstrip("/")
        self._token, self._http = token or managed_identity_token, http

    def _call(self, method: str, name: str, **kw):
        try:
            r = self._http.request(method, f"{self.url}/secrets/{name}", params={"api-version": API_VERSION},
                                   headers={"Authorization": f"Bearer {self._token()}"}, timeout=15, **kw)
        except VaultError:
            raise
        except Exception as exc:
            raise VaultError(f"Key Vault unreachable ({type(exc).__name__})") from exc
        return r

    def get(self, name: str) -> Optional[str]:
        r = self._call("GET", name)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise VaultError(f"Key Vault refused to read '{name}' (HTTP {r.status_code})")
        return r.json()["value"]

    def set(self, name: str, value: str) -> None:
        r = self._call("PUT", name, json={"value": value, "contentType": "application/json"})
        if r.status_code >= 400:
            raise VaultError(f"Key Vault refused to write '{name}' (HTTP {r.status_code})")

    def exists(self, name: str) -> bool:
        return self.get(name) is not None


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)                         # atomic: a reader never sees a half-written key file
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def pull(vault: KeyVault, dest: Path) -> List[str]:
    """Vault -> directory. Returns the files written; secrets the vault does not hold yet are skipped."""
    written = []
    for name, filename in SECRET_FILES.items():
        value = vault.get(name)
        if value is not None:
            _write_private(Path(dest) / filename, value)
            written.append(filename)
    return written


def push(vault: KeyVault, src: Path) -> List[str]:
    """Directory -> vault, only what differs (a new vault version per real change). Returns names updated."""
    updated = []
    for name, filename in SECRET_FILES.items():
        path = Path(src) / filename
        if not path.exists():
            continue
        local = path.read_text()
        if vault.get(name) != local:
            vault.set(name, local)
            updated.append(name)
    return updated


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="app.azure_secrets")
    ap.add_argument("command", choices=["pull", "push", "status"])
    ap.add_argument("--vault", default=os.getenv("ALEXANDRIA_KEYVAULT_URL"))
    ap.add_argument("--dir", default=os.getenv("ALEXANDRIA_SECRETS", "/run/alexandria"))
    args = ap.parse_args(argv)
    if not args.vault:
        print("set ALEXANDRIA_KEYVAULT_URL (or pass --vault)", file=sys.stderr)
        return 2
    vault = KeyVault(args.vault)
    try:
        if args.command == "pull":
            files = pull(vault, Path(args.dir))
            print(f"pulled {len(files)} secret file(s) into {args.dir}: {', '.join(files) or 'none yet'}")
        elif args.command == "push":
            names = push(vault, Path(args.dir))
            print(f"updated {len(names)} secret(s) in the vault: {', '.join(names) or 'nothing changed'}")
        else:
            for name in SECRET_FILES:
                print(f"{name:<20} {'present' if vault.exists(name) else 'missing'}")
    except VaultError as exc:
        print(f"key vault error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
