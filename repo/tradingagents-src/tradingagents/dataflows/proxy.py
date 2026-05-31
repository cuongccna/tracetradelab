"""Shared outbound proxy helpers for public data providers.

The helpers intentionally use environment variables only. Put credentials in
repo/.env or tradingagents-src/.env; never hard-code proxy secrets in code.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, urlopen, ProxyHandler


PROXY_ENV_KEYS = (
    "TRACE_HTTPS_PROXY",
    "TRACE_HTTP_PROXY",
    "TRACE_ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "TWOCAPTCHA_PROXY_URI",
    "TWO_CAPTCHA_PROXY_URI",
    "CAPTCHA_PROXY_URI",
)


def get_proxy_url() -> str | None:
    """Return the first configured proxy URI, if any."""
    for key in PROXY_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return normalize_proxy_url(value.strip())
    return None


def normalize_proxy_url(value: str) -> str:
    """Normalize common proxy formats into scheme://user:pass@host:port."""
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
        netloc = f"{username}:{password}@{host}:{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return value


def apply_proxy_env() -> str | None:
    """Populate standard proxy env vars from TraceTrade-specific settings."""
    proxy = get_proxy_url()
    if not proxy:
        return None
    os.environ.setdefault("HTTPS_PROXY", proxy)
    os.environ.setdefault("HTTP_PROXY", proxy)
    os.environ.setdefault("ALL_PROXY", proxy)
    os.environ.setdefault("https_proxy", proxy)
    os.environ.setdefault("http_proxy", proxy)
    os.environ.setdefault("all_proxy", proxy)
    return proxy


def requests_kwargs() -> dict:
    """Keyword arguments for requests/httpx-like clients that accept proxies."""
    proxy = apply_proxy_env()
    if not proxy:
        return {}
    return {"proxies": {"http": proxy, "https": proxy}}


def urlopen_with_proxy(req: Request | str, timeout: float):
    """Open a urllib request using configured proxy settings when present."""
    proxy = apply_proxy_env()
    if not proxy:
        return urlopen(req, timeout=timeout)
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    return opener.open(req, timeout=timeout)
