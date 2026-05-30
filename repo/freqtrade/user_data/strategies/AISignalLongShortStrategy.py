"""Conservative Binance futures strategy driven by TradingAgents bridge signals.

Use only in dry-run first. It expects TRACE_ENABLE_SHORT_SIGNALS=true in the
TradingAgents runner environment so SELL is written as a short signal instead
of being converted to EXIT for spot.
"""

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
MIN_CONFIDENCE = float(os.getenv("TRACE_MIN_CONFIDENCE", "0.68"))
TRACE_LEVERAGE = float(os.getenv("TRACE_LEVERAGE", "1.0"))
MIN_VOLUME_RATIO = float(os.getenv("TRACE_MIN_VOLUME_RATIO", "0.50"))
EXIT_ON_STALE_SIGNAL = os.getenv("TRACE_EXIT_ON_STALE_SIGNAL", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _signal_symbol(pair: str) -> str:
    return pair.split(":", 1)[0]


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


class AISignalLongShortStrategy(IStrategy):
    """Long on BUY, short on SELL, flatten on stale/EXIT."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True

    minimal_roi = {"0": 0.035, "180": 0.022, "480": 0.012, "960": 0}
    stoploss = -0.018
    use_custom_stoploss = True
    trailing_stop = True
    trailing_stop_positive = 0.012
    trailing_stop_positive_offset = 0.026
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    startup_candle_count = 80

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["ema_20"] = dataframe["close"].ewm(span=20, adjust=False).mean()
        dataframe["ema_50"] = dataframe["close"].ewm(span=50, adjust=False).mean()
        dataframe["vol_mean_20"] = dataframe["volume"].rolling(20).mean()
        return dataframe

    def _last_candle_time(self, dataframe: pd.DataFrame) -> datetime | None:
        if "date" not in dataframe.columns:
            return None
        try:
            value = dataframe["date"].iloc[-1]
            if hasattr(value, "to_pydatetime"):
                value = value.to_pydatetime()
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        except Exception:
            return None
        return None

    def _skip_market_gap(self, dataframe: pd.DataFrame, pair: str) -> str | None:
        last_ts = self._last_candle_time(dataframe)
        if last_ts and last_ts.hour in {22, 23, 0, 1}:
            return f"low-liquidity UTC window on candle {last_ts.isoformat()}"
        vol_mean = dataframe["vol_mean_20"].iloc[-1]
        if vol_mean > 0 and dataframe["volume"].iloc[-1] < MIN_VOLUME_RATIO * vol_mean:
            vol_ratio = dataframe["volume"].iloc[-1] / vol_mean
            return f"thin volume: {vol_ratio:.2f}x 20-candle average < {MIN_VOLUME_RATIO:.2f}x"
        return None

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = ""
        if dataframe.empty or len(dataframe) < self.startup_candle_count:
            return dataframe

        pair = metadata["pair"]
        signal = _read_signal(_signal_symbol(pair))
        if not signal or signal["confidence"] < MIN_CONFIDENCE:
            return dataframe
        skip_reason = self._skip_market_gap(dataframe, pair)
        if skip_reason:
            log.info(
                "%s: active %s signal %.2f skipped by market filter: %s",
                pair,
                signal["action"],
                signal["confidence"],
                skip_reason,
            )
            return dataframe

        last = dataframe.iloc[-1]
        if signal["action"] == "BUY" and last["close"] > last["ema_50"]:
            dataframe.loc[dataframe.index[-1], "enter_long"] = 1
            dataframe.loc[dataframe.index[-1], "enter_tag"] = f"AI_LONG_{signal['confidence']:.2f}"
        elif signal["action"] == "SELL" and last["close"] < last["ema_50"]:
            dataframe.loc[dataframe.index[-1], "enter_short"] = 1
            dataframe.loc[dataframe.index[-1], "enter_tag"] = f"AI_SHORT_{signal['confidence']:.2f}"
        else:
            log.info(
                "%s: active %s signal %.2f skipped by EMA50 filter: close %.4f ema50 %.4f",
                pair,
                signal["action"],
                signal["confidence"],
                last["close"],
                last["ema_50"],
            )
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = ""
        if dataframe.empty:
            return dataframe

        signal = _read_signal(_signal_symbol(metadata["pair"]))
        if signal is None:
            if EXIT_ON_STALE_SIGNAL:
                dataframe.loc[dataframe.index[-1], ["exit_long", "exit_short"]] = 1
                dataframe.loc[dataframe.index[-1], "exit_tag"] = "STALE_OR_EXIT"
            return dataframe
        if signal["action"] == "EXIT":
            dataframe.loc[dataframe.index[-1], ["exit_long", "exit_short"]] = 1
            dataframe.loc[dataframe.index[-1], "exit_tag"] = "AI_EXIT"
        elif signal["action"] == "SELL":
            dataframe.loc[dataframe.index[-1], "exit_long"] = 1
            dataframe.loc[dataframe.index[-1], "exit_tag"] = f"AI_SELL_{signal['confidence']:.2f}"
        elif signal["action"] == "BUY":
            dataframe.loc[dataframe.index[-1], "exit_short"] = 1
            dataframe.loc[dataframe.index[-1], "exit_tag"] = f"AI_BUY_{signal['confidence']:.2f}"
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
        signal = _read_signal(_signal_symbol(pair))
        if signal is None:
            return "STALE_SIGNAL" if EXIT_ON_STALE_SIGNAL else None
        if signal["action"] == "EXIT":
            return f"AI_EXIT_{signal['confidence']:.2f}"
        if trade.is_short and signal["action"] == "BUY":
            return f"AI_BUY_COVER_{signal['confidence']:.2f}"
        if not trade.is_short and signal["action"] == "SELL":
            return f"AI_SELL_FLAT_{signal['confidence']:.2f}"
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
        signal = _read_signal(_signal_symbol(pair))
        if signal and signal["stop_loss"] is not None:
            return -min(abs(signal["stop_loss"]), 0.025)
        return None

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return max(1.0, min(TRACE_LEVERAGE, max_leverage, 2.0))
