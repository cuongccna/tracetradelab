#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
#  TraceTradeLab — VPS Setup Script
#
#  Cài đặt:  bash setup.sh
#  Yêu cầu:  Ubuntu 22.04+, Python 3.11+, Docker + Docker Compose
#
#  Kiến trúc 2 core:
#   Core 1 — TradingAgents (AI): Python lib, chạy qua cron mỗi giờ
#   Core 2 — Freqtrade:          Docker container, chạy liên tục
# ════════════════════════════════════════════════════════════════
set -e

BASE=/opt/TraceTradeLab
VENV=$BASE/.venv
PYTHON=$VENV/bin/python
PIP=$VENV/bin/pip
REPO_DIR=$(dirname "$(realpath "$0")")

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     TraceTradeLab — VPS Setup v2         ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Kiểm tra dependencies ────────────────────────────────────────
for cmd in python3 git docker; do
    if ! command -v $cmd &>/dev/null; then
        echo "❌ Thiếu: $cmd"
        echo "   Cài: sudo apt install python3 python3-venv git docker.io docker-compose-plugin"
        exit 1
    fi
done
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PY_VER | git $(git --version | cut -d' ' -f3) | Docker $(docker --version | cut -d' ' -f3 | tr -d ',')"

# ════════════════════════════════════════════════════════════════
#  PHẦN 1 — CƠ SỞ HẠ TẦNG
# ════════════════════════════════════════════════════════════════

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " PHẦN 1: Cơ sở hạ tầng"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Tạo thư mục ───────────────────────────────────────────────
echo "[1/3] Tạo thư mục..."
sudo mkdir -p $BASE/{dashboard/static,signal_bridge,logs,freqtrade/user_data/{strategies,logs}}
sudo chown -R $USER:$USER $BASE
echo "✓ $BASE/"

# ── 2. Copy source files ──────────────────────────────────────────
echo "[2/3] Copy source files..."
cp -r $REPO_DIR/dashboard/*    $BASE/dashboard/
cp -r $REPO_DIR/signal_bridge/ $BASE/
cp -r $REPO_DIR/freqUI/        $BASE/
cp    $REPO_DIR/freqtrade/user_data/strategies/AISignalStrategy.py \
      $BASE/freqtrade/user_data/strategies/
echo "✓ Files copied"

# ── 3. Cấu hình .env ─────────────────────────────────────────────
echo "[3/3] Cấu hình môi trường..."
if [ ! -f "$BASE/.env" ]; then
    cp $REPO_DIR/.env.example $BASE/.env
fi
if [ ! -f "$BASE/freqtrade/.env" ]; then
    cp $REPO_DIR/freqtrade/.env.example $BASE/freqtrade/.env
fi
echo "✓ .env files created"
echo ""
echo "  ⚠️  QUAN TRỌNG — Điền secrets trước khi tiếp tục:"
echo "     nano $BASE/.env              (DeepSeek key, Telegram)"
echo "     nano $BASE/freqtrade/.env    (Freqtrade passwords)"

# ════════════════════════════════════════════════════════════════
#  PHẦN 2 — CORE 1: TRADINGAGENTS (AI FRAMEWORK)
# ════════════════════════════════════════════════════════════════

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " PHẦN 2: Core 1 — TradingAgents (AI)"
echo " [Không phải service — là Python library]"
echo " [Cron trigger: mỗi giờ chạy agent_runner_v2.py]"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 4. Clone TradingAgents ────────────────────────────────────────
echo "[4/6] Copy TradingAgents (custom crypto prompts)..."
if [ ! -d "$BASE/tradingagents-src" ]; then
    cp -r $REPO_DIR/tradingagents-src $BASE/tradingagents-src
    echo "✓ TradingAgents copied từ repo (custom crypto build)"
else
    echo "✓ TradingAgents đã có — cập nhật nếu cần:"
    echo "   cp -r $REPO_DIR/tradingagents-src/. $BASE/tradingagents-src/"
fi

# ── 5. Tạo Python venv ────────────────────────────────────────────
echo "[5/6] Tạo Python venv + cài packages..."
python3 -m venv $VENV
$PIP install --upgrade pip -q
$PIP install -r $REPO_DIR/requirements.txt -q
# Cài TradingAgents dưới dạng editable package (bỏ qua requirements.txt có ".")
$PIP install -e $BASE/tradingagents-src/ -q
echo "✓ venv: $VENV"

# ── 6. Khởi tạo database ─────────────────────────────────────────
echo "[6/6] Khởi tạo database..."
cd $BASE/dashboard
$PYTHON -c "import sys; sys.path.insert(0,'$BASE/dashboard'); from db_v2 import init_db; init_db()" 2>/dev/null
echo "✓ tracetrader.db ready"

# ── Cài crontab ───────────────────────────────────────────────────
CRON_FILE=$(mktemp)
crontab -l 2>/dev/null > $CRON_FILE || true
if ! grep -q "TraceTradeLab" $CRON_FILE 2>/dev/null; then
    cat >> $CRON_FILE << EOF

# TraceTradeLab — AI signal pipeline
2  * * * * cd $BASE/dashboard && $PYTHON agent_runner_v2.py --symbol BTC/USDT >> $BASE/logs/cron.log 2>&1
5  * * * * cd $BASE/dashboard && $PYTHON market_regime.py >> $BASE/logs/regime.log 2>&1
*/5 * * * * cd $BASE/dashboard && $PYTHON signal_lifecycle.py >> $BASE/logs/lifecycle.log 2>&1
32 * * * * cd $BASE/dashboard && $PYTHON feedback_collector.py >> $BASE/logs/feedback.log 2>&1
EOF
    crontab $CRON_FILE
    echo "✓ Crontab: 4 jobs installed"
fi
rm $CRON_FILE

# ════════════════════════════════════════════════════════════════
#  PHẦN 3 — CORE 2: FREQTRADE (TRADING ENGINE)
# ════════════════════════════════════════════════════════════════

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " PHẦN 3: Core 2 — Freqtrade (Trading Engine)"
echo " [Docker container, chạy liên tục 24/7]"
echo " [Đọc signals từ signal_bridge/signals.db]"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Copy docker-compose
cp $REPO_DIR/freqtrade/docker-compose.yml $BASE/freqtrade/

# Tạo config.json từ example nếu chưa có
if [ ! -f "$BASE/freqtrade/user_data/config.json" ]; then
    cp $REPO_DIR/freqtrade/user_data/config.example.json \
       $BASE/freqtrade/user_data/config.json
    echo ""
    echo "  ⚠️  Chỉnh config Freqtrade:"
    echo "     nano $BASE/freqtrade/user_data/config.json"
    echo "     (đổi exchange key/secret nếu live trading)"
fi

# Pull Docker image
echo "Pulling Freqtrade Docker image..."
docker pull freqtradeorg/freqtrade:stable -q
echo "✓ Docker image ready"

# Start Freqtrade
echo "Starting Freqtrade container..."
cd $BASE/freqtrade
docker compose up -d
echo "✓ Freqtrade running → http://localhost:8080"

# ════════════════════════════════════════════════════════════════
#  PHẦN 4 — DASHBOARD API
# ════════════════════════════════════════════════════════════════

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " PHẦN 4: TraceTradeLab Dashboard"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Tạo systemd service để auto-start sau reboot
sudo tee /etc/systemd/system/tracetradelab.service > /dev/null << EOF
[Unit]
Description=TraceTradeLab Dashboard API
After=network.target

[Service]
User=$USER
WorkingDirectory=$BASE/dashboard
ExecStart=$VENV/bin/uvicorn api_v2:app --host 0.0.0.0 --port 8888 --workers 1
Restart=always
RestartSec=5
EnvironmentFile=$BASE/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tracetradelab
sudo systemctl start tracetradelab
echo "✓ Dashboard API started (systemd) → http://localhost:8888"

# ════════════════════════════════════════════════════════════════
#  HOÀN THÀNH
# ════════════════════════════════════════════════════════════════

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ✅  TraceTradeLab Setup Hoàn Thành!     ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo " Services đang chạy:"
echo "   Dashboard API  → http://localhost:8888"
echo "   Freqtrade      → http://localhost:8080"
echo "   Cron AI Runner → mỗi giờ lúc :02"
echo ""
echo " Bước tiếp theo:"
echo "   1. Điền secrets: nano $BASE/.env"
echo "   2. Test Telegram: $PYTHON $BASE/dashboard/telegram_reporter.py --test"
echo "   3. Truy cập điện thoại: cloudflared tunnel --url http://localhost:8888"
echo "   4. Logs: tail -f $BASE/logs/cron.log"
echo ""
