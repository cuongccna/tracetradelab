"""
Adaptive outback + risk/money management layer.

This module is intentionally conservative. It can ingest Freqtrade outback
telemetry, compute a quantitative proposal from a sliding window, critique the
proposal, and write only clamped, dry-run-safe values back to Freqtrade config.
"""

from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_v2 import get_conn, init_db


DEFAULT_TRACE_ROOT = Path(__file__).resolve().parents[1]
TRACE_ROOT = Path(os.getenv("TRACE_ROOT", str(DEFAULT_TRACE_ROOT)))
FREQTRADE_CONFIG_PATH = Path(
    os.getenv("FREQTRADE_CONFIG_PATH", str(TRACE_ROOT / "freqtrade/user_data/config.json"))
)


AGENT_A_SYSTEM_PROMPT = """Bạn là Agent A - Quantitative Optimizer trong nhóm RiskAndMoneyManagementAgents.

Mục tiêu tối cao: bảo vệ tài khoản trước, tối ưu lợi nhuận sau.

Nhiệm vụ:
- Đọc outback từ Freqtrade: current balance, trade history/ROI, drawdown, volatility/ATR, số lệnh mở.
- Chỉ dùng sliding window gần nhất có đặc tính thị trường tương đồng; không học từ dữ liệu quá cũ hoặc regime khác.
- Đề xuất Dynamic Volume theo % equity, Adaptive SL/TP theo ATR/biến động, và Max Co-current Trades.
- Nếu dữ liệu ít, drawdown tăng, loss streak xuất hiện, hoặc volatility cao: giảm rủi ro hoặc đề xuất PAUSE.
- Không được tăng rủi ro chỉ vì vài lệnh thắng ngắn hạn.

Output bắt buộc là JSON với:
dynamic_stake_pct, stake_amount, stoploss_pct, take_profit_pct, max_open_trades,
min_volume_ratio, confidence, evidence, reasoning, safety_flags.
"""


AGENT_B_SYSTEM_PROMPT = """Bạn là Agent B - Risk Critic trong nhóm RiskAndMoneyManagementAgents.

Vai trò của bạn là cực kỳ bảo thủ và chuyên phản biện Agent A.
Bạn tìm mọi lỗ hổng có thể làm mất vốn: overfitting, sample size nhỏ, drawdown tăng,
loss streak, stoploss quá rộng, stake quá lớn, quá nhiều lệnh đồng thời, tín hiệu cũ,
thanh khoản mỏng, funding/slippage, và sai lệch giữa dry-run/live.

Nguyên tắc:
- Nếu không đủ dữ liệu, mặc định giảm rủi ro.
- Nếu đề xuất tăng rủi ro khi drawdown hoặc volatility tăng, yêu cầu bác bỏ.
- Không cho phép cấu hình làm tổng exposure vượt giới hạn an toàn.
- Luôn ưu tiên giảm stake, giảm max_open_trades, hoặc PAUSE khi nghi ngờ.

Output bắt buộc là JSON với:
decision: APPROVE / REDUCE / BLOCK,
required_changes, risk_flags, reasoning.
"""


MANAGER_SYSTEM_PROMPT = """Bạn là Agent Manager - Safety Arbitrator.

Bạn điều phối tranh luận giữa Agent A và Agent B, tối đa 1-2 lượt.
Phán quyết cuối cùng phải theo tiêu chí: an toàn là trên hết.

Quy tắc phán quyết:
- Agent B có quyền phủ quyết mọi đề xuất tăng rủi ro nếu dữ liệu yếu.
- Chỉ áp dụng cấu hình đã qua hard clamp: dry_run=true, leverage thấp, stake giới hạn,
  stoploss giới hạn, max_open_trades giới hạn.
- Khi nghi ngờ, chọn cấu hình rủi ro thấp hơn hoặc PAUSE.
- Phải ghi rõ thông số cũ, thông số mới, lý do và các guard được kích hoạt.

Output bắt buộc là JSON với:
safety_status: APPROVED_SAFE / REDUCED_SAFE / BLOCKED,
final_config, manager_reasoning, apply_allowed.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_float(payload: dict, *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        cur: Any = payload
        ok = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            try:
                return float(cur)
            except (TypeError, ValueError):
                pass
    return default


def _as_int(payload: dict, *keys: str, default: int = 0) -> int:
    value = _as_float(payload, *keys, default=float(default))
    return int(value or 0)


def _ratio_to_pct(value: float | None) -> float | None:
    if value is None:
        return None
    if abs(value) <= 1:
        return value * 100
    return value


def normalize_outback_payload(payload: dict) -> dict:
    """Normalize free-form Freqtrade/outback payload into DB columns."""
    profit = payload.get("profit_summary") if isinstance(payload.get("profit_summary"), dict) else payload
    status = payload.get("open_trades") if isinstance(payload.get("open_trades"), list) else payload.get("status")
    trades = payload.get("trade_history") or payload.get("trades") or []
    if isinstance(trades, dict):
        trades = trades.get("trades", [])
    if not isinstance(trades, list):
        trades = []

    symbol = payload.get("symbol") or payload.get("pair")
    if not symbol and trades:
        symbol = trades[0].get("pair") or trades[0].get("symbol")

    open_count = len(status) if isinstance(status, list) else _as_int(payload, "open_trade_count", default=0)
    closed_count = len(trades) if trades else _as_int(payload, "closed_trade_count", default=0)

    explicit_balance = _as_float(
        payload,
        "current_balance",
        "balance",
        "equity",
        "wallet.total",
        "profit_summary.starting_balance",
        "profit_summary.total",
        default=None,
    )
    dry_run_wallet = _as_float(payload, "dry_run_wallet", default=None)
    total_profit_abs = _as_float(
        profit,
        "profit_all_coin",
        "profit_closed_coin",
        "profit_all_fiat",
        "total_profit",
        "profit_total_abs",
        "closed_trade_count_profit",
        "total_profit_abs",
        default=None,
    )
    current_balance = explicit_balance
    if current_balance is None and dry_run_wallet is not None:
        current_balance = dry_run_wallet + (total_profit_abs or 0)

    total_profit_pct = _as_float(
        profit,
        "profit_all_percent",
        "profit_closed_percent",
        "profit_total",
        "total_profit_pct",
        "profit_total_pct",
        default=None,
    )
    if total_profit_pct is None:
        total_profit_pct = _ratio_to_pct(_as_float(profit, "profit_all_ratio", "profit_closed_ratio", default=None))

    drawdown_pct = _as_float(payload, "drawdown_pct", default=None)
    if drawdown_pct is None:
        drawdown_pct = _ratio_to_pct(
            _as_float(payload, "current_drawdown", "drawdown", default=None)
            if _as_float(payload, "current_drawdown", "drawdown", default=None) is not None
            else _as_float(profit, "current_drawdown", "drawdown", default=None)
        )
    max_drawdown_pct = _as_float(payload, "max_drawdown_pct", default=None)
    if max_drawdown_pct is None:
        max_drawdown_pct = _ratio_to_pct(
            _as_float(payload, "max_drawdown", default=None)
            if _as_float(payload, "max_drawdown", default=None) is not None
            else _as_float(profit, "max_drawdown", default=drawdown_pct)
        )
    volatility_atr_pct = _as_float(payload, "volatility_atr_pct", "atr_pct", "volatility", default=None)

    return {
        "source": str(payload.get("source") or "freqtrade"),
        "payload_id": payload.get("payload_id"),
        "symbol": symbol or "BTC/USDT",
        "market": str(payload.get("market") or payload.get("trading_mode") or "futures").lower(),
        "timeframe": payload.get("timeframe") or "1h",
        "current_balance": current_balance,
        "available_balance": _as_float(payload, "available_balance", "free_balance", default=current_balance),
        "equity_peak": _as_float(payload, "equity_peak", default=None),
        "drawdown_pct": drawdown_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "volatility_atr_pct": volatility_atr_pct,
        "total_profit_pct": total_profit_pct,
        "total_profit_abs": total_profit_abs,
        "open_trade_count": open_count,
        "closed_trade_count": closed_count,
        "trade_history": trades,
        "raw_payload": payload,
    }


def record_outback_payload(payload: dict) -> dict:
    init_db()
    item = normalize_outback_payload(payload)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO freqtrade_outback_events (
                source, payload_id, symbol, market, timeframe,
                current_balance, available_balance, equity_peak,
                drawdown_pct, max_drawdown_pct, volatility_atr_pct,
                total_profit_pct, total_profit_abs,
                open_trade_count, closed_trade_count,
                trade_history, raw_payload, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item["source"],
                item["payload_id"],
                item["symbol"],
                item["market"],
                item["timeframe"],
                item["current_balance"],
                item["available_balance"],
                item["equity_peak"],
                item["drawdown_pct"],
                item["max_drawdown_pct"],
                item["volatility_atr_pct"],
                item["total_profit_pct"],
                item["total_profit_abs"],
                item["open_trade_count"],
                item["closed_trade_count"],
                json.dumps(item["trade_history"], ensure_ascii=False),
                json.dumps(item["raw_payload"], ensure_ascii=False),
                _now(),
            ),
        )
        item["id"] = cur.lastrowid
    return item


def _load_old_config() -> dict:
    if not FREQTRADE_CONFIG_PATH.exists():
        return {}
    return json.loads(FREQTRADE_CONFIG_PATH.read_text(encoding="utf-8"))


def load_sliding_window(
    symbol: str = "BTC/USDT",
    market: str = "futures",
    max_trades: int = 30,
    min_trades: int = 5,
) -> dict:
    """Load a recent, regime-aware window from outcomes and outback telemetry."""
    init_db()
    with get_conn() as conn:
        latest_outback = conn.execute(
            """
            SELECT * FROM freqtrade_outback_events
            WHERE symbol = ? AND market = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (symbol, market),
        ).fetchone()
        if latest_outback is None:
            latest_outback = conn.execute(
                """
                SELECT * FROM freqtrade_outback_events
                WHERE market = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (market,),
            ).fetchone()

        latest_regime = conn.execute(
            """
            SELECT * FROM market_regimes
            WHERE symbol = ?
            ORDER BY timestamp DESC LIMIT 1
            """,
            (symbol,),
        ).fetchone()

        rows = conn.execute(
            """
            SELECT so.*, s.confidence, s.created_at as signal_created_at,
                   ar.market_regime
            FROM signal_outcomes so
            JOIN signals s ON s.id = so.signal_id
            LEFT JOIN agent_runs ar ON ar.id = s.run_id
            WHERE so.symbol = ?
              AND so.outcome_correct IS NOT NULL
            ORDER BY so.closed_at DESC
            LIMIT ?
            """,
            (symbol, max_trades * 3),
        ).fetchall()

    latest_regime_name = latest_regime["regime"] if latest_regime else None
    similar = []
    fallback = []
    for row in rows:
        d = dict(row)
        fallback.append(d)
        if latest_regime_name and d.get("market_regime") and d["market_regime"] != latest_regime_name:
            continue
        similar.append(d)

    chosen = similar[:max_trades] if len(similar) >= min_trades else fallback[:max_trades]
    return {
        "symbol": symbol,
        "market": market,
        "latest_outback": dict(latest_outback) if latest_outback else None,
        "latest_regime": dict(latest_regime) if latest_regime else None,
        "rows": chosen,
        "used_regime_filter": len(similar) >= min_trades,
        "window_start": chosen[-1]["closed_at"] if chosen else None,
        "window_end": chosen[0]["closed_at"] if chosen else None,
    }


def _metrics(rows: list[dict]) -> dict:
    total = len(rows)
    wins = sum(1 for r in rows if r.get("outcome_correct"))
    pnl = [float(r.get("profit_pct") or 0) for r in rows]
    losses = [p for p in pnl if p < 0]
    gains = [p for p in pnl if p > 0]
    recent = pnl[: min(5, total)]
    loss_streak = 0
    for r in rows:
        if r.get("outcome_correct"):
            break
        loss_streak += 1
    gross_profit = sum(gains)
    gross_loss = abs(sum(losses))
    avg_gain = sum(gains) / len(gains) if gains else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    win_rate = wins / total if total else 0
    loss_rate = 1 - win_rate if total else 0
    expectancy = (win_rate * avg_gain) + (loss_rate * avg_loss) if total else 0
    return {
        "sample_size": total,
        "win_rate": round(win_rate * 100, 2) if total else 0,
        "loss_rate": round(loss_rate * 100, 2) if total else 0,
        "avg_pnl": round(sum(pnl) / total, 4) if total else 0,
        "median_pnl": round(statistics.median(pnl), 4) if pnl else 0,
        "expectancy_pct": round(expectancy, 4),
        "avg_gain": round(avg_gain, 4),
        "avg_loss": round(avg_loss, 4),
        "recent_loss_rate": round(sum(1 for p in recent if p < 0) / len(recent) * 100, 2) if recent else 0,
        "max_loss": round(min(pnl), 4) if pnl else 0,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "loss_streak": loss_streak,
    }


def compute_quantitative_proposal(symbol: str = "BTC/USDT", market: str = "futures") -> dict:
    old_config = _load_old_config()
    window = load_sliding_window(symbol=symbol, market=market)
    rows = window["rows"]
    m = _metrics(rows)
    outback = window["latest_outback"] or {}

    equity = float(outback.get("current_balance") or old_config.get("dry_run_wallet") or 1000)
    drawdown = float(outback.get("drawdown_pct") or outback.get("max_drawdown_pct") or 0)
    atr_pct = float(
        outback.get("volatility_atr_pct")
        or (window["latest_regime"] or {}).get("atr_pct")
        or (1.0 if market == "futures" else 1.4)
    )

    base_stake_pct = 0.08 if market == "futures" else 0.05
    if m["sample_size"] < 10:
        base_stake_pct *= 0.75
    if m["sample_size"] >= 5 and m["median_pnl"] <= 0:
        base_stake_pct *= 0.7
    if m["sample_size"] >= 5 and m["expectancy_pct"] <= 0:
        base_stake_pct *= 0.65
    if m["sample_size"] >= 10 and m["win_rate"] < 35:
        base_stake_pct *= 0.75
    if m["sample_size"] >= 5 and m["recent_loss_rate"] >= 60:
        base_stake_pct *= 0.7
    if m["loss_streak"] >= 2:
        base_stake_pct *= 0.55
    if drawdown >= 8:
        base_stake_pct *= 0.25
    elif drawdown >= 5:
        base_stake_pct *= 0.5
    elif drawdown >= 3:
        base_stake_pct *= 0.75
    if m["sample_size"] >= 20 and m["win_rate"] >= 55 and (m["profit_factor"] or 0) >= 1.2 and drawdown < 3:
        base_stake_pct *= 1.15

    max_stake_pct = 0.10 if market == "futures" else 0.08
    min_stake_pct = 0.015
    stake_pct = _clamp(base_stake_pct, min_stake_pct, max_stake_pct)

    sl_pct = _clamp(atr_pct * (1.35 if market == "futures" else 1.55), 1.2, 2.5 if market == "futures" else 3.0)
    tp_pct = _clamp(sl_pct * 1.6, 1.8, 4.5)
    max_open = 2
    if drawdown >= 5 or m["loss_streak"] >= 2 or m["sample_size"] < 10 or m["median_pnl"] <= 0:
        max_open = 1
    if drawdown >= 10 or m["loss_streak"] >= 3:
        max_open = 0

    min_volume_ratio = 0.5
    if drawdown >= 5 or atr_pct >= 2.2:
        min_volume_ratio = 0.65

    evidence = {
        "metrics": m,
        "equity": equity,
        "drawdown_pct": drawdown,
        "atr_pct": atr_pct,
        "used_regime_filter": window["used_regime_filter"],
        "window_start": window["window_start"],
        "window_end": window["window_end"],
    }
    safety_flags = []
    if m["sample_size"] < 10:
        safety_flags.append("SMALL_SAMPLE")
    if m["sample_size"] >= 5 and m["median_pnl"] <= 0:
        safety_flags.append("NEGATIVE_MEDIAN_PNL")
    if m["sample_size"] >= 5 and m["expectancy_pct"] <= 0:
        safety_flags.append("NON_POSITIVE_EXPECTANCY")
    if m["sample_size"] >= 5 and m["recent_loss_rate"] >= 60:
        safety_flags.append("RECENT_LOSS_RATE_HIGH")
    if drawdown >= 5:
        safety_flags.append("DRAWDOWN_REDUCTION")
    if m["loss_streak"] >= 2:
        safety_flags.append("LOSS_STREAK_REDUCTION")

    return {
        "agent": "Agent A - Quantitative Optimizer",
        "symbol": symbol,
        "market": market,
        "dynamic_stake_pct": round(stake_pct, 4),
        "stake_amount": round(equity * stake_pct, 2),
        "stoploss_pct": round(sl_pct, 3),
        "take_profit_pct": round(tp_pct, 3),
        "max_open_trades": max_open,
        "min_volume_ratio": min_volume_ratio,
        "confidence": 0.45 if m["sample_size"] < 10 else 0.65,
        "evidence": evidence,
        "safety_flags": safety_flags,
        "reasoning": (
            "Sliding window selected recent comparable outcomes. "
            "Sizing is reduced when sample size, drawdown, or loss streak weakens confidence."
        ),
    }


def risk_critic_review(proposal: dict) -> dict:
    flags = []
    required = {}
    market = proposal.get("market", "futures")
    max_stake = 0.10 if market == "futures" else 0.08
    if proposal["dynamic_stake_pct"] > max_stake:
        flags.append("STAKE_TOO_HIGH")
        required["dynamic_stake_pct"] = max_stake
    if proposal["stoploss_pct"] > (2.5 if market == "futures" else 3.0):
        flags.append("STOPLOSS_TOO_WIDE")
        required["stoploss_pct"] = 2.5 if market == "futures" else 3.0
    if proposal["max_open_trades"] > 2:
        flags.append("TOO_MANY_CONCURRENT_TRADES")
        required["max_open_trades"] = 2

    evidence = proposal.get("evidence", {})
    metrics = evidence.get("metrics", {})
    if metrics.get("sample_size", 0) < 10:
        flags.append("SMALL_SAMPLE_REQUIRE_MIN_RISK")
        required["max_open_trades"] = min(proposal["max_open_trades"], 1)
        required["dynamic_stake_pct"] = min(proposal["dynamic_stake_pct"], 0.06 if market == "futures" else 0.04)
    if metrics.get("sample_size", 0) >= 5 and metrics.get("median_pnl", 0) <= 0:
        flags.append("NEGATIVE_MEDIAN_REQUIRE_REDUCTION")
        required["dynamic_stake_pct"] = min(required.get("dynamic_stake_pct", proposal["dynamic_stake_pct"]), 0.05 if market == "futures" else 0.035)
        required["max_open_trades"] = 1
    if metrics.get("sample_size", 0) >= 5 and metrics.get("expectancy_pct", 0) <= 0:
        flags.append("EXPECTANCY_NOT_POSITIVE")
        required["dynamic_stake_pct"] = min(required.get("dynamic_stake_pct", proposal["dynamic_stake_pct"]), 0.035 if market == "futures" else 0.025)
        required["max_open_trades"] = 1
    if metrics.get("loss_streak", 0) >= 3:
        flags.append("LOSS_STREAK_PAUSE")
        required["max_open_trades"] = 0
    if evidence.get("drawdown_pct", 0) >= 8:
        flags.append("DRAWDOWN_HIGH")
        required["dynamic_stake_pct"] = min(required.get("dynamic_stake_pct", proposal["dynamic_stake_pct"]), 0.025)
        required["max_open_trades"] = 1

    decision = "APPROVE" if not flags else "REDUCE"
    if evidence.get("drawdown_pct", 0) >= 12 or metrics.get("loss_streak", 0) >= 3:
        decision = "BLOCK"
        required["max_open_trades"] = 0

    return {
        "agent": "Agent B - Risk Critic",
        "decision": decision,
        "required_changes": required,
        "risk_flags": flags,
        "reasoning": "Risk Critic applied capital-preservation clamps before any config update.",
    }


def arbitrate_final_config(proposal: dict, critique: dict, old_config: dict) -> dict:
    final = dict(proposal)
    for key, value in critique.get("required_changes", {}).items():
        final[key] = value

    market = proposal.get("market", "futures")
    equity = proposal.get("evidence", {}).get("equity") or old_config.get("dry_run_wallet") or 1000
    equity = float(equity)
    max_stake_pct = 0.10 if market == "futures" else 0.08
    max_stake_amount = round(equity * max_stake_pct, 2)
    final["dynamic_stake_pct"] = round(_clamp(float(final["dynamic_stake_pct"]), 0.0, max_stake_pct), 4)
    final["stake_amount"] = round(min(equity * final["dynamic_stake_pct"], max_stake_amount), 2)
    final["stoploss_pct"] = round(_clamp(float(final["stoploss_pct"]), 1.0, 2.5 if market == "futures" else 3.0), 3)
    final["take_profit_pct"] = round(_clamp(float(final["take_profit_pct"]), 1.4, 4.5), 3)
    final["max_open_trades"] = int(_clamp(int(final["max_open_trades"]), 0, 2))
    final["min_volume_ratio"] = round(_clamp(float(final.get("min_volume_ratio", 0.5)), 0.5, 0.85), 2)
    final["safety_flags"] = list(dict.fromkeys(final.get("safety_flags", []) + critique.get("risk_flags", [])))

    min_notional = float(os.getenv("TRACE_FUTURES_MIN_NOTIONAL_USDT", "80"))
    if market == "futures" and final["max_open_trades"] > 0 and final["stake_amount"] < min_notional:
        if min_notional > max_stake_amount:
            final["max_open_trades"] = 0
            final["stake_amount"] = 0.0
            final["safety_flags"].append("MIN_NOTIONAL_EXCEEDS_EXPOSURE_CAP")
        else:
            final["stake_amount"] = min_notional
            final["dynamic_stake_pct"] = round(min_notional / equity, 4) if equity else 0
            final["safety_flags"].append("MIN_NOTIONAL_RAISED_STAKE")

    status = "APPROVED_SAFE" if critique["decision"] == "APPROVE" and not final["safety_flags"] else "REDUCED_SAFE"
    if critique["decision"] == "BLOCK" or final["max_open_trades"] == 0:
        status = "BLOCKED"

    return {
        "agent": "Agent Manager - Safety Arbitrator",
        "safety_status": status,
        "apply_allowed": status != "BLOCKED",
        "final_config": final,
        "manager_reasoning": (
            "Final config follows the lower-risk side of the debate. "
            "dry_run remains forced true; only stake, stoploss, ROI and max_open_trades are writable."
        ),
    }


def save_adaptive_proposal(proposal: dict, critique: dict, arbitration: dict) -> dict:
    old_config = _load_old_config()
    evidence = proposal.get("evidence", {})
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO adaptive_risk_proposals (
                symbol, market, timeframe, window_start, window_end, sample_size,
                old_config, quant_proposal, risk_critique, final_config,
                manager_reasoning, safety_status, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                proposal["symbol"],
                proposal["market"],
                old_config.get("timeframe"),
                evidence.get("window_start"),
                evidence.get("window_end"),
                evidence.get("metrics", {}).get("sample_size", 0),
                json.dumps(old_config, ensure_ascii=False),
                json.dumps(proposal, ensure_ascii=False),
                json.dumps(critique, ensure_ascii=False),
                json.dumps(arbitration["final_config"], ensure_ascii=False),
                arbitration["manager_reasoning"],
                arbitration["safety_status"],
                _now(),
            ),
        )
        proposal_id = cur.lastrowid
    return {"id": proposal_id, "proposal": proposal, "critique": critique, "arbitration": arbitration}


def run_adaptive_workflow(symbol: str = "BTC/USDT", market: str = "futures") -> dict:
    old_config = _load_old_config()
    proposal = compute_quantitative_proposal(symbol=symbol, market=market)
    critique = risk_critic_review(proposal)
    arbitration = arbitrate_final_config(proposal, critique, old_config)
    saved = save_adaptive_proposal(proposal, critique, arbitration)
    saved["prompts"] = {
        "agent_a": AGENT_A_SYSTEM_PROMPT,
        "agent_b": AGENT_B_SYSTEM_PROMPT,
        "manager": MANAGER_SYSTEM_PROMPT,
    }
    return saved


def apply_proposal_to_freqtrade(proposal_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM adaptive_risk_proposals WHERE id=?",
            (proposal_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"adaptive proposal not found: {proposal_id}")
    if row["safety_status"] == "BLOCKED":
        raise ValueError("blocked adaptive proposal cannot be applied")

    final = json.loads(row["final_config"])
    config = _load_old_config()
    backup = FREQTRADE_CONFIG_PATH.with_suffix(
        FREQTRADE_CONFIG_PATH.suffix + f".adaptive.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    if FREQTRADE_CONFIG_PATH.exists():
        backup.write_text(FREQTRADE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    config["dry_run"] = True
    config["stake_amount"] = round(float(final["stake_amount"]), 2)
    config["max_open_trades"] = int(final["max_open_trades"])
    config["stoploss"] = -round(float(final["stoploss_pct"]) / 100, 5)
    tp = round(float(final["take_profit_pct"]) / 100, 5)
    config["minimal_roi"] = {
        "0": tp,
        "180": round(tp * 0.63, 5),
        "480": round(tp * 0.34, 5),
        "960": 0,
    }

    tmp = FREQTRADE_CONFIG_PATH.with_suffix(FREQTRADE_CONFIG_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(FREQTRADE_CONFIG_PATH)

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE adaptive_risk_proposals
            SET applied=1, applied_at=?, config_backup_path=?
            WHERE id=?
            """,
            (_now(), str(backup), proposal_id),
        )
    return {"proposal_id": proposal_id, "backup": str(backup), "applied_config": config}


def get_adaptive_dashboard(limit: int = 10) -> dict:
    init_db()
    with get_conn() as conn:
        latest_outback = conn.execute(
            "SELECT * FROM freqtrade_outback_events ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        proposals = conn.execute(
            "SELECT * FROM adaptive_risk_proposals ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    items = []
    for row in proposals:
        d = dict(row)
        for key in ("old_config", "quant_proposal", "risk_critique", "final_config"):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                pass
        items.append(d)
    return {
        "latest_outback": dict(latest_outback) if latest_outback else None,
        "proposals": items,
        "prompts": {
            "agent_a": AGENT_A_SYSTEM_PROMPT,
            "agent_b": AGENT_B_SYSTEM_PROMPT,
            "manager": MANAGER_SYSTEM_PROMPT,
        },
    }
