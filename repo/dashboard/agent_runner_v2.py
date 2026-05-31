"""
agent_runner.py — TradingAgents runner VỚI feedback loop
Thay đổi so với v1:
  - Inject past_context (Freqtrade outcomes) vào TradingAgents trước khi run
  - Portfolio Manager biết các trade trước đúng/sai như thế nào
  - Ghi token usage để track chi phí DeepSeek

Path: /opt/TraceTradeLab/dashboard/agent_runner.py
"""

import sys, os, re, json, argparse, logging, fcntl
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

DEFAULT_TRACE_ROOT = Path(__file__).resolve().parents[1]
TRACE_ROOT = Path(os.getenv("TRACE_ROOT", str(DEFAULT_TRACE_ROOT)))
TRADINGAGENTS_SRC = Path(
    os.getenv("TRADINGAGENTS_SRC", str(TRACE_ROOT / "tradingagents-src"))
)
DASHBOARD_DIR = Path(os.getenv("TRACE_DASHBOARD_DIR", str(TRACE_ROOT / "dashboard")))
LOG_DIR = Path(os.getenv("TRACE_LOG_DIR", str(TRACE_ROOT / "logs")))
ENV_FILE = Path(os.getenv("TRACE_ENV_FILE", str(TRACE_ROOT / ".env")))
AGENT_LOCK_FILE = Path(
    os.getenv("TRACE_AGENT_LOCK_FILE", str(LOG_DIR / "agent_runner.lock"))
)

sys.path.insert(0, str(TRADINGAGENTS_SRC))

# ── Load API keys từ .env — phải chạy TRƯỚC khi import TradingAgents ──
import os as _os

if ENV_FILE.exists():
    for _line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, str(DASHBOARD_DIR))
from db_v2 import (
    init_db, create_run, finish_run, fail_run,
    add_agent_message, write_signal
)

# Graceful imports for new modules
try:
    from market_regime import get_current_regime
    REGIME_OK = True
except ImportError:
    REGIME_OK = False

try:
    from agent_bias_extractor import extract_all as extract_bias, extract_position_size
    BIAS_OK = True
except ImportError:
    BIAS_OK = False

try:
    from signal_lifecycle import process_signal
    LIFECYCLE_OK = True
except ImportError:
    LIFECYCLE_OK = False

try:
    from telegram_reporter import send_run_report, send_error_alert
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False

# Import feedback module (graceful — nếu chưa có schema sẽ tự tạo)
try:
    from feedback_collector import (
        ensure_feedback_schema, get_past_context, run_feedback_collection
    )
    FEEDBACK_AVAILABLE = True
except ImportError:
    FEEDBACK_AVAILABLE = False

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "agent_runner.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

AGENT_META = {
    "market_analyst":       {"name": "Technical Analyst",    "role": "Phân tích kỹ thuật",  "layer": "analysts"},
    "fundamentals_analyst": {"name": "Fundamentals Analyst", "role": "Phân tích cơ bản",    "layer": "analysts"},
    "news_analyst":         {"name": "News Analyst",         "role": "Phân tích tin tức",   "layer": "analysts"},
    "sentiment_analyst":    {"name": "Sentiment Analyst",    "role": "Phân tích tâm lý",    "layer": "analysts"},
    "social_media_analyst": {"name": "Sentiment Analyst",    "role": "Phân tích tâm lý",    "layer": "analysts"},
    "bull_researcher":      {"name": "Bull Researcher",      "role": "Luận điểm tăng",      "layer": "researchers"},
    "bear_researcher":      {"name": "Bear Researcher",      "role": "Luận điểm giảm",      "layer": "researchers"},
    "research_manager":     {"name": "Research Manager",     "role": "Tổng hợp nghiên cứu", "layer": "researchers"},
    "aggressive_debator":   {"name": "Aggressive Debater",   "role": "Rủi ro cao",          "layer": "risk_mgmt"},
    "conservative_debator": {"name": "Conservative Debater", "role": "Rủi ro thấp",         "layer": "risk_mgmt"},
    "neutral_debator":      {"name": "Neutral Debater",      "role": "Cân bằng",            "layer": "risk_mgmt"},
    "trader":               {"name": "Trader",               "role": "Đề xuất giao dịch",   "layer": "execution"},
    "portfolio_manager":    {"name": "Portfolio Manager",    "role": "Quyết định cuối",     "layer": "execution"},
}

CRYPTO_MAP = {
    "BTC/USDT": "BTC", "ETH/USDT": "ETH", "SOL/USDT": "SOL",
    "BNB/USDT": "BNB", "UNI/USDT": "UNI", "ONDO/USDT": "ONDO",
}
MIN_CONFIDENCE = float(os.getenv("TRACE_MIN_CONFIDENCE", "0.60"))
ENABLE_SHORT_SIGNALS = os.getenv("TRACE_ENABLE_SHORT_SIGNALS", "false").lower() in ("1", "true", "yes", "on")
DEFAULT_MAX_SIGNAL_RUNTIME_MINUTES = 45 if ENABLE_SHORT_SIGNALS else 180
MAX_SIGNAL_RUNTIME_MINUTES = float(
    os.getenv("TRACE_MAX_SIGNAL_RUNTIME_MINUTES", str(DEFAULT_MAX_SIGNAL_RUNTIME_MINUTES))
)
DEFAULT_MIN_RUN_INTERVAL_MINUTES = 50 if ENABLE_SHORT_SIGNALS else 180
MIN_RUN_INTERVAL_MINUTES = float(
    os.getenv("TRACE_MIN_RUN_INTERVAL_MINUTES", str(DEFAULT_MIN_RUN_INTERVAL_MINUTES))
)
RUN_INTERVAL_FILE = Path(
    os.getenv("TRACE_RUN_INTERVAL_FILE", str(LOG_DIR / "agent_runner.last_start.json"))
)
_RUN_LOCK_HANDLE = None


def _append_current_market_context(symbol: str, past_context: str, regime_data: dict | None) -> str:
    """Add DB-backed current market context to the prompt memory block."""
    sections = []
    if past_context:
        sections.append(past_context)
    if regime_data and regime_data.get("regime") and regime_data.get("regime") != "UNKNOWN":
        sections.append(
            "Current market regime from DB/market data:\n"
            f"  {symbol}: {regime_data.get('regime')} | {regime_data.get('reason', '')}\n"
            f"  ADX={regime_data.get('adx')} ATR%={regime_data.get('atr_pct')} "
            f"close={regime_data.get('close_price')} source={regime_data.get('data_source', 'unknown')}"
        )
    try:
        from db_v2 import get_conn
        with get_conn() as conn:
            outback = conn.execute(
                """
                SELECT market, timeframe, current_balance, drawdown_pct,
                       max_drawdown_pct, volatility_atr_pct, total_profit_pct,
                       open_trade_count, closed_trade_count, created_at
                FROM freqtrade_outback_events
                WHERE symbol=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            proposal = conn.execute(
                """
                SELECT safety_status, sample_size, final_config, created_at
                FROM adaptive_risk_proposals
                WHERE symbol=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        if outback:
            sections.append(
                "Latest Freqtrade outback snapshot from DB:\n"
                f"  market={outback['market']} timeframe={outback['timeframe']} "
                f"balance={outback['current_balance']} drawdown={outback['drawdown_pct']}% "
                f"max_drawdown={outback['max_drawdown_pct']}% atr={outback['volatility_atr_pct']}% "
                f"profit={outback['total_profit_pct']}% open_trades={outback['open_trade_count']} "
                f"closed_trades={outback['closed_trade_count']} captured_at={outback['created_at']}"
            )
        if proposal:
            final = json.loads(proposal["final_config"] or "{}")
            sections.append(
                "Latest adaptive risk debate result from DB:\n"
                f"  status={proposal['safety_status']} sample_size={proposal['sample_size']} "
                f"stake_amount={final.get('stake_amount')} stoploss_pct={final.get('stoploss_pct')} "
                f"take_profit_pct={final.get('take_profit_pct')} max_open_trades={final.get('max_open_trades')} "
                f"created_at={proposal['created_at']}"
            )
    except Exception as exc:
        log.warning("Could not append DB market context: %s", exc)
    return "\n\n".join(sections)


def _defang_late_trade_signal(signal: dict, elapsed_minutes: float, run_id: int) -> dict:
    """Convert late trade signals to HOLD before they reach Freqtrade."""
    if signal.get("action") not in ("BUY", "SELL"):
        return signal
    if elapsed_minutes <= MAX_SIGNAL_RUNTIME_MINUTES:
        return signal

    original_action = signal["action"]
    original_confidence = signal.get("confidence", 0)
    reason = (
        f"Signal blocked: AI runtime {elapsed_minutes:.1f}m exceeded "
        f"TRACE_MAX_SIGNAL_RUNTIME_MINUTES={MAX_SIGNAL_RUNTIME_MINUTES:.1f}m. "
        f"Original action was {original_action} at confidence {original_confidence:.2f}. "
        f"{signal.get('reason', '')}"
    )
    safe_signal = dict(signal)
    safe_signal["action"] = "HOLD"
    safe_signal["confidence"] = min(float(original_confidence or 0), 0.5)
    safe_signal["reason"] = reason[:300]
    add_agent_message(
        run_id=run_id,
        agent_name="Risk Guard",
        agent_role="Chặn signal trễ",
        layer="risk_mgmt",
        content=reason,
        agent_bias="neutral",
        agent_confidence=safe_signal["confidence"],
        agent_recommendation="BLOCK",
    )
    log.warning("[RiskGuard] Blocked late %s signal after %.1fm", original_action, elapsed_minutes)
    return safe_signal


def _require_deepseek_key():
    if not _os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError(f"DEEPSEEK_API_KEY không tìm thấy trong {ENV_FILE}")


def _acquire_run_lock():
    """Prevent cron/manual/API triggers from writing competing bridge signals."""
    global _RUN_LOCK_HANDLE
    AGENT_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = open(AGENT_LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning(f"Another agent_runner is active; skipping this run ({AGENT_LOCK_FILE})")
        fh.close()
        sys.exit(0)
    fh.write(f"pid={os.getpid()} started_at={datetime.now(timezone.utc).isoformat()}\n")
    fh.flush()
    _RUN_LOCK_HANDLE = fh


def _enforce_run_interval(symbol: str, force: bool = False) -> None:
    """Prevent sequential manual/API/cron runs from hammering the same symbol."""
    if MIN_RUN_INTERVAL_MINUTES <= 0:
        return
    now = datetime.now(timezone.utc)
    key = symbol.upper()
    state = {}
    if RUN_INTERVAL_FILE.exists():
        try:
            state = json.loads(RUN_INTERVAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    last_raw = state.get(key)
    if last_raw and not force:
        try:
            last = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (now - last).total_seconds() / 60
            if elapsed < MIN_RUN_INTERVAL_MINUTES:
                wait = MIN_RUN_INTERVAL_MINUTES - elapsed
                log.warning(
                    "Run interval guard: skipping %s after %.1fm; need %.1fm "
                    "(TRACE_MIN_RUN_INTERVAL_MINUTES=%.1f).",
                    symbol,
                    elapsed,
                    wait,
                    MIN_RUN_INTERVAL_MINUTES,
                )
                sys.exit(0)
        except Exception:
            pass
    RUN_INTERVAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    state[key] = now.isoformat()
    tmp = RUN_INTERVAL_FILE.with_suffix(RUN_INTERVAL_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(RUN_INTERVAL_FILE)


def _extract_content(state: dict, node_name: str) -> str | None:
    report_keys = {
        "market_analyst":       "market_report",
        "fundamentals_analyst": "fundamentals_report",
        "news_analyst":         "news_report",
        "sentiment_analyst":    "sentiment_report",
        "social_media_analyst": "sentiment_report",
        "research_manager":     "investment_plan",
        "trader":               "trader_investment_plan",
        "portfolio_manager":    "final_trade_decision",
    }
    key = report_keys.get(node_name)
    if key and state.get(key):
        c = state[key]
        if isinstance(c, str) and len(c) > 10:
            return c

    if state.get("messages"):
        last = state["messages"][-1]
        if hasattr(last, "content") and last.content:
            return last.content

    for dk in ["investment_debate_state", "risk_debate_state"]:
        d = state.get(dk, {})
        if isinstance(d, dict):
            for fld in ["current_response", "current_aggressive_response",
                        "current_conservative_response", "current_neutral_response"]:
                if node_name in fld.replace("current_", "").replace("_response", "") or fld == "current_response":
                    val = d.get(fld)
                    if val:
                        return val
    return None


def _parse_final(raw) -> dict:
    result = {"action": "HOLD", "confidence": 0.5, "stop_loss": 0.015, "take_profit": 0.025, "reason": ""}
    data = {}
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        try:
            m = re.search(r'\{[^{}]*"action"[^{}]*\}', raw, re.DOTALL)
            if m:
                data = json.loads(m.group())
        except Exception:
            pass

    if "action" in data and data["action"].upper() in ("BUY", "SELL", "HOLD", "EXIT"):
        result["action"] = data["action"].upper()
    for fld in ("confidence", "stop_loss_pct", "take_profit_pct"):
        if fld in data:
            try:
                key = fld.replace("_pct", "").replace("_loss", "_loss").replace("take_profit", "take_profit")
                result[key] = float(data[fld])
            except Exception:
                pass
    if "primary_reason" in data:
        result["reason"] = str(data["primary_reason"])[:300]

    # keyword fallback
    if result["action"] == "HOLD" and isinstance(raw, str):
        t = raw.upper()
        b = t.count("BUY") + t.count("LONG") + t.count("BULLISH")
        s = t.count("SELL") + t.count("SHORT") + t.count("BEARISH")
        if b > s + 1:
            result["action"] = "BUY"
            result["confidence"] = min(0.55 + b * 0.02, 0.78)
        elif s > b + 1:
            result["action"] = "SELL"
            result["confidence"] = min(0.55 + s * 0.02, 0.78)

    return result


def run_analysis(symbol: str, trade_date: str, run_id: int, past_context: str) -> dict:
    _require_deepseek_key()
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    ticker = CRYPTO_MAP.get(symbol, symbol.split("/")[0])

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"]    = "deepseek"
    config["backend_url"]     = "https://api.deepseek.com"
    config["deep_think_llm"]  = "deepseek-chat"
    config["quick_think_llm"] = "deepseek-chat"
    config["max_debate_rounds"]   = 1
    config["checkpoint_enabled"]  = True
    config["online_tools"]        = True

    ta = TradingAgentsGraph(debug=False, config=config)
    raw_graph = getattr(ta, "graph", None)

    # ── Ghi context message để anh thấy trên dashboard ──
    if past_context and "No trade history" not in past_context:
        add_agent_message(
            run_id=run_id,
            agent_name="Memory",
            agent_role="Lịch sử giao dịch",
            layer="system",
            content=f"📊 Freqtrade feedback được inject:\n\n{past_context}",
        )

    add_agent_message(
        run_id=run_id,
        agent_name="System",
        agent_role="Khởi động",
        layer="system",
        content=f"Phân tích {symbol} ({ticker}) ngày {trade_date} | DeepSeek V4 Flash | debate_rounds=1",
    )

    raw_decision = None

    if raw_graph and hasattr(raw_graph, "stream"):
        log.info(f"[Run {run_id}] STREAM mode")
        seen = set()
        final_state = None

        # Inject past_context vào initial state
        # Use propagator to create fully-initialized state (includes count:0, all debate fields)
        if hasattr(ta, "propagator"):
            init_state = ta.propagator.create_initial_state(
                ticker, trade_date,
                asset_type="crypto",
                past_context=past_context or "",
            )
        else:
            init_state = {
                "company_of_interest": ticker,
                "trade_date": trade_date,
                "past_context": past_context or "",
                "investment_debate_state": {
                    "bull_history": "", "bear_history": "", "history": "",
                    "current_response": "", "judge_decision": "", "count": 0,
                },
                "risk_debate_state": {
                    "aggressive_history": "", "conservative_history": "",
                    "neutral_history": "", "history": "", "latest_speaker": "",
                    "current_aggressive_response": "", "current_conservative_response": "",
                    "current_neutral_response": "", "judge_decision": "", "count": 0,
                },
                "market_report": "", "fundamentals_report": "",
                "sentiment_report": "", "news_report": "",
            }

        for step in raw_graph.stream(init_state, config={"recursion_limit": 60}):
            if not isinstance(step, dict):
                continue
            for node, nstate in step.items():
                if node in seen:
                    continue
                seen.add(node)
                meta = AGENT_META.get(node, {
                    "name": node.replace("_", " ").title(),
                    "role": "Processing",
                    "layer": "system"
                })
                content = _extract_content(nstate, node) or f"[{meta['name']}] Hoàn thành."
                log.info(f"[Run {run_id}] {meta['name']} ({meta['layer']})")
                # Extract bias/confidence/recommendation from content
                extracted = {}
                if BIAS_OK:
                    extracted = extract_bias(str(content)[:5000], meta["layer"])
                add_agent_message(
                    run_id, meta["name"], meta["role"], meta["layer"],
                    str(content)[:5000],
                    agent_bias=extracted.get("agent_bias"),
                    agent_confidence=extracted.get("agent_confidence"),
                    agent_recommendation=extracted.get("agent_recommendation"),
                )
                final_state = nstate

        if final_state:
            raw_decision = (
                final_state.get("final_trade_decision") or
                final_state.get("trader_investment_plan") or
                final_state.get("investment_plan")
            )
    else:
        # Fallback batch mode
        log.warning(f"[Run {run_id}] Batch mode (no stream)")
        add_agent_message(run_id, "System", "Batch mode", "system",
                          "Chạy ở chế độ batch — không có streaming per-agent.")
        final_state, raw_decision = ta.propagate(ticker, trade_date)

        report_map = {
            "market_report":     ("Technical Analyst", "Phân tích kỹ thuật", "analysts"),
            "fundamentals_report":("Fundamentals Analyst","Phân tích cơ bản","analysts"),
            "news_report":       ("News Analyst", "Phân tích tin tức", "analysts"),
            "sentiment_report":  ("Sentiment Analyst", "Phân tích tâm lý", "analysts"),
            "investment_plan":   ("Research Manager", "Tổng hợp nghiên cứu", "researchers"),
            "final_trade_decision":("Portfolio Manager","Quyết định cuối","execution"),
        }
        if final_state:
            for key, (nm, role, layer) in report_map.items():
                val = final_state.get(key)
                if val:
                    add_agent_message(run_id, nm, role, layer, str(val)[:5000])

    signal = _parse_final(raw_decision)

    if signal["confidence"] < MIN_CONFIDENCE:
        orig = signal["action"]
        signal["action"] = "HOLD"
        signal["reason"] = f"Confidence {signal['confidence']:.2f} < {MIN_CONFIDENCE} (was {orig}). {signal['reason']}"

    add_agent_message(
        run_id=run_id,
        agent_name="System",
        agent_role="Kết quả cuối",
        layer="system",
        content=(
            f"✅ Phân tích hoàn tất\n"
            f"Quyết định: **{signal['action']}** | Confidence: {signal['confidence']:.2f}\n"
            f"SL: {signal.get('stop_loss', 0)*100:.2f}% | TP: {signal.get('take_profit', 0)*100:.2f}%\n"
            f"Reason: {signal['reason'][:300]}"
        ),
    )

    return signal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--date",   default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--skip-feedback", action="store_true", help="Bỏ qua feedback collection")
    parser.add_argument("--force", action="store_true", help="Bỏ qua interval guard cho test thủ công")
    args = parser.parse_args()
    _acquire_run_lock()
    _enforce_run_interval(args.symbol, force=args.force)

    init_db()
    if FEEDBACK_AVAILABLE:
        ensure_feedback_schema()

    symbol     = args.symbol
    trade_date = args.date

    # ── BƯỚC 1: Thu thập feedback từ Freqtrade (luồng ngược) ──────
    if FEEDBACK_AVAILABLE and not args.skip_feedback:
        log.info("Collecting Freqtrade feedback before analysis...")
        try:
            feedback_summary = run_feedback_collection()
            if isinstance(feedback_summary, dict):
                log.info(
                    "Feedback summary: %s outcomes, %s outback snapshots, %s adaptive proposals",
                    feedback_summary.get("recorded", 0),
                    len(feedback_summary.get("outback") or []),
                    len(feedback_summary.get("adaptive") or []),
                )
        except Exception as e:
            log.warning(f"Feedback collection failed (non-fatal): {e}")

    # ── BƯỚC 2: Lấy past_context để inject vào TradingAgents ──────
    past_context = ""
    if FEEDBACK_AVAILABLE:
        try:
            past_context = get_past_context(symbol, n=10)
            log.info(f"Past context ready: {len(past_context)} chars")
        except Exception as e:
            log.warning(f"Could not build past_context: {e}")

    # ── BƯỚC 2.5: Tính market regime trước khi run ───────────────
    current_regime = None
    regime_data = None
    if REGIME_OK:
        try:
            regime_data = get_current_regime(symbol)
            regime_value = regime_data.get("regime")
            regime_reason = regime_data.get("reason", "")
            if regime_value == "UNKNOWN":
                log.warning(f"Market regime UNKNOWN: {regime_reason}")
                current_regime = None  # treat UNKNOWN as not-available so Telegram shows N/A
            else:
                current_regime = regime_value
                log.info(f"Market regime: {current_regime} ({regime_reason})")
        except Exception as e:
            log.warning(f"Regime computation failed: {e}")

    past_context = _append_current_market_context(symbol, past_context, regime_data)

    # ── BƯỚC 3: Chạy TradingAgents với context ────────────────────
    log.info(f"=== Starting analysis: {symbol} on {trade_date} ===")
    run_id = create_run(symbol)

    try:
        analysis_started = monotonic()
        signal = run_analysis(symbol, trade_date, run_id, past_context)
        elapsed_minutes = (monotonic() - analysis_started) / 60
        signal = _defang_late_trade_signal(signal, elapsed_minutes, run_id)

        signal_id = write_signal(
            symbol=symbol,
            action=signal["action"],
            confidence=signal["confidence"],
            stop_loss=signal.get("stop_loss"),
            take_profit=signal.get("take_profit"),
            position_size_pct=signal.get("position_size_pct"),
            reason=signal.get("reason", ""),
            run_id=run_id,
        )

        # ── BƯỚC 3b: Ghi vào signal_bridge/signals.db để Freqtrade đọc ──
        try:
            sys.path.insert(0, str(TRACE_ROOT))
            from signal_bridge.signal_db import (
                write_signal as bridge_write,
                init_db as bridge_init_db,
            )
            bridge_init_db()
            bridge_action = signal["action"] if ENABLE_SHORT_SIGNALS else {"SELL": "EXIT"}.get(signal["action"], signal["action"])
            if bridge_action in ("BUY", "SELL", "HOLD", "EXIT"):
                bridge_write(
                    symbol=symbol,
                    action=bridge_action,
                    confidence=signal["confidence"],
                    stop_loss=signal.get("stop_loss", 0.02),
                    reason=(signal.get("reason", "") or "")[:300],
                )
                log.info(f"[Bridge→FT] Wrote {bridge_action} ({signal['confidence']:.2f}) to signals.db")
        except Exception as e:
            log.warning(f"[Bridge→FT] Failed (non-fatal): {e}")

        past_ctx_injected = bool(past_context and "No trade history" not in past_context)
        finish_run(
            run_id, signal["action"], signal["confidence"],
            position_size=signal.get("position_size_pct"),
            regime=current_regime,
            past_ctx_injected=past_ctx_injected,
        )

        # ── BƯỚC 3c: Gửi báo cáo Telegram ─────────────────────
        if TELEGRAM_OK:
            try:
                send_run_report(
                    run_id=run_id,
                    symbol=symbol,
                    action=signal["action"],
                    confidence=signal["confidence"],
                    regime=current_regime,
                    past_ctx_injected=past_ctx_injected,
                    signal_id=signal_id,
                )
            except Exception as e:
                log.warning(f"[Telegram] Gửi báo cáo thất bại (non-fatal): {e}")

        # ── BƯỚC 4: Signal lifecycle check ────────────────────────
        if LIFECYCLE_OK and signal["action"] in ("BUY","SELL") and signal_id:
            try:
                process_signal(signal_id, {
                    "symbol": symbol, "action": signal["action"],
                    "confidence": signal["confidence"],
                    "expires_at": None,  # will be fetched from DB
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                log.warning(f"Lifecycle check failed (non-fatal): {e}")

        log.info(f"=== Done: {symbol} → {signal['action']} ({signal['confidence']:.2f}) ===")

    except Exception as e:
        fail_run(run_id, str(e))
        log.error(f"Run {run_id} failed: {e}", exc_info=True)
        write_signal(symbol=symbol, action="HOLD", confidence=0.0,
                     reason=f"error: {str(e)[:100]}", run_id=run_id)
        if TELEGRAM_OK:
            try:
                send_error_alert(run_id=run_id, symbol=symbol, error=str(e)[:200])
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
