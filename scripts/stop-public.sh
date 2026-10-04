#!/usr/bin/env bash
# Take Alexandria off the internet: stop the tunnel and the API.
pkill -f "cloudflared tunnel" 2>/dev/null && echo "tunnel stopped" || echo "no tunnel running"
pkill -f "uvicorn app.server" 2>/dev/null && echo "API stopped" || echo "no API running"
