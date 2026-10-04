# Key rotation runbook

Run these on the server from the repo root (`.venv/bin/python -m app.cli ...`). They work while the
API is running: keyrings reload when their file changes. `keys` lists the key ids in each ring.

| Key | Protects | Rotate | Retire the old key |
|---|---|---|---|
| `jwt` | login sessions | `rotate-keys jwt` | `rotate-keys jwt --retire` after the session lifetime (15 min) has passed; **this ends any session still using the old key** |
| `fernet` | 2FA secrets and audit payloads at rest | `rotate-keys fernet` (re-encrypts everything) | `rotate-keys fernet --retire`: refused unless the new key alone can read every stored value |
| `ledger` | HMAC on the actor/action/payload hashes written on-chain | `rotate-keys ledger` | `--retire --i-understand`: after this, old events' on-chain hashes can no longer be recomputed or matched |
| `authority` | write access to the Solana ledger | `rotate-keys authority` | the old key file is archived as `authority.json.retired-*`; delete it when sure |

## Notes
- **Rotate, then retire later.** Rotation adds a key; retirement removes the old ones. Between the two, old data and
  old sessions keep working.
- **Solana authority.** The new key is written to `authority.json.next` *before* the on-chain handover (one
  transaction signed by both keys; the old key's SOL moves with it). If the process dies mid-way, the next run
  finishes or discards the pending file automatically. The program's *upgrade* authority is separate:
  `solana program set-upgrade-authority ...` (always pass `--url`; the CLI's default is mainnet).
- **Keys from environment variables** (`ALEXANDRIA_JWT_SECRET`, ...) are a single fixed key and cannot be rotated here.
  Prefer the key files in the gitignored `.secrets/`.
- **Back up `.secrets/`** (offline, encrypted) before and after rotating. Losing the Fernet keys makes 2FA secrets and
  audit payloads unreadable; losing the authority key makes the ledger read-only forever.
- Suggested cadence: `jwt` quarterly, `fernet` yearly, `authority` on staff changes or suspected exposure,
  `ledger` rarely (it complicates verification of history).
