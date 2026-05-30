"""Stress-test adaptive risk guards without calling AI or placing orders."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "dashboard"
REPORT_PATH = Path(__file__).resolve().parents[1] / "adaptive_risk_stress_report.md"


def _write_config(path: Path, dry_run_wallet: float = 1000) -> None:
    path.write_text(
        json.dumps(
            {
                "dry_run": True,
                "dry_run_wallet": dry_run_wallet,
                "timeframe": "1h",
                "stake_amount": 80,
                "max_open_trades": 2,
                "minimal_roi": {"0": 0.035, "180": 0.022, "480": 0.012, "960": 0},
                "stoploss": -0.018,
                "trading_mode": "futures",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _insert_outcomes(db_path: Path, symbol: str, pnls_newest_first: list[float]) -> None:
    now = datetime.now(timezone.utc)
    with sqlite3.connect(db_path) as conn:
        for idx, pnl in enumerate(pnls_newest_first):
            created_at = (now - timedelta(minutes=idx * 90 + 20)).isoformat()
            closed_at = (now - timedelta(minutes=idx * 90)).isoformat()
            cur = conn.execute(
                """
                INSERT INTO signals (
                    symbol, timeframe, action, confidence, stop_loss, reason,
                    raw_output, created_at, expires_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    symbol,
                    "1h",
                    "BUY" if pnl >= 0 else "SELL",
                    0.7,
                    0.018,
                    "stress",
                    "{}",
                    created_at,
                    (now + timedelta(minutes=90)).isoformat(),
                ),
            )
            signal_id = cur.lastrowid
            conn.execute(
                """
                INSERT INTO signal_outcomes (
                    signal_id, ft_trade_id, symbol, signal_action,
                    actual_entry, actual_exit, profit_pct, profit_abs,
                    trade_duration, sl_triggered, tp1_triggered,
                    outcome_correct, close_reason, closed_at, recorded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal_id,
                    10000 + idx,
                    symbol,
                    "BUY" if pnl >= 0 else "SELL",
                    100,
                    100 + pnl,
                    pnl,
                    pnl,
                    60,
                    0,
                    1 if pnl > 0 else 0,
                    1 if pnl > 0 else 0,
                    "stress",
                    closed_at,
                    now.isoformat(),
                ),
            )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="adaptive-risk-") as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "tracetrader.db"
        config_path = tmp / "config.json"
        _write_config(config_path)

        os.environ["TRACE_DB_PATH"] = str(db_path)
        os.environ["FREQTRADE_CONFIG_PATH"] = str(config_path)
        os.environ["TRACE_FUTURES_MIN_NOTIONAL_USDT"] = "80"
        sys.path.insert(0, str(DASHBOARD_DIR))

        from db_v2 import init_db
        from adaptive_risk import normalize_outback_payload, record_outback_payload, run_adaptive_workflow

        init_db()

        cases = [
            {
                "id": "BALANCE_DRAWDOWN_NORMALIZATION",
                "symbol": "BTC/USDT",
                "wallet": 1000,
                "outback": {
                    "source": "stress",
                    "symbol": "BTC/USDT",
                    "market": "futures",
                    "dry_run_wallet": 1000,
                    "profit_summary": {
                        "profit_all_coin": -25,
                        "profit_all_ratio": -0.025,
                        "current_drawdown": 0.025,
                        "max_drawdown": 0.04,
                    },
                },
                "pnls": [-0.05, -0.27, -0.22, 2.14],
            },
            {
                "id": "MIN_NOTIONAL_BLOCK_SMALL_ACCOUNT",
                "symbol": "ETH/USDT",
                "wallet": 300,
                "outback": {
                    "source": "stress",
                    "symbol": "ETH/USDT",
                    "market": "futures",
                    "dry_run_wallet": 300,
                    "profit_summary": {"profit_all_coin": 0},
                },
                "pnls": [0.12, 0.08, -0.06, 0.1, -0.04, 0.09, 0.11, -0.05, 0.07, 0.06, -0.03, 0.1],
            },
            {
                "id": "SKEWED_PNL_MEDIAN_GUARD",
                "symbol": "SOL/USDT",
                "wallet": 1000,
                "outback": {
                    "source": "stress",
                    "symbol": "SOL/USDT",
                    "market": "futures",
                    "dry_run_wallet": 1000,
                    "profit_summary": {"profit_all_coin": 0},
                },
                "pnls": [12.0, -0.4, -0.35, -0.5, -0.45, -0.25, -0.55, -0.3, -0.5, -0.4, -0.35, -0.45],
            },
            {
                "id": "LOSS_STREAK_PAUSE",
                "symbol": "BNB/USDT",
                "wallet": 1000,
                "outback": {
                    "source": "stress",
                    "symbol": "BNB/USDT",
                    "market": "futures",
                    "dry_run_wallet": 1000,
                    "profit_summary": {"profit_all_coin": -10, "current_drawdown": 0.01},
                },
                "pnls": [-0.2, -0.3, -0.4, 0.8, 0.2, -0.1, 0.3, -0.2, 0.4, 0.1, -0.1, 0.2],
            },
        ]

        rows = []
        for case in cases:
            _write_config(config_path, dry_run_wallet=case["wallet"])
            _insert_outcomes(db_path, case["symbol"], case["pnls"])
            normalized = normalize_outback_payload(case["outback"])
            record_outback_payload(case["outback"])
            result = run_adaptive_workflow(symbol=case["symbol"], market="futures")
            final = result["arbitration"]["final_config"]
            metrics = final["evidence"]["metrics"]
            rows.append(
                {
                    "case": case["id"],
                    "balance": normalized["current_balance"],
                    "drawdown": normalized["drawdown_pct"],
                    "status": result["arbitration"]["safety_status"],
                    "stake": final["stake_amount"],
                    "max_trades": final["max_open_trades"],
                    "median": metrics["median_pnl"],
                    "expectancy": metrics["expectancy_pct"],
                    "loss_streak": metrics["loss_streak"],
                    "flags": ", ".join(final.get("safety_flags", [])) or "-",
                }
            )

        lines = [
            "# Adaptive risk stress report",
            "",
            "Không gọi AI, không đặt lệnh. Test chỉ kiểm tra guard quản trị vốn/rủi ro.",
            "",
            "| Case | Balance | Drawdown % | Status | Stake | Max trades | Median PnL | Expectancy | Loss streak | Flags |",
            "|---|---:|---:|---|---:|---:|---:|---:|---:|---|",
        ]
        for row in rows:
            lines.append(
                f"| {row['case']} | {row['balance']:.2f} | {row['drawdown'] or 0:.2f} | "
                f"{row['status']} | {row['stake']:.2f} | {row['max_trades']} | "
                f"{row['median']:.4f} | {row['expectancy']:.4f} | {row['loss_streak']} | {row['flags']} |"
            )
        lines.extend(
            [
                "",
                "Kết luận:",
                "",
                "- Balance dùng `dry_run_wallet + realized PnL` khi chưa có equity trực tiếp.",
                "- Drawdown ratio từ Freqtrade được đổi sang phần trăm.",
                "- Nếu Binance futures min-notional vượt trần exposure, proposal bị `BLOCKED`.",
                "- Median/expectancy/loss-streak guard chặn trường hợp avg PnL bị một lệnh thắng lớn làm lệch.",
            ]
        )
        REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(REPORT_PATH)
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
