# Oracle v7.1 — Deployment Guide for Dell R740xd XL Cluster

**Pretend you are starting from absolute zero.** This guide takes you from "I just unloaded a rack of Dell servers from a truck" all the way to "Oracle is running on 5 servers, accessible from my laptop and phone via Tailscale, with a live monitoring dashboard."

**Target hardware (per server, 5 servers total):**
- Dell PowerEdge R740xd XL
- 2× Intel Xeon Gold 6238R (28 cores each = 56C / 112T per box)
- 512 GB DDR4 ECC RDIMM
- 2× 1100W PSU (redundant power)

**Total cluster compute:** 280 cores, 560 threads, 2.5 TB RAM. Massive overkill for Oracle — which is the point. Headroom for the next 30 agents, multiple LLM experiments, and full backtest parallelization.

---

## Table of Contents

1. [Before you start](#1-before-you-start)
2. [Physical setup (rack, power, network)](#2-physical-setup)
3. [Server 1 — install Ubuntu Server 24.04](#3-server-1--install-ubuntu-server-2404)
4. [Server 1 — install Docker + essentials](#4-server-1--install-docker--essentials)
5. [Server 1 — install Tailscale (secure remote access)](#5-server-1--install-tailscale)
6. [Move your Oracle code from your laptop to Server 1](#6-move-your-oracle-code-from-laptop-to-server-1)
7. [Stand up Postgres + TimescaleDB in Docker](#7-stand-up-postgres--timescaledb-in-docker)
8. [Migrate predictions.db (SQLite → Postgres)](#8-migrate-predictionsdb-sqlite--postgres)
9. [Run Oracle inside Docker](#9-run-oracle-inside-docker)
10. [Servers 2–5: replicate the install + assign roles](#10-servers-25--replicate-the-install)
11. [Networking the 5 servers together](#11-networking-the-5-servers-together)
12. [Monitoring stack (Grafana + Prometheus + Loki)](#12-monitoring-stack)
13. [Day-to-day maintenance](#13-day-to-day-maintenance)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Before you start

You'll need:

| Item | Why | Approx cost |
|---|---|---|
| The 5 R740xd XL servers | Already bought ($450 the lot) | ✓ |
| At least one keyboard + monitor (or KVM) | First-boot OS install needs a console | $0 if you have one |
| 1 USB stick, 16 GB+ | Burn the Ubuntu installer to it | $10 |
| Ethernet cables (5+) | One per server to the rack switch | $0 if rack came with them |
| 5× SSDs, 1 TB each (SATA, used) | OS + Postgres data — DON'T trust spinning disks | $50-80 each used = $250-400 |
| 1× external USB SSD, 2 TB+ | Backups | $80-120 |
| The Cisco SG300 switch in the rack | Internal cluster network | ✓ |
| 30-amp circuit (NEMA 5-30 or L5-30 outlet) | 5 servers idle ~700W, peak ~3500W | Garage/basement breaker |
| Garage / basement / utility room | These are LOUD (~65-75 dB) and HOT | — |
| Your laptop with the Oracle code | Source of truth right now | ✓ |
| A free Tailscale account | Secure remote access | Free |

**On power:** Five R740xd XLs at light load draw ~700W combined; under heavy load they can hit 3500W. A standard US 15A wall outlet maxes out at ~1800W — you WILL trip it. Use a 30-amp circuit, or split across two 15A circuits on different breakers (max 2 servers per circuit).

**On internet:** No special bandwidth needs. yfinance + FRED + scrapers total maybe 50 MB/day. Any home internet works.

---

## 2. Physical setup

### 2.1 Rack the servers

If they aren't already in the rack: each R740xd XL is 2U and weighs ~60-80 lbs loaded. **Two-person lift.** Use the rail kits — they should have come with the rack. Heaviest server goes lowest (center of gravity).

### 2.2 Cable each server

For each of the 5 servers:

1. **Power:** Plug both PSUs (redundancy). Ideally one PSU per server goes to a different circuit/PDU so a single breaker doesn't kill everything.
2. **Network:** Plug the LEFT-most onboard NIC (port `1`) into the Cisco SG300 switch. Don't worry about the other 3 NICs yet — we'll use them later for Postgres replication and inter-server traffic.
3. **iDRAC (optional but recommended):** The dedicated `iDRAC` port (small, often labeled, separate from the 4 main NICs) also goes into the switch. iDRAC is Dell's out-of-band management — it lets you power-cycle and console into a server even if its OS is dead. Game-changer for debugging.

### 2.3 Plug the switch into your home router

One Ethernet from the Cisco SG300 → your home router. This gets all 5 servers internet access through your home connection.

### 2.4 Power on Server 1 only

Don't power on all 5 yet. Start with one. Keep the others off — fewer fans, easier to debug.

---

## 3. Server 1 — install Ubuntu Server 24.04

### 3.1 Burn Ubuntu Server 24.04 LTS to USB

On your laptop:

1. Download Ubuntu Server 24.04.1 LTS ISO: https://ubuntu.com/download/server (about 2.5 GB)
2. Download Rufus (Windows) or Balena Etcher (Mac/Linux): https://etcher.io
3. Insert the USB stick, open Etcher, select the ISO, select the USB drive, click Flash. Takes ~3 minutes.

### 3.2 Boot Server 1 from USB

1. Plug keyboard + monitor + USB stick into Server 1
2. Power on. As the Dell logo appears, mash **F11** to enter the boot menu
3. Select your USB stick (it'll show as "USB:Kingston" or similar)
4. Ubuntu installer loads in ~30 seconds

### 3.3 Walk through the Ubuntu installer

When prompted:

| Screen | What to pick | Why |
|---|---|---|
| Language | English | — |
| Keyboard | US (or your layout) | — |
| Network | DHCP on the wired interface | We'll lock down to a static IP later |
| Proxy | Leave blank | — |
| Mirror | Default Ubuntu mirror | — |
| Storage | "Use entire disk" + LVM | LVM gives you easy snapshots later |
| Profile | server name: `oracle-1`, your name + password | Pick a strong password — write it down |
| **SSH** | ✅ Install OpenSSH server | Critical — you'll never want to use the keyboard again after this |
| **Import SSH key** | If you have a GitHub account, paste your username | Lets you SSH in keyless |
| Featured server snaps | None | We're using Docker, don't want overlap |

After install (~10 min), it will ask to reboot. **Pull the USB stick before it reboots** or it'll boot the installer again.

### 3.4 First login

After reboot, log in at the console with the username + password you set.

```bash
# Update everything
sudo apt update && sudo apt upgrade -y

# Find this machine's IP address (write it down)
ip a | grep "inet " | grep -v 127.0.0.1
# You'll see something like: inet 192.168.1.42/24
```

Write down the IP. You'll SSH to it from now on.

### 3.5 SSH from your laptop

From your laptop terminal:

```bash
ssh your-username@192.168.1.42   # use the IP you just wrote down
```

You're in. **Unplug the keyboard + monitor from the server** — you don't need them anymore. Everything else happens via SSH.

### 3.6 Lock the IP address

So the server's IP doesn't change when your router reboots. Edit:

```bash
sudo nano /etc/netplan/00-installer-config.yaml
```

Replace contents with (adjust your interface name `eno1` or whatever shows in `ip a`):

```yaml
network:
  version: 2
  ethernets:
    eno1:
      dhcp4: false
      addresses: [192.168.1.41/24]
      routes:
        - to: default
          via: 192.168.1.1     # your home router IP
      nameservers:
        addresses: [1.1.1.1, 8.8.8.8]
```

Apply: `sudo netplan apply`. SSH session will drop briefly. Reconnect with the new IP.

We'll use IPs 192.168.1.41 through .45 for servers 1-5.

---

## 4. Server 1 — install Docker + essentials

```bash
# Required packages
sudo apt install -y ca-certificates curl gnupg lsb-release git tmux htop vim ufw

# Add Docker's official repo
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine + Compose
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Run Docker without sudo for your user
sudo usermod -aG docker $USER
newgrp docker   # apply group change without logging out

# Verify
docker --version
docker compose version
docker run hello-world    # should download and print "Hello from Docker!"
```

If `hello-world` works, Docker is ready.

---

## 5. Server 1 — install Tailscale

Tailscale gives you a private VPN between all your devices (laptop, phone, all 5 servers). No port forwarding, no public IPs exposed. Free for personal use up to 100 devices.

### 5.1 On the server

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

It prints a URL like `https://login.tailscale.com/a/abc123`. Open that in your laptop browser, log in with Google/Microsoft/email.

After auth, the server will show: `Success. Logged in as you@email.com`

```bash
# Get the Tailscale IP (will look like 100.x.y.z)
tailscale ip -4
# e.g. 100.96.42.51
```

### 5.2 On your laptop

Install Tailscale:
- **Mac:** download from https://tailscale.com/download/mac
- **Windows:** download from https://tailscale.com/download/windows
- **Linux:** same `curl | sh` command as above

Sign in with the SAME account.

### 5.3 Test it

From your laptop, anywhere in the world:

```bash
ssh your-username@oracle-1     # Tailscale auto-resolves the hostname
# OR
ssh your-username@100.96.42.51 # the Tailscale IP from above
```

You're SSH'd into the server through the Tailscale tunnel — encrypted, no port forwarding, no public exposure.

### 5.4 Optional but recommended: enable MagicDNS

In the Tailscale admin console (https://login.tailscale.com/admin/dns), toggle on **MagicDNS**. Now you can SSH using bare hostnames: `ssh your-username@oracle-1`.

---

## 6. Move your Oracle code from laptop to Server 1

### Option A: via Git (recommended)

If you keep your Oracle code in a git repo on GitHub/GitLab:

```bash
# On Server 1
cd ~
git clone https://github.com/your-username/oracle.git
cd oracle
ls   # confirm files are there
```

If the repo is private, you'll need a personal access token or an SSH deploy key — the GitHub error message will tell you which. The simplest path is:

1. On GitHub: Settings → Developer settings → Personal access tokens → Generate new (classic), with `repo` scope, copy the token
2. When git asks for password, paste the token

### Option B: via rsync (if your code is local-only)

From your laptop terminal:

```bash
# Replace /path/to/oracle with the actual path on your laptop
# Replace oracle-1 with the Tailscale hostname OR LAN IP
rsync -avz --progress \
  --exclude '__pycache__' --exclude '.venv' --exclude 'node_modules' \
  /path/to/oracle/ \
  your-username@oracle-1:~/oracle/
```

This copies your entire Oracle codebase to the server. Repeat any time you want to push local changes.

### Option C: via SCP (single-shot, no incremental)

```bash
scp -r /path/to/oracle your-username@oracle-1:~/
```

After it lands, on the server:

```bash
cd ~/oracle
ls artifacts/api-server/trading/
# You should see: agents.py server.py backtest.py predictions.db indicators.py etc.
```

---

## 7. Stand up Postgres + TimescaleDB in Docker

### 7.1 Create a docker-compose file

```bash
mkdir -p ~/oracle-stack
cd ~/oracle-stack
nano docker-compose.yml
```

Paste this:

```yaml
services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    container_name: oracle-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: oracle
      POSTGRES_USER: oracle
      POSTGRES_PASSWORD: change-this-to-a-strong-password
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U oracle -d oracle"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
```

### 7.2 Create the init SQL

```bash
nano init.sql
```

Paste:

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Mirror of the existing SQLite predictions table
CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    horizon TEXT NOT NULL,
    signal TEXT NOT NULL,
    confidence DOUBLE PRECISION,
    price_at_signal DOUBLE PRECISION,
    target_price DOUBLE PRECISION,
    stop_price DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    outcome TEXT,
    realized_return_pct DOUBLE PRECISION,
    raw_payload JSONB
);

-- Convert to TimescaleDB hypertable for fast range queries
SELECT create_hypertable('predictions', 'created_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_pred_symbol ON predictions (symbol);
CREATE INDEX IF NOT EXISTS idx_pred_horizon ON predictions (horizon);
CREATE INDEX IF NOT EXISTS idx_pred_outcome ON predictions (outcome);

-- Agent weights (per agent reliability scores)
CREATE TABLE IF NOT EXISTS agent_weights (
    agent_name TEXT PRIMARY KEY,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    n_signals INTEGER NOT NULL DEFAULT 0,
    n_wins INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Regime statistics (per market regime, per symbol stats)
CREATE TABLE IF NOT EXISTS regime_stats (
    regime TEXT NOT NULL,
    symbol TEXT NOT NULL,
    horizon TEXT NOT NULL,
    n_signals INTEGER DEFAULT 0,
    n_wins INTEGER DEFAULT 0,
    avg_return_pct DOUBLE PRECISION DEFAULT 0,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (regime, symbol, horizon)
);

-- Discovered strategies (from meta_learning)
CREATE TABLE IF NOT EXISTS discovered_strategies (
    id SERIAL PRIMARY KEY,
    rule TEXT NOT NULL,
    win_rate DOUBLE PRECISION,
    n_signals INTEGER,
    confidence_boost DOUBLE PRECISION,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active BOOLEAN DEFAULT TRUE
);
```

### 7.3 Bring it up

```bash
docker compose up -d
docker compose ps    # should show oracle-postgres "healthy"
docker compose logs postgres | tail -20
```

### 7.4 Verify the database

```bash
docker exec -it oracle-postgres psql -U oracle -d oracle -c "\dt"
# Should list: predictions, agent_weights, regime_stats, discovered_strategies
```

---

## 8. Migrate predictions.db (SQLite → Postgres)

This is your gold — every signal Oracle has ever made plus its outcome. We preserve every row.

### 8.1 Create the migration script

```bash
cd ~/oracle/artifacts/api-server/trading
nano migrate_to_postgres.py
```

Paste:

```python
#!/usr/bin/env python3
"""One-shot migration: predictions.db (SQLite) → oracle (Postgres + TimescaleDB)."""
import sqlite3
import os
import json
import sys

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("Installing psycopg2-binary...")
    os.system(f"{sys.executable} -m pip install psycopg2-binary")
    import psycopg2
    from psycopg2.extras import execute_values

SQLITE_PATH = os.environ.get("SQLITE_PATH", "predictions.db")
PG_DSN = os.environ.get("PG_DSN",
    "host=localhost port=5432 dbname=oracle user=oracle password=change-this-to-a-strong-password")

def main():
    if not os.path.exists(SQLITE_PATH):
        print(f"ERROR: {SQLITE_PATH} not found"); sys.exit(1)

    print(f"Reading from SQLite: {SQLITE_PATH}")
    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row

    print(f"Connecting to Postgres...")
    pg = psycopg2.connect(PG_DSN)
    pg.autocommit = False
    cur = pg.cursor()

    # Discover SQLite tables
    tables = [r[0] for r in sq.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()]
    print(f"SQLite tables found: {tables}")

    # Copy predictions
    if "predictions" in tables:
        rows = sq.execute("SELECT * FROM predictions").fetchall()
        print(f"  predictions: {len(rows)} rows")
        if rows:
            cols = rows[0].keys()
            # Map known columns; unknown ones go into raw_payload as JSON
            known = {"symbol","horizon","signal","confidence","price_at_signal",
                     "target_price","stop_price","created_at","resolved_at",
                     "outcome","realized_return_pct"}
            insertable = []
            for r in rows:
                d = dict(r)
                core = {k: d.get(k) for k in known if k in d}
                extras = {k: v for k, v in d.items() if k not in known and k != "id"}
                core["raw_payload"] = json.dumps(extras, default=str) if extras else None
                insertable.append(core)
            cols_list = list(insertable[0].keys())
            tpl = "(" + ",".join(["%s"] * len(cols_list)) + ")"
            sql = f"INSERT INTO predictions ({','.join(cols_list)}) VALUES %s"
            execute_values(cur, sql, [tuple(r[c] for c in cols_list) for r in insertable], template=tpl)
            print(f"  → inserted {len(insertable)} into Postgres")

    # Copy other known tables verbatim if they exist
    for t in ("agent_weights", "regime_stats", "discovered_strategies"):
        if t in tables:
            rows = sq.execute(f"SELECT * FROM {t}").fetchall()
            if not rows:
                continue
            print(f"  {t}: {len(rows)} rows")
            cols_list = list(rows[0].keys())
            try:
                cur.execute(f"DELETE FROM {t}")  # idempotent re-run
                tpl = "(" + ",".join(["%s"] * len(cols_list)) + ")"
                sql = f"INSERT INTO {t} ({','.join(cols_list)}) VALUES %s ON CONFLICT DO NOTHING"
                execute_values(cur, sql, [tuple(r[c] for c in cols_list) for r in rows], template=tpl)
                print(f"  → inserted into {t}")
            except Exception as e:
                print(f"  WARN: could not migrate {t}: {e}")

    pg.commit()
    print("Migration committed. Verifying counts...")
    cur.execute("SELECT COUNT(*) FROM predictions")
    print(f"Postgres predictions count: {cur.fetchone()[0]}")
    cur.close(); pg.close(); sq.close()
    print("DONE.")

if __name__ == "__main__":
    main()
```

### 8.2 Run the migration

```bash
cd ~/oracle/artifacts/api-server/trading
python3 migrate_to_postgres.py
```

You should see something like:

```
SQLite tables found: ['predictions', 'agent_weights', 'regime_stats', ...]
  predictions: 1247 rows
  → inserted 1247 into Postgres
  agent_weights: 30 rows
  → inserted into agent_weights
Postgres predictions count: 1247
DONE.
```

**Keep the original `predictions.db` file forever as a backup.** Don't delete it.

### 8.3 Verify in Postgres

```bash
docker exec -it oracle-postgres psql -U oracle -d oracle -c \
  "SELECT symbol, horizon, signal, confidence, created_at FROM predictions ORDER BY created_at DESC LIMIT 10;"
```

You should see your most recent 10 predictions.

---

## 9. Run Oracle inside Docker

### 9.1 Create a Dockerfile for Oracle

```bash
cd ~/oracle/artifacts/api-server/trading
nano Dockerfile
```

Paste:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir psycopg2-binary

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python3", "server.py"]
```

If `requirements.txt` doesn't exist yet, generate it on your laptop:

```bash
# On your laptop, in the oracle directory
cd artifacts/api-server/trading
pip freeze > requirements.txt
# Then push to the server (rsync command from section 6)
```

Or generate it inline if needed:

```bash
cat > requirements.txt <<'EOF'
fastapi
uvicorn[standard]
yfinance
pandas
numpy
scipy
scikit-learn
beautifulsoup4
requests
feedparser
python-dateutil
EOF
```

### 9.2 Add Oracle to docker-compose

Edit `~/oracle-stack/docker-compose.yml`:

```yaml
services:
  postgres:
    # ... (same as before, unchanged)

  oracle-api:
    build:
      context: /home/your-username/oracle/artifacts/api-server/trading
    container_name: oracle-api
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      PORT: 8080
      PG_DSN: "host=postgres port=5432 dbname=oracle user=oracle password=change-this-to-a-strong-password"
    ports:
      - "8080:8080"
    volumes:
      - /home/your-username/oracle/artifacts/api-server/trading:/app

volumes:
  postgres_data:
```

(Replace `your-username` with your actual Linux username.)

### 9.3 Build and start

```bash
cd ~/oracle-stack
docker compose up -d --build oracle-api
docker compose logs -f oracle-api
```

You should see:

```
INFO:     Started server process [1]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080
```

Press Ctrl-C to detach (it keeps running in background).

### 9.4 Test it

From the server itself:

```bash
curl -s http://localhost:8080/api/health
# {"status":"ok","agents":31,"version":"7.1.0-30-agent-expansion"}

curl -s "http://localhost:8080/api/analyze/SPY?horizon=swing" | head -c 200
```

From your laptop (over Tailscale):

```bash
curl -s http://oracle-1:8080/api/health
# Same response — proves Tailscale is routing
```

You can also open `http://oracle-1:8080/api/health` in your laptop's browser.

---

## 10. Servers 2–5: replicate the install

For each remaining server, repeat **sections 3, 4, 5** with these changes:

| Server | Hostname | LAN IP | Role |
|---|---|---|---|
| 1 | `oracle-1` | 192.168.1.41 | API + frontend (already done) |
| 2 | `oracle-2` | 192.168.1.42 | Agent compute cluster |
| 3 | `oracle-3` | 192.168.1.43 | Database + data ingestion |
| 4 | `oracle-4` | 192.168.1.44 | Backtester + ML training lab |
| 5 | `oracle-5` | 192.168.1.45 | Monitoring + hot standby |

### 10.1 Speed up servers 2-5 with a script

Once Server 1 works, save your install commands as a one-liner script. From your laptop:

```bash
ssh your-username@oracle-1 "cat > ~/setup-server.sh" <<'SCRIPT'
#!/usr/bin/env bash
set -e
sudo apt update && sudo apt upgrade -y
sudo apt install -y ca-certificates curl gnupg lsb-release git tmux htop vim ufw
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
echo "DONE. Reboot recommended."
SCRIPT
```

For each new server, after you do the Ubuntu install + first SSH, just `scp` this script over and run it:

```bash
scp ~/setup-server.sh your-username@192.168.1.42:~/
ssh your-username@192.168.1.42 "bash setup-server.sh"
```

### 10.2 Move the right code to the right server

| Server | What to copy from your laptop / Server 1 |
|---|---|
| Server 2 (compute) | Same Oracle codebase — runs the agent containers |
| Server 3 (database) | Just `docker-compose.yml` + `init.sql` — moves Postgres here |
| Server 4 (lab) | Oracle codebase + the `backtest.py` script |
| Server 5 (monitoring) | Just docker-compose for Grafana/Prometheus/Loki (next section) |

---

## 11. Networking the 5 servers together

### 11.1 Move Postgres to Server 3

Once Server 3 is up:

1. SSH into Server 3
2. Copy `~/oracle-stack/docker-compose.yml` and `init.sql` from Server 1 to Server 3
3. **Take a Postgres backup on Server 1:** `docker exec oracle-postgres pg_dump -U oracle oracle > /tmp/oracle-dump.sql`
4. Copy that dump to Server 3: `scp your-username@oracle-1:/tmp/oracle-dump.sql ~/`
5. On Server 3, start Postgres: `cd ~/oracle-stack && docker compose up -d postgres`
6. Restore: `docker exec -i oracle-postgres psql -U oracle -d oracle < ~/oracle-dump.sql`
7. Verify: `docker exec -it oracle-postgres psql -U oracle -d oracle -c "SELECT COUNT(*) FROM predictions;"`
8. On Server 1, edit `docker-compose.yml`: change `PG_DSN` to point at `oracle-3`:
   ```yaml
   PG_DSN: "host=oracle-3 port=5432 dbname=oracle user=oracle password=..."
   ```
9. On Server 1, stop the local Postgres: remove the `postgres:` service from compose, then `docker compose up -d` (oracle-api now talks to Server 3's Postgres over Tailscale)

### 11.2 Distribute the agent containers to Server 2

Once you split the codebase into per-tier agent processes (a future refactor), Server 2 hosts those. For v7.1 right now, all 30 agents live in one Python process on Server 1 — fine for the current load (Oracle uses maybe 1-2% of one Xeon core).

Move agents to Server 2 when:
- You add real-time data feeds that need always-on processing
- You decide to run agents as separate microservices

**Until then, Server 2 sits idle as a hot standby.** That's acceptable — you have headroom you'll grow into.

---

## 12. Monitoring stack

On **Server 5**, set up Grafana + Prometheus + Loki. These are all free and open-source.

### 12.1 Create monitoring compose

```bash
mkdir -p ~/monitoring && cd ~/monitoring
nano docker-compose.yml
```

Paste:

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    restart: unless-stopped
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_PASSWORD: change-me-too
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana

  loki:
    image: grafana/loki:latest
    container_name: loki
    restart: unless-stopped
    ports:
      - "3100:3100"

  node-exporter:
    image: prom/node-exporter:latest
    container_name: node-exporter
    restart: unless-stopped
    ports:
      - "9100:9100"
    pid: host
    volumes:
      - /:/host:ro,rslave

volumes:
  prometheus_data:
  grafana_data:
```

### 12.2 Prometheus config

```bash
nano prometheus.yml
```

Paste:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets:
          - 'oracle-1:9100'
          - 'oracle-2:9100'
          - 'oracle-3:9100'
          - 'oracle-4:9100'
          - 'oracle-5:9100'
```

### 12.3 Bring it up

```bash
docker compose up -d
```

### 12.4 Install node-exporter on Servers 1-4

So Prometheus can scrape their CPU/RAM/disk. On each:

```bash
ssh your-username@oracle-1 "docker run -d --restart unless-stopped --pid host --name node-exporter -p 9100:9100 -v /:/host:ro,rslave prom/node-exporter:latest"
# repeat for oracle-2, oracle-3, oracle-4
```

### 12.5 Open Grafana

In your laptop browser: `http://oracle-5:3000`

Login: `admin` / `change-me-too` (change on first login).

Add Prometheus as a data source:
1. Configuration → Data sources → Add → Prometheus
2. URL: `http://prometheus:9090`
3. Save & test

Import a pre-built dashboard:
1. Dashboards → Import → enter dashboard ID `1860` (Node Exporter Full)
2. Pick Prometheus as data source → Import

You now have a live dashboard showing CPU/RAM/disk/network for all 5 servers.

---

## 13. Day-to-day maintenance

### 13.1 Update Oracle code

**On your laptop**, make changes locally. Test them. Then push:

```bash
# If using git
git add -A && git commit -m "your changes" && git push
ssh your-username@oracle-1 "cd ~/oracle && git pull && cd ~/oracle-stack && docker compose restart oracle-api"

# OR if using rsync
rsync -avz --exclude __pycache__ --exclude .venv \
  /path/to/oracle/ your-username@oracle-1:~/oracle/
ssh your-username@oracle-1 "cd ~/oracle-stack && docker compose restart oracle-api"
```

### 13.2 View live logs

```bash
ssh your-username@oracle-1 "cd ~/oracle-stack && docker compose logs -f oracle-api"
# Ctrl-C to stop tailing
```

### 13.3 Backup the database (run nightly via cron)

```bash
# On Server 3 (or Server 5 backup target)
mkdir -p ~/backups
crontab -e
# Add this line:
0 3 * * * docker exec oracle-postgres pg_dump -U oracle oracle | gzip > /home/your-username/backups/oracle-$(date +\%Y\%m\%d).sql.gz
```

This creates a timestamped backup every day at 3 AM.

### 13.4 Restart everything cleanly

```bash
ssh your-username@oracle-1 "cd ~/oracle-stack && docker compose restart"
```

### 13.5 Power-cycle a stuck server (via iDRAC)

If a server's OS hangs and SSH doesn't work:

1. Open `https://192.168.1.41-iDRAC` in browser (find the iDRAC IP via `arp -a` on your router)
2. Login (default `root` / `calvin` if not changed — CHANGE THIS in iDRAC settings)
3. Power → Hard Reset

---

## 14. Troubleshooting

### "Docker says permission denied"
You forgot `sudo usermod -aG docker $USER`. Run it, then log out + back in.

### "Postgres connection refused from oracle-api"
The two containers must share a network or use Tailscale hostnames. If both are on the same Server 1, Docker Compose puts them on the same network automatically — `host: postgres` works. If across servers, use the Tailscale hostname (`oracle-3`).

### "Tailscale hostname doesn't resolve"
Enable MagicDNS in the Tailscale admin console (https://login.tailscale.com/admin/dns).

### "yfinance keeps rate-limiting"
The free yfinance API throttles aggressively. Solutions:
- Spread requests over more time (Oracle's existing cache helps)
- Add a small `time.sleep(0.3)` between bulk fetches
- Add longer waits between bulk fetches in your code if you scale up symbols

### "Server fans are jet-engine loud"
R740xd XLs run their fans at 100% under any load. Solutions:
- Put them in a garage or basement (ideal)
- Use iDRAC to set a custom fan curve (Settings → Cooling → Fan profile → Sound) — only works on iDRAC Enterprise license
- Sound dampening foam on the room walls helps a few dB

### "Can't access Grafana/API from phone"
Install the Tailscale app on your phone. Once logged in with the same account, you can hit `http://oracle-5:3000` from anywhere. No public IP, no port forwarding.

### "Server 1 is hot. Like really hot."
Normal for these chassis. Idle CPU temp 50-65°C, load 75-85°C is fine. Above 90°C sustained = bad airflow. Make sure the rack has 4-6" clearance behind it for exhaust.

---

## Quick reference card

```bash
# Tail all Oracle logs
ssh oracle-1 "cd ~/oracle-stack && docker compose logs -f oracle-api"

# Restart Oracle without rebuild
ssh oracle-1 "cd ~/oracle-stack && docker compose restart oracle-api"

# Rebuild Oracle after code changes
ssh oracle-1 "cd ~/oracle-stack && docker compose up -d --build oracle-api"

# Postgres shell
ssh oracle-3 "docker exec -it oracle-postgres psql -U oracle -d oracle"

# Manual backup
ssh oracle-3 "docker exec oracle-postgres pg_dump -U oracle oracle | gzip > ~/backups/manual-$(date +%Y%m%d-%H%M).sql.gz"

# Sync local code → server
rsync -avz --exclude __pycache__ ~/path/to/oracle/ oracle-1:~/oracle/

# Check API
curl -s http://oracle-1:8080/api/health
curl -s "http://oracle-1:8080/api/analyze/SPY?horizon=swing" | jq .judgment

# Check all 5 servers are alive on Tailscale
for h in oracle-1 oracle-2 oracle-3 oracle-4 oracle-5; do
  echo -n "$h: "; ssh -o ConnectTimeout=3 $h "uptime" 2>&1 | head -1
done
```

---

## What's next (after this is running) — all free

Once Oracle is running on the cluster, the next free upgrades:

1. **FRED API for macro data** — free, register at https://fred.stlouisfed.org/docs/api/api_key.html. Wires the Yield Curve and Macro Events agents.
2. **SEC EDGAR Form 4 RSS** — free, no key needed. Wires the Insider Trading agent with real filings.
3. **FINRA short interest** — free public CSV downloads. Wires the Short Interest agent with real bi-monthly numbers.
4. **Postgres-backed predictions read path** — refactor `signal_persistence.py` to query Postgres instead of SQLite, so all 5 servers see the same prediction history.
5. **Per-tier agent containers** — split the 30 agents across Server 2's CPU cores so the API server stays snappy.

Every one of these is free. Keep the rack running this baseline first to make sure the hardware works for you, then layer on the data sources one at a time.
