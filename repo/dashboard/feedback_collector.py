"""
feedback_collector.py — Feedback loop từ Freqtrade outcomes → TradingAgents memory

Nhiệm vụ:
  1. Poll Freqtrade API lấy closed trades
  2. Match với signals trong DB theo time-window
  3. Ghi signal_outcomes (profit, sl, outcome_correct)
  4. Build past_context string để inject vào next agent run

Path: /opt/TraceTradeLab/dashboard/feedback_collector.py
Cron: python feedback_collector.py  (mỗi 30 phút)
"""

import sys, json, logging, sqlite3
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, "/opt/TraceTradeLab/dashboard")

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  CONFIG — đọc password từ config.json, không hard-code
# ═══════════════════════════════════════════════════════════════════

def _load_ft_config() -> tuple[str, str, str]:
    """Return (url, user, password) from Freqtrade config.json.
    Falls back to freqtrade/.env when password is 'overridden_by_environment'."""
    _url = "http://127.0.0.1:8080/api/v1"
    _user = "admin"
    _pass = ""
    config_path = Path("/opt/TraceTradeLab/freqtrade/user_data/config.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        api = cfg.get("api_server", {})
        _url  = f"http://127.0.0.1:{api.get('listen_port', 8080)}/api/v1"
        _user = api.get("username", "admin")
        _pass = api.get("password", "")
    except Exception as e:
        log.warning(f"Cannot read FT config.json: {e}")
    if not _pass or _pass == "overridden_by_environment":
        try:
            env_path = Path("/opt/TraceTradeLab/freqtrade/.env")
            for line in env_path.read_text().splitlines():
                if line.startswith("FREQTRADE_API_PASSWORD="):
                    _pass = line.split("=", 1)[1].strip()
                    break
        except Exception as e:
            log.warning(f"Cannot load FT .env: {e}")
    return _url, _user, _pass

FREQTRADE_URL, FREQTRADE_USER, FREQTRADE_PASS = _load_ft_config()


# ═══════════════════════════════════════════════════════════════════
#  FREQTRADE HTTP HELPER
# ═══════════════════════════════════════════════════════════════════

def _ft_get(path: str) -> dict | list | None:
    """GET request to Freqtrade API. Returns None on any error."""
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(
                f"{FREQTRADE_URL}{path}",
                auth=(FREQTRADE_USER, FREQTRADE_PASS),
            )
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        log.debug("Freqtrade offline (ConnectError)")
        return None
    except Exception as e:
        log.debug(f"FT API [{path}]: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
#  SCHEMA
# ═══════════════════════════════════════════════════════════════════

def ensure_feedback_schema():
    """Ensure all V2 tables exist (idempotent, delegates to db_v2.init_db)."""
    try:
        from db_v2 import init_db
        init_db()
        log.debug("Feedback schema OK (db_v2.init_db called)")
    except Exception as e:
        log.error(f"ensure_feedback_schema error: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════════
#  ACCURACY STATS
# ═══════════════════════════════════════════════════════════════════

def get_accuracy_stats() -> dict:
    """
    Return aggregate stats from signal_outcomes.
    Always returns a dict (never raises).
    """
    empty = {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "avg_pnl": 0, "sl_triggered": 0}
    try:
        from db_v2 import DB_PATH, get_conn
        with get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(outcome_correct) as wins,
                       AVG(profit_pct) as avg_pnl,
                       SUM(sl_triggered) as sl_count
                FROM signal_outcomes
                WHERE outcome_correct IS NOT NULL
            """).fetchone()
        total = row["total"] or 0
        wins  = int(row["wins"] or 0)
        return {
            "total":        total,
            "wins":         wins,
            "losses":       total - wins,
            "win_rate":     round(wins / total * 100, 1) if total else 0,
            "avg_pnl":      round(row["avg_pnl"] or 0, 3),
            "sl_triggered": int(row["sl_count"] or 0),
        }
    except Exception as e:
        log.error(f"get_accuracy_stats error: {e}")
        return empty


# ═══════════════════════════════════════════════════════════════════
#  PAST CONTEXT — string injected into agent prompt
# ═══════════════════════════════════════════════════════════════════

def get_past_context(symbol: str, n: int = 5) -> str:
    """
    Build a formatted string summarising the last n closed trades for
    the given symbol. Returned string is injected into agent prompt.
    Returns empty string on error, "No trade history" when table is empty.
    """
    try:
        from db_v2 import get_conn
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT so.signal_action, so.profit_pct, so.outcome_correct,
                       so.close_reason, so.sl_triggered, so.closed_at,
                       s.confidence, ar.market_regime
                FROM signal_outcomes so
                JOIN signals s ON s.id = so.signal_id
                LEFT JOIN agent_runs ar ON ar.id = s.run_id
                WHERE so.symbol = ? AND so.outcome_correct IS NOT NULL
                ORDER BY so.closed_at DESC LIMIT ?
            """, (symbol, n)).fetchall()

        if not rows:
            return f"No trade history for {symbol} yet."

        lines = [f"Recent {symbol} closed trades (last {len(rows)}):"]
        total_pnl = 0.0
        wins = 0
        sl_count = 0

        for r in rows:
            pnl   = r["profit_pct"] or 0.0
            pct_s = f"{pnl:+.2f}%"
            ok    = "✓" if r["outcome_correct"] else "✗"
            date  = (r["closed_at"] or "")[:10]
            conf  = r["confidence"] or 0
            regime_s = f" [{r['market_regime']}]" if r["market_regime"] else ""
            sl_s     = " ⚠ SL" if r["sl_triggered"] else ""
            reason   = r["close_reason"] or "unknown"
            lines.append(
                f"  {ok} {date}: {r['signal_action']} conf={conf:.2f}"
                f" → {pct_s} ({reason}){sl_s}{regime_s}"
            )
            total_pnl += pnl
            if r["outcome_correct"]:
                wins += 1
            if r["sl_triggered"]:
                sl_count += 1

        total = len(rows)
        avg   = total_pnl / total
        lines.append(
            f"Summary: {wins}/{total} wins ({wins/total*100:.0f}%), avg PnL {avg:+.2f}%"
        )
        if sl_count:
            lines.append(f"Note: Stop-loss triggered {sl_count}/{total} recent trades — check position sizing.")

        return "\n".join(lines)

    except Exception as e:
        log.error(f"get_past_context error: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════
#  INTERNAL — match & record helpers
# ═══════════════════════════════════════════════════════════════════

def _parse_ft_dt(dt_str: str) -> datetime | None:
    """Parse Freqtrade datetime string (handles space or T separator)."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace(" ", "T"))
    except Exception:
        return None


def _find_matching_signal(pair: str, trade_open_dt: datetime) -> dict | None:
    """
    Look for a BUY/SELL signal in DB that:
      - matches pair/symbol
      - was created 0–2 hours before the FT trade opened
      - has no signal_outcome recorded yet
    Returns first match or None.
    """
    try:
        from db_v2 import get_conn
        if trade_open_dt.tzinfo is None:
            trade_open_dt = trade_open_dt.replace(tzinfo=timezone.utc)
        window_start = (trade_open_dt - timedelta(hours=2)).isoformat()
        window_end   = trade_open_dt.isoformat()

        with get_conn() as conn:
            row = conn.execute("""
                SELECT s.id, s.symbol, s.action, s.confidence,
                       s.created_at, s.run_id
                FROM signals s
                LEFT JOIN signal_outcomes so ON so.signal_id = s.id
                WHERE s.symbol = ?
                  AND s.action IN ('BUY','SELL')
                  AND s.created_at BETWEEN ? AND ?
                  AND so.id IS NULL
                ORDER BY s.created_at DESC LIMIT 1
            """, (pair, window_start, window_end)).fetchone()
        return dict(row) if row else None
    except Exception as e:
        log.error(f"_find_matching_signal error: {e}")
        return None


def _outcome_already_recorded(ft_trade_id: int) -> bool:
    """Return True if this FT trade ID already has a signal_outcomes row."""
    try:
        from db_v2 import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM signal_outcomes WHERE ft_trade_id=?", (ft_trade_id,)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _get_execution_id(signal_id: int) -> int | None:
    try:
        from db_v2 import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM executions WHERE signal_id=? LIMIT 1", (signal_id,)
            ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None


def _record_outcome(signal_row: dict, ft_trade: dict):
    """
    Insert one signal_outcomes row for a matched closed FT trade.
    Idempotent by ft_trade_id.
    """
    try:
        ft_trade_id = int(ft_trade.get("trade_id", 0))
        if _outcome_already_recorded(ft_trade_id):
            return

        # ── Extract fields from FT trade ──────────────────────────
        profit_pct   = float(ft_trade.get("profit_ratio", 0)) * 100
        profit_abs   = float(ft_trade.get("profit_abs", 0))
        entry_price  = float(ft_trade.get("open_rate", 0))
        exit_price   = float(ft_trade.get("close_rate", 0))

        # trade_duration_s (seconds, FT ≥2023) or trade_duration (minutes, older)
        dur_s = ft_trade.get("trade_duration_s") or ft_trade.get("trade_duration")
        duration_min = int(dur_s or 0) // 60 if ft_trade.get("trade_duration_s") else int(dur_s or 0)

        close_reason = str(
            ft_trade.get("sell_reason") or ft_trade.get("close_reason") or "unknown"
        ).lower()
        closed_at    = str(ft_trade.get("close_date") or "")
        sl_triggered = 1 if "stop_loss" in close_reason else 0
        tp_triggered = 1 if close_reason in ("roi", "take_profit", "trailing_stop_loss") else 0

        action = signal_row.get("action", "BUY")
        if action == "BUY":
            outcome_correct = 1 if profit_pct > 0 else 0
        elif action == "SELL":
            outcome_correct = 1 if profit_pct < 0 else 0
        else:
            outcome_correct = None

        execution_id = _get_execution_id(signal_row["id"])
        now = datetime.now(timezone.utc).isoformat()

        from db_v2 import get_conn
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO signal_outcomes
                  (signal_id, execution_id, ft_trade_id, symbol, signal_action,
                   actual_entry, actual_exit, profit_pct, profit_abs,
                   trade_duration, sl_triggered, tp1_triggered,
                   outcome_correct, close_reason, closed_at, recorded_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                signal_row["id"], execution_id, ft_trade_id,
                signal_row["symbol"], action,
                entry_price, exit_price, profit_pct, profit_abs,
                duration_min, sl_triggered, tp_triggered,
                outcome_correct, close_reason, closed_at, now,
            ))

        log.info(
            f"Outcome recorded: signal #{signal_row['id']} ↔ FT #{ft_trade_id}"
            f" | {action} {profit_pct:+.2f}% ({close_reason})"
        )
    except Exception as e:
        log.error(f"_record_outcome error: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════════
#  MAIN COLLECTION LOOP
# ═══════════════════════════════════════════════════════════════════

def run_feedback_collection():
    """
    Poll Freqtrade for recent closed trades.
    Match each closed trade to a DB signal and record outcome.
    Safe to call repeatedly — idempotent per ft_trade_id.
    """
    log.info("=== Feedback collection started ===")

    data = _ft_get("/trades?limit=100")
    if isinstance(data, dict):
        all_trades = data.get("trades", [])
    elif isinstance(data, list):
        all_trades = data
    else:
        all_trades = []

    if not all_trades:
        log.info("No trades from Freqtrade (offline or empty)")
        return

    closed = [t for t in all_trades if not t.get("is_open", True)]
    log.info(f"Processing {len(closed)} closed FT trades...")

    recorded = 0
    for ft_trade in closed:
        pair = ft_trade.get("pair", "")
        if not pair:
            continue

        # Skip if already recorded
        ft_trade_id = int(ft_trade.get("trade_id", 0))
        if _outcome_already_recorded(ft_trade_id):
            continue

        open_dt = _parse_ft_dt(str(ft_trade.get("open_date", "")))
        if not open_dt:
            continue

        signal_row = _find_matching_signal(pair, open_dt)
        if signal_row:
            _record_outcome(signal_row, ft_trade)
            recorded += 1

    log.info(f"=== Feedback collection done: {recorded} new outcomes recorded ===")


# ═══════════════════════════════════════════════════════════════════
#  STANDALONE
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    ensure_feedback_schema()
    run_feedback_collection()
    stats = get_accuracy_stats()
    print(f"\nAccuracy stats: {json.dumps(stats, indent=2)}")
    ctx = get_past_context("BTC/USDT", n=5)
    print(f"\nPast context preview:\n{ctx}")
