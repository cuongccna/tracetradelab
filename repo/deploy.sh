#!/usr/bin/env bash
set -Eeuo pipefail

# TraceTradeLab VPS deploy
# Usage:
#   bash deploy.sh
# Optional env:
#   TRACE_ROOT=/opt/tracetrader
#   PUBLIC_HOST=your-domain-or-ip
#   DASHBOARD_PORT=8888
#   FREQTRADE_UI_PORT=8080

TRACE_ROOT="${TRACE_ROOT:-/opt/tracetrader}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8888}"
FREQTRADE_UI_PORT="${FREQTRADE_UI_PORT:-8080}"
FREQTRADE_SERVICE="${FREQTRADE_SERVICE:-freqtrade}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$TRACE_ROOT/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

log() { printf "\n\033[1;36m%s\033[0m\n" "$*"; }
ok() { printf "\033[1;32mOK\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33mWARN\033[0m %s\n" "$*"; }
die() { printf "\033[1;31mERR\033[0m %s\n" "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

copy_dir() {
  local src="$1"
  local dst="$2"
  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      "$src"/ "$dst"/
  else
    find "$dst" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    cp -a "$src"/. "$dst"/
    find "$dst" -type d -name __pycache__ -prune -exec rm -rf {} + || true
    find "$dst" -type f -name '*.pyc' -delete || true
  fi
}

public_host() {
  if [ -n "${PUBLIC_HOST:-}" ]; then
    printf "%s" "$PUBLIC_HOST"
    return
  fi
  if command -v curl >/dev/null 2>&1; then
    local ip
    ip="$(curl -fsS --max-time 3 https://ifconfig.me 2>/dev/null || true)"
    if [ -n "$ip" ]; then
      printf "%s" "$ip"
      return
    fi
  fi
  hostname -I 2>/dev/null | awk '{print $1}'
}

freqtrade_bind_host() {
  local env_file="$TRACE_ROOT/freqtrade/.env"
  if [ -f "$env_file" ]; then
    grep -E '^FREQTRADE_BIND_HOST=' "$env_file" | tail -1 | cut -d= -f2- || true
  fi
}

log "TraceTradeLab deploy -> $TRACE_ROOT"
require_cmd python3
require_cmd docker
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is required"

if ! command -v pm2 >/dev/null 2>&1; then
  require_cmd npm
  log "Installing PM2"
  sudo npm install -g pm2
fi

log "Create directories"
sudo mkdir -p \
  "$TRACE_ROOT/dashboard/static" \
  "$TRACE_ROOT/signal_bridge" \
  "$TRACE_ROOT/logs" \
  "$TRACE_ROOT/freqtrade/user_data/strategies" \
  "$TRACE_ROOT/freqtrade/user_data/logs"
sudo chown -R "$USER:$USER" "$TRACE_ROOT"
ok "Directories ready"

log "Copy application files"
copy_dir "$REPO_DIR/dashboard" "$TRACE_ROOT/dashboard"
copy_dir "$REPO_DIR/signal_bridge" "$TRACE_ROOT/signal_bridge"
copy_dir "$REPO_DIR/freqUI" "$TRACE_ROOT/freqUI"
copy_dir "$REPO_DIR/tradingagents-src" "$TRACE_ROOT/tradingagents-src"
cp "$REPO_DIR/freqtrade/docker-compose.yml" "$TRACE_ROOT/freqtrade/docker-compose.yml"
cp "$REPO_DIR/freqtrade/user_data/strategies/AISignalStrategy.py" \
   "$TRACE_ROOT/freqtrade/user_data/strategies/AISignalStrategy.py"

if [ ! -f "$TRACE_ROOT/.env" ]; then
  cp "$REPO_DIR/.env.example" "$TRACE_ROOT/.env"
  warn "Created $TRACE_ROOT/.env - fill API keys before live agent runs"
fi
if [ ! -f "$TRACE_ROOT/freqtrade/.env" ]; then
  cp "$REPO_DIR/freqtrade/.env.example" "$TRACE_ROOT/freqtrade/.env"
  warn "Created $TRACE_ROOT/freqtrade/.env - fill Freqtrade API secrets"
fi
if [ ! -f "$TRACE_ROOT/freqtrade/user_data/config.json" ]; then
  cp "$REPO_DIR/freqtrade/user_data/config.example.json" "$TRACE_ROOT/freqtrade/user_data/config.json"
  warn "Created Freqtrade config.json from example"
fi
ok "Files copied"

log "Install Python environment"
python3 -m venv "$VENV"
"$PIP" install --upgrade pip
"$PIP" install -r "$REPO_DIR/requirements.txt"
"$PIP" install -e "$TRACE_ROOT/tradingagents-src"
ok "Python venv ready"

log "Initialize database"
(
  cd "$TRACE_ROOT/dashboard"
  TRACE_ROOT="$TRACE_ROOT" \
  TRACE_DB_PATH="$TRACE_ROOT/dashboard/tracetrader.db" \
  "$PYTHON" -c "from db_v2 import init_db; init_db()"
)
ok "Dashboard DB ready"

log "Start/restart Freqtrade Docker"
(
  cd "$TRACE_ROOT/freqtrade"
  docker compose pull freqtrade
  docker compose up -d freqtrade
)
ok "Freqtrade container running"

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

log "Configure PM2 dashboard process"
cat > "$TRACE_ROOT/ecosystem.config.cjs" <<EOF
module.exports = {
  apps: [{
    name: "tracetrader-dashboard",
    cwd: "$TRACE_ROOT/dashboard",
    script: "$VENV/bin/uvicorn",
    args: "api_v2:app --host 0.0.0.0 --port $DASHBOARD_PORT --workers 1",
    interpreter: "none",
    autorestart: true,
    max_restarts: 10,
    env: {
      TRACE_ROOT: "$TRACE_ROOT",
      TRACE_DASHBOARD_DIR: "$TRACE_ROOT/dashboard",
      TRACE_LOG_DIR: "$TRACE_ROOT/logs",
      TRACE_DB_PATH: "$TRACE_ROOT/dashboard/tracetrader.db",
      TRACE_ENV_FILE: "$TRACE_ROOT/.env",
      TRACE_VENV_PYTHON: "$PYTHON",
      TRADINGAGENTS_SRC: "$TRACE_ROOT/tradingagents-src",
      FREQTRADE_CONFIG_PATH: "$TRACE_ROOT/freqtrade/user_data/config.json",
      FREQTRADE_ENV_PATH: "$TRACE_ROOT/freqtrade/.env",
      FREQTRADE_COMPOSE_FILE: "$TRACE_ROOT/freqtrade/docker-compose.yml",
      FREQTRADE_SERVICE: "$FREQTRADE_SERVICE",
      FREQTRADE_UI_URL: "$FREQTRADE_UI_URL",
      DASHBOARD_URL: "$DASHBOARD_URL"
    }
  }]
}
EOF

pm2 startOrReload "$TRACE_ROOT/ecosystem.config.cjs" --update-env
pm2 save
pm2 startup systemd -u "$USER" --hp "$HOME" >/tmp/tracetrader-pm2-startup.log 2>&1 || \
  warn "PM2 startup command needs manual review: /tmp/tracetrader-pm2-startup.log"
ok "PM2 process ready"

log "Install cron jobs"
CRON_TMP="$(mktemp)"
crontab -l 2>/dev/null | sed '/# TraceTradeLab begin/,/# TraceTradeLab end/d' > "$CRON_TMP" || true
cat >> "$CRON_TMP" <<EOF

# TraceTradeLab begin
2 0,4,8,12,16,20 * * * cd $TRACE_ROOT/dashboard && TRACE_ROOT=$TRACE_ROOT TRACE_ENV_FILE=$TRACE_ROOT/.env $PYTHON agent_runner_v2.py --symbol BTC/USDT >> $TRACE_ROOT/logs/cron.log 2>&1
5 0,4,8,12,16,20 * * * cd $TRACE_ROOT/dashboard && TRACE_ROOT=$TRACE_ROOT $PYTHON market_regime.py >> $TRACE_ROOT/logs/regime.log 2>&1
*/5 * * * * cd $TRACE_ROOT/dashboard && TRACE_ROOT=$TRACE_ROOT $PYTHON signal_lifecycle.py >> $TRACE_ROOT/logs/lifecycle.log 2>&1
32 * * * * cd $TRACE_ROOT/dashboard && TRACE_ROOT=$TRACE_ROOT $PYTHON feedback_collector.py >> $TRACE_ROOT/logs/feedback.log 2>&1
# TraceTradeLab end
EOF
crontab "$CRON_TMP"
rm -f "$CRON_TMP"
ok "Cron jobs installed"

log "Links"
pm2 list
printf "\n"
printf "Custom dashboard UI : %s\n" "$DASHBOARD_URL"
printf "Freqtrade native UI : %s\n" "$FREQTRADE_UI_URL"
if [ "$BIND_HOST" = "127.0.0.1" ] || [ "$BIND_HOST" = "localhost" ]; then
  printf "Freqtrade UI note   : bound to localhost. Use SSH tunnel: ssh -L %s:127.0.0.1:%s user@%s\n" "$FREQTRADE_UI_PORT" "$FREQTRADE_UI_PORT" "$HOST"
fi
printf "PM2 logs            : pm2 logs tracetrader-dashboard\n"
printf "Freqtrade logs      : docker compose -f %s logs -f freqtrade\n" "$TRACE_ROOT/freqtrade/docker-compose.yml"
