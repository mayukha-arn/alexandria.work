# Alexandria — handoff notes

NJIT GirlHacks 2026 (ADP, Azure/Avanade, Solana tracks). Enterprise Q&A: ask a *department or expert*, not a channel. Clearance-aware RAG plus a Solana audit trail.
**Rule:** the UI must never say "demo", "mock" or "sample".

## Live
- **Site:** https://www.the-only-one-who-knew-this-left-in-2019.work (Azure Static Web App, DNS at Porkbun). To deploy fresh code: `cd web && npm run build && cd .. && scripts/deploy-site.sh`.
- **API:** Azure VM `alexandria-gh-api.mexicocentral.cloudapp.azure.com`.
  - Runs Caddy, the systemd unit `alexandria-api` (port 8088) and code at `/opt/alexandria`.
  - Data is in `/var/lib/alexandria`.
  - To update: rerun `deploy/azure/setup.sh` over ssh (key `~/.ssh/alexandria_azure`, user `alexandria`).
- **Secrets:** Key Vault `alexandria-gh-kv`, pulled into tmpfs `/run/alexandria` at start (`app/azure_secrets.py`).
- **Monitoring:** Application Insights (`app/telemetry.py`) plus 5 alert rules.
- **Solana:** Anchor program on Devnet. Approvals and events are anchored via an outbox (`app/anchoring.py`, `app/chain.py`).

## Backend (`app/`, FastAPI, SQLite, ChromaDB + FTS5)
- **Auth** (`security.py`, `main.py`): Argon2 passwords, JWT, mandatory TOTP 2FA, sessions.
  - Minimum password length comes from `ALEXANDRIA_MIN_PASSWORD`: 8 on the VM, default 12.
- **Roles** (`roles.py`): department, level, clearance 0-100. Seniors manage only their direct reports.
- **Documents** (`documents.py`, `ingestion_layer.py`): PDF/URL upload, dedup, staging.
  - Four-eyes rule: every document needs a wallet-signed approval by a *different* person.
- **RAG** (`rag.py`, `vectorstore.py`, `guardrails.py`): hybrid search filtered by clearance, streaming `/ask/stream`, PII masking.
- **Language model** (`server.py`): `ALEXANDRIA_LLM_PROVIDER=auto` uses Claude (`claude_llm.py`) if `ANTHROPIC_API_KEY` is set, else Ollama `llama3.2:3b` (~30 s per answer on CPU).
  - Store a key with `scripts/set-llm-key.sh`. The owner will supply the key through its hidden terminal prompt.
  - SDK errors surface as `LLMUnavailable`.
- **Messaging** (`messaging.py`, `realtime.py`): channels, pings (department requests), WebSocket `/ws`.
- **Routing and learning:**
  - `routing.py`: `POST /route` ranks departments and specific experts for a question, using:
    - documents they wrote or approved, filtered to what the asker can read;
    - past answered pings (counted only, never quoted);
    - a keyword vocabulary per department;
    - local working hours (`users.timezone`) and online presence (`hub.online_ids`).
  - `GET /directory`, `PUT /auth/me/timezone`, `GET /insights` (documents learned from requests, resolved/waiting counts, median first answer).
  - Pings accept `to_user`. That sets `requested_id`, which puts the ping at the top of that person's inbox; teammates can still answer.
  - Auto-learn (`ALEXANDRIA_AUTO_LEARN=1`): resolving a ping drafts a knowledge article for the answerer and stages it for review.
  - We deliberately use retrieval, not fine-tuning: it respects clearance and supports deletion.
- **Tests:** `.venv/bin/python -m pytest -q tests` — latest local run: 366 passed, 16 integration tests skipped; routing tests are in `tests/test_routing.py`.

## Frontend (`web/`, Next 14 static export, Tailwind)
- **Theme:** CSS variables in `src/app/globals.css`, now green (emerald and teal); no purple anywhere.
- **Shell** (`src/app/(app)/layout.tsx`): light labelled sidebar (Ask Alexandria, Requests, Knowledge base, People, Team spaces, then governance pages), ⌘K palette, Account menu. Sign-in lands on `/ask/`.
- **Components:** `components/ai-answer.tsx` (streams, citations, `onDone` callback, "Learned from a resolved request" chip), `avatar.tsx`, `lib/markdown.tsx`, `lib/people.ts` (display names), `lib/format.ts` `docTitle`.
- **Chat** (`chat/page.tsx`): `@alexandria` answers inline, `@dept` creates a ping, attachments upload.
- **Tests:** `npm run e2e` (27 tests passing; uses a local backend and Ollama), `playwright.live.config.ts` (smoke test of the live site).

## Completed redesign (2026-10-04)
- Ask is the landing hub: persona suggestions for Meridian, knowledge activity from `/insights`, streaming answers, and “Did this answer it?” feedback.
- `components/expert-router.tsx` opens after No, an answer with no sources, an ungrounded answer, or an unavailable model. It shows department queues and specific experts with evidence, availability, and local time. A visibility selector controls the request classification; manual department selection covers no matches and routing failures. Sent receipts link to Requests and persist when feedback is toggled.
- `/people/` lists colleagues by department with search, local time, working hours, and online presence. The directory refreshes every minute.
- Requests labels are consistent and directly addressed requests show “Asked you directly”. Team spaces uses a light sidebar.
- Meridian's 14 people now include IANA time zones. New seed runs assign them automatically. Existing databases can be updated without reseeding:
  `sudo -u alexapi /opt/alexandria/.venv/bin/python /opt/alexandria/scripts/update_meridian_timezones.py --db /var/lib/alexandria/app.db`
- Browser coverage includes directory search, routing retry/no-match fallback, feedback, expert delivery, and updated navigation labels. Live smoke checks use the Ask hub without posting chat messages or sending requests.

## Validation
- Production Next.js build and type checks passed.
- Frontend unit tests: 13 passed. Browser tests: 27 passed.
- Time-zone migration checked for repeatability and preservation of credentials and unrelated accounts.

## Remaining owner action
- Add a valid Anthropic key using `scripts/set-llm-key.sh` (hidden terminal prompt). It verifies the key, stores it in Azure Key Vault, and restarts the API. No key belongs in source control or browser code. Until configured, the hosted service uses Ollama.

## Data / accounts
- **Seed:** `scripts/seed_meridian.py` (company "Meridian": 14 people, 9 PDFs in `seed/meridian/pdf`, chat history, pings). The upload sample is in `seed/meridian/upload/`.
- **Credentials** (git-ignored): `.secrets/meridian-accounts.txt`, `.secrets/meridian-2fa.txt`.
- **Team accounts:** `rishabmohandoss` and `mayukhaarn`, both security_admin.
- **Backup:** the pre-seed VM data is at `/var/lib/alexandria-backup-20261004-124859`.
- **Installs:** see `INSTALLED.md`. Always pass `--url` to the solana CLI.
