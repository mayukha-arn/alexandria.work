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
| 2026-10-03 | Anchor CLI (via `avm install latest`) | building | `~/.avm`, `~/.cargo/bin/anchor` | Build / deploy the Solana audit program | `avm uninstall <ver>` |

PATH additions needed in a shell (not written to any dotfile):
`export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.azure-cli-venv/bin:$PATH"`
