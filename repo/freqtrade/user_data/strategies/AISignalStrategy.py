"""Freqtrade long-only strategy driven by the external TradingAgents signal DB."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from freqtrade.strategy import IStrategy


log = logging.getLogger(__name__)
DB_PATH = Path(os.getenv("SIGNAL_DB_PATH", "/bridge/signals.db"))
MIN_CONFIDENCE = float(os.getenv("TRACE_MIN_CONFIDENCE", "0.65"))


def _read_signal(symbol: str) -> dict | None:
    if not DB_PATH.exists():
        log.warning("Signal database not found at %s", DB_PATH)
        return None
    try:
        uri = f"{DB_PATH.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            row = conn.execute(
                """
                SELECT action, confidence, stop_loss, reason, created_at
                FROM signals
                WHERE symbol = ? AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (symbol, datetime.now(timezone.utc).isoformat()),
            ).fetchone()
    except sqlite3.Error as exc:
        log.error("Unable to read signal database: %s", exc)
        return None
    if row is None:
        return None
    return {
        "action": row[0],
        "confidence": row[1],
        "stop_loss": row[2],
        "reason": row[3] or "",
        "created_at": row[4],
    }


class AISignalStrategy(IStrategy):
    """Execute only fresh high-conviction long entries during dry-run."""

    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False

    minimal_roi = {"0": 0.04, "720": 0.025, "1440": 0.015}
    stoploss = -0.025
    use_custom_stoploss = True
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    startup_candle_count = 20

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = ""
        if dataframe.empty:
            return dataframe

        signal = _read_signal(metadata["pair"])
        if signal and signal["action"] == "BUY" and signal["confidence"] >= MIN_CONFIDENCE:
            # Filter: dead zone 22:00–01:00 UTC (volume thấp, spread rộng)
            last_ts = dataframe.index[-1]
            if hasattr(last_ts, "hour") and last_ts.hour in {22, 23, 0, 1}:
                log.debug("%s: skipping BUY — dead zone hour %d UTC", metadata["pair"], last_ts.hour)
                return dataframe

            # Filter: volume bất thường thấp (< 50% trung bình 20 candle)
            vol_mean = dataframe["volume"].iloc[-20:].mean()
            if vol_mean > 0 and dataframe["volume"].iloc[-1] < 0.5 * vol_mean:
                log.debug("%s: skipping BUY — volume too low (ratio %.2f)", metadata["pair"],
                          dataframe["volume"].iloc[-1] / vol_mean)
                return dataframe

            dataframe.loc[dataframe.index[-1], "enter_long"] = 1
            dataframe.loc[dataframe.index[-1], "enter_tag"] = (
                f"AI_BUY_{signal['confidence']:.2f}"
            )
            log.info("%s: entry from AI signal (%0.2f)", metadata["pair"], signal["confidence"])
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_tag"] = ""
        if dataframe.empty:
            return dataframe

        signal = _read_signal(metadata["pair"])
        if signal is None:
            dataframe.loc[dataframe.index[-1], "exit_long"] = 1
            dataframe.loc[dataframe.index[-1], "exit_tag"] = "STALE_SIGNAL"
        elif signal["action"] == "EXIT":
            dataframe.loc[dataframe.index[-1], "exit_long"] = 1
            dataframe.loc[dataframe.index[-1], "exit_tag"] = (
                f"AI_EXIT_{signal['confidence']:.2f}"
            )
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        """Apply safety exits as soon as a new AI signal is stored."""
        signal = _read_signal(pair)
        if signal is None:
            return "STALE_SIGNAL"
        if signal["action"] == "EXIT":
            return f"AI_EXIT_{signal['confidence']:.2f}"
        return None

    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        signal = _read_signal(pair)
        if signal and signal["stop_loss"] is not None:
            return -min(abs(signal["stop_loss"]), 0.03)
        return None
