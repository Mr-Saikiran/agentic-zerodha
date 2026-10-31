#!/usr/bin/env bash
# One-shot setup for Agentic Zerodha on a fresh Ubuntu VM (built for Oracle Cloud Always Free).
# Safe to re-run: it keeps an existing .env and only fills in what's missing.
#
# Run on the VM:
#   curl -fsSL https://raw.githubusercontent.com/Mr-Saikiran/agentic-zerodha/main/deploy/oracle/setup.sh | bash
#
# Optional settings (environment variables), e.g. TUNNEL_TOKEN=eyJ... bash setup.sh
#   TUNNEL_TOKEN   Cloudflare Tunnel token. Asked for if not set; leave it empty to run without the tunnel.
#   BASE_PATH      URL prefix the app is served under (default: /equity). Use "" for a whole (sub)domain.
#   PUBLIC_URL     Public address of the app (default: https://techswitch.in/equity); sets the Kite redirect URL.
#   APP_DIR        Where to put the code (default: ~/agentic-zerodha)
#   REPO           Git repository to clone (default: the public GitHub repo)
set -euo pipefail

REPO="${REPO:-https://github.com/Mr-Saikiran/agentic-zerodha.git}"
APP_DIR="${APP_DIR:-$HOME/agentic-zerodha}"
BASE_PATH="${BASE_PATH-/equity}"
PUBLIC_URL="${PUBLIC_URL:-https://techswitch.in/equity}"
TUNNEL_TOKEN="${TUNNEL_TOKEN:-}"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mxx %s\033[0m\n' "$*" >&2; exit 1; }

# ---- 1. Checks -----------------------------------------------------------------
step "Checking the system"
. /etc/os-release
case "${ID:-}" in
  ubuntu | debian) echo "OS: $PRETTY_NAME ($(uname -m))" ;;
  *) die "This script supports Ubuntu/Debian. Create the VM with an Ubuntu image (see docs/ORACLE_CLOUD.md)." ;;
esac
command -v sudo >/dev/null || die "sudo is required."

# ---- 2. Swap (the 1 GB AMD micro VM needs it to build the image) ---------------------
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_mb" -lt 2048 ] && ! swapon --show | grep -q .; then
  step "Adding a 2 GB swap file (this VM has ${mem_mb} MB RAM)"
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# ---- 3. Packages + Docker ------------------------------------------------------------
step "Installing git, curl and openssl"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl openssl ca-certificates >/dev/null

if ! command -v docker >/dev/null; then
  step "Installing Docker"
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo systemctl enable --now docker >/dev/null 2>&1 || true
sudo usermod -aG docker "$USER" || true   # lets you run `docker` without sudo after you log in again
sudo docker compose version >/dev/null || die "Docker Compose plugin is missing; see docs/ORACLE_CLOUD.md troubleshooting."

# ---- 4. Code ---------------------------------------------------------------------
if [ -d "$APP_DIR/.git" ]; then
  step "Updating the code in $APP_DIR"
  git -C "$APP_DIR" pull --ff-only
else
  step "Cloning $REPO into $APP_DIR"
  git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

# ---- 5. Settings (.env) -------------------------------------------------------------
set_env() {  # set_env KEY VALUE: replace or append KEY=VALUE in .env
  local key=$1 value=$2
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >>.env
  fi
}

step "Writing settings to $APP_DIR/.env"
touch .env
chmod 600 .env
# Keep an existing SESSION_SECRET: changing it signs everyone out.
grep -q '^SESSION_SECRET=.\+' .env || set_env SESSION_SECRET "$(openssl rand -base64 48 | tr -d '\n=+/')"
set_env BASE_PATH "$BASE_PATH"
set_env KITE_REDIRECT_URL "${PUBLIC_URL%/}/callback"

if [ -z "$TUNNEL_TOKEN" ] && ! grep -q '^CLOUDFLARE_TUNNEL_TOKEN=.\+' .env && [ -r /dev/tty ]; then
  echo
  echo "Paste your Cloudflare Tunnel token (Zero Trust -> Networks -> Tunnels -> your tunnel)."
  echo "Press Enter to skip; the app then runs only on this VM at http://127.0.0.1:8000."
  read -r -p "Tunnel token: " TUNNEL_TOKEN </dev/tty || true
fi
[ -n "$TUNNEL_TOKEN" ] && set_env CLOUDFLARE_TUNNEL_TOKEN "$TUNNEL_TOKEN"

PROFILE=()
grep -q '^CLOUDFLARE_TUNNEL_TOKEN=.\+' .env && PROFILE=(--profile tunnel)

# ---- 6. Start -----------------------------------------------------------------------
step "Building and starting the containers (first build takes a few minutes)"
sudo docker compose "${PROFILE[@]}" up -d --build

step "Waiting for the app to answer"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done
[ "${ok:-}" = 1 ] || die "The app didn't start. Check: sudo docker compose logs app"

echo
printf '\033[1;32mAgentic Zerodha is running.\033[0m\n'
echo "  Local check:  curl http://127.0.0.1:8000/healthz"
if [ ${#PROFILE[@]} -gt 0 ]; then
  echo "  Tunnel:       sudo docker compose logs cloudflared   (look for 'Registered tunnel connection')"
  echo "  Next:         point the Cloudflare Worker's ORIGIN_URL at your tunnel hostname, then open ${PUBLIC_URL%/}/"
else
  warn "No tunnel token: the app isn't reachable from the internet yet. Re-run this script with TUNNEL_TOKEN=... to add it."
fi
echo "  Update later: bash $APP_DIR/deploy/oracle/update.sh"
