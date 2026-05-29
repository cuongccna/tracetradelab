"""
telegram_reporter.py — Gửi báo cáo AI run về Telegram
Path: /opt/tracetrader/dashboard/telegram_reporter.py

Cấu hình:
  Điền TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID bên dưới
  hoặc set biến môi trường: export TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=xxx
"""

import os
import json
import logging
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  ⚙️  CẤU HÌNH — Điền vào đây (hoặc dùng biến môi trường)
# ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")   # ← Điền token bot
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")   # ← Điền chat ID của bạn
DASHBOARD_URL      = os.environ.get("DASHBOARD_URL", "http://localhost:8888")  # ← URL public nếu có
DB_PATH            = Path("/opt/tracetrader/dashboard/tracetrader.db")

ACTION_EMOJI = {"BUY": "📈", "SELL": "📉", "HOLD": "⏸️", "EXIT": "🚪"}
REGIME_EMOJI = {"TRENDING_UP": "🔼", "TRENDING_DOWN": "🔽", "RANGING": "↔️",
                "VOLATILE": "⚡", "UNKNOWN": "❓"}


def _send(text: str) -> bool:
    """Gửi tin nhắn qua Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("[Telegram] Chưa cấu hình BOT_TOKEN hoặc CHAT_ID — bỏ qua")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()

    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                log.info("[Telegram] Tin nhắn đã gửi")
                return True
            else:
                log.warning(f"[Telegram] API lỗi: {result}")
                return False
    except urllib.error.URLError as e:
        log.warning(f"[Telegram] Không kết nối được: {e}")
        return False
    except Exception as e:
        log.warning(f"[Telegram] Lỗi gửi: {e}")
        return False


def _get_overview() -> dict:
    """Lấy tóm tắt từ DB."""
    if not DB_PATH.exists():
        return {}
    try:
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        # KPI tổng hợp
        cur.execute("""
            SELECT
                COUNT(*) total_runs,
                SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done_runs
            FROM runs WHERE created_at > datetime('now', '-7 days')
        """)
        row = cur.fetchone()
        stats = {"runs_7d": row[0] or 0, "done_7d": row[1] or 0}

        # Outcomes
        cur.execute("""
            SELECT COUNT(*), AVG(pnl_pct),
                   SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END)
            FROM outcomes
        """)
        row = cur.fetchone()
        stats["total_outcomes"] = row[0] or 0
        stats["avg_pnl"]        = row[1]
        stats["wins"]           = row[2] or 0

        con.close()
        return stats
    except Exception as e:
        log.warning(f"[Telegram] DB query lỗi: {e}")
        return {}


def send_run_report(
    run_id: int,
    symbol: str,
    action: str,
    confidence: float,
    regime: str | None = None,
    past_ctx_injected: bool = False,
    signal_id: int | None = None,
    exec_status: str | None = None,
    error: str | None = None,
) -> None:
    """
    Gửi báo cáo ngay sau mỗi lượt AI chạy xong.
    Gọi từ agent_runner_v2.py sau finish_run().
    """
    now_vn = datetime.now(timezone.utc).strftime("%H:%M %d/%m/%Y") + " UTC"
    action_e = ACTION_EMOJI.get(action, "🔔")
    regime_e = REGIME_EMOJI.get(regime or "", "❓")

    conf_bar = "█" * round(confidence * 10) + "░" * (10 - round(confidence * 10))
    conf_pct = f"{confidence * 100:.0f}%"

    overview = _get_overview()
    total_out = overview.get("total_outcomes", 0)
    avg_pnl   = overview.get("avg_pnl")
    wins      = overview.get("wins", 0)
    win_rate  = f"{wins/total_out*100:.0f}%" if total_out > 0 else "N/A"

    if error:
        msg = (
            f"🚨 <b>TraceTrader AI — Lỗi!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now_vn}\n"
            f"📊 <b>Run #{run_id}</b> | {symbol}\n"
            f"❌ Lỗi: <code>{error[:200]}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <a href='{DASHBOARD_URL}'>Mở Dashboard</a>"
        )
    else:
        exec_line = ""
        if exec_status:
            exec_map = {
                "EXECUTED":   "✅ Lệnh đã gửi FT",
                "ACCEPTED":   "✅ FT chấp nhận",
                "REJECTED_EXISTING_OPEN_TRADE": "⚠️ FT từ chối (đang có lệnh mở)",
                "REJECTED":   "❌ FT từ chối",
                "OPEN":       "🟢 Lệnh đang mở",
            }
            exec_line = f"\n📤 Thực thi: {exec_map.get(exec_status, exec_status)}"

        feedback_line = ""
        if past_ctx_injected:
            feedback_line = "\n🧠 Đã dùng phản hồi Freqtrade"

        stat_lines = ""
        if total_out > 0:
            pnl_str = f"{avg_pnl:+.2f}%" if avg_pnl is not None else "N/A"
            stat_lines = (
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Thống kê tích lũy</b>\n"
                f"   Kết quả: {total_out} | Thắng: {win_rate} | PnL TB: {pnl_str}"
            )

        msg = (
            f"{action_e} <b>TraceTrader AI — Run #{run_id}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now_vn}\n"
            f"💹 <b>{symbol}</b> → <b>{action}</b>\n"
            f"📶 Confidence: {conf_bar} {conf_pct}\n"
            f"{regime_e} Chế độ thị trường: {regime or 'N/A'}"
            f"{exec_line}"
            f"{feedback_line}"
            f"{stat_lines}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <a href='{DASHBOARD_URL}'>Mở Dashboard</a>"
        )

    _send(msg)


def send_error_alert(run_id: int, symbol: str, error: str) -> None:
    """Gửi cảnh báo khi run bị lỗi nghiêm trọng."""
    send_run_report(run_id=run_id, symbol=symbol, action="HOLD",
                    confidence=0.0, error=error)


def send_daily_summary() -> None:
    """
    Gửi báo cáo tổng hợp ngày.
    Gọi từ crontab: 0 8 * * * python3 /opt/tracetrader/dashboard/telegram_reporter.py --daily
    """
    overview = _get_overview()
    now_vn = datetime.now(timezone.utc).strftime("%d/%m/%Y") + " UTC"

    runs_7d = overview.get("runs_7d", 0)
    total_out = overview.get("total_outcomes", 0)
    avg_pnl   = overview.get("avg_pnl")
    wins      = overview.get("wins", 0)
    win_rate  = f"{wins/total_out*100:.0f}%" if total_out > 0 else "Chưa có"
    pnl_str   = f"{avg_pnl:+.2f}%" if avg_pnl is not None else "Chưa có"

    msg = (
        f"📋 <b>TraceTrader AI — Báo cáo ngày {now_vn}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Lượt chạy (7 ngày): <b>{runs_7d}</b>\n"
        f"📊 Tổng kết quả: <b>{total_out}</b>\n"
        f"🏆 Tỷ lệ thắng: <b>{win_rate}</b>\n"
        f"💰 PnL trung bình: <b>{pnl_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 <a href='{DASHBOARD_URL}'>Mở Dashboard</a>"
    )
    _send(msg)


# ─── Chạy trực tiếp để test ────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test",  action="store_true", help="Gửi tin nhắn test")
    parser.add_argument("--daily", action="store_true", help="Gửi báo cáo ngày")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.daily:
        send_daily_summary()
    elif args.test:
        send_run_report(
            run_id=0, symbol="BTC/USDT", action="BUY",
            confidence=0.75, regime="TRENDING_UP",
            past_ctx_injected=True, exec_status="EXECUTED"
        )
    else:
        parser.print_help()
