#!/usr/bin/env python3
"""Point a Porkbun domain at the Azure Static Web App, through Porkbun's API.

Dry run by default: it prints what it WOULD change. Add --apply to do it.

    scripts/porkbun_dns.py --txt-token _75ffmm...            # show the plan
    scripts/porkbun_dns.py --txt-token _75ffmm... --apply     # make the changes

Credentials are read from a private file, never from the command line or chat:
    ~/.porkbun-keys          (mode 600, two lines)
        apikey=pk1_...
        secretapikey=sk1_...
Create the keys at Porkbun: Account -> API Access, then open the domain's Details and switch
"API ACCESS" on for it. Delete the keys afterwards if you like.

It only ever deletes Porkbun's own parking records (an ALIAS / CNAME at the bare domain, `www` or `*`
that point at pixie.porkbun.com). Anything else on the domain is left alone.
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

API = "https://api.porkbun.com/api/json/v3"
DEFAULT_DOMAIN = "the-only-one-who-knew-this-left-in-2019.work"
DEFAULT_TARGET = "purple-hill-0222aa91e.1.azurestaticapps.net"
PARKING_HOST = "pixie.porkbun.com"


def load_keys(path: Path) -> Dict[str, str]:
    if not path.exists():
        raise SystemExit(f"{path} not found. See the top of this script for how to create it.")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"{path} is readable by other users; run: chmod 600 {path}")
    keys = dict(line.strip().split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    if not keys.get("apikey") or not keys.get("secretapikey"):
        raise SystemExit(f"{path} must contain apikey=... and secretapikey=... lines")
    return {"apikey": keys["apikey"], "secretapikey": keys["secretapikey"]}


def call(path: str, keys: Dict[str, str], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    r = requests.post(f"{API}/{path}", json={**keys, **(extra or {})}, timeout=30)
    try:
        body = r.json()
    except ValueError:
        raise SystemExit(f"Porkbun returned something unexpected (HTTP {r.status_code})")
    if body.get("status") != "SUCCESS":
        raise SystemExit(f"Porkbun refused: {body.get('message', body)}")
    return body


def rel(name: str, domain: str) -> str:
    """Record name relative to the domain ('' for the bare domain)."""
    name = name.rstrip(".")
    return "" if name == domain else name[: -len(domain) - 1] if name.endswith("." + domain) else name


def plan(records: List[Dict[str, Any]], domain: str, target: str, token: str) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str, str]]]:
    """(records to delete, records to create). Pure function, so it can be tested without any network."""
    delete = [r for r in records
              if r["type"] in ("ALIAS", "CNAME") and r["content"].rstrip(".").lower() == PARKING_HOST
              and rel(r["name"], domain) in ("", "www", "*")]
    survivors = [r for r in records if r not in delete]
    have = {(r["type"], rel(r["name"], domain), r["content"].rstrip(".").strip('"')) for r in survivors}
    want = [("TXT", "", token), ("ALIAS", "", target), ("CNAME", "www", target)]
    # A name can hold only one ALIAS/CNAME, and `www` can't hold a CNAME next to any address/alias record.
    # Anything already there that isn't what we want (and isn't parking) is somebody's real record: stop.
    clash = [r for r in survivors
             if (r["type"] == "ALIAS" and rel(r["name"], domain) == "" and r["content"].rstrip(".") != target)
             or (rel(r["name"], domain) == "www" and r["type"] in ("CNAME", "A", "AAAA", "ALIAS")
                 and (r["type"], "www", r["content"].rstrip(".")) != ("CNAME", "www", target))]
    if clash:
        names = ", ".join(f"{r['type']} {rel(r['name'], domain) or '@'} -> {r['content']}" for r in clash)
        raise SystemExit(f"Existing record(s) would clash and are not parking records, so I won't touch them: {names}")
    return delete, [w for w in want if w not in have]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", default=DEFAULT_DOMAIN)
    ap.add_argument("--target", default=DEFAULT_TARGET, help="the Azure Static Web App hostname")
    ap.add_argument("--txt-token", required=True, help="Azure's domain-ownership token")
    ap.add_argument("--keys", default=str(Path.home() / ".porkbun-keys"))
    ap.add_argument("--apply", action="store_true", help="actually change DNS (default: only show the plan)")
    args = ap.parse_args(argv)

    keys = load_keys(Path(args.keys))
    call("ping", keys)                                           # fails clearly if the keys are wrong
    records = call(f"dns/retrieve/{args.domain}", keys).get("records", [])
    delete, create = plan(records, args.domain, args.target, args.txt_token)

    print(f"{args.domain}: {len(records)} existing record(s)")
    for r in delete:
        print(f"  delete  {r['type']:<6} {rel(r['name'], args.domain) or '@':<4} -> {r['content']}   (Porkbun parking)")
    for t, n, c in create:
        print(f"  create  {t:<6} {n or '@':<4} -> {c}")
    if not delete and not create:
        print("  nothing to change: the records are already in place")
    if not args.apply:
        print("\nDry run. Nothing was changed. Re-run with --apply to make these changes.")
        return 0
    for r in delete:
        call(f"dns/delete/{args.domain}/{r['id']}", keys)
    for t, n, c in create:
        call(f"dns/create/{args.domain}", keys, {"name": n, "type": t, "content": c, "ttl": "600"})
    print("\nDone. DNS changes usually show up within a few minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
