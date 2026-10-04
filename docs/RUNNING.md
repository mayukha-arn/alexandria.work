# Running Alexandria locally

For local operation with Ollama, the app and models run on one machine. You need: the repo's Python environment
(`.venv`), Ollama with two models, and Node for the web app. See `INSTALLED.md` for what was installed.

```bash
# 0. one-time: the models (about 5 GB; remove later with `ollama rm`)
~/Applications/Ollama.app/Contents/Resources/ollama serve &        # skip if the Ollama app is already running
ollama pull llama3:8b && ollama pull nomic-embed-text

# 1. the API  (http://127.0.0.1:8088; not 8000, which the local Solana validator uses)
cd ~/alexandria
.venv/bin/python -m app.cli seed-demo            # first run only: 10 demo users, asks for one shared password (12+ chars)
.venv/bin/uvicorn app.server:build_app --factory --port 8088

# 2. the web app  (http://localhost:3000)
cd web && npm install --cache /tmp/npm-cache     # first run only
npm run dev
```

Sign in as any seeded user (`admin`, `senior_eng`, `senior_eng2`, `support_lead`, `support_lead2`, `developer`,
`support_rep`, `pm`, `legal`, `exec`). Every account sets up two-factor authentication on its first sign-in.
To try the document review flow, use two different seniors (`senior_eng` and `senior_eng2`): the person who
submits a document can never be the one who approves it.

## Record the audit trail on Solana (the live instance already does this on Devnet)
```bash
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$PATH"
solana-test-validator --reset &                                    # a local chain, free and offline
cd solana && anchor build
solana --url localhost --keypair ../.secrets/authority.json airdrop 50
solana --url localhost --keypair ../.secrets/authority.json program deploy \
  target/deploy/alexandria_audit.so --program-id target/deploy/alexandria_audit-keypair.json
cd .. && ALEXANDRIA_CHAIN=solana SOLANA_RPC_URLS=http://127.0.0.1:8899 \
  .venv/bin/uvicorn app.server:build_app --factory --port 8088
```
(`.secrets/authority.json` is created with `solana-keygen new --no-bip39-passphrase --outfile .secrets/authority.json`.)
Always pass an explicit `--url` to the Solana CLI: its default points at mainnet.

## Configuration (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `ALEXANDRIA_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | web origins allowed to call the API |
| `ALEXANDRIA_CHAIN` | `off` | `solana` to anchor the audit trail |
| `SOLANA_RPC_URLS` | public Devnet | comma-separated, tried in order with backoff |
| `ALEXANDRIA_REQUIRE_APPROVAL` | `1` | `0` restores the PRD's "seniors auto-approve" (not recommended) |
| `ALEXANDRIA_MODEL` / `ALEXANDRIA_EMBED_MODEL` | `llama3:8b` / `nomic-embed-text` | local models |
| `NEXT_PUBLIC_API_URL` (web, at build) | `http://127.0.0.1:8088` | where the web app finds the API |

## Tests
```bash
.venv/bin/python -m pytest                          # backend (add a local validator for the on-chain tests)
cd web && npm test                                  # unit tests for the web helpers
NEXT_PUBLIC_API_URL=http://127.0.0.1:8123 npm run build && npm run e2e   # real-browser tests (uses the local models)
```

## Restyling the web app
All colours live in one block of CSS variables at the top of `web/src/app/globals.css`; components use
semantic names (`bg-panel`, `text-mute`, `border-line`, `bg-brand`, ...), so editing that block restyles everything.
Shared building blocks are in `web/src/components/ui.tsx`.

## Hosted deployment

The site is hosted on Azure Static Web Apps and calls the Azure VM API. The production addresses and service details are in `HANDOFF.md`.

```bash
# Build current source, then upload the static export. Requires an active Azure login.
cd web && npm run build && cd ..
scripts/deploy-site.sh
```

The deployment script writes the hosted API address to the exported `/config.js`. It leaves `web/public/config.js` unchanged so local builds can use `NEXT_PUBLIC_API_URL`.

The API deployment procedure is documented at the top of `deploy/azure/setup.sh`; it pulls the current GitHub branch and restarts the services. Push the intended commit before running it. Preserve the existing CORS origins and Key Vault URL from `/etc/alexandria/env`.

## Connect Claude

For the hosted API, run this in your terminal and paste the key at the hidden prompt:

```bash
cd ~/alexandria
scripts/set-llm-key.sh
```

The script checks the key before changing anything, stores it in Azure Key Vault, and restarts the hosted API. The startup message confirms the selected provider. Use `scripts/set-llm-key.sh --remove` to remove it and return to Ollama.

For a local API, set `ALEXANDRIA_LLM_PROVIDER=auto` and `ANTHROPIC_API_KEY` in that API process's environment. The app does not automatically load a `.env` file. With `auto`, it selects Claude when a key is present and Ollama otherwise. Ollama still supplies embeddings for document search when Claude supplies the answers.

## Existing Meridian accounts

The seed script only runs on an empty database. To apply the time zones from the Meridian roster to existing accounts without changing their credentials, documents, or conversations:

```bash
sudo -u alexapi /opt/alexandria/.venv/bin/python /opt/alexandria/scripts/update_meridian_timezones.py --db /var/lib/alexandria/app.db
```

The operation updates only the 14 named Meridian accounts and can safely be repeated.
