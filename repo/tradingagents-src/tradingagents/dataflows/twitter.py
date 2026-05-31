"""X/Twitter recent-search fetcher for ticker sentiment.

Uses the official X API v2 recent search endpoint when a bearer token is
available. The fetcher returns a prompt-ready plaintext block and degrades
gracefully when credentials are missing, access is not enabled, or the endpoint
is rate-limited.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from .proxy import urlopen_with_proxy

logger = logging.getLogger(__name__)

_API_HOSTS = (
    "https://api.x.com/2/tweets/search/recent",
    "https://api.twitter.com/2/tweets/search/recent",
)
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"


def _bearer_token() -> str | None:
    for key in (
        "X_BEARER_TOKEN",
        "TWITTER_BEARER_TOKEN",
        "TWITTER_API_BEARER_TOKEN",
        "X_API_BEARER_TOKEN",
    ):
        value = os.getenv(key)
        if value:
            return value.strip()
    return None


def _query(ticker: str) -> str:
    symbol = ticker.upper().lstrip("$")
    return f'("${symbol}" OR ${symbol}) (crypto OR bitcoin OR ethereum OR trading OR market) -is:retweet lang:en'


def _format_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        try:
            body = exc.read(500).decode("utf-8", "replace")
        except Exception:
            body = ""
        return f"HTTP {exc.code}: {body[:220]}"
    return f"{type(exc).__name__}: {str(exc)[:220]}"


def fetch_twitter_posts(ticker: str, limit: int = 20, timeout: float = 12.0) -> str:
    """Fetch recent X/Twitter posts mentioning ``ticker``.

    Requires one of: X_BEARER_TOKEN, TWITTER_BEARER_TOKEN,
    TWITTER_API_BEARER_TOKEN, X_API_BEARER_TOKEN.
    """
    token = _bearer_token()
    if not token:
        return (
            "<twitter unavailable: missing bearer token. Set X_BEARER_TOKEN "
            "or TWITTER_BEARER_TOKEN to enable official X recent search>"
        )

    max_results = max(10, min(int(limit or 20), 100))
    params = urlencode(
        {
            "query": _query(ticker),
            "max_results": max_results,
            "tweet.fields": "created_at,public_metrics,lang,author_id,possibly_sensitive",
            "expansions": "author_id",
            "user.fields": "username,verified,public_metrics",
        }
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": _UA,
        "Accept": "application/json",
    }

    last_error = None
    payload = None
    for host in _API_HOSTS:
        req = Request(f"{host}?{params}", headers=headers)
        try:
            with urlopen_with_proxy(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
                break
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as exc:
            last_error = exc
            logger.warning("X/Twitter fetch failed for %s via %s: %s", ticker, host, exc)

    if payload is None:
        return f"<twitter unavailable: {_format_error(last_error) if last_error else 'unknown error'}>"

    tweets = payload.get("data") or []
    users = {
        str(user.get("id")): user
        for user in (payload.get("includes") or {}).get("users", [])
        if isinstance(user, dict)
    }
    if not tweets:
        return f"<no X/Twitter posts found for {ticker.upper()} in recent search window>"

    lines = [f"X/Twitter recent search - {len(tweets[:limit])} posts mentioning {ticker.upper()}:"]
    for tweet in tweets[:limit]:
        metrics = tweet.get("public_metrics") or {}
        author = users.get(str(tweet.get("author_id")), {})
        username = author.get("username", "?")
        verified = " verified" if author.get("verified") else ""
        created = tweet.get("created_at", "?")
        text = (tweet.get("text") or "").replace("\n", " ").strip()
        if len(text) > 280:
            text = text[:280] + "..."
        lines.append(
            f"  [{created} | @{username}{verified} | "
            f"like={metrics.get('like_count', 0)} rt={metrics.get('retweet_count', 0)} "
            f"reply={metrics.get('reply_count', 0)} quote={metrics.get('quote_count', 0)}] {text}"
        )
    return "\n".join(lines)
