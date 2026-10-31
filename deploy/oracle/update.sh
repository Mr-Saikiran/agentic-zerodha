#!/usr/bin/env bash
# Pulls the latest code and rebuilds the containers. Run on the VM:  bash ~/agentic-zerodha/deploy/oracle/update.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

git pull --ff-only

PROFILE=()
grep -q '^CLOUDFLARE_TUNNEL_TOKEN=.\+' .env 2>/dev/null && PROFILE=(--profile tunnel)
sudo docker compose "${PROFILE[@]}" up -d --build
sudo docker image prune -f >/dev/null   # drop old image layers so the small disk doesn't fill up

for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && { echo "Updated and running."; exit 0; }
  sleep 2
done
echo "The app didn't come back. Check: sudo docker compose logs app" >&2
exit 1
