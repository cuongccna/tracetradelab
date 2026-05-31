#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  TraceTradeLab — Local Run Script
#  Chạy từ thư mục repo/ (không cần /opt/tracetrader/)
#
#  Usage:
#    ./run_local.sh agent [--symbol BTC/USDT] [--force]   # chạy agent runner
#    ./run_local.sh api                                    # khởi động API server
#    ./run_local.sh test                                   # chạy smoke test
#    ./run_local.sh cron-install                           # cài crontab multi-symbol
#    ./run_local.sh cron-show                              # xem crontab hiện tại
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
VENV="${ROOT}/tradingagents-src/.venv"
PYTHON="${VENV}/bin/python"
DASHBOARD="${ROOT}/dashboard"
LOG_DIR="${ROOT}/logs"

# Kiểm tra .venv
if [[ ! -x "${PYTHON}" ]]; then
  echo "❌ Không tìm thấy Python venv tại: ${VENV}"
  echo "   Chạy: cd tradingagents-src && python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
fi

mkdir -p "${LOG_DIR}"

# Export TRACE_ROOT để tất cả module dùng repo/ làm root
export TRACE_ROOT="${ROOT}"
export TRACE_DASHBOARD_DIR="${DASHBOARD}"
export TRACE_LOG_DIR="${LOG_DIR}"
export TRACE_ENV_FILE="${ROOT}/.env"
export TRACE_VENV_PYTHON="${PYTHON}"

CMD="${1:-help}"
shift || true

case "${CMD}" in
  agent)
    echo "=== [LOCAL] TradingAgents Runner ==="
    echo "    TRACE_ROOT  : ${ROOT}"
    echo "    Symbol args : $*"
    echo ""
    cd "${DASHBOARD}"
    exec "${PYTHON}" agent_runner_v2.py "$@"
    ;;

  api)
    echo "=== [LOCAL] API Server — http://localhost:8888 ==="
    echo "    TRACE_ROOT  : ${ROOT}"
    echo ""
    cd "${DASHBOARD}"
    exec "${PYTHON}" -m uvicorn api_v2:app --host 0.0.0.0 --port 8888 --reload
    ;;

  test)
    echo "=== [LOCAL] Smoke Test ==="
    cd "${DASHBOARD}"
    echo "--- db_v2 import ---"
    "${PYTHON}" -c "import sys; sys.path.insert(0,'${DASHBOARD}'); from db_v2 import init_db; init_db(); print('✅ db_v2 OK')"
    echo "--- market_regime import ---"
    "${PYTHON}" -c "import sys; sys.path.insert(0,'${DASHBOARD}'); from market_regime import classify_regime; print('✅ market_regime OK')"
    echo "--- agent_bias_extractor import ---"
    "${PYTHON}" -c "import sys; sys.path.insert(0,'${DASHBOARD}'); from agent_bias_extractor import extract_all; print('✅ agent_bias_extractor OK')"
    echo "--- feedback_collector import ---"
    "${PYTHON}" -c "import sys; sys.path.insert(0,'${DASHBOARD}'); from feedback_collector import ensure_feedback_schema; ensure_feedback_schema(); print('✅ feedback_collector OK')"
    echo "--- signal_lifecycle import ---"
    "${PYTHON}" -c "import sys; sys.path.insert(0,'${DASHBOARD}'); from signal_lifecycle import validate_signal; print('✅ signal_lifecycle OK')"
    echo "--- telegram_reporter import ---"
    "${PYTHON}" -c "import sys; sys.path.insert(0,'${DASHBOARD}'); import telegram_reporter; print('✅ telegram_reporter OK')"
    echo "--- crypto_full dataflow import ---"
    "${PYTHON}" -c "import sys; sys.path.insert(0,'${ROOT}/tradingagents-src'); from tradingagents.dataflows.crypto_full import fetch_crypto_full_block; print('✅ crypto_full OK')"
    echo ""
    echo "✅ Tất cả module import thành công"
    ;;

  cron-install)
    echo "=== [LOCAL] Cài crontab multi-symbol ==="
    TMPFILE=$(mktemp)
    # Giữ các job không liên quan
    crontab -l 2>/dev/null | grep -v "TraceTradeLab\|tracetrader\|agent_runner_v2\|market_regime\|signal_lifecycle\|feedback_collector" > "${TMPFILE}" || true
    # Append jobs mới dùng đúng path repo/
    cat >> "${TMPFILE}" << CRONEOF
# TraceTradeLab — AI signal pipeline (multi-symbol, 4H candle close)
# 4H closes: 00:02 / 04:02 / 08:02 / 12:02 / 16:02 / 20:02 UTC
# ETH offset +10 min → 00:12 / 04:12 / 08:12 / 12:12 / 16:12 / 20:12 UTC
2  0,4,8,12,16,20 * * * cd ${DASHBOARD} && TRACE_ROOT=${ROOT} ${PYTHON} agent_runner_v2.py --symbol BTC/USDT >> ${LOG_DIR}/btc_cron.log 2>&1
12 0,4,8,12,16,20 * * * cd ${DASHBOARD} && TRACE_ROOT=${ROOT} ${PYTHON} agent_runner_v2.py --symbol ETH/USDT >> ${LOG_DIR}/eth_cron.log 2>&1
5  0,4,8,12,16,20 * * * cd ${DASHBOARD} && TRACE_ROOT=${ROOT} ${PYTHON} market_regime.py >> ${LOG_DIR}/regime.log 2>&1
*/5 * * * * cd ${DASHBOARD} && TRACE_ROOT=${ROOT} ${PYTHON} signal_lifecycle.py >> ${LOG_DIR}/lifecycle.log 2>&1
32 * * * * cd ${DASHBOARD} && TRACE_ROOT=${ROOT} ${PYTHON} feedback_collector.py >> ${LOG_DIR}/feedback.log 2>&1
CRONEOF
    crontab "${TMPFILE}"
    rm "${TMPFILE}"
    echo "✅ Crontab đã cài (4H candle close: 00/04/08/12/16/20 UTC):"
    echo "   :02 → BTC/USDT  (log: ${LOG_DIR}/btc_cron.log)"
    echo "   :12 → ETH/USDT  (log: ${LOG_DIR}/eth_cron.log)"
    echo "   :05 → market_regime"
    echo "   :*/5 → signal_lifecycle (mỗi 5 phút)"
    echo "   :32 → feedback_collector (mỗi giờ)"
    echo ""
    crontab -l | grep -E "tracetrader|TraceTrade|agent_runner|market_regime|signal_lifecycle|feedback"
    ;;

  cron-show)
    echo "=== Crontab hiện tại ==="
    crontab -l 2>/dev/null || echo "(trống)"
    ;;


  help|*)
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "Commands:"
    echo "  agent [--symbol BTC/USDT] [--force] [--skip-feedback]"
    echo "            Chạy TradingAgents full flow"
    echo "  api       Khởi động FastAPI server tại http://localhost:8888"
    echo "  test      Smoke test tất cả module imports"
    echo "  cron-install  Cài/cập nhật crontab (BTC :02 + ETH :12 mỗi giờ)"
    echo "  cron-show     Xem crontab hiện tại"
    echo ""
    echo "Env files cần có:"
    echo "  ${ROOT}/.env                          ← API keys, Telegram, TRACE_*"
    echo "  ${ROOT}/freqtrade/.env                ← FREQTRADE_API_PASSWORD"
    echo "  ${ROOT}/freqtrade/user_data/config.json ← Freqtrade config"
    echo "  ${ROOT}/tradingagents-src/.env        ← DEEPSEEK_API_KEY"
    ;;
esac
