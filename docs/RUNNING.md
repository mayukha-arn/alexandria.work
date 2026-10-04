# Running Alexandria locally

Everything runs on one machine and nothing leaves it. You need: the repo's Python environment
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

## Optional: record the audit trail on Solana
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

## Putting it on the internet (from your own machine)
The site is a static export hosted on Azure Static Web Apps; the API and the model stay on your machine and are reached
through a Cloudflare quick tunnel (only the API port is exposed, never Ollama).
```bash
scripts/go-public.sh      # starts the API, opens the tunnel, points the site at it, redeploys
scripts/stop-public.sh    # takes it off the internet
caffeinate -dimsu &       # optional: stop the Mac sleeping while people use it
```
The tunnel address changes whenever it restarts, so run `go-public.sh` again after a reboot or a long sleep.
The public API runs with `ALEXANDRIA_DOCS=0` and only accepts calls from your site's origins.

**Before sharing it:** create accounts with a strong password (`python -m app.cli seed-demo`), and sign in to each one
yourself first. Whoever signs in first to an account sets up its 2FA, so don't hand out a password until you have.
The deployed site's address is `web/public/config.js` -> `apiUrl`; the deploy script rewrites it each time.
`DEPLOYED_URL=https://... npx playwright test -c playwright.deployed.config.ts` checks a live site.
