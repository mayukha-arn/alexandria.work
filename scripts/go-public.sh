#!/usr/bin/env bash
# Put Alexandria on the internet from this machine, in one command:
#   1. starts the API (port 8088) if it isn't running, locked down for public use
#   2. opens a Cloudflare quick tunnel to it (only the API port; Ollama stays private)
#   3. points the Azure-hosted site at the tunnel and redeploys it
# The tunnel address changes every time, so run this again after a restart, a reboot or a long sleep.
# Needs: Ollama running, `az login` done, ~/.local/bin/cloudflared (see INSTALLED.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${ALEXANDRIA_LOG_DIR:-$ROOT/.run}"; mkdir -p "$LOG"
AZ="${AZ:-$HOME/.azure-cli-venv/bin/az}"
APP="${SWA_NAME:-alexandria-gh-web}"; RG="${SWA_RG:-alexandria-rg}"
DOMAIN="${SITE_DOMAIN:-the-only-one-who-knew-this-left-in-2019.work}"
SITE_HOST="$("$AZ" staticwebapp show -n "$APP" -g "$RG" --query defaultHostname -o tsv)"
CHAIN="${ALEXANDRIA_CHAIN:-solana}"                     # the audit trail goes to Devnet; ALEXANDRIA_CHAIN=off to only queue it

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/3 API"
if ! curl -fs localhost:8088/health >/dev/null 2>&1; then
  ALEXANDRIA_CORS_ORIGINS="https://$SITE_HOST,https://www.$DOMAIN,https://$DOMAIN" ALEXANDRIA_DOCS=0 \
  ALEXANDRIA_CHAIN="$CHAIN" SOLANA_RPC_URLS="${SOLANA_RPC_URLS:-https://api.devnet.solana.com}" \
    nohup "$ROOT/.venv/bin/uvicorn" app.server:build_app --factory --host 127.0.0.1 --port 8088 \
    --app-dir "$ROOT" > "$LOG/backend.log" 2>&1 &
  for _ in $(seq 1 60); do curl -fs localhost:8088/health >/dev/null 2>&1 && break; sleep 1; done
fi
curl -fs localhost:8088/health >/dev/null || { echo "API did not start; see $LOG/backend.log"; exit 1; }
echo "API is up on 127.0.0.1:8088"

say "2/3 tunnel"
pkill -f "cloudflared tunnel" 2>/dev/null || true; sleep 1
# CF_PROTOCOL=http2 forces TCP (default: cloudflared picks). On a flaky network neither protocol was reliably better.
nohup "$HOME/.local/bin/cloudflared" tunnel --no-autoupdate ${CF_PROTOCOL:+--protocol "$CF_PROTOCOL"} --url http://127.0.0.1:8088 > "$LOG/tunnel.log" 2>&1 &
URL=""
for _ in $(seq 1 60); do URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG/tunnel.log" | head -1 || true)"; [ -n "$URL" ] && break; sleep 1; done
[ -n "$URL" ] || { echo "no tunnel address; see $LOG/tunnel.log"; exit 1; }
for _ in $(seq 1 60); do curl -fs -m 5 "$URL/health" >/dev/null 2>&1 && break; sleep 2; done   # DNS takes a moment
curl -fs -m 5 "$URL/health" >/dev/null || { echo "tunnel $URL is not answering yet; try again in a minute"; exit 1; }
echo "tunnel: $URL"

say "3/3 site"
cd "$ROOT/web"
[ -d out ] || NEXT_TELEMETRY_DISABLED=1 npx next build
printf 'window.ALEXANDRIA_CONFIG = { apiUrl: "%s" };\n' "$URL" > out/config.js
cp public/staticwebapp.config.json out/
TOKEN="$("$AZ" staticwebapp secrets list -n "$APP" -g "$RG" --query properties.apiKey -o tsv)"
npx --yes @azure/static-web-apps-cli deploy ./out --deployment-token "$TOKEN" --env production 2>&1 | tail -2 | sed "s/$TOKEN/***/g"

say "Live"
echo "  site: https://$SITE_HOST"
echo "  (custom domain, once its DNS record exists: https://www.$DOMAIN)"
echo "  Keep this machine awake and Ollama running while people use it. Stop with: scripts/stop-public.sh"
