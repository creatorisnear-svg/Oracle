#!/usr/bin/env bash
# ============================================================================
# bootstrap-oracle-cluster.sh
#
# One-shot setup for any node in the Oracle / TradeSignal AI cluster.
# Idempotent: safe to re-run. Reads/writes only inside $HOME and /etc.
#
# USAGE
#   curl -fsSL https://raw.githubusercontent.com/<you>/oracle/main/scripts/bootstrap-oracle-cluster.sh | bash -s -- --role api
#   ssh oracle-1 "bash -s -- --role api --hostname oracle-1" < bootstrap-oracle-cluster.sh
#   bash bootstrap-oracle-cluster.sh --role db --hostname oracle-3
#
# REQUIRED FLAGS
#   --role <api|db|monitor|lab|standby>
#       api      = oracle-1 / oracle-2 — Python API + nginx + frontend
#       db       = oracle-3            — Postgres + TimescaleDB
#       monitor  = oracle-5            — Prometheus + Grafana + Loki
#       lab      = oracle-4            — bare host for backtest sweeps
#       standby  = identical to api but skips nginx (backup API only)
#
# OPTIONAL FLAGS
#   --hostname <name>      Set the system hostname (e.g. oracle-1)
#   --pg-host <host>       Postgres hostname for api role  (default: oracle-3)
#   --pg-pass <pass>       Postgres password               (default: prompt)
#   --code-dir <path>      Where Oracle source lives       (default: $HOME/oracle)
#   --skip-tailscale       Don't install/start Tailscale
#   --skip-docker          Don't install Docker (assume present)
#   --no-build             Bring up containers from existing images, no docker build
#   -h | --help            This message
#
# WHAT IT DOES (per role)
#   ALL ROLES: apt update, install essentials, Docker, Tailscale,
#              node-exporter on :9100, UFW allow Tailscale subnet
#   db:        bring up timescale/timescaledb-pg16 with init.sql schema
#   api:       build & run oracle-api container, nginx with SPA + /api proxy
#   standby:   build & run oracle-api container only (no nginx)
#   monitor:   bring up Prometheus + Grafana + Loki + node-exporter stack
#   lab:       essentials only (run backtests manually with `python backtest.py`)
# ============================================================================

set -euo pipefail

# ---------- defaults ----------
ROLE=""
HOSTNAME_NEW=""
PG_HOST="oracle-3"
PG_PASS=""
CODE_DIR="$HOME/oracle"
SKIP_TAILSCALE=0
SKIP_DOCKER=0
NO_BUILD=0

# ---------- arg parse ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)            ROLE="$2"; shift 2 ;;
    --hostname)        HOSTNAME_NEW="$2"; shift 2 ;;
    --pg-host)         PG_HOST="$2"; shift 2 ;;
    --pg-pass)         PG_PASS="$2"; shift 2 ;;
    --code-dir)        CODE_DIR="$2"; shift 2 ;;
    --skip-tailscale)  SKIP_TAILSCALE=1; shift ;;
    --skip-docker)     SKIP_DOCKER=1; shift ;;
    --no-build)        NO_BUILD=1; shift ;;
    -h|--help)         sed -n '2,40p' "$0"; exit 0 ;;
    *)                 echo "Unknown flag: $1"; exit 1 ;;
  esac
done

if [[ -z "$ROLE" ]]; then
  echo "ERROR: --role is required (api | db | monitor | lab | standby)"
  echo "Run with --help for usage."
  exit 1
fi

case "$ROLE" in
  api|db|monitor|lab|standby) ;;
  *) echo "ERROR: invalid role '$ROLE'"; exit 1 ;;
esac

# ---------- helpers ----------
log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

require_root_or_sudo() {
  if [[ $EUID -ne 0 ]] && ! command -v sudo >/dev/null; then
    die "Need root or sudo on PATH"
  fi
}

prompt_pg_pass_if_needed() {
  if [[ "$ROLE" == "db" || "$ROLE" == "api" || "$ROLE" == "standby" ]]; then
    if [[ -z "$PG_PASS" ]]; then
      if [[ -f "$HOME/.oracle-secrets" ]] && grep -q '^POSTGRES_PASSWORD=' "$HOME/.oracle-secrets"; then
        # Reuse password from a previous run
        PG_PASS="$(grep '^POSTGRES_PASSWORD=' "$HOME/.oracle-secrets" | cut -d= -f2-)"
        log "Loaded Postgres password from ~/.oracle-secrets"
      else
        read -rsp "Postgres password (will be saved to ~/.oracle-secrets, chmod 600): " PG_PASS
        echo
        [[ -z "$PG_PASS" ]] && die "Empty password"
      fi
    fi
    umask 077
    cat > "$HOME/.oracle-secrets" <<EOF
POSTGRES_PASSWORD=$PG_PASS
EOF
    chmod 600 "$HOME/.oracle-secrets"
  fi
}

# ============================================================================
# COMMON: hostname, apt, essentials, Docker, Tailscale, node-exporter, UFW
# ============================================================================
phase_common() {
  require_root_or_sudo

  if [[ -n "$HOSTNAME_NEW" ]]; then
    log "Setting hostname → $HOSTNAME_NEW"
    sudo hostnamectl set-hostname "$HOSTNAME_NEW"
  fi

  log "apt update + upgrade (this is the slow step, 2-5 min on a fresh box)"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get -y -qq upgrade

  log "Install base packages"
  sudo apt-get install -y -qq \
    ca-certificates curl gnupg lsb-release git tmux htop vim ufw rsync jq

  if [[ $SKIP_DOCKER -eq 0 ]]; then
    if ! command -v docker >/dev/null; then
      log "Install Docker Engine + Compose plugin"
      sudo install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      sudo chmod a+r /etc/apt/keyrings/docker.gpg
      ARCH="$(dpkg --print-architecture)"
      CODENAME="$(lsb_release -cs)"
      echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $CODENAME stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
      sudo apt-get update -qq
      sudo apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    else
      log "Docker already installed → skipping"
    fi
    if ! groups "$USER" | grep -q docker; then
      log "Adding $USER to docker group (re-login or run 'newgrp docker' to apply)"
      sudo usermod -aG docker "$USER"
    fi
  fi

  if [[ $SKIP_TAILSCALE -eq 0 ]]; then
    if ! command -v tailscale >/dev/null; then
      log "Install Tailscale"
      curl -fsSL https://tailscale.com/install.sh | sh
    fi
    if ! sudo tailscale status >/dev/null 2>&1; then
      log "Bringing Tailscale up — open the URL it prints, log in with the SAME account on every box"
      sudo tailscale up || warn "Tailscale up returned non-zero (you may need to auth interactively)"
    else
      log "Tailscale already up: $(sudo tailscale ip -4 || echo unknown)"
    fi
  fi

  log "Start node-exporter on :9100 for Prometheus to scrape"
  if ! sudo docker ps --format '{{.Names}}' | grep -q '^node-exporter$'; then
    sudo docker run -d --restart unless-stopped --pid host \
      --name node-exporter -p 9100:9100 -v /:/host:ro,rslave \
      prom/node-exporter:latest >/dev/null
  fi

  log "Configure UFW: allow SSH + Tailscale subnet"
  sudo ufw --force reset >/dev/null
  sudo ufw default deny incoming >/dev/null
  sudo ufw default allow outgoing >/dev/null
  sudo ufw allow 22/tcp comment 'SSH' >/dev/null
  sudo ufw allow in on tailscale0 comment 'Tailscale' >/dev/null
  # Open frontend ports only on the api role; everything else stays Tailscale-only
  if [[ "$ROLE" == "api" ]]; then
    sudo ufw allow 80/tcp  comment 'nginx HTTP'  >/dev/null
    sudo ufw allow 443/tcp comment 'nginx HTTPS' >/dev/null
  fi
  sudo ufw --force enable >/dev/null
}

# ============================================================================
# DB ROLE: Postgres + TimescaleDB
# ============================================================================
phase_db() {
  log "Setting up Postgres + TimescaleDB"
  mkdir -p "$HOME/oracle-stack"
  cd "$HOME/oracle-stack"

  cat > docker-compose.yml <<EOF
services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    container_name: oracle-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: oracle
      POSTGRES_USER: oracle
      POSTGRES_PASSWORD: \${POSTGRES_PASSWORD}
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
EOF

  cat > init.sql <<'EOF'
CREATE EXTENSION IF NOT EXISTS timescaledb;

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
SELECT create_hypertable('predictions', 'created_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_pred_symbol  ON predictions (symbol);
CREATE INDEX IF NOT EXISTS idx_pred_horizon ON predictions (horizon);
CREATE INDEX IF NOT EXISTS idx_pred_outcome ON predictions (outcome);

CREATE TABLE IF NOT EXISTS agent_weights (
    agent_name TEXT PRIMARY KEY,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    n_signals INTEGER NOT NULL DEFAULT 0,
    n_wins INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS discovered_strategies (
    id SERIAL PRIMARY KEY,
    rule TEXT NOT NULL,
    win_rate DOUBLE PRECISION,
    n_signals INTEGER,
    confidence_boost DOUBLE PRECISION,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active BOOLEAN DEFAULT TRUE
);
EOF

  log "Starting Postgres container"
  POSTGRES_PASSWORD="$PG_PASS" sudo -E docker compose up -d
  log "Waiting for Postgres to be healthy"
  for i in {1..30}; do
    if sudo docker exec oracle-postgres pg_isready -U oracle -d oracle >/dev/null 2>&1; then
      log "Postgres is ready"
      break
    fi
    sleep 2
  done

  log "Schema verification:"
  sudo docker exec oracle-postgres psql -U oracle -d oracle -c "\dt" || warn "psql check failed"

  log "Nightly backup cron — pg_dump | gzip → ~/backups/"
  mkdir -p "$HOME/backups"
  CRON_LINE="0 3 * * * docker exec oracle-postgres pg_dump -U oracle oracle | gzip > $HOME/backups/oracle-\$(date +\\%Y\\%m\\%d).sql.gz"
  ( crontab -l 2>/dev/null | grep -v 'oracle-postgres pg_dump' ; echo "$CRON_LINE" ) | crontab -
  log "Cron installed: 3 AM daily snapshot"
}

# ============================================================================
# API ROLE: build oracle-api container, configure nginx
# ============================================================================
phase_api() {
  if [[ ! -d "$CODE_DIR/artifacts/api-server/trading" ]]; then
    die "Source code not found at $CODE_DIR/artifacts/api-server/trading
Push your code first:  rsync -avz <local>/oracle/ <this-host>:$CODE_DIR/"
  fi

  log "Writing Dockerfile + requirements.txt for v7.1.2"
  cd "$CODE_DIR/artifacts/api-server/trading"

  cat > requirements.txt <<'EOF'
fastapi>=0.136.1
uvicorn[standard]>=0.46.0
httpx>=0.28.1
websockets>=16.0
yfinance>=1.3.0
pandas>=3.0.2
numpy>=2.4.4
scipy>=1.17.1
ta>=0.11.0
pytrends>=4.9.2
requests>=2.33.1
psycopg2-binary>=2.9
EOF

  cat > Dockerfile <<'EOF'
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
ENV OMP_NUM_THREADS=4
ENV MKL_NUM_THREADS=4
ENV OPENBLAS_NUM_THREADS=4
EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4"]
EOF

  log "Writing oracle-api docker-compose at ~/oracle-stack"
  mkdir -p "$HOME/oracle-stack"
  cd "$HOME/oracle-stack"

  cat > docker-compose.yml <<EOF
services:
  oracle-api:
    build:
      context: $CODE_DIR/artifacts/api-server/trading
    container_name: oracle-api
    restart: unless-stopped
    environment:
      PORT: 8080
      PG_DSN: "host=$PG_HOST port=5432 dbname=oracle user=oracle password=\${POSTGRES_PASSWORD}"
    ports:
      - "8080:8080"
    volumes:
      - $CODE_DIR/artifacts/api-server/trading:/app
EOF

  if [[ $NO_BUILD -eq 0 ]]; then
    log "Building oracle-api image (first run takes 2-3 min)"
    POSTGRES_PASSWORD="$PG_PASS" sudo -E docker compose build oracle-api
  fi

  log "Starting oracle-api container"
  POSTGRES_PASSWORD="$PG_PASS" sudo -E docker compose up -d oracle-api

  log "Waiting for /api/health"
  for i in {1..30}; do
    if curl -sf http://localhost:8080/api/health >/dev/null 2>&1; then
      log "API is healthy"
      break
    fi
    sleep 2
  done
  curl -s http://localhost:8080/api/health | head -c 200 || warn "Health check did not return 200"
  echo

  if [[ "$ROLE" == "api" ]]; then
    phase_nginx
  fi
}

# ============================================================================
# NGINX: SPA + /api reverse proxy with WebSocket upgrade
# ============================================================================
phase_nginx() {
  log "Installing nginx + writing site config"
  sudo apt-get install -y -qq nginx

  FRONT_DIR="$HOME/oracle-frontend"
  if [[ ! -d "$FRONT_DIR" ]]; then
    warn "Frontend not found at $FRONT_DIR"
    warn "On your laptop run:"
    warn "    cd artifacts/mockup-sandbox && PORT=5000 BASE_PATH=/ pnpm build"
    warn "    rsync -avz dist/ $USER@$(hostname):$FRONT_DIR/"
    warn "Then re-run this script — nginx will pick it up."
    mkdir -p "$FRONT_DIR"
    cat > "$FRONT_DIR/index.html" <<HTML
<!doctype html><html><body>
<h1>oracle-api up — frontend not yet uploaded</h1>
<p>Build & rsync <code>artifacts/mockup-sandbox/dist/</code> here.</p>
</body></html>
HTML
  fi

  sudo tee /etc/nginx/sites-available/oracle >/dev/null <<EOF
server {
    listen 80 default_server;
    server_name _;

    root $FRONT_DIR;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_read_timeout                 1h;
    }
}
EOF
  sudo ln -sf /etc/nginx/sites-available/oracle /etc/nginx/sites-enabled/oracle
  sudo rm -f /etc/nginx/sites-enabled/default
  sudo nginx -t
  sudo systemctl reload nginx
  log "nginx serving SPA from $FRONT_DIR  →  proxying /api → 127.0.0.1:8080"
}

# ============================================================================
# MONITOR ROLE: Prometheus + Grafana + Loki
# ============================================================================
phase_monitor() {
  log "Setting up Prometheus + Grafana + Loki"
  mkdir -p "$HOME/monitoring"
  cd "$HOME/monitoring"

  cat > docker-compose.yml <<'EOF'
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
      GF_SECURITY_ADMIN_PASSWORD: change-me-on-first-login
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

volumes:
  prometheus_data:
  grafana_data:
EOF

  cat > prometheus.yml <<'EOF'
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
EOF

  sudo docker compose up -d
  log "Grafana at  http://$(hostname):3000   (admin / change-me-on-first-login)"
  log "Prometheus  http://$(hostname):9090"
}

# ============================================================================
# LAB ROLE: bare host, no extras
# ============================================================================
phase_lab() {
  log "Lab role: common setup only. Run backtests with:"
  log "   cd $CODE_DIR/artifacts/api-server/trading && python3 backtest.py"
}

# ============================================================================
# MAIN
# ============================================================================
log "=== Oracle bootstrap — role=$ROLE host=${HOSTNAME_NEW:-$(hostname)} ==="

prompt_pg_pass_if_needed
phase_common

case "$ROLE" in
  db)       phase_db ;;
  api)      phase_api ;;
  standby)  phase_api ;;
  monitor)  phase_monitor ;;
  lab)      phase_lab ;;
esac

log "=== DONE — role=$ROLE ==="
log ""
log "Next steps:"
case "$ROLE" in
  db)
    log "  1. (one-time) migrate predictions.db with migrate_to_postgres.py from the deploy guide"
    log "  2. Verify: docker exec -it oracle-postgres psql -U oracle -d oracle -c 'SELECT COUNT(*) FROM predictions;'"
    ;;
  api|standby)
    log "  1. Health: curl http://localhost:8080/api/health"
    log "  2. Logs:   cd ~/oracle-stack && sudo docker compose logs -f oracle-api"
    [[ "$ROLE" == "api" ]] && log "  3. Open: http://$(hostname)/  (Tailscale or LAN)"
    ;;
  monitor)
    log "  1. Open Grafana → add Prometheus datasource (http://prometheus:9090)"
    log "  2. Import dashboard ID 1860 (Node Exporter Full)"
    ;;
  lab)
    log "  1. Push code: rsync -avz <laptop>/oracle/ $(hostname):$CODE_DIR/"
    log "  2. Backtest: cd $CODE_DIR/artifacts/api-server/trading && python3 backtest.py"
    ;;
esac
