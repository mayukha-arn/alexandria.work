# Installed tooling log

Everything installed on this machine for Alexandria. Newest at the bottom.
All installs are user-level (no sudo) unless noted. Disk figures are approximate.

| Date | What | Version | Where | Why | Remove with |
|------|------|---------|-------|-----|-------------|
| 2026-10-03 | Ollama | 0.35.1 | `~/Applications/Ollama.app` (data in `~/.ollama`) | Local LLM server (Layer 3) | delete app, `rm -rf ~/.ollama` |
| 2026-10-03 | Model `llama3:8b` | Q4, 4.7 GB | `~/.ollama/models` | Private LLM, zero data leakage | `ollama rm llama3:8b` |
| 2026-10-03 | Conda env (Python 3.12) | 3.12 | `~/alexandria/.venv` | Python deps; system Python 3.14 lacks wheels | `rm -rf ~/alexandria/.venv` |
| 2026-10-03 | pip deps from `requirements-dev.txt` | see file | `~/alexandria/.venv` | Layer 1 + tests | with the env |
| 2026-10-03 | Rust toolchain (rustup) | rustc 1.99.0 | `~/.cargo`, `~/.rustup` (1.3 GB) | Build the Solana/Anchor program (Layer 4) | `rustup self uninstall` |
| 2026-10-03 | Solana CLI (Agave) | 4.3.0 | `~/.local/share/solana` (209 MB) | Deploy to Devnet, keypairs, airdrops | `rm -rf ~/.local/share/solana` |
| 2026-10-03 | avm (Anchor version manager) | 1.2.0 | `~/.cargo/bin/avm` | Installs the Anchor CLI | `cargo uninstall avm` |
| 2026-10-03 | Azure CLI (`az`, pip, own venv) | 2.90.0 | `~/.azure-cli-venv` (683 MB) | Deploy / manage Azure resources | `rm -rf ~/.azure-cli-venv` |
| 2026-10-03 | Anchor CLI (via `avm install latest`) | 1.2.0 | `~/.avm`, `~/.cargo/bin/anchor` | Build / deploy the Solana audit program | `avm uninstall <ver>` |
| 2026-10-03 | Rust 1.89.0 toolchain (pinned by the Anchor workspace) + Solana SBF platform-tools | 1.89.0 | `~/.rustup`, `~/.cache/solana` | `anchor build` of the audit program | `rustup toolchain uninstall 1.89.0`, `rm -rf ~/.cache/solana` |
| 2026-10-03 | Python: solders (JSON-RPC is done with `requests`; solana-py 0.41 is async-only, so not used) | 0.29.0 | `~/alexandria/.venv` | Build / sign Solana transactions | with the env |
| 2026-10-03 | Build cache `solana/target` (gitignored) | ~2 GB | `~/alexandria/solana/target` | Anchor / cargo build output | `cd solana && cargo clean` (rebuild when needed) |
| 2026-10-03 | Ollama model `nomic-embed-text` | 274 MB | `~/.ollama/models` | Local embeddings (long context; MiniLM truncates at ~256 tokens) | `ollama rm nomic-embed-text` |
| 2026-10-03 | Python: chromadb (+ onnxruntime etc.) | 1.5.9 | `~/alexandria/.venv` (venv now ~780 MB) | Layer 2 vector store; data in gitignored `./chroma_db` | with the env |
| 2026-10-03 | Web app dependencies (`npm install`: next 14, react 18, tailwind, qrcode, tweetnacl, bs58, lucide-react, vitest, @playwright/test) | see `web/package.json` | `~/alexandria/web/node_modules` (~344 MB, gitignored) | The Next.js frontend | `rm -rf web/node_modules` (reinstall with `npm install --cache /tmp/npm-cache`) |
| 2026-10-03 | Playwright Chromium headless shell | 153 (v1243) | `~/Library/Caches/ms-playwright` (~94 MB) | Real-browser end-to-end tests | `rm -rf ~/Library/Caches/ms-playwright` |
| 2026-10-03 | Static site build `web/out` and `web/.next` (gitignored) | n/a | `~/alexandria/web` | Build output | `rm -rf web/out web/.next` |

PATH additions needed in a shell (not written to any dotfile):
`export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.azure-cli-venv/bin:$PATH"`

Azure providers registered on the student subscription (2026-10-03): Microsoft.Compute, Microsoft.Web, Microsoft.App, Microsoft.OperationalInsights, Microsoft.KeyVault (free; undo with `az provider unregister -n <name>`).
Python deps added for the backend: fastapi, uvicorn, pyjwt, argon2-cffi, pyotp, pynacl, base58, cryptography, python-multipart, httpx (dev).

Local validator for tests/offline demos: `solana-test-validator` ships with the Solana CLI (ledger lives in the session scratchpad, disposable). Note the Solana CLI default config points at MAINNET; always pass `--url`.
