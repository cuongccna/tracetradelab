"""
signal_lifecycle.py — Track signal status lifecycle và match với Freqtrade

Vấn đề cần giải quyết:
  Signal tạo ra → Freqtrade có nhận không? Nếu không thì vì lý do gì?

Cách làm:
  Poll Freqtrade API sau mỗi signal → match trade với signal → update status

signal_status lifecycle:
  CREATED → VALIDATED → ACCEPTED / REJECTED_* → EXECUTED → CLOSED

Path: /opt/TraceTradeLab/dashboard/signal_lifecycle.py
"""

import sys, logging, httpx
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/opt/TraceTradeLab/dashboard")

log = logging.getLogger(__name__)

def _load_ft_pass() -> tuple[str, str, str]:
    """Read Freqtrade credentials. Falls back to freqtrade/.env if config.json
    has 'overridden_by_environment' as password."""
    import json as _json
    from pathlib import Path as _Path
    _url = "http://127.0.0.1:8080/api/v1"
    _user = "admin"
    _pass = ""
    try:
        cfg_path = _Path("/opt/TraceTradeLab/freqtrade/user_data/config.json")
        with open(cfg_path) as _f:
            _cfg = _json.load(_f)
        _api = _cfg.get("api_server", {})
        _url = f"http://127.0.0.1:{_api.get('listen_port', 8080)}/api/v1"
        _user = _api.get("username", "admin")
        _pass = _api.get("password", "")
    except Exception as _e:
        log.warning(f"Cannot load FT config.json: {_e}")
    if not _pass or _pass == "overridden_by_environment":
        try:
            env_path = _Path("/opt/TraceTradeLab/freqtrade/.env")
            for line in env_path.read_text().splitlines():
                if line.startswith("FREQTRADE_API_PASSWORD="):
                    _pass = line.split("=", 1)[1].strip()
                    break
        except Exception as _e:
            log.warning(f"Cannot load FT .env: {_e}")
    return _url, _user, _pass

FREQTRADE_URL, FREQTRADE_USER, FREQTRADE_PASS = _load_ft_pass()

# Rejection reasons
class RejectionReason:
    LOW_CONFIDENCE  = "REJECTED_LOW_CONFIDENCE"
    RISK_RULE       = "REJECTED_RISK_RULE"
    DUPLICATE       = "REJECTED_DUPLICATE"
    EXPIRED         = "REJECTED_EXPIRED"
    INVALID         = "REJECTED_INVALID_FORMAT"
    OPEN_TRADE      = "REJECTED_EXISTING_OPEN_TRADE"
    FT_OFFLINE      = "REJECTED_BOT_OFFLINE"


def ft_get(path: str) -> dict | list | None:
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{FREQTRADE_URL}{path}", auth=(FREQTRADE_USER, FREQTRADE_PASS))
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        return None
    except Exception as e:
        log.debug(f"FT API [{path}]: {e}")
        return None


def validate_signal(signal: dict) -> tuple[bool, str]:
    """
    Kiểm tra signal có hợp lệ không trước khi gửi cho Freqtrade.
    Trả về (is_valid, rejection_reason)
    """
    MIN_CONFIDENCE = 0.60

    # Confidence check
    if float(signal.get("confidence", 0)) < MIN_CONFIDENCE:
        return False, RejectionReason.LOW_CONFIDENCE

    # Action check
    if signal.get("action") not in ("BUY", "SELL", "EXIT"):
        return False, RejectionReason.INVALID

    # Expiry check
    if signal.get("expires_at"):
        try:
            exp = datetime.fromisoformat(signal["expires_at"])
            if datetime.now(timezone.utc) > exp:
                return False, RejectionReason.EXPIRED
        except Exception:
            pass

    return True, ""


def check_freqtrade_acceptance(signal: dict) -> tuple[str, str | None]:
    """
    Kiểm tra Freqtrade có nhận signal không bằng cách poll status.
    Trả về (status, rejection_reason)
    """
    # Bot online không?
    bot_status = ft_get("/show_config")
    if not bot_status:
        return RejectionReason.FT_OFFLINE, "Freqtrade bot offline or unreachable"

    # Có open trade cùng pair không?
    open_trades = ft_get("/status")
    if isinstance(open_trades, list):
        pair = signal.get("symbol", "").replace("/", "/")
        for trade in open_trades:
            if trade.get("pair") == pair:
                return RejectionReason.OPEN_TRADE, f"Existing open trade for {pair}"

    # Signal được chấp nhận về nguyên tắc
    return "ACCEPTED", None


def match_signal_to_ft_trade(signal: dict, ft_trades: list) -> dict | None:
    """
    Match signal với Freqtrade trade dựa trên timing và pair.
    """
    sig_time = datetime.fromisoformat(signal["created_at"])
    pair = signal.get("symbol", "").replace("/", "/")

    candidates = []
    for trade in ft_trades:
        if trade.get("pair") != pair:
            continue
        open_str = trade.get("open_date") or ""
        try:
            if isinstance(open_str, (int, float)):
                t_open = datetime.fromtimestamp(open_str / 1000, tz=timezone.utc)
            else:
                t_open = datetime.fromisoformat(open_str.replace("Z", "+00:00"))
        except Exception:
            continue
        diff = (t_open - sig_time).total_seconds()
        if 0 <= diff <= 7200:
            candidates.append((diff, trade))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def process_signal(signal_id: int, signal: dict) -> dict:
    """
    Full lifecycle processing cho một signal.
    1. Validate
    2. Check FT acceptance
    3. Match với FT trade nếu có
    4. Update DB

    Trả về status dict.
    """
    from db_v2 import (
        update_signal_status, create_execution,
        update_execution, get_conn
    )

    result = {"signal_id": signal_id, "status": None, "rejection": None}

    # ── Step 1: Validate signal ───────────────────────────────────
    is_valid, rejection = validate_signal(signal)
    if not is_valid:
        update_signal_status(signal_id, rejection)
        create_execution(signal_id, status=rejection, rejection_reason=rejection)
        result.update({"status": rejection, "rejection": rejection})
        log.info(f"Signal #{signal_id} rejected: {rejection}")
        return result

    update_signal_status(signal_id, "VALIDATED")

    # ── Step 2: Check Freqtrade acceptance ────────────────────────
    ft_status, ft_reason = check_freqtrade_acceptance(signal)
    if ft_status != "ACCEPTED":
        update_signal_status(signal_id, ft_status)
        create_execution(signal_id, status=ft_status, rejection_reason=ft_reason)
        result.update({"status": ft_status, "rejection": ft_reason})
        log.info(f"Signal #{signal_id} not accepted by FT: {ft_reason}")
        return result

    # Create execution record as ACCEPTED
    exec_id = create_execution(signal_id, status="ACCEPTED")
    update_signal_status(signal_id, "ACCEPTED")
    result["execution_id"] = exec_id

    # ── Step 3: Wait and match with FT trade ─────────────────────
    # Poll FT trades để tìm match (sẽ được gọi lại sau)
    all_trades = ft_get("/trades?limit=50")
    if isinstance(all_trades, dict):
        all_trades = all_trades.get("trades", [])
    if not all_trades:
        all_trades = []

    matched = match_signal_to_ft_trade(signal, all_trades)
    if matched:
        ft_trade_id = int(matched.get("trade_id", 0))
        is_open = not matched.get("is_open", True) is False

        update_execution(
            exec_id,
            status="TRADE_OPENED" if matched.get("is_open") else "CLOSED",
            ft_trade_id=ft_trade_id,
            order_created=True,
            trade_opened=True,
        )
        update_signal_status(signal_id, "EXECUTED")
        result.update({
            "status": "EXECUTED",
            "ft_trade_id": ft_trade_id,
        })
        log.info(f"Signal #{signal_id} → FT trade #{ft_trade_id}")
    else:
        result["status"] = "ACCEPTED"  # Trade chưa mở
        log.debug(f"Signal #{signal_id} accepted but no matching FT trade yet")

    return result


def sync_all_pending_signals():
    """
    Sync tất cả signals chưa có execution record.
    Chạy mỗi 5 phút để cập nhật lifecycle.
    """
    import sqlite3
    from db_v2 import DB_PATH, update_signal_status, create_execution

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # Lấy signals BUY/SELL chưa có execution
        rows = conn.execute("""
            SELECT s.id, s.symbol, s.action, s.confidence,
                   s.created_at, s.expires_at, s.signal_status
            FROM signals s
            LEFT JOIN executions e ON e.signal_id = s.id
            WHERE e.id IS NULL AND s.action IN ('BUY','SELL')
            ORDER BY s.created_at DESC LIMIT 100
        """).fetchall()
        conn.close()
    except Exception as e:
        log.error(f"DB query error: {e}")
        return

    if not rows:
        log.debug("No pending signals to sync")
        return

    log.info(f"Syncing {len(rows)} pending signals...")
    for row in rows:
        try:
            sig = dict(row)
            process_signal(sig["id"], sig)
        except Exception as e:
            log.error(f"Error processing signal #{row['id']}: {e}")


def update_closed_executions():
    """
    Update executions từ TRADE_OPENED → CLOSED khi Freqtrade đóng trade.
    Match dựa trên ft_trade_id.
    """
    import sqlite3
    from db_v2 import DB_PATH, update_execution, update_signal_status

    # Lấy ft trades đã đóng
    closed = ft_get("/trades?limit=100")
    if isinstance(closed, dict):
        closed = closed.get("trades", [])
    if not closed:
        return

    closed_ids = {
        int(t.get("trade_id", 0))
        for t in closed
        if not t.get("is_open", True)
    }
    if not closed_ids:
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        open_execs = conn.execute("""
            SELECT id, signal_id, freqtrade_trade_id
            FROM executions
            WHERE status='TRADE_OPENED' AND freqtrade_trade_id IS NOT NULL
        """).fetchall()
        conn.close()
    except Exception as e:
        log.error(f"DB query error: {e}")
        return

    for ex in open_execs:
        if int(ex["freqtrade_trade_id"] or 0) in closed_ids:
            update_execution(ex["id"], status="CLOSED",
                           ft_trade_id=ex["freqtrade_trade_id"])
            update_signal_status(ex["signal_id"], "CLOSED")
            log.info(f"Execution #{ex['id']} marked CLOSED (FT trade #{ex['freqtrade_trade_id']})")


def run_lifecycle_sync():
    """Entry point cho cron job."""
    log.info("=== Signal lifecycle sync started ===")
    sync_all_pending_signals()
    update_closed_executions()
    log.info("=== Signal lifecycle sync done ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_lifecycle_sync()
