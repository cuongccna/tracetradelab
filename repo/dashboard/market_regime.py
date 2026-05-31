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

import os
import sys, logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)
DEFAULT_TRACE_ROOT = Path(__file__).resolve().parents[1]
TRACE_ROOT = Path(os.getenv("TRACE_ROOT", str(DEFAULT_TRACE_ROOT)))
ENV_FILE = Path(os.getenv("TRACE_ENV_FILE", str(TRACE_ROOT / ".env")))


def _load_env_defaults() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_defaults()
_LAST_FETCH_DIAGNOSTIC = ""

# ─── CCXT import ─────────────────────────────────────────────────
try:
    import ccxt
    CCXT_OK = True
except ImportError:
    CCXT_OK = False
    log.error("ccxt not installed in this venv — pip install ccxt. Market regime will use fallback data if available.")

# ─── DB ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _proxy_url() -> str | None:
    for key in (
        "TRACE_HTTPS_PROXY",
        "TRACE_HTTP_PROXY",
        "TRACE_ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "TWOCAPTCHA_PROXY_URI",
        "TWO_CAPTCHA_PROXY_URI",
        "CAPTCHA_PROXY_URI",
    ):
        value = os.getenv(key)
        if value:
            return _normalize_proxy_url(value.strip())
    return None


def _normalize_proxy_url(value: str) -> str:
    if "://" not in value:
        return value
    parsed = urlsplit(value)
    netloc = parsed.netloc
    if "@" in netloc:
        return value
    parts = netloc.split(":")
    if len(parts) >= 4 and parts[1].isdigit():
        host = parts[0]
        port = parts[1]
        username = parts[2]
        password = ":".join(parts[3:])
        return urlunsplit((parsed.scheme, f"{username}:{password}@{host}:{port}", parsed.path, parsed.query, parsed.fragment))
    return value


def _ccxt_config() -> dict:
    cfg = {
        "enableRateLimit": True,
        "timeout": int(os.getenv("TRACE_CCXT_TIMEOUT_MS", "20000")),
    }
    proxy = _proxy_url()
    if proxy:
        cfg["proxies"] = {"http": proxy, "https": proxy}
    return cfg


def _diagnose_ccxt_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if "451" in text or "restricted location" in low:
        return f"binance_geo_or_ip_block:{name}:{text[:180]}"
    if "429" in text or "rate limit" in low or "too many" in low:
        return f"binance_rate_limited:{name}:{text[:180]}"
    if "timeout" in low or name in {"RequestTimeout", "TimeoutError"}:
        return f"binance_timeout:{name}:{text[:180]}"
    if "403" in text or "forbidden" in low:
        return f"binance_forbidden_or_waf:{name}:{text[:180]}"
    return f"binance_fetch_error:{name}:{text[:180]}"


def _ticker_for_yfinance(symbol: str) -> str:
    base = symbol.split("/", 1)[0].split(":", 1)[0].upper()
    return f"{base}-USD"


def _timeframe_to_yf_interval(timeframe: str) -> tuple[str, str]:
    mapping = {
        "15m": ("15m", "30d"),
        "30m": ("30m", "60d"),
        "1h": ("1h", "730d"),
        "2h": ("1h", "730d"),
        "4h": ("1h", "730d"),
        "1d": ("1d", "5y"),
    }
    return mapping.get(timeframe, ("1h", "730d"))


def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> pd.DataFrame | None:
    """Lấy OHLCV từ Binance qua CCXT, fallback sang yfinance nếu Binance lỗi."""
    global _LAST_FETCH_DIAGNOSTIC
    _LAST_FETCH_DIAGNOSTIC = ""
    proxy = _proxy_url()
    if proxy:
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", proxy)
    if not CCXT_OK:
        log.error("fetch_ohlcv skipped Binance CCXT: ccxt not installed")
        _LAST_FETCH_DIAGNOSTIC = "ccxt_not_installed"
    else:
        try:
            exchange = ccxt.binance(_ccxt_config())
            raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            df = df.set_index("ts")
            df.attrs["data_source"] = "binance_ccxt"
            return df
        except Exception as e:
            _LAST_FETCH_DIAGNOSTIC = _diagnose_ccxt_error(e)
            log.error("CCXT fetch_ohlcv error for %s: %s", symbol, _LAST_FETCH_DIAGNOSTIC)

    try:
        import yfinance as yf
        interval, period = _timeframe_to_yf_interval(timeframe)
        ticker = _ticker_for_yfinance(symbol)
        data = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if data is None or data.empty:
            _LAST_FETCH_DIAGNOSTIC += f"; yfinance_empty:{ticker}"
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        data = data.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        df = data[["open", "high", "low", "close", "volume"]].dropna().tail(limit).copy()
        df.index = pd.to_datetime(df.index, utc=True)
        df.attrs["data_source"] = f"yfinance:{ticker}"
        log.warning("Using yfinance fallback for market regime: %s (%s)", symbol, ticker)
        return df
    except Exception as e:
        _LAST_FETCH_DIAGNOSTIC += f"; yfinance_fallback_failed:{type(e).__name__}:{str(e)[:160]}"
        log.error("Market regime fallback failed for %s: %s", symbol, _LAST_FETCH_DIAGNOSTIC)
        return None


def _load_cached_regime(symbol: str, max_age_hours: float = 6) -> dict | None:
    try:
        from db_v2 import get_conn
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM market_regimes
                WHERE symbol = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        ts = datetime.fromisoformat(str(data["timestamp"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - ts > timedelta(hours=max_age_hours):
            return None
        return {
            "regime": data["regime"],
            "reason": f"using_cached_regime_after_fetch_failed:{_LAST_FETCH_DIAGNOSTIC}",
            "adx": data.get("adx"),
            "atr": data.get("atr"),
            "atr_pct": data.get("atr_pct"),
            "ema_fast": data.get("ema_fast"),
            "ema_slow": data.get("ema_slow"),
            "ema_aligned": bool(data.get("ema_aligned")),
            "volume_zscore": data.get("volume_zscore"),
            "close_price": data.get("close_price"),
            "data_source": "cached_db",
        }
    except Exception as exc:
        log.warning("Could not load cached regime for %s: %s", symbol, exc)
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
        cached = _load_cached_regime(symbol)
        if cached:
            return cached
        return {"regime": "UNKNOWN", "reason": _LAST_FETCH_DIAGNOSTIC or "fetch_failed"}

    df = compute_indicators(df)
    result = classify_regime(df)
    result["data_source"] = df.attrs.get("data_source", "unknown")
    if result["reason"]:
        result["reason"] = f"{result['reason']} | source={result['data_source']}"

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
