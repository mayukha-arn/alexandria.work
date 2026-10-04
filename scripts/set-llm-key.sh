#!/usr/bin/env bash
# Store the hosted model's API key in Key Vault and restart the API so it switches to Claude.
# The key is read with hidden input, written to a private temp file only long enough to upload, and
# never printed or passed on a command line (where `ps` could see it).
#   scripts/set-llm-key.sh            # prompts for the key
#   scripts/set-llm-key.sh --remove   # delete it again: the API falls back to the local model
set -euo pipefail
AZ="${AZ:-$HOME/.azure-cli-venv/bin/az}"
VAULT="${VAULT:-alexandria-gh-kv}"
VM_HOST="${VM_HOST:-alexandria-gh-api.mexicocentral.cloudapp.azure.com}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/alexandria_azure}"
vm() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "alexandria@$VM_HOST" "$@"; }

if [ "${1:-}" = "--remove" ]; then
  "$AZ" keyvault secret delete --vault-name "$VAULT" --name llm-env -o none
  echo "Removed. Restarting the API on the local model…"
else
  printf "Anthropic API key (input hidden): "; IFS= read -rs KEY; echo
  case "$KEY" in sk-ant-*) ;; *) echo "That doesn't look like an Anthropic key (sk-ant-…). Nothing changed." >&2; exit 1;; esac
  # Check it before storing it, so a typo doesn't take the assistant down.
  tmp=$(mktemp -d); chmod 700 "$tmp"; trap 'rm -rf "$tmp"' EXIT
  printf 'x-api-key: %s\n' "$KEY" > "$tmp/h"
  code=$(curl -s -o /dev/null -w '%{http_code}' https://api.anthropic.com/v1/models -H "anthropic-version: 2023-06-01" -H @"$tmp/h")
  if [ "$code" != "200" ]; then echo "Anthropic rejected that key (HTTP $code). Nothing changed." >&2; exit 1; fi
  printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" > "$tmp/llm.env"; unset KEY
  "$AZ" keyvault secret set --vault-name "$VAULT" --name llm-env --file "$tmp/llm.env" -o none
  echo "Key verified and stored in Key Vault ($VAULT/llm-env). Restarting the API…"
fi
vm 'sudo systemctl restart alexandria-api && sleep 4 && sudo journalctl -u alexandria-api -n 40 --no-pager | grep -o "language model: [a-z]*" | tail -1'
