"""
market_regime.py — Tính market regime từ OHLCV data
Rule-based, không cần ML, đủ dùng cho Phase 1-2.

Regimes:
  TRENDING_UP      — EMA aligned up, ADX > 25, higher highs
  TRENDING_DOWN    — EMA aligned down, ADX > 25, lower lows
  SIDEWAYS         — ADX < 20, price in range
  HIGH_VOLATILITY  — ATR% > 3%, volume z-score cao
  LOW_VOLATILITY   — ATR% < 1%, narrow range
  NEWS_SHOCK       — Spike bất thường (chưa implement, cần news data)

Path: /opt/TraceTradeLab/dashboard/market_regime.py
Gọi từ: agent_runner_v2.py trước mỗi run
         cron riêng mỗi 1h để log lịch sử
"""

import sys, logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ─── CCXT import ─────────────────────────────────────────────────
try:
    import ccxt
    CCXT_OK = True
except ImportError:
    CCXT_OK = False
    log.warning("ccxt not installed — pip install ccxt")

# ─── DB ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))


def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> pd.DataFrame | None:
    """Lấy OHLCV từ Binance qua CCXT."""
    if not CCXT_OK:
        return None
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts")
        return df
    except Exception as e:
        log.error(f"CCXT fetch_ohlcv error for {symbol}: {e}")
        return None


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Tính ADX, ATR, EMA, volume z-score."""
    df = df.copy()
    n = len(df)
    if n < 30:
        return df

    # ── EMA ──────────────────────────────────────────────────────
    df["ema20"]  = df["close"].ewm(span=20,  adjust=False).mean()
    df["ema50"]  = df["close"].ewm(span=50,  adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    # ── ATR (14) ─────────────────────────────────────────────────
    df["prev_close"] = df["close"].shift(1)
    df["tr"] = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["prev_close"]).abs(),
        (df["low"]  - df["prev_close"]).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = df["tr"].ewm(span=14, adjust=False).mean()
    df["atr_pct"] = df["atr14"] / df["close"] * 100   # ATR as % of price

    # ── ADX (14) ─────────────────────────────────────────────────
    # Directional Movement
    df["dm_plus"]  = (df["high"] - df["high"].shift(1)).clip(lower=0)
    df["dm_minus"] = (df["low"].shift(1) - df["low"]).clip(lower=0)
    # When DM+ > DM-, set DM- to 0 and vice versa
    mask = df["dm_plus"] >= df["dm_minus"]
    df.loc[mask, "dm_minus"] = 0
    df.loc[~mask, "dm_plus"] = 0

    df["di_plus"]  = 100 * df["dm_plus"].ewm(span=14, adjust=False).mean()  / df["atr14"].replace(0, np.nan)
    df["di_minus"] = 100 * df["dm_minus"].ewm(span=14, adjust=False).mean() / df["atr14"].replace(0, np.nan)
    df["dx"] = (100 * (df["di_plus"] - df["di_minus"]).abs()
                / (df["di_plus"] + df["di_minus"]).replace(0, np.nan))
    df["adx"] = df["dx"].ewm(span=14, adjust=False).mean()

    # ── Volume z-score ────────────────────────────────────────────
    vol_mean = df["volume"].rolling(20).mean()
    vol_std  = df["volume"].rolling(20).std().replace(0, np.nan)
    df["vol_zscore"] = (df["volume"] - vol_mean) / vol_std

    # ── Trend structure ───────────────────────────────────────────
    df["hh"] = (df["high"] > df["high"].shift(1)).astype(int)   # higher high
    df["ll"] = (df["low"]  < df["low"].shift(1)).astype(int)    # lower low

    return df


def classify_regime(df: pd.DataFrame) -> dict:
    """
    Phân loại regime từ các indicators.
    Trả về dict với regime name và raw values.
    """
    if df is None or len(df) < 30:
        return {"regime": "UNKNOWN", "reason": "insufficient_data"}

    last = df.iloc[-1]
    prev_5 = df.iloc[-5:]

    adx      = float(last.get("adx", 0) or 0)
    atr_pct  = float(last.get("atr_pct", 0) or 0)
    ema20    = float(last.get("ema20", 0) or 0)
    ema50    = float(last.get("ema50", 0) or 0)
    ema200   = float(last.get("ema200", 0) or 0)
    close    = float(last.get("close", 0) or 0)
    vol_z    = float(last.get("vol_zscore", 0) or 0)
    di_plus  = float(last.get("di_plus", 0) or 0)
    di_minus = float(last.get("di_minus", 0) or 0)

    ema_up_aligned   = ema20 > ema50 > ema200
    ema_down_aligned = ema20 < ema50 < ema200
    price_above_ema20 = close > ema20
    price_below_ema20 = close < ema20

    hh_count = int(prev_5["hh"].sum())
    ll_count = int(prev_5["ll"].sum())

    # ── Classification logic ──────────────────────────────────────
    regime = "SIDEWAYS"
    reason = ""

    # High volatility overrides trend — check first
    if atr_pct > 3.5 or abs(vol_z) > 2.5:
        regime = "HIGH_VOLATILITY"
        reason = f"ATR%={atr_pct:.2f} vol_z={vol_z:.2f}"

    # Low volatility
    elif atr_pct < 0.8 and adx < 15:
        regime = "LOW_VOLATILITY"
        reason = f"ATR%={atr_pct:.2f} ADX={adx:.1f}"

    # Strong uptrend
    elif adx > 25 and ema_up_aligned and di_plus > di_minus and hh_count >= 3:
        regime = "TRENDING_UP"
        reason = f"ADX={adx:.1f} EMA aligned up DI+={di_plus:.1f} HH={hh_count}"

    # Strong downtrend
    elif adx > 25 and ema_down_aligned and di_minus > di_plus and ll_count >= 3:
        regime = "TRENDING_DOWN"
        reason = f"ADX={adx:.1f} EMA aligned down DI-={di_minus:.1f} LL={ll_count}"

    # Weak trend / sideways
    elif adx < 20:
        regime = "SIDEWAYS"
        reason = f"ADX={adx:.1f} low momentum"

    # Moderate trend
    elif adx >= 20 and ema_up_aligned:
        regime = "TRENDING_UP"
        reason = f"ADX={adx:.1f} moderate uptrend"

    elif adx >= 20 and ema_down_aligned:
        regime = "TRENDING_DOWN"
        reason = f"ADX={adx:.1f} moderate downtrend"

    else:
        regime = "SIDEWAYS"
        reason = f"ADX={adx:.1f} no clear direction"

    return {
        "regime":       regime,
        "reason":       reason,
        "adx":          round(adx, 2),
        "atr":          round(float(last.get("atr14", 0) or 0), 2),
        "atr_pct":      round(atr_pct, 3),
        "ema_fast":     round(ema20, 2),
        "ema_slow":     round(ema50, 2),
        "ema_aligned":  ema_up_aligned or ema_down_aligned,
        "volume_zscore":round(vol_z, 2),
        "close_price":  round(close, 2),
        "di_plus":      round(di_plus, 2),
        "di_minus":     round(di_minus, 2),
    }


def get_current_regime(symbol: str, timeframe: str = "1h") -> dict:
    """
    Tính và lưu regime hiện tại cho symbol.
    Gọi từ agent_runner_v2.py trước mỗi run.
    """
    df = fetch_ohlcv(symbol, timeframe, limit=220)
    if df is None:
        return {"regime": "UNKNOWN", "reason": "fetch_failed"}

    df = compute_indicators(df)
    result = classify_regime(df)

    # Lưu vào DB
    try:
        from db_v2 import save_regime
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00+00:00")
        save_regime(
            symbol=symbol,
            timestamp=ts,
            regime=result["regime"],
            adx=result["adx"],
            atr=result["atr"],
            atr_pct=result["atr_pct"],
            ema_fast=result["ema_fast"],
            ema_slow=result["ema_slow"],
            ema_aligned=result["ema_aligned"],
            volume_zscore=result["volume_zscore"],
            close_price=result["close_price"],
        )
        log.info(f"Regime saved: {symbol} → {result['regime']} ({result['reason']})")
    except Exception as e:
        log.warning(f"Could not save regime to DB: {e}")

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = get_current_regime("BTC/USDT")
    print(f"\n{'='*40}")
    print(f"Regime: {result['regime']}")
    print(f"Reason: {result['reason']}")
    print(f"ADX: {result['adx']} | ATR%: {result['atr_pct']}%")
    print(f"EMA20: {result['ema_fast']} | EMA50: {result['ema_slow']}")
    print(f"Volume Z-score: {result['volume_zscore']}")
