# alexandria_audit (Layer 4)

Anchor program that stores Alexandria's tamper-evident audit trail on Solana.
**Only hashes go on-chain, never document text.** Plaintext stays in the organisation's encrypted local database.

| Account | PDA seeds | Purpose |
|---|---|---|
| `Ledger` | `["ledger"]` | Singleton. Holds the `authority` (the backend key) and the next entry index. |
| `AuditEntry` | `["entry", index_le]` | One immutable record: keyed actor/action/payload hashes, department, required clearance, approver wallet, timestamp. |
| `DocRecord` | `["doc", doc_hash]` | Proof a document version was approved. **Its existence is the tamper check.** |

Instructions: `initialize`, `log_audit_event`, `anchor_document`. The last two are authority-only.

## Differences from the PRD's sample program (and why)
- **Authority check.** The PRD's `log_audit_event` let *any* signer write any entry, so the log could be forged.
- **Fixed-size hash fields** (`[u8; 32]`) instead of `String`; the PRD's `space` math was too small for 64-char hex strings and would have failed.
- **PDAs** instead of anonymous accounts, so "is this hash approved?" is a direct lookup.
- **Write-once documents**: anchoring the same hash twice fails.
- **Keyed hashes (HMAC)** for actor/action/payload (done in `app/anchoring.py`). Plain SHA-256 of a small value set, such as the action names, can be reversed by hashing every candidate, which would make clearance redaction meaningless. Document hashes stay plain SHA-256 so anyone holding a document can verify it.
- Clearance is validated `<= 100` on-chain.

On-chain data is public. Clearance-based redaction is enforced by the API (`app/redaction.py`); the chain only ever holds opaque hashes.

## Build, test, deploy
```bash
export PATH="$HOME/.cargo/bin:$HOME/.avm/bin:$HOME/.local/share/solana/install/active_release/bin:$PATH"
cd solana && anchor build

# local validator (offline demos / tests)
solana-test-validator --reset &
solana --url localhost --keypair ../.secrets/authority.json airdrop 50
solana --url localhost --keypair ../.secrets/authority.json program deploy \
  target/deploy/alexandria_audit.so --program-id target/deploy/alexandria_audit-keypair.json
cd .. && .venv/bin/python -m pytest tests/test_chain_validator.py

# Devnet: always pass --url explicitly. The CLI's default config points at mainnet.
solana --url devnet --keypair ../.secrets/authority.json program deploy ...
```
Keypairs live in the gitignored `.secrets/` (`authority.json` = backend signer, `program-keypair.json` = program/upgrade identity). Back them up; anyone with `authority.json` can write to the ledger.

Backend config: `SOLANA_RPC_URLS` (comma-separated, tried in order with 2s/4s/8s backoff; default public Devnet), `ALEXANDRIA_AUTHORITY_KEYPAIR`, `ALEXANDRIA_PROGRAM_ID`, `ALEXANDRIA_CHAIN=off` to queue without anchoring.
