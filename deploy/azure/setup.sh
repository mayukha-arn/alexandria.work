#!/usr/bin/env bash
# Idempotent setup / update of the Alexandria API on the Azure VM. Run as root:
#   ssh vm 'sudo API_HOST=... CORS_ORIGINS=... KEYVAULT_URL=... bash -s' < deploy/azure/setup.sh
set -euo pipefail
: "${API_HOST:?}" "${CORS_ORIGINS:?}" "${KEYVAULT_URL:?}"
MODEL="${MODEL:-llama3.2:3b}"; TOP_K="${TOP_K:-3}"; CTX="${CTX:-2400}"; CHAIN="${CHAIN:-solana}"
MIN_PW="${MIN_PW:-8}"     # this deployment's owners chose an 8-character minimum (the app default is 12)
REPO="${REPO:-https://github.com/mayukha-arn/alexandria.work}"

id alexapi >/dev/null 2>&1 || useradd --system --home /var/lib/alexandria --shell /usr/sbin/nologin alexapi
install -d -o alexapi -g alexapi -m 700 /var/lib/alexandria
install -d -m 755 /etc/alexandria /opt/alexandria

echo "== code"
if [ -d /opt/alexandria/.git ]; then git -c safe.directory=/opt/alexandria -C /opt/alexandria pull --ff-only -q; else git clone -q "$REPO" /opt/alexandria; fi
echo "at $(git -c safe.directory=/opt/alexandria -C /opt/alexandria rev-parse --short HEAD)"

echo "== python environment"
[ -x /opt/alexandria/.venv/bin/python ] || python3 -m venv /opt/alexandria/.venv
/opt/alexandria/.venv/bin/pip install -q --upgrade pip
/opt/alexandria/.venv/bin/pip install -q -r /opt/alexandria/requirements.txt

echo "== configuration (no secrets in this file)"
cat > /etc/alexandria/env <<ENV
ALEXANDRIA_DB=/var/lib/alexandria/app.db
ALEXANDRIA_REGISTRY=/var/lib/alexandria/registry.db
ALEXANDRIA_CHROMA=/var/lib/alexandria/chroma
ALEXANDRIA_SECRETS=/run/alexandria
ALEXANDRIA_AUTHORITY_KEYPAIR=/run/alexandria/authority.json
ALEXANDRIA_KEYVAULT_URL=$KEYVAULT_URL
ALEXANDRIA_CORS_ORIGINS=$CORS_ORIGINS
ALEXANDRIA_DOCS=0
ALEXANDRIA_CHAIN=$CHAIN
SOLANA_RPC_URLS=https://api.devnet.solana.com
ALEXANDRIA_MODEL=$MODEL
ALEXANDRIA_LLM_PROVIDER=auto
ALEXANDRIA_MIN_PASSWORD=$MIN_PW
ALEXANDRIA_AUTO_LEARN=1
ALEXANDRIA_TOP_K=$TOP_K
ALEXANDRIA_CONTEXT_CHARS=$CTX
OLLAMA_URL=http://127.0.0.1:11434
ANONYMIZED_TELEMETRY=False
ENV
chmod 640 /etc/alexandria/env; chown root:alexapi /etc/alexandria/env

echo "== ollama (local only; keep the models loaded between questions)"
install -d /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'OV'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_NUM_PARALLEL=1"
OV

echo "== services"
install -m 644 /opt/alexandria/deploy/azure/alexandria-api.service /etc/systemd/system/alexandria-api.service
sed "s/__API_HOST__/$API_HOST/" /opt/alexandria/deploy/azure/Caddyfile.template > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl restart ollama
systemctl enable alexandria-api caddy >/dev/null 2>&1
systemctl restart alexandria-api
systemctl reload-or-restart caddy
echo "== done"
