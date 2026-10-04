#!/usr/bin/env bash
# Build the web app (if needed) and deploy it to Azure Static Web Apps, pointed at the Azure-hosted API.
#   scripts/deploy-site.sh                       # uses the default API address below
#   API_URL=https://other.example scripts/deploy-site.sh
# Needs `az login`. No tunnel involved: the API runs on the Azure VM (see deploy/azure/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AZ="${AZ:-$HOME/.azure-cli-venv/bin/az}"
APP="${SWA_NAME:-alexandria-gh-web}"; RG="${SWA_RG:-alexandria-rg}"
API_URL="${API_URL:-https://alexandria-gh-api.mexicocentral.cloudapp.azure.com}"
DOMAIN="${SITE_DOMAIN:-the-only-one-who-knew-this-left-in-2019.work}"

cd "$ROOT/web"
[ -d out ] || NEXT_TELEMETRY_DISABLED=1 npx next build
printf 'window.ALEXANDRIA_CONFIG = { apiUrl: "%s" };\n' "$API_URL" > out/config.js
cp public/staticwebapp.config.json out/
TOKEN="$("$AZ" staticwebapp secrets list -n "$APP" -g "$RG" --query properties.apiKey -o tsv)"
npx --yes @azure/static-web-apps-cli deploy ./out --deployment-token "$TOKEN" --env production 2>&1 | tail -2 | sed "s/$TOKEN/***/g"
echo "site: https://www.$DOMAIN   (API: $API_URL)"
