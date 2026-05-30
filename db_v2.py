"""
db_v2.py — Extended schema theo đúng spec TraceTrader Lab
Thay thế db.py — backward compatible (ADD COLUMN không xóa data cũ)

Bổ sung so với db.py:
  - executions table (hoàn toàn mới)
  - market_regimes table (hoàn toàn mới)
  - feedback_events table (mới, thay agent_memory)
  - agent_runs: thêm market_regime, position_size, feedback fields
  - agent_messages: thêm agent_bias, agent_confidence, agent_recommendation
  - signals: thêm signal_status, position_size_pct, entry_price

Path: /opt/TraceTradeLab/dashboard/db_v2.py
"""

import os, sqlite3, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import contextmanager

DEFAULT_TRACE_ROOT = Path(__file__).resolve().parents[1]
TRACE_ROOT = Path(os.getenv("TRACE_ROOT", str(DEFAULT_TRACE_ROOT)))
DASHBOARD_DIR = Path(os.getenv("TRACE_DASHBOARD_DIR", str(TRACE_ROOT / "dashboard")))
DB_PATH = Path(os.getenv("TRACE_DB_PATH", str(DASHBOARD_DIR / "tracetrader.db")))
SIGNAL_TTL_MINUTES = 70

@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════
#  SCHEMA
# ═══════════════════════════════════════════════════════════════════

def init_db():
    with get_conn() as conn:

        # ── 1. agent_runs (extended) ──────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol                  TEXT NOT NULL,
                status                  TEXT NOT NULL DEFAULT 'running',
                started_at              TEXT NOT NULL,
                finished_at             TEXT,
                final_action            TEXT,
                final_confidence        REAL,
                final_position_size_pct REAL,
                market_regime           TEXT,
                past_context_injected   INTEGER DEFAULT 0,
                memory_referenced_by_pm INTEGER DEFAULT 0,
                lesson_applied          INTEGER DEFAULT 0,
                similar_failed_count    INTEGER DEFAULT 0,
                error_msg               TEXT
            )
        """)
        # ADD COLUMN cho bảng cũ nếu thiếu
        _safe_add_column(conn, "agent_runs", "market_regime",           "TEXT")
        _safe_add_column(conn, "agent_runs", "final_position_size_pct", "REAL")
        _safe_add_column(conn, "agent_runs", "past_context_injected",   "INTEGER DEFAULT 0")
        _safe_add_column(conn, "agent_runs", "memory_referenced_by_pm", "INTEGER DEFAULT 0")
        _safe_add_column(conn, "agent_runs", "lesson_applied",          "INTEGER DEFAULT 0")
        _safe_add_column(conn, "agent_runs", "similar_failed_count",    "INTEGER DEFAULT 0")

        # ── 2. agent_messages (extended) ─────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_messages (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              INTEGER NOT NULL REFERENCES agent_runs(id),
                agent_name          TEXT NOT NULL,
                agent_role          TEXT NOT NULL,
                layer               TEXT NOT NULL,
                content             TEXT NOT NULL,
                agent_bias          TEXT,   -- bullish|bearish|neutral|mixed
                agent_confidence    REAL,   -- 0-1 parsed từ content
                agent_recommendation TEXT,  -- BUY|SELL|HOLD|REDUCE_SIZE|BLOCK
                tokens_used         INTEGER DEFAULT 0,
                created_at          TEXT NOT NULL
            )
        """)
        _safe_add_column(conn, "agent_messages", "agent_bias",           "TEXT")
        _safe_add_column(conn, "agent_messages", "agent_confidence",     "REAL")
        _safe_add_column(conn, "agent_messages", "agent_recommendation", "TEXT")

        # ── 3. signals (extended) ─────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              INTEGER REFERENCES agent_runs(id),
                symbol              TEXT NOT NULL,
                timeframe           TEXT NOT NULL DEFAULT '1h',
                action              TEXT NOT NULL CHECK(action IN ('BUY','SELL','HOLD','EXIT')),
                confidence          REAL NOT NULL,
                position_size_pct   REAL,
                entry_price         REAL,
                stop_loss           REAL,
                take_profit         REAL,
                signal_status       TEXT NOT NULL DEFAULT 'CREATED',
                reason              TEXT,
                raw_output          TEXT,
                created_at          TEXT NOT NULL,
                expires_at          TEXT NOT NULL
            )
        """)
        _safe_add_column(conn, "signals", "signal_status",     "TEXT DEFAULT 'CREATED'")
        _safe_add_column(conn, "signals", "position_size_pct", "REAL")
        _safe_add_column(conn, "signals", "entry_price",       "REAL")

        # signal_status lifecycle:
        # CREATED → VALIDATED → ACCEPTED / REJECTED_* → EXECUTED → CLOSED

        # ── 4. executions (HOÀN TOÀN MỚI) ───────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id           INTEGER NOT NULL REFERENCES signals(id),
                freqtrade_trade_id  INTEGER,
                status              TEXT NOT NULL DEFAULT 'PENDING',
                rejection_reason    TEXT,
                order_created       INTEGER DEFAULT 0,
                trade_opened        INTEGER DEFAULT 0,
                executed_at         TEXT,
                updated_at          TEXT NOT NULL
            )
        """)
        # status values:
        # PENDING, ACCEPTED, REJECTED_LOW_CONFIDENCE, REJECTED_RISK_RULE,
        # REJECTED_DUPLICATE, REJECTED_EXPIRED, REJECTED_INVALID,
        # ORDER_CREATED, TRADE_OPENED, CLOSED

        # ── 5. signal_outcomes (extended từ feedback_collector) ───
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id       INTEGER REFERENCES signals(id),
                execution_id    INTEGER REFERENCES executions(id),
                ft_trade_id     INTEGER,
                symbol          TEXT NOT NULL,
                signal_action   TEXT NOT NULL,
                actual_entry    REAL,
                actual_exit     REAL,
                profit_pct      REAL,
                profit_abs      REAL,
                trade_duration  INTEGER,
                sl_triggered    INTEGER DEFAULT 0,
                tp1_triggered   INTEGER DEFAULT 0,
                outcome_correct INTEGER,
                close_reason    TEXT,
                closed_at       TEXT,
                recorded_at     TEXT NOT NULL
            )
        """)
        _safe_add_column(conn, "signal_outcomes", "execution_id", "INTEGER")

        # ── 6. market_regimes (HOÀN TOÀN MỚI) ───────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_regimes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                regime          TEXT NOT NULL,
                adx             REAL,
                atr             REAL,
                atr_pct         REAL,
                ema_fast        REAL,
                ema_slow        REAL,
                ema_aligned     INTEGER,
                volume_zscore   REAL,
                close_price     REAL,
                created_at      TEXT NOT NULL,
                UNIQUE(symbol, timestamp)
            )
        """)

        # ── 7. feedback_events (HOÀN TOÀN MỚI, chi tiết hơn agent_memory) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback_events (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id                  INTEGER REFERENCES agent_runs(id),
                source_outcome_ids      TEXT,   -- JSON array of outcome IDs used
                past_context            TEXT,
                memory_referenced       INTEGER DEFAULT 0,
                lesson_applied          INTEGER DEFAULT 0,
                repeat_mistake_detected INTEGER DEFAULT 0,
                action_changed          INTEGER DEFAULT 0,  -- 1 nếu feedback đổi quyết định
                action_before_memory    TEXT,
                action_after_memory     TEXT,
                created_at              TEXT NOT NULL
            )
        """)

        # ── 8. agent_memory (giữ lại cho backward compat) ────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_memory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                memory_type TEXT NOT NULL DEFAULT 'outcome',
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                used_in_run INTEGER
            )
        """)

        # ── 9. freqtrade_snapshots (giữ nguyên) ──────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS freqtrade_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                open_trades     TEXT,
                closed_trades   TEXT,
                profit_summary  TEXT,
                bot_status      TEXT,
                captured_at     TEXT NOT NULL
            )
        """)

        # ── 10. outback telemetry từ Freqtrade ───────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS freqtrade_outback_events (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                source              TEXT NOT NULL DEFAULT 'freqtrade',
                payload_id          TEXT,
                symbol              TEXT,
                market              TEXT,
                timeframe           TEXT,
                current_balance     REAL,
                available_balance   REAL,
                equity_peak         REAL,
                drawdown_pct        REAL,
                max_drawdown_pct    REAL,
                volatility_atr_pct  REAL,
                total_profit_pct    REAL,
                total_profit_abs    REAL,
                open_trade_count    INTEGER DEFAULT 0,
                closed_trade_count  INTEGER DEFAULT 0,
                trade_history       TEXT,
                raw_payload         TEXT NOT NULL,
                created_at          TEXT NOT NULL
            )
        """)

        # ── 11. adaptive risk proposals ──────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS adaptive_risk_proposals (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol              TEXT NOT NULL,
                market              TEXT NOT NULL,
                timeframe           TEXT,
                window_start        TEXT,
                window_end          TEXT,
                sample_size         INTEGER DEFAULT 0,
                old_config          TEXT NOT NULL,
                quant_proposal      TEXT NOT NULL,
                risk_critique       TEXT NOT NULL,
                final_config        TEXT NOT NULL,
                manager_reasoning   TEXT NOT NULL,
                safety_status       TEXT NOT NULL,
                applied             INTEGER DEFAULT 0,
                applied_at          TEXT,
                config_backup_path  TEXT,
                created_at          TEXT NOT NULL
            )
        """)

        # ── Indexes ───────────────────────────────────────────────
        idxs = [
            ("idx_msgs_run",       "agent_messages(run_id)"),
            ("idx_msgs_bias",      "agent_messages(agent_bias)"),
            ("idx_sigs_symbol",    "signals(symbol, created_at)"),
            ("idx_sigs_status",    "signals(signal_status)"),
            ("idx_sigs_run",       "signals(run_id)"),
            ("idx_exec_signal",    "executions(signal_id)"),
            ("idx_exec_status",    "executions(status)"),
            ("idx_outcomes_sig",   "signal_outcomes(signal_id)"),
            ("idx_outcomes_sym",   "signal_outcomes(symbol, closed_at)"),
            ("idx_regimes_sym",    "market_regimes(symbol, timestamp)"),
            ("idx_runs_symbol",    "agent_runs(symbol, started_at)"),
            ("idx_runs_regime",    "agent_runs(market_regime)"),
            ("idx_feedback_run",   "feedback_events(run_id)"),
            ("idx_outback_symbol", "freqtrade_outback_events(symbol, created_at)"),
            ("idx_outback_market", "freqtrade_outback_events(market, created_at)"),
            ("idx_adaptive_symbol","adaptive_risk_proposals(symbol, created_at)"),
        ]
        for name, cols in idxs:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {cols}")
        # Freqtrade dry-run trade_id can restart from 1 when the bot/database is
        # recreated, and spot/futures may both produce trade_id=1.  Uniqueness
        # therefore needs enough context to avoid dropping valid feedback.
        conn.execute("DROP INDEX IF EXISTS idx_outcomes_ft_trade_unique")
        _safe_create_unique_index(
            conn,
            "idx_outcomes_ft_trade_unique_v2",
            "signal_outcomes(ft_trade_id, symbol, closed_at)",
        )

    print(f"[DB v2] Schema initialized at {DB_PATH}")


def _safe_add_column(conn, table: str, col: str, col_type: str):
    """ALTER TABLE ADD COLUMN nếu chưa tồn tại — không lỗi nếu đã có."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    except sqlite3.OperationalError:
        pass  # Column already exists


def _safe_create_unique_index(conn, name: str, expr: str):
    """Create a uniqueness guard when existing data allows it."""
    try:
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {expr}")
    except sqlite3.IntegrityError as e:
        print(f"[DB v2] Skipped unique index {name}: existing duplicates ({e})")


# ═══════════════════════════════════════════════════════════════════
#  WRITE HELPERS
# ═══════════════════════════════════════════════════════════════════

def create_run(symbol: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO agent_runs (symbol, status, started_at) VALUES (?,?,?)",
            (symbol, 'running', _now())
        )
        return cur.lastrowid

def finish_run(run_id: int, action: str, confidence: float,
               position_size: float = None, regime: str = None,
               past_ctx_injected: bool = False, memory_ref: bool = False,
               lesson: bool = False, similar_fails: int = 0):
    with get_conn() as conn:
        conn.execute("""
            UPDATE agent_runs SET
              status='done', finished_at=?, final_action=?,
              final_confidence=?, final_position_size_pct=?,
              market_regime=?, past_context_injected=?,
              memory_referenced_by_pm=?, lesson_applied=?,
              similar_failed_count=?
            WHERE id=?
        """, (_now(), action, confidence, position_size, regime,
              int(past_ctx_injected), int(memory_ref), int(lesson),
              similar_fails, run_id))

def fail_run(run_id: int, error: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE agent_runs SET status='error', finished_at=?, error_msg=? WHERE id=?",
            (_now(), error[:500], run_id)
        )

def add_agent_message(run_id: int, agent_name: str, agent_role: str,
                      layer: str, content: str, tokens: int = 0,
                      agent_bias: str = None, agent_confidence: float = None,
                      agent_recommendation: str = None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO agent_messages
              (run_id, agent_name, agent_role, layer, content,
               agent_bias, agent_confidence, agent_recommendation,
               tokens_used, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (run_id, agent_name, agent_role, layer, content,
              agent_bias, agent_confidence, agent_recommendation,
              tokens, _now()))

def write_signal(symbol: str, action: str, confidence: float,
                 stop_loss: float = None, take_profit: float = None,
                 position_size_pct: float = None, entry_price: float = None,
                 reason: str = "", raw_output: str = "", run_id: int = None) -> int:
    action = action.upper().strip()
    if action not in ("BUY","SELL","HOLD","EXIT"):
        raise ValueError(f"Invalid action: {action}")
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=SIGNAL_TTL_MINUTES)
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO signals
              (run_id, symbol, action, confidence, position_size_pct,
               entry_price, stop_loss, take_profit, signal_status,
               reason, raw_output, created_at, expires_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (run_id, symbol, action, confidence, position_size_pct,
              entry_price, stop_loss, take_profit, 'CREATED',
              reason, raw_output[:2000], now.isoformat(), expires.isoformat()))
        return cur.lastrowid

def update_signal_status(signal_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE signals SET signal_status=? WHERE id=?", (status, signal_id))

def create_execution(signal_id: int, status: str = 'PENDING',
                     rejection_reason: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO executions (signal_id, status, rejection_reason, updated_at)
            VALUES (?,?,?,?)
        """, (signal_id, status, rejection_reason, _now()))
        return cur.lastrowid

def update_execution(execution_id: int, status: str,
                     ft_trade_id: int = None, rejection_reason: str = None,
                     order_created: bool = False, trade_opened: bool = False):
    with get_conn() as conn:
        conn.execute("""
            UPDATE executions SET
              status=?, freqtrade_trade_id=?, rejection_reason=?,
              order_created=?, trade_opened=?,
              executed_at=?, updated_at=?
            WHERE id=?
        """, (status, ft_trade_id, rejection_reason,
              int(order_created), int(trade_opened),
              _now() if trade_opened else None, _now(), execution_id))

def save_regime(symbol: str, timestamp: str, regime: str,
                adx: float = None, atr: float = None, atr_pct: float = None,
                ema_fast: float = None, ema_slow: float = None,
                ema_aligned: bool = None, volume_zscore: float = None,
                close_price: float = None):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO market_regimes
              (symbol, timestamp, regime, adx, atr, atr_pct,
               ema_fast, ema_slow, ema_aligned, volume_zscore,
               close_price, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (symbol, timestamp, regime, adx, atr, atr_pct,
              ema_fast, ema_slow, int(ema_aligned) if ema_aligned is not None else None,
              volume_zscore, close_price, _now()))

def save_feedback_event(run_id: int, source_outcome_ids: list,
                        past_context: str, memory_referenced: bool,
                        lesson_applied: bool, repeat_mistake: bool,
                        action_before: str = None, action_after: str = None):
    action_changed = action_before and action_after and action_before != action_after
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO feedback_events
              (run_id, source_outcome_ids, past_context,
               memory_referenced, lesson_applied, repeat_mistake_detected,
               action_changed, action_before_memory, action_after_memory, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (run_id, json.dumps(source_outcome_ids), past_context,
              int(memory_referenced), int(lesson_applied), int(repeat_mistake),
              int(bool(action_changed)), action_before, action_after, _now()))

def save_freqtrade_snapshot(open_trades, closed_trades, profit_summary, bot_status):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO freqtrade_snapshots
              (open_trades, closed_trades, profit_summary, bot_status, captured_at)
            VALUES (?,?,?,?,?)
        """, (json.dumps(open_trades), json.dumps(closed_trades),
              json.dumps(profit_summary), json.dumps(bot_status), _now()))
        conn.execute("""
            DELETE FROM freqtrade_snapshots WHERE id NOT IN (
                SELECT id FROM freqtrade_snapshots ORDER BY id DESC LIMIT 500
            )
        """)


# ═══════════════════════════════════════════════════════════════════
#  QUERY HELPERS
# ═══════════════════════════════════════════════════════════════════

def get_recent_runs(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, symbol, status, started_at, finished_at,
                   final_action, final_confidence, final_position_size_pct,
                   market_regime, past_context_injected, memory_referenced_by_pm,
                   lesson_applied, similar_failed_count, error_msg
            FROM agent_runs ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]

def get_run_messages(run_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, agent_name, agent_role, layer, content,
                   agent_bias, agent_confidence, agent_recommendation,
                   tokens_used, created_at
            FROM agent_messages WHERE run_id=? ORDER BY id ASC
        """, (run_id,)).fetchall()
    return [dict(r) for r in rows]

def get_signal_history(symbol: str = None, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        q = """SELECT id, run_id, symbol, action, confidence, position_size_pct,
                      entry_price, stop_loss, take_profit, signal_status,
                      reason, created_at, expires_at
               FROM signals"""
        p = []
        if symbol:
            q += " WHERE symbol=?"
            p.append(symbol)
        q += " ORDER BY created_at DESC LIMIT ?"
        p.append(limit)
        rows = conn.execute(q, p).fetchall()
    return [dict(r) for r in rows]

def get_latest_valid_signal(symbol: str) -> dict | None:
    now = _now()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT action, confidence, position_size_pct, stop_loss,
                   take_profit, reason, created_at, expires_at, signal_status
            FROM signals WHERE symbol=? AND expires_at>?
            ORDER BY created_at DESC LIMIT 1
        """, (symbol, now)).fetchone()
    return dict(row) if row else None

def get_latest_freqtrade_snapshot() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT open_trades, closed_trades, profit_summary, bot_status, captured_at
            FROM freqtrade_snapshots ORDER BY id DESC LIMIT 1
        """).fetchone()
    if not row:
        return None
    return {
        "open_trades":   json.loads(row["open_trades"] or "[]"),
        "closed_trades": json.loads(row["closed_trades"] or "[]"),
        "profit_summary":json.loads(row["profit_summary"] or "{}"),
        "bot_status":    json.loads(row["bot_status"] or "{}"),
        "captured_at":   row["captured_at"],
    }


# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD AGGREGATE QUERIES
# ═══════════════════════════════════════════════════════════════════

def get_overview_stats() -> dict:
    """Tab Overview — KPI aggregates."""
    with get_conn() as conn:
        runs = conn.execute("SELECT COUNT(*) as n FROM agent_runs").fetchone()["n"]
        sigs = conn.execute("SELECT COUNT(*) as n FROM signals").fetchone()["n"]
        valid_sigs = conn.execute(
            "SELECT COUNT(*) as n FROM signals WHERE action IN ('BUY','SELL')"
        ).fetchone()["n"]
        execs = conn.execute("SELECT COUNT(*) as n FROM executions").fetchone()["n"]
        trades_opened = conn.execute(
            "SELECT COUNT(*) as n FROM executions WHERE trade_opened=1"
        ).fetchone()["n"]
        outcomes = conn.execute(
            "SELECT COUNT(*) as n FROM signal_outcomes WHERE outcome_correct IS NOT NULL"
        ).fetchone()["n"]
        wins = conn.execute(
            "SELECT COUNT(*) as n FROM signal_outcomes WHERE outcome_correct=1"
        ).fetchone()["n"]
        avg_pnl = conn.execute(
            "SELECT AVG(profit_pct) as v FROM signal_outcomes WHERE outcome_correct IS NOT NULL"
        ).fetchone()["v"]
        feedback_used = conn.execute(
            "SELECT COUNT(*) as n FROM agent_runs WHERE past_context_injected=1"
        ).fetchone()["n"]
        # Traceability: runs có đủ run→signal→execution→outcome
        traceable = conn.execute("""
            SELECT COUNT(DISTINCT ar.id) as n
            FROM agent_runs ar
            JOIN signals s ON s.run_id = ar.id
            JOIN executions e ON e.signal_id = s.id
            JOIN signal_outcomes so ON so.signal_id = s.id
        """).fetchone()["n"]

    win_rate = round(wins / outcomes * 100, 1) if outcomes else 0
    exec_rate = round(trades_opened / valid_sigs * 100, 1) if valid_sigs else 0
    trace_rate = round(traceable / runs * 100, 1) if runs else 0
    gp = 0; gl = 0
    with get_conn() as conn:
        r = conn.execute("""
            SELECT SUM(CASE WHEN profit_pct>0 THEN profit_pct ELSE 0 END) as gp,
                   SUM(CASE WHEN profit_pct<0 THEN ABS(profit_pct) ELSE 0 END) as gl
            FROM signal_outcomes WHERE outcome_correct IS NOT NULL
        """).fetchone()
        gp = r["gp"] or 0; gl = r["gl"] or 0

    return {
        "total_runs": runs, "total_signals": sigs,
        "valid_signals": valid_sigs, "trades_opened": trades_opened,
        "outcomes": outcomes, "wins": wins,
        "win_rate": win_rate, "execution_rate": exec_rate,
        "avg_pnl": round(avg_pnl or 0, 3),
        "profit_factor": round(gp/gl, 2) if gl else None,
        "traceability_rate": trace_rate,
        "feedback_used_runs": feedback_used,
        "funnel": {
            "runs": runs, "valid_signals": valid_sigs,
            "executed": trades_opened, "outcomes": outcomes,
            "feedback_used": feedback_used,
        }
    }

def get_signal_behavior_stats(symbol: str = None, days: int = 30) -> dict:
    """Tab Signal Behavior."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        q_base = "FROM signals WHERE created_at>?"
        p = [since]
        if symbol:
            q_base += " AND symbol=?"
            p.append(symbol)
        total = conn.execute(f"SELECT COUNT(*) as n {q_base}", p).fetchone()["n"]
        buy   = conn.execute(f"SELECT COUNT(*) as n {q_base} AND action='BUY'", p).fetchone()["n"]
        sell  = conn.execute(f"SELECT COUNT(*) as n {q_base} AND action='SELL'", p).fetchone()["n"]
        hold  = conn.execute(f"SELECT COUNT(*) as n {q_base} AND action='HOLD'", p).fetchone()["n"]
        avg_c = conn.execute(f"SELECT AVG(confidence) as v {q_base}", p).fetchone()["v"]
        # Overconfidence: confidence >0.75 AND outcome wrong
        oc = conn.execute("""
            SELECT COUNT(*) as n FROM signals s
            JOIN signal_outcomes so ON so.signal_id=s.id
            WHERE s.confidence>0.75 AND so.outcome_correct=0
              AND s.created_at>?
        """, [since]).fetchone()["n"]
        # Confidence by action
        cba = conn.execute(f"""
            SELECT action, AVG(confidence) as avg_c, COUNT(*) as cnt
            {q_base} GROUP BY action
        """, p).fetchall()
        # Timeline
        timeline = conn.execute(f"""
            SELECT id, symbol, action, confidence, position_size_pct,
                   stop_loss, take_profit, reason, created_at, signal_status
            {q_base} ORDER BY created_at DESC LIMIT 50
        """, p).fetchall()

    return {
        "total": total,
        "buy_rate":  round(buy/total*100,1)  if total else 0,
        "sell_rate": round(sell/total*100,1) if total else 0,
        "hold_rate": round(hold/total*100,1) if total else 0,
        "avg_confidence": round(avg_c or 0, 3),
        "overconfidence_rate": round(oc/total*100,1) if total else 0,
        "distribution": {"BUY":buy,"SELL":sell,"HOLD":hold},
        "confidence_by_action": [dict(r) for r in cba],
        "timeline": [dict(r) for r in timeline],
    }

def get_execution_stats() -> dict:
    """Tab Execution Compatibility."""
    with get_conn() as conn:
        total_sigs = conn.execute(
            "SELECT COUNT(*) as n FROM signals WHERE action IN ('BUY','SELL')"
        ).fetchone()["n"]
        execs = conn.execute("SELECT status, COUNT(*) as n FROM executions GROUP BY status").fetchall()
        reasons = conn.execute("""
            SELECT rejection_reason, COUNT(*) as n
            FROM executions WHERE rejection_reason IS NOT NULL
            GROUP BY rejection_reason ORDER BY n DESC
        """).fetchall()
        accepted  = conn.execute("SELECT COUNT(*) as n FROM executions WHERE status NOT LIKE 'REJECTED%'").fetchone()["n"]
        orders    = conn.execute("SELECT COUNT(*) as n FROM executions WHERE order_created=1").fetchone()["n"]
        opened    = conn.execute("SELECT COUNT(*) as n FROM executions WHERE trade_opened=1").fetchone()["n"]

    status_map = {r["status"]: r["n"] for r in execs}
    return {
        "total_signals": total_sigs,
        "accepted": accepted,
        "orders_created": orders,
        "trades_opened": opened,
        "execution_rate": round(opened/total_sigs*100,1) if total_sigs else 0,
        "rejection_reasons": [dict(r) for r in reasons],
        "status_breakdown": status_map,
        "funnel": {
            "signals": total_sigs, "accepted": accepted,
            "orders": orders, "opened": opened,
        }
    }

def get_outcome_stats(symbol: str = None) -> dict:
    """Tab Outcome Metrics."""
    with get_conn() as conn:
        q = "FROM signal_outcomes WHERE outcome_correct IS NOT NULL"
        p = []
        if symbol:
            q += " AND symbol=?"
            p.append(symbol)
        row = conn.execute(f"""
            SELECT COUNT(*) as total,
                   SUM(outcome_correct) as wins,
                   AVG(profit_pct) as avg_pnl,
                   MIN(profit_pct) as min_pnl,
                   MAX(profit_pct) as max_pnl,
                   AVG(trade_duration) as avg_dur,
                   SUM(sl_triggered) as sl_count,
                   SUM(tp1_triggered) as tp_count,
                   SUM(CASE WHEN profit_pct>0 THEN profit_pct ELSE 0 END) as gp,
                   SUM(CASE WHEN profit_pct<0 THEN ABS(profit_pct) ELSE 0 END) as gl
            {q}
        """, p).fetchone()
        # PnL by confidence bucket
        buckets = conn.execute(f"""
            SELECT
              CASE
                WHEN s.confidence<0.6  THEN '0.50-0.60'
                WHEN s.confidence<0.7  THEN '0.60-0.70'
                WHEN s.confidence<0.8  THEN '0.70-0.80'
                WHEN s.confidence<0.9  THEN '0.80-0.90'
                ELSE '0.90-1.00'
              END as bucket,
              COUNT(*) as n,
              AVG(so.profit_pct) as avg_pnl,
              SUM(so.outcome_correct) as wins
            FROM signal_outcomes so
            JOIN signals s ON s.id = so.signal_id
            WHERE so.outcome_correct IS NOT NULL
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        # Recent outcomes
        recent = conn.execute(f"""
            SELECT so.id, s.id as signal_id, so.ft_trade_id, so.symbol,
                   so.signal_action, s.confidence, so.profit_pct, so.profit_abs,
                   so.trade_duration, so.sl_triggered, so.close_reason,
                   so.closed_at, so.outcome_correct
            {q.replace('FROM signal_outcomes', 'FROM signal_outcomes so JOIN signals s ON s.id=so.signal_id')}
            ORDER BY so.closed_at DESC LIMIT 50
        """, p).fetchall()

    total = row["total"] or 0
    wins  = row["wins"]  or 0
    gp    = row["gp"]    or 0
    gl    = row["gl"]    or 0
    return {
        "total": total, "wins": wins, "losses": total - wins,
        "win_rate": round(wins/total*100,1) if total else 0,
        "avg_pnl":  round(row["avg_pnl"] or 0, 3),
        "min_pnl":  round(row["min_pnl"] or 0, 3),
        "max_pnl":  round(row["max_pnl"] or 0, 3),
        "profit_factor": round(gp/gl,2) if gl else None,
        "avg_duration": round(row["avg_dur"] or 0),
        "sl_triggered": row["sl_count"] or 0,
        "tp_hit": row["tp_count"] or 0,
        "confidence_buckets": [dict(r) for r in buckets],
        "recent": [dict(r) for r in recent],
    }

def get_agent_attribution_stats() -> dict:
    """Tab Agent Attribution."""
    with get_conn() as conn:
        matrix = conn.execute("""
            SELECT agent_name,
                   COUNT(*) as total,
                   SUM(CASE WHEN agent_bias='bullish' THEN 1 ELSE 0 END) as bullish,
                   SUM(CASE WHEN agent_bias='bearish' THEN 1 ELSE 0 END) as bearish,
                   SUM(CASE WHEN agent_bias='neutral'  THEN 1 ELSE 0 END) as neutral,
                   AVG(agent_confidence) as avg_conf
            FROM agent_messages
            WHERE agent_bias IS NOT NULL AND layer != 'system'
            GROUP BY agent_name
        """).fetchall()
        # Risk override rate: risk_mgmt layer said BLOCK/REDUCE but PM said BUY
        risk_block = conn.execute("""
            SELECT COUNT(DISTINCT run_id) as n FROM agent_messages
            WHERE layer='risk_mgmt' AND agent_recommendation IN ('BLOCK','REDUCE_SIZE')
        """).fetchone()["n"]
        pm_override = conn.execute("""
            SELECT COUNT(DISTINCT ar.id) as n FROM agent_runs ar
            JOIN agent_messages pm ON pm.run_id=ar.id AND pm.layer='execution'
              AND pm.agent_name='Portfolio Manager' AND pm.agent_recommendation='BUY'
            JOIN agent_messages rm ON rm.run_id=ar.id AND rm.layer='risk_mgmt'
              AND rm.agent_recommendation IN ('BLOCK','REDUCE_SIZE')
        """).fetchone()["n"]

    return {
        "agent_matrix": [dict(r) for r in matrix],
        "risk_block_count": risk_block,
        "pm_override_count": pm_override,
        "risk_override_rate": round(pm_override/risk_block*100,1) if risk_block else 0,
    }

def get_regime_stats() -> dict:
    """Tab Market Regime."""
    with get_conn() as conn:
        perf = conn.execute("""
            SELECT ar.market_regime as regime,
                   COUNT(DISTINCT ar.id) as runs,
                   COUNT(DISTINCT s.id) as signals,
                   AVG(so.profit_pct) as avg_pnl,
                   SUM(so.outcome_correct) as wins,
                   COUNT(so.id) as outcomes,
                   AVG(s.confidence) as avg_conf
            FROM agent_runs ar
            LEFT JOIN signals s ON s.run_id=ar.id AND s.action IN ('BUY','SELL')
            LEFT JOIN signal_outcomes so ON so.signal_id=s.id
            WHERE ar.market_regime IS NOT NULL
            GROUP BY ar.market_regime
        """).fetchall()
        latest = conn.execute("""
            SELECT symbol, timestamp, regime, adx, atr_pct,
                   ema_aligned, volume_zscore, close_price
            FROM market_regimes ORDER BY timestamp DESC LIMIT 10
        """).fetchall()
    result = []
    for r in perf:
        d = dict(r)
        d["win_rate"] = round(d["wins"]/d["outcomes"]*100,1) if d["outcomes"] else 0
        d["trade_rate"] = round(d["signals"]/d["runs"]*100,1) if d["runs"] else 0
        result.append(d)
    return {"regime_performance": result, "latest_regimes": [dict(r) for r in latest]}

def get_feedback_learning_stats() -> dict:
    """Tab Feedback Learning."""
    with get_conn() as conn:
        fe = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(memory_referenced) as mem_used,
                   SUM(lesson_applied) as lessons,
                   SUM(repeat_mistake_detected) as repeats,
                   SUM(action_changed) as changed
            FROM feedback_events
        """).fetchone()
        # Before vs after feedback
        before = conn.execute("""
            SELECT AVG(so.profit_pct) as avg_pnl,
                   SUM(so.outcome_correct) as wins, COUNT(*) as total
            FROM signal_outcomes so
            JOIN signals s ON s.id=so.signal_id
            JOIN agent_runs ar ON ar.id=s.run_id
            WHERE ar.past_context_injected=0 AND so.outcome_correct IS NOT NULL
        """).fetchone()
        after = conn.execute("""
            SELECT AVG(so.profit_pct) as avg_pnl,
                   SUM(so.outcome_correct) as wins, COUNT(*) as total
            FROM signal_outcomes so
            JOIN signals s ON s.id=so.signal_id
            JOIN agent_runs ar ON ar.id=s.run_id
            WHERE ar.past_context_injected=1 AND so.outcome_correct IS NOT NULL
        """).fetchone()

    return {
        "total_feedback_events": fe["total"] or 0,
        "memory_usage_rate": round((fe["mem_used"] or 0)/(fe["total"] or 1)*100,1),
        "lesson_applied_rate": round((fe["lessons"] or 0)/(fe["total"] or 1)*100,1),
        "repeat_mistake_rate": round((fe["repeats"] or 0)/(fe["total"] or 1)*100,1),
        "action_changed_count": fe["changed"] or 0,
        "before_feedback": {
            "total": before["total"] or 0,
            "win_rate": round((before["wins"] or 0)/(before["total"] or 1)*100,1),
            "avg_pnl": round(before["avg_pnl"] or 0, 3),
        },
        "after_feedback": {
            "total": after["total"] or 0,
            "win_rate": round((after["wins"] or 0)/(after["total"] or 1)*100,1),
            "avg_pnl": round(after["avg_pnl"] or 0, 3),
        },
    }

def get_run_trace(run_id: int) -> dict:
    """Run Trace Viewer — màn hình quan trọng nhất."""
    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM agent_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not run:
            return {}
        msgs = conn.execute(
            "SELECT * FROM agent_messages WHERE run_id=? ORDER BY id",
            (run_id,)
        ).fetchall()
        signal = conn.execute(
            "SELECT * FROM signals WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (run_id,)
        ).fetchone()
        execution = None
        outcome = None
        feedback = None
        regime = None
        if signal:
            execution = conn.execute(
                "SELECT * FROM executions WHERE signal_id=? ORDER BY id DESC LIMIT 1",
                (signal["id"],)
            ).fetchone()
            outcome = conn.execute(
                "SELECT * FROM signal_outcomes WHERE signal_id=? ORDER BY id DESC LIMIT 1",
                (signal["id"],)
            ).fetchone()
        feedback = conn.execute(
            "SELECT * FROM feedback_events WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (run_id,)
        ).fetchone()
        if run["market_regime"]:
            regime = conn.execute("""
                SELECT * FROM market_regimes
                WHERE symbol=? ORDER BY timestamp DESC LIMIT 1
            """, (run["symbol"],)).fetchone()

    return {
        "run":       dict(run),
        "regime":    dict(regime) if regime else None,
        "messages":  [dict(m) for m in msgs],
        "signal":    dict(signal) if signal else None,
        "execution": dict(execution) if execution else None,
        "outcome":   dict(outcome) if outcome else None,
        "feedback":  dict(feedback) if feedback else None,
    }

def get_signal_lineage(signal_id: int) -> dict:
    """Signal lineage: agent reasoning → signal → execution → outcome → feedback."""
    with get_conn() as conn:
        sig = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not sig:
            return {}
        run_msgs = []
        if sig["run_id"]:
            run_msgs = conn.execute(
                "SELECT agent_name, agent_role, layer, agent_bias, agent_confidence, agent_recommendation, content FROM agent_messages WHERE run_id=? AND layer!='system' ORDER BY id",
                (sig["run_id"],)
            ).fetchall()
        exec_ = conn.execute(
            "SELECT * FROM executions WHERE signal_id=? ORDER BY id DESC LIMIT 1",
            (signal_id,)
        ).fetchone()
        out = conn.execute(
            "SELECT * FROM signal_outcomes WHERE signal_id=? ORDER BY id DESC LIMIT 1",
            (signal_id,)
        ).fetchone()
        fb = conn.execute(
            "SELECT * FROM feedback_events WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (sig["run_id"],)
        ).fetchone() if sig["run_id"] else None

    return {
        "agent_reasoning": [dict(m) for m in run_msgs],
        "signal":    dict(sig),
        "execution": dict(exec_) if exec_ else None,
        "outcome":   dict(out) if out else None,
        "feedback":  dict(fb) if fb else None,
    }


if __name__ == "__main__":
    init_db()
