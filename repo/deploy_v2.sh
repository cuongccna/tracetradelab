#!/usr/bin/env bash
# TraceTradeLab deploy v2
# Deploy thẳng tại thư mục repo được clone về — không copy sang thư mục khác.
#
# Usage:
#   bash deploy_v2.sh
#
# Optional env overrides:
#   DASHBOARD_PORT=8888
#   FREQTRADE_UI_PORT=8080
#   FREQTRADE_SERVICE=freqtrade
#   PUBLIC_HOST=your-domain-or-ip
set -Eeuo pipefail

DASHBOARD_PORT="${DASHBOARD_PORT:-8888}"
FREQTRADE_UI_PORT="${FREQTRADE_UI_PORT:-8080}"
FREQTRADE_SERVICE="${FREQTRADE_SERVICE:-freqtrade}"

# Root = thư mục chứa script này (tức là thư mục repo vừa clone)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_ROOT/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

# Các đường dẫn con
DASHBOARD_DIR="$REPO_ROOT/dashboard"
SIGNAL_BRIDGE_DIR="$REPO_ROOT/signal_bridge"
FREQUI_DIR="$REPO_ROOT/freqUI"
TRADINGAGENTS_DIR="$REPO_ROOT/tradingagents-src"
FREQTRADE_DIR="$REPO_ROOT/freqtrade"
LOG_DIR="$REPO_ROOT/logs"

# ── helpers ────────────────────────────────────────────────────────────────
log()  { printf "\n\033[1;36m%s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32mOK\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33mWARN\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31mERR\033[0m %s\n" "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1 — please install it first"
}

public_host() {
  if [ -n "${PUBLIC_HOST:-}" ]; then
    printf "%s" "$PUBLIC_HOST"; return
  fi
  if command -v curl >/dev/null 2>&1; then
    local ip
    ip="$(curl -fsS --max-time 3 https://ifconfig.me 2>/dev/null || true)"
    [ -n "$ip" ] && { printf "%s" "$ip"; return; }
  fi
  hostname -I 2>/dev/null | awk '{print $1}'
}

freqtrade_bind_host() {
  local env_file="$FREQTRADE_DIR/.env"
  if [ -f "$env_file" ]; then
    grep -E '^FREQTRADE_BIND_HOST=' "$env_file" | tail -1 | cut -d= -f2- || true
  fi
}

# ── preflight ──────────────────────────────────────────────────────────────
log "TraceTradeLab deploy v2 — root: $REPO_ROOT"
require_cmd python3
require_cmd docker
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is required"

if ! command -v pm2 >/dev/null 2>&1; then
  require_cmd npm
  log "Installing PM2 globally"
  sudo npm install -g pm2
fi

# ── directories ────────────────────────────────────────────────────────────
log "Ensure runtime directories"
mkdir -p \
  "$LOG_DIR" \
  "$DASHBOARD_DIR/static" \
  "$FREQTRADE_DIR/user_data/logs"

# Freqtrade Docker chạy uid 1000 — cần quyền ghi user_data
sudo chown -R 1000:1000 "$FREQTRADE_DIR/user_data"
ok "Directories ready"

# ── env files (chỉ tạo lần đầu, không ghi đè) ─────────────────────────────
log "Check env files"
if [ ! -f "$REPO_ROOT/.env" ]; then
  if [ -f "$REPO_ROOT/.env.example" ]; then
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
    warn "Created $REPO_ROOT/.env — điền API keys trước khi chạy agent"
  else
    warn ".env.example không tồn tại, tạo file $REPO_ROOT/.env rỗng"
    touch "$REPO_ROOT/.env"
  fi
fi

if [ ! -f "$FREQTRADE_DIR/.env" ]; then
  if [ -f "$FREQTRADE_DIR/.env.example" ]; then
    cp "$FREQTRADE_DIR/.env.example" "$FREQTRADE_DIR/.env"
    warn "Created $FREQTRADE_DIR/.env — điền Freqtrade API secrets"
  else
    warn "$FREQTRADE_DIR/.env.example không tồn tại"
  fi
fi

if [ ! -f "$FREQTRADE_DIR/user_data/config.json" ]; then
  for candidate in \
      "$FREQTRADE_DIR/user_data/config.example.json" \
      "$FREQTRADE_DIR/user_data/config.spot.example.json"; do
    if [ -f "$candidate" ]; then
      cp "$candidate" "$FREQTRADE_DIR/user_data/config.json"
      warn "Created config.json từ $candidate — kiểm tra lại trước khi live"
      break
    fi
  done
fi
ok "Env files checked"

# ── Python venv ────────────────────────────────────────────────────────────
log "Set up Python virtualenv ($VENV)"
python3 -m venv "$VENV"
"$PIP" install --upgrade pip
"$PIP" install -r "$REPO_ROOT/requirements.txt"

if [ -f "$TRADINGAGENTS_DIR/pyproject.toml" ] || [ -f "$TRADINGAGENTS_DIR/setup.py" ]; then
  "$PIP" install -e "$TRADINGAGENTS_DIR"
fi
ok "Python venv ready"

# ── database init ──────────────────────────────────────────────────────────
log "Initialize dashboard database"
(
  cd "$DASHBOARD_DIR"
  REPO_ROOT="$REPO_ROOT" \
  TRACE_DB_PATH="$DASHBOARD_DIR/tracetrader.db" \
  "$PYTHON" -c "from db_v2 import init_db; init_db()"
)
ok "Dashboard DB ready"

# ── Freqtrade Docker ───────────────────────────────────────────────────────
log "Start Freqtrade Docker container"
(
  cd "$FREQTRADE_DIR"
  docker compose pull freqtrade || warn "docker compose pull failed — using cached image"
  docker compose up -d freqtrade
)
ok "Freqtrade container running"

# ── resolve URLs ───────────────────────────────────────────────────────────
HOST="$(public_host)"
[ -n "$HOST" ] || HOST="localhost"
BIND_HOST="$(freqtrade_bind_host)"
[ -n "$BIND_HOST" ] || BIND_HOST="127.0.0.1"

DASHBOARD_URL="${DASHBOARD_URL:-http://$HOST:$DASHBOARD_PORT}"
if [ "$BIND_HOST" = "127.0.0.1" ] || [ "$BIND_HOST" = "localhost" ]; then
  FREQTRADE_UI_URL="${FREQTRADE_UI_URL:-http://127.0.0.1:$FREQTRADE_UI_PORT}"
else
  FREQTRADE_UI_URL="${FREQTRADE_UI_URL:-http://$HOST:$FREQTRADE_UI_PORT}"
fi

# ── PM2 ecosystem ──────────────────────────────────────────────────────────
log "Configure PM2"
cat > "$REPO_ROOT/ecosystem.config.cjs" <<EOF
module.exports = {
  apps: [{
    name: "tracetrader-dashboard",
    cwd: "$DASHBOARD_DIR",
    script: "$VENV/bin/uvicorn",
    args: "api_v2:app --host 0.0.0.0 --port $DASHBOARD_PORT --workers 1",
    interpreter: "none",
    autorestart: true,
    max_restarts: 10,
    restart_delay: 3000,
    env: {
      REPO_ROOT: "$REPO_ROOT",
      TRACE_DASHBOARD_DIR: "$DASHBOARD_DIR",
      TRACE_LOG_DIR: "$LOG_DIR",
      TRACE_DB_PATH: "$DASHBOARD_DIR/tracetrader.db",
      TRACE_ENV_FILE: "$REPO_ROOT/.env",
      TRACE_VENV_PYTHON: "$PYTHON",
      TRADINGAGENTS_SRC: "$TRADINGAGENTS_DIR",
      FREQTRADE_CONFIG_PATH: "$FREQTRADE_DIR/user_data/config.json",
      FREQTRADE_ENV_PATH: "$FREQTRADE_DIR/.env",
      FREQTRADE_COMPOSE_FILE: "$FREQTRADE_DIR/docker-compose.yml",
      FREQTRADE_SERVICE: "$FREQTRADE_SERVICE",
      FREQTRADE_UI_URL: "$FREQTRADE_UI_URL",
      DASHBOARD_URL: "$DASHBOARD_URL"
    }
  }]
}
EOF

pm2 startOrReload "$REPO_ROOT/ecosystem.config.cjs" --update-env
pm2 save
pm2 startup systemd -u "$USER" --hp "$HOME" >/tmp/tracetrader-pm2-startup.log 2>&1 \
  || warn "PM2 startup needs manual review: /tmp/tracetrader-pm2-startup.log"
ok "PM2 process ready"

# ── cron jobs ──────────────────────────────────────────────────────────────
log "Install cron jobs"
CRON_TMP="$(mktemp)"
crontab -l 2>/dev/null | sed '/# TraceTradeLab begin/,/# TraceTradeLab end/d' > "$CRON_TMP" || true
cat >> "$CRON_TMP" <<EOF

# TraceTradeLab begin
2 0,4,8,12,16,20 * * * cd $DASHBOARD_DIR && REPO_ROOT=$REPO_ROOT TRACE_ENV_FILE=$REPO_ROOT/.env $PYTHON agent_runner_v2.py --symbol BTC/USDT >> $LOG_DIR/cron.log 2>&1
5 0,4,8,12,16,20 * * * cd $DASHBOARD_DIR && REPO_ROOT=$REPO_ROOT $PYTHON market_regime.py >> $LOG_DIR/regime.log 2>&1
*/5 * * * * cd $DASHBOARD_DIR && REPO_ROOT=$REPO_ROOT $PYTHON signal_lifecycle.py >> $LOG_DIR/lifecycle.log 2>&1
32 * * * * cd $DASHBOARD_DIR && REPO_ROOT=$REPO_ROOT $PYTHON feedback_collector.py >> $LOG_DIR/feedback.log 2>&1
# TraceTradeLab end
EOF
crontab "$CRON_TMP"
rm -f "$CRON_TMP"
ok "Cron jobs installed"

# ── summary ────────────────────────────────────────────────────────────────
log "Deploy complete"
pm2 list
printf "\n"
printf "Repo root           : %s\n" "$REPO_ROOT"
printf "Dashboard UI        : %s\n" "$DASHBOARD_URL"
printf "Freqtrade UI        : %s\n" "$FREQTRADE_UI_URL"
if [ "$BIND_HOST" = "127.0.0.1" ] || [ "$BIND_HOST" = "localhost" ]; then
  printf "Freqtrade UI note   : bound to localhost. SSH tunnel: ssh -L %s:127.0.0.1:%s user@%s\n" \
    "$FREQTRADE_UI_PORT" "$FREQTRADE_UI_PORT" "$HOST"
fi
printf "PM2 logs            : pm2 logs tracetrader-dashboard\n"
printf "Freqtrade logs      : docker compose -f %s logs -f freqtrade\n" "$FREQTRADE_DIR/docker-compose.yml"
printf "App logs            : tail -f %s/cron.log\n" "$LOG_DIR"
