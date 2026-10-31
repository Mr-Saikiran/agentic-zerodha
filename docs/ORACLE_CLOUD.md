# Hosting on Oracle Cloud Always Free

This guide runs Agentic Zerodha on a free Oracle Cloud VM and publishes it at `https://techswitch.in/equity/`
through a Cloudflare Tunnel. No open ports, no TLS certificates, no monthly bill.

```
Browser ─▶ techswitch.in/equity/*  ─▶  Cloudflare Worker  ─▶  equity-origin.techswitch.in
                                                               │ (Cloudflare Tunnel, outbound from the VM)
                                                               ▼
                                         Oracle VM: docker compose ─ app (FastAPI :8000) + cloudflared
```

**Time:** about 30-45 minutes, most of it Oracle signup and waiting for builds.

## Checklist

| # | Step | Where |
|---|---|---|
| 1 | Create an Oracle Cloud account | cloud.oracle.com |
| 2 | Create an Always Free Ubuntu VM | Oracle console |
| 3 | SSH into the VM from Windows | PowerShell |
| 4 | Create a Cloudflare Tunnel and copy its token | Cloudflare Zero Trust |
| 5 | Run the setup script on the VM | SSH session |
| 6 | Deploy the Worker | `deploy/` on your PC |
| 7 | Check the Kite app's Redirect URL and log in | developers.kite.trade, browser |

---

## 1. Create the Oracle Cloud account

1. Go to <https://www.oracle.com/cloud/free/> → **Start for free**.
2. **Home region** - choose carefully, it can't be changed later and Always Free VMs can only be created there.
   Pick one near you, e.g. *India West (Mumbai)* or *India South (Hyderabad)*.
3. Card verification: Oracle places a small temporary hold to verify you; Always Free resources are not charged.

**If signup fails** (very common, usually "Error processing transaction" or the form just resets):
- Use a **credit card**. Debit, prepaid and virtual cards are often rejected.
- Name and billing address must match the card exactly.
- Turn off VPNs and ad blockers; try another browser or an incognito window.
- Don't retry many times in a row - wait a few hours or a day, then try again.
- Still stuck: Oracle's signup support chat (on the signup page) can verify you manually.

**Recommended once the account works:** *Billing → Upgrade to Pay As You Go*. Always Free resources stay free,
but it makes free Ampere VMs easier to get, and Oracle doesn't reclaim them for being idle. On free-tier-only
accounts Oracle may reclaim an Always Free VM that stays almost idle for 7 days (this app is idle most of the
time). Check Oracle's current Always Free policy before deciding; you can set a budget alert of ₹1 in
*Billing → Budgets* to be safe.

## 2. Create the VM

Console → ☰ → **Compute → Instances → Create instance**.

| Setting | Value |
|---|---|
| Name | `agentic-zerodha` |
| Image | **Change image → Ubuntu → Canonical Ubuntu 24.04** (the script needs Ubuntu or Debian) |
| Shape | **Change shape → Ampere → VM.Standard.A1.Flex**, **1 OCPU, 6 GB** memory (free up to 4 OCPU / 24 GB in total). Fallback: **Specialty and previous generation → VM.Standard.E2.1.Micro** (AMD, 1 GB) |
| Networking | Defaults: create a new VCN and public subnet, **Assign a public IPv4 address: yes** |
| SSH keys | **Generate a key pair for me → Save private key** (keep this file safe) |
| Boot volume | Default (about 47 GB) is fine |

Check the shape shows **Always Free-eligible**, then **Create**. When it's *Running*, copy the **Public IP address**.

**"Out of capacity for shape VM.Standard.A1.Flex":** free Ampere capacity runs out often.
- Try another **Availability domain** (in the Placement section) if your region has more than one.
- Try again at a different time of day; capacity frees up.
- Use the **VM.Standard.E2.1.Micro** fallback. The setup script adds swap so the app builds and runs on its 1 GB.
- Pay As You Go accounts (above) get Ampere capacity more easily.

No inbound ports need opening: the tunnel connects out from the VM. Leave the security list at its default (SSH only).

## 3. Connect from Windows

In PowerShell on your PC (replace the key file name and IP):

```powershell
mkdir $HOME\.ssh -Force
Move-Item $HOME\Downloads\ssh-key-*.key $HOME\.ssh\oracle.key
icacls $HOME\.ssh\oracle.key /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -i $HOME\.ssh\oracle.key ubuntu@<PUBLIC-IP>
```

Type `yes` the first time. You're in when the prompt looks like `ubuntu@agentic-zerodha:~$`.

`icacls` makes the key readable only by you; without it, ssh refuses with *"UNPROTECTED PRIVATE KEY FILE"*.

## 4. Create the Cloudflare Tunnel

In the Cloudflare dashboard → **Zero Trust → Networks → Tunnels → Create a tunnel**:

1. Type **Cloudflared**, name it `agentic-zerodha`.
2. On *Install and run a connector*, pick any OS and copy the token: it's the long `eyJ...` value after `--token`
   in the command shown. Don't run that command; the setup script runs cloudflared in Docker for you.
3. **Public hostname**: subdomain `equity-origin`, domain `techswitch.in`, path empty,
   service type **HTTP**, URL **`app:8000`**. Save.

## 5. Run the setup script on the VM

In the SSH session:

```bash
curl -fsSL https://raw.githubusercontent.com/Mr-Saikiran/agentic-zerodha/main/deploy/oracle/setup.sh -o setup.sh
bash setup.sh
```

Paste the tunnel token when asked. The script ([deploy/oracle/setup.sh](../deploy/oracle/setup.sh)):

- adds 2 GB of swap on small VMs,
- installs Docker and git,
- clones the repo to `~/agentic-zerodha`,
- writes `~/agentic-zerodha/.env` with a generated `SESSION_SECRET`, `BASE_PATH=/equity`,
  `KITE_REDIRECT_URL=https://techswitch.in/equity/callback` and your tunnel token (file readable only by you),
- builds and starts the app and cloudflared (they restart on their own after crashes and reboots),
- waits until `/healthz` answers.

The first build takes 3-8 minutes. It ends with **"Agentic Zerodha is running."**

Check the tunnel: `sudo docker compose -f ~/agentic-zerodha/docker-compose.yml logs cloudflared` should show
`Registered tunnel connection`, and `https://equity-origin.techswitch.in/healthz` in your browser shows `{"ok":true}`.

Different setup? Set variables before running, e.g. a whole subdomain instead of a path:
`BASE_PATH= PUBLIC_URL=https://equity.techswitch.in bash setup.sh` (and point the tunnel's public hostname there).

## 6. Deploy the Worker

`deploy/wrangler.toml` already points `ORIGIN_URL` at `https://equity-origin.techswitch.in`. On your PC, from the
repo's `deploy/` folder:

```bash
npx wrangler deploy
```

Open `https://techswitch.in/equity/` - you should see the setup screen.

## 7. Kite app and first login

At [developers.kite.trade](https://developers.kite.trade) your app's **Redirect URL** must be
`https://techswitch.in/equity/callback` (unchanged if you used it before). Then on the setup screen enter the Kite API
key and secret plus your AI key, and **Log in with Zerodha**.

Suspend or delete the old Render service / Hugging Face Space so nothing else answers for the app.

---

## Day-to-day

| Task | Command (on the VM) |
|---|---|
| Update to the latest code | `bash ~/agentic-zerodha/deploy/oracle/update.sh` |
| App logs | `cd ~/agentic-zerodha && sudo docker compose logs -f app` |
| Tunnel logs | `cd ~/agentic-zerodha && sudo docker compose logs -f cloudflared` |
| Status | `cd ~/agentic-zerodha && sudo docker compose ps` |
| Restart | `cd ~/agentic-zerodha && sudo docker compose --profile tunnel restart` |
| Stop | `cd ~/agentic-zerodha && sudo docker compose --profile tunnel down` |

- **Back up `~/agentic-zerodha/.env`** somewhere safe (password manager). If `SESSION_SECRET` changes, every user
  has to enter their keys again.
- Ubuntu installs security updates automatically. Reboot now and then (`sudo reboot`); the containers come back
  by themselves.
- After logging out and back in, `docker` works without `sudo`.

## Optional hardening

**Origin lock** - make `equity-origin.techswitch.in` answer only the Worker:

```bash
# on the VM
cd ~/agentic-zerodha && echo "ORIGIN_SECRET=$(openssl rand -hex 32)" >> .env && grep ORIGIN_SECRET .env
sudo docker compose --profile tunnel up -d
```
```bash
# on your PC, in deploy/ - paste the same value
npx wrangler secret put ORIGIN_SECRET
```

If the two values don't match, every page returns `{"detail":"Forbidden"}`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `This script supports Ubuntu/Debian` | Recreate the VM with the Ubuntu image (Oracle Linux isn't supported by the script) |
| Build killed / very slow on the 1 GB VM | Check swap: `swapon --show`. Re-run `bash setup.sh`; it adds swap if missing |
| `{"detail":"Forbidden"}` on every page | `ORIGIN_SECRET` on the VM and on the Worker differ. Make them equal, or delete it from `.env` and run `npx wrangler secret delete ORIGIN_SECRET` |
| Cloudflare error 1033 / 502 at `equity-origin.techswitch.in` | Tunnel not connected: `sudo docker compose logs cloudflared`. Wrong token → re-run `TUNNEL_TOKEN=eyJ... bash setup.sh` |
| Tunnel connected but 502 | The public hostname's service must be `http://app:8000` (not `localhost:8000`) |
| `techswitch.in/equity` shows your static site's 404 | Worker not deployed, or its routes don't match. Run `npx wrangler deploy` in `deploy/` |
| Zerodha says redirect URL mismatch | Kite app Redirect URL must equal `KITE_REDIRECT_URL` in `.env` exactly |
| VM disappeared / stopped after a quiet week | Idle reclamation on free-tier accounts; see the Pay As You Go note in step 1 |
| Disk filling up | `sudo docker system prune -af` (logs are already capped at 30 MB per container) |
