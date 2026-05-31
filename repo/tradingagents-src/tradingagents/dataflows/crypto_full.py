"""Crypto RSS + market mood fetcher for BTC/ETH/BNB/SOL.

This module is the app-integrated version of ``crypto_full_fetcher_v2.py``.
It keeps the V2 shape, but routes outbound requests through TraceTradeLab's
proxy helper and formats a compact prompt block for TradingAgents.
"""

from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request

from .proxy import get_proxy_url, urlopen_with_proxy

logger = logging.getLogger(__name__)

FETCHER_VERSION = "crypto_full_v2_app"
TARGET_COINS = ("bitcoin", "ethereum", "binancecoin", "solana")

COIN_META = {
    "bitcoin": {
        "symbol": "BTC",
        "aliases": ("bitcoin", "btc", "$btc", "btc/usdt", "bitcoin etf", "bitcoin etfs"),
    },
    "ethereum": {
        "symbol": "ETH",
        "aliases": ("ethereum", "ether", "eth", "$eth", "eth/usdt", "ethereum etf", "ethereum etfs"),
    },
    "binancecoin": {
        "symbol": "BNB",
        "aliases": ("bnb", "$bnb", "binance coin", "binancecoin", "bnb/usdt"),
    },
    "solana": {
        "symbol": "SOL",
        "aliases": ("solana", "sol", "$sol", "sol/usdt"),
    },
}

BULLISH = (
    "surge", "rally", "bull", "bullish", "breakout", "adoption", "upgrade",
    "approval", "pump", "moon", "ath", "all-time high", "soar", "rocket",
    "gain", "uptrend", "green", "positive", "optimistic", "accumulate",
    "buy", "support", "recover", "inflow", "record inflow",
)
BEARISH = (
    "crash", "dump", "bear", "bearish", "plunge", "decline", "hack",
    "exploit", "lawsuit", "ban", "fear", "liquidation", "sell",
    "resistance", "dip", "correction", "down", "drop", "fall",
    "negative", "pessimistic", "scam", "rug", "investigation", "freeze",
    "outflow", "seized", "fraud", "risk",
)
SOURCES = {
    "news_cointelegraph": ("cointelegraph_rss", "https://cointelegraph.com/rss"),
    "news_decrypt": ("decrypt_rss", "https://decrypt.co/feed"),
    "news_coindesk": ("coindesk_rss", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    "news_beincrypto": ("beincrypto_rss", "https://beincrypto.com/feed/"),
}


def _trace_root() -> Path:
    return Path(os.getenv("TRACE_ROOT", Path.cwd())).resolve()


def default_cache_path() -> Path:
    value = os.getenv("CRYPTO_FULL_FETCHER_CACHE")
    if value:
        return Path(value).expanduser().resolve()
    return _trace_root() / "results" / "10_crypto_full_fetcher.json"


def cache_candidates() -> list[Path]:
    root = _trace_root()
    candidates = [
        default_cache_path(),
        root.parent / "10_crypto_full_fetcher.json",
        root.parent / "results" / "10_crypto_full_fetcher.json",
        root / "10_crypto_full_fetcher.json",
    ]
    seen = set()
    unique: list[Path] = []
    for path in candidates:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    return unique


def _require_proxy() -> bool:
    value = os.getenv("CRYPTO_FULL_REQUIRE_PROXY", "true").strip().lower()
    return value not in ("0", "false", "no", "off")


def _open_json(url: str, timeout: float = 20.0) -> Any:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (TraceTradeLab CryptoBot v2)", "Accept": "application/json"},
    )
    with urlopen_with_proxy(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _open_xml(url: str, timeout: float = 20.0) -> bytes:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (TraceTradeLab CryptoBot v2)", "Accept": "application/rss+xml, application/xml, text/xml"},
    )
    with urlopen_with_proxy(req, timeout=timeout) as resp:
        return resp.read()


def _strip_html(raw: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", raw or "")
    clean = unescape(clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def _contains_term(text: str, term: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if term.startswith("$"):
        return term in text
    if "/" in term or " " in term or "-" in term:
        return term in text
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def analyze_sentiment(text: str) -> tuple[str, int, int]:
    lowered = text.lower()
    bullish = sum(1 for word in BULLISH if _contains_term(lowered, word))
    bearish = sum(1 for word in BEARISH if _contains_term(lowered, word))
    if bullish > bearish:
        return "bullish", bullish, bearish
    if bearish > bullish:
        return "bearish", bullish, bearish
    return "neutral", bullish, bearish


def detect_tickers(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for coin_id, meta in COIN_META.items():
        if any(_contains_term(lowered, alias) for alias in meta["aliases"]):
            found.append(coin_id)
    return found


def source_market() -> dict[str, Any]:
    qs = urlencode(
        {
            "vs_currency": "usd",
            "ids": ",".join(TARGET_COINS),
            "order": "market_cap_desc",
            "sparkline": "false",
        }
    )
    data = _open_json(f"https://api.coingecko.com/api/v3/coins/markets?{qs}")
    return {
        "source": "coingecko_market",
        "status": "ok",
        "count": len(data),
        "coins": {
            coin["id"]: {
                "symbol": coin.get("symbol"),
                "name": coin.get("name"),
                "current_price": coin.get("current_price"),
                "market_cap": coin.get("market_cap"),
                "total_volume": coin.get("total_volume"),
                "price_change_24h": coin.get("price_change_24h"),
                "price_change_percentage_24h": coin.get("price_change_percentage_24h"),
                "high_24h": coin.get("high_24h"),
                "low_24h": coin.get("low_24h"),
                "circulating_supply": coin.get("circulating_supply"),
                "last_updated": coin.get("last_updated"),
            }
            for coin in data
            if isinstance(coin, dict) and coin.get("id")
        },
    }


def source_fear_greed() -> dict[str, Any]:
    data = _open_json("https://api.alternative.me/fng/?limit=7")
    return {
        "source": "alternative.me_fear_greed",
        "status": "ok",
        "count": len(data.get("data", [])),
        "data": [
            {
                "value": int(row.get("value", 0)),
                "classification": row.get("value_classification"),
                "timestamp": row.get("timestamp"),
            }
            for row in data.get("data", [])
            if isinstance(row, dict)
        ],
    }


def source_trending() -> dict[str, Any]:
    data = _open_json("https://api.coingecko.com/api/v3/search/trending")
    coins = data.get("coins", [])
    return {
        "source": "coingecko_trending",
        "status": "ok",
        "count": len(coins),
        "coins": [
            {
                "name": (coin.get("item") or {}).get("name"),
                "symbol": (coin.get("item") or {}).get("symbol"),
                "market_cap_rank": (coin.get("item") or {}).get("market_cap_rank"),
                "score": (coin.get("item") or {}).get("score"),
            }
            for coin in coins
            if isinstance(coin, dict)
        ],
    }


def source_rss(source_name: str, url: str, max_items: int = 10) -> dict[str, Any]:
    try:
        xml = _open_xml(url)
    except Exception as exc:
        return {"source": source_name, "status": "error", "error": str(exc), "items": []}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return {"source": source_name, "status": "error", "error": f"XML parse: {exc}", "items": []}

    items = []
    for item in root.findall(".//item")[:max_items]:
        title = ((item.findtext("title") or "").strip())
        link = ((item.findtext("link") or "").strip())
        published = ((item.findtext("pubDate") or "").strip())
        description = _strip_html((item.findtext("description") or "").strip())
        combined = f"{title} {description}"
        sentiment, bull_count, bear_count = analyze_sentiment(combined)
        items.append(
            {
                "title": title,
                "link": link,
                "published": published,
                "description": description,
                "sentiment": sentiment,
                "sentiment_signals": {"bullish": bull_count, "bearish": bear_count},
                "relevant_tickers": detect_tickers(combined),
            }
        )
    return {"source": source_name, "status": "ok", "count": len(items), "items": items}


def fetch_crypto_full_data(write_cache: bool = True) -> dict[str, Any]:
    """Fetch crypto market/news/mood data through the configured proxy."""
    proxy = get_proxy_url()
    if _require_proxy() and not proxy:
        raise RuntimeError("CRYPTO_FULL_REQUIRE_PROXY=true but no proxy env is configured")

    fetchers = {
        "market": source_market,
        "fear_greed": source_fear_greed,
        "trending": source_trending,
        **{key: (lambda name=name, rss_url=url: source_rss(name, rss_url)) for key, (name, url) in SOURCES.items()},
    }

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=min(7, len(fetchers))) as executor:
        futures = {executor.submit(fn): name for name, fn in fetchers.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                logger.warning("Crypto full fetch failed for %s: %s", name, exc)
                results[name] = {"source": name, "status": "error", "error": str(exc)}

    cached = load_cached_crypto_full_data()
    cached_sources = (cached or {}).get("sources") or {}
    for name, result in list(results.items()):
        if result.get("status") == "ok":
            continue
        cached_result = cached_sources.get(name)
        if isinstance(cached_result, dict) and cached_result.get("status") == "ok":
            restored = dict(cached_result)
            restored["stale_from_cache"] = True
            restored["live_error"] = result.get("error")
            restored["cache_fetched_at"] = (cached or {}).get("fetched_at")
            results[name] = restored

    output = {
        "version": FETCHER_VERSION,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "target_coins": list(TARGET_COINS),
        "proxy_used": bool(proxy),
        "sources": results,
    }
    if write_cache:
        path = default_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def load_cached_crypto_full_data() -> dict[str, Any] | None:
    for path in cache_candidates():
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not load crypto full cache %s: %s", path, exc)
    return None


def fetch_or_load_crypto_full_data() -> dict[str, Any] | None:
    try:
        return fetch_crypto_full_data(write_cache=True)
    except Exception as exc:
        logger.warning("Live crypto full fetch failed; falling back to cache: %s", exc)
        return load_cached_crypto_full_data()


def _coin_id_for_ticker(ticker: str) -> str | None:
    normalized = ticker.upper().split("/")[0].lstrip("$")
    for coin_id, meta in COIN_META.items():
        if normalized == meta["symbol"]:
            return coin_id
    return None


def _fmt_num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "n/a"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "n/a"


def format_crypto_full_block(ticker: str, data: dict[str, Any] | None = None, max_items: int = 12) -> str:
    """Return a compact prompt block focused on ``ticker``."""
    data = data or fetch_or_load_crypto_full_data()
    if not data:
        return "<crypto full RSS unavailable: no live data and no cache>"

    coin_id = _coin_id_for_ticker(ticker)
    if not coin_id:
        return f"<crypto full RSS unavailable: unsupported ticker {ticker}>"

    sources = data.get("sources") or {}
    lines = [
        f"Crypto RSS + market mood snapshot for {ticker.upper()}",
        f"fetched_at={data.get('fetched_at', 'unknown')} proxy_used={data.get('proxy_used', 'unknown')}",
    ]

    market = ((sources.get("market") or {}).get("coins") or {}).get(coin_id) or {}
    if market:
        lines.append(
            "Market: "
            f"price=${_fmt_num(market.get('current_price'))}, "
            f"24h={_fmt_pct(market.get('price_change_percentage_24h'))}, "
            f"volume=${_fmt_num(market.get('total_volume'), 0)}, "
            f"high/low=${_fmt_num(market.get('high_24h'))}/${_fmt_num(market.get('low_24h'))}, "
            f"updated={market.get('last_updated', 'n/a')}"
        )

    fng = (sources.get("fear_greed") or {}).get("data") or []
    if fng:
        latest = fng[0]
        prior = fng[1] if len(fng) > 1 else {}
        lines.append(
            "Fear & Greed: "
            f"{latest.get('value')} {latest.get('classification')} "
            f"(previous={prior.get('value', 'n/a')} {prior.get('classification', '')})"
        )

    trending = (sources.get("trending") or {}).get("coins") or []
    if trending:
        top = ", ".join(
            f"{coin.get('symbol')}#{coin.get('score')}"
            for coin in trending[:8]
            if coin.get("symbol") is not None
        )
        lines.append(f"CoinGecko trending top: {top}")

    matching_items = []
    macro_items = []
    for source_key, source in sources.items():
        if not source_key.startswith("news_"):
            continue
        source_name = source.get("source") or source_key
        for item in source.get("items") or []:
            row = {**item, "source": source_name}
            if coin_id in (item.get("relevant_tickers") or []):
                matching_items.append(row)
            elif not item.get("relevant_tickers"):
                macro_items.append(row)

    selected = matching_items[:max_items]
    if len(selected) < max_items:
        selected.extend(macro_items[: max_items - len(selected)])

    if selected:
        lines.append("Relevant RSS headlines:")
        for item in selected:
            title = (item.get("title") or "").replace("\n", " ").strip()
            desc = (item.get("description") or "").replace("\n", " ").strip()
            if len(desc) > 180:
                desc = desc[:177] + "..."
            lines.append(
                f"- [{item.get('source')}] {item.get('published', '?')} | "
                f"{item.get('sentiment', 'neutral')} "
                f"{item.get('sentiment_signals', {})} | {title}"
                + (f" — {desc}" if desc else "")
            )
    else:
        lines.append("Relevant RSS headlines: <no ticker-specific or macro headlines found>")

    if matching_items:
        bull = sum(1 for item in matching_items if item.get("sentiment") == "bullish")
        bear = sum(1 for item in matching_items if item.get("sentiment") == "bearish")
        neutral = sum(1 for item in matching_items if item.get("sentiment") == "neutral")
        lines.append(
            f"Ticker-specific RSS sentiment count: bullish={bull}, bearish={bear}, neutral={neutral}, total={len(matching_items)}"
        )
    else:
        lines.append("Ticker-specific RSS sentiment count: no direct ticker matches; use macro/news context only")

    return "\n".join(lines)


def fetch_crypto_full_block(ticker: str, max_items: int = 12) -> str:
    try:
        return format_crypto_full_block(ticker, max_items=max_items)
    except Exception as exc:
        logger.warning("Crypto full block failed for %s: %s", ticker, exc)
        return f"<crypto full RSS unavailable: {type(exc).__name__}>"
