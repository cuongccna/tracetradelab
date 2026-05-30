"""Persistent signal store shared by the TradingAgents runner and Freqtrade."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "signals.db"
DB_PATH = Path(os.getenv("SIGNAL_DB_PATH", str(DEFAULT_DB_PATH)))
SIGNAL_TTL_MINUTES = int(os.getenv("SIGNAL_TTL_MINUTES", "70"))
VALID_ACTIONS = frozenset({"BUY", "SELL", "HOLD", "EXIT"})


def _ensure_signal_actions_schema(conn: sqlite3.Connection) -> None:
    """Upgrade older bridge DBs so futures dry-run can store SELL signals."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='signals'"
    ).fetchone()
    if not row or "CHECK(action IN ('BUY', 'HOLD', 'EXIT'))" not in (row[0] or ""):
        return

    conn.execute("ALTER TABLE signals RENAME TO signals_old")
    conn.execute(
        """
        CREATE TABLE signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL DEFAULT '1h',
            action      TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'HOLD', 'EXIT')),
            confidence  REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            stop_loss   REAL,
            reason      TEXT,
            raw_output  TEXT,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO signals (
            id, symbol, timeframe, action, confidence, stop_loss, reason,
            raw_output, created_at, expires_at
        )
        SELECT id, symbol, timeframe, action, confidence, stop_loss, reason,
               raw_output, created_at, expires_at
        FROM signals_old
        """
    )
    conn.execute("DROP TABLE signals_old")


def init_db(db_path: Path | None = None) -> Path:
    """Create the signal database and return its location."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                timeframe   TEXT NOT NULL DEFAULT '1h',
                action      TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'HOLD', 'EXIT')),
                confidence  REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
                stop_loss   REAL,
                reason      TEXT,
                raw_output  TEXT,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL
            )
            """
        )
        _ensure_signal_actions_schema(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_symbol_created "
            "ON signals(symbol, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS whale_alerts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id         TEXT NOT NULL UNIQUE,
                blockchain       TEXT,
                symbol           TEXT NOT NULL,
                tx_type          TEXT,
                amount           REAL,
                amount_usd       REAL,
                from_owner       TEXT,
                to_owner         TEXT,
                transaction_hash TEXT,
                event_time       TEXT NOT NULL,
                text             TEXT,
                raw_output       TEXT,
                created_at       TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_whale_symbol_time "
            "ON whale_alerts(symbol, event_time DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS onchain_flows (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                source           TEXT NOT NULL,
                event_id         TEXT NOT NULL UNIQUE,
                chain            TEXT NOT NULL,
                asset            TEXT NOT NULL,
                direction        TEXT NOT NULL,
                entity           TEXT NOT NULL,
                amount           REAL NOT NULL,
                amount_usd       REAL,
                transaction_hash TEXT NOT NULL,
                event_time       TEXT NOT NULL,
                scope_note       TEXT,
                raw_output       TEXT,
                created_at       TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_onchain_asset_time "
            "ON onchain_flows(asset, event_time DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public_whale_feed (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id     TEXT NOT NULL UNIQUE,
                item_type   TEXT NOT NULL,
                asset       TEXT,
                chain       TEXT,
                amount      REAL,
                amount_usd  REAL,
                direction   TEXT,
                from_owner  TEXT,
                to_owner    TEXT,
                title       TEXT,
                url         TEXT,
                summary     TEXT,
                sentiment   TEXT,
                impact      TEXT,
                event_time  TEXT NOT NULL,
                raw_text    TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_public_whale_time "
            "ON public_whale_feed(event_time DESC)"
        )
    return path


def write_signal(
    symbol: str,
    action: str,
    confidence: float,
    stop_loss: float | None = None,
    reason: str = "",
    raw_output: str = "",
    db_path: Path | None = None,
) -> None:
    """Validate and append one signal to the audit history."""
    path = init_db(db_path)
    normalized_action = action.upper().strip()
    if normalized_action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"Invalid confidence: {confidence}")
    if stop_loss is not None and not 0.0 < stop_loss <= 0.03:
        raise ValueError(f"Invalid stop_loss: {stop_loss}")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SIGNAL_TTL_MINUTES)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO signals (
                symbol, action, confidence, stop_loss, reason, raw_output,
                created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                normalized_action,
                confidence,
                stop_loss,
                reason,
                raw_output,
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )


def get_latest_valid_signal(symbol: str, db_path: Path | None = None) -> dict | None:
    """Return the newest unexpired signal, or None when no signal is usable."""
    path = db_path or DB_PATH
    if not path.exists():
        return None

    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            """
            SELECT action, confidence, stop_loss, reason, raw_output,
                   created_at, expires_at
            FROM signals
            WHERE symbol = ? AND expires_at > ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol, now),
        ).fetchone()
    if row is None:
        return None
    return {
        "action": row[0],
        "confidence": row[1],
        "stop_loss": row[2],
        "reason": row[3],
        "raw_output": row[4],
        "created_at": row[5],
        "expires_at": row[6],
    }


def write_whale_alert(event: dict, db_path: Path | None = None) -> int:
    """Store one Whale Alert websocket event, one row per transferred asset."""
    path = init_db(db_path)
    transaction = event.get("transaction") or {}
    tx_hash = str(transaction.get("hash") or event.get("transaction_hash") or "")
    timestamp = event.get("timestamp")
    if timestamp is None:
        raise ValueError("Whale alert does not contain a timestamp")
    event_time = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
    amounts = event.get("amounts") or []
    if not isinstance(amounts, list) or not amounts:
        return 0

    created_at = datetime.now(timezone.utc).isoformat()
    raw_output = json.dumps(event, separators=(",", ":"), ensure_ascii=True)[:20000]
    inserted = 0
    with sqlite3.connect(path) as conn:
        for index, item in enumerate(amounts):
            if not isinstance(item, dict) or not item.get("symbol"):
                continue
            symbol = str(item["symbol"]).lower()
            event_id = (
                f"{tx_hash}:{symbol}:{index}"
                if tx_hash
                else f"{event.get('channel_id', 'alert')}:{timestamp}:{symbol}:{index}"
            )
            result = conn.execute(
                """
                INSERT OR IGNORE INTO whale_alerts (
                    event_id, blockchain, symbol, tx_type, amount, amount_usd,
                    from_owner, to_owner, transaction_hash, event_time, text,
                    raw_output, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event.get("blockchain"),
                    symbol,
                    event.get("transaction_type"),
                    item.get("amount"),
                    item.get("value_usd"),
                    event.get("from"),
                    event.get("to"),
                    tx_hash,
                    event_time,
                    event.get("text"),
                    raw_output,
                    created_at,
                ),
            )
            inserted += result.rowcount
    return inserted


def get_recent_whale_alerts(
    symbol: str, hours: int = 6, db_path: Path | None = None
) -> list[dict]:
    """Return recent stored on-chain whale events for one token symbol."""
    path = db_path or DB_PATH
    if not path.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            """
            SELECT blockchain, symbol, tx_type, amount, amount_usd,
                   from_owner, to_owner, transaction_hash, event_time, text
            FROM whale_alerts
            WHERE lower(symbol) = lower(?) AND event_time >= ?
            ORDER BY event_time DESC
            LIMIT 30
            """,
            (symbol, cutoff),
        ).fetchall()
    columns = (
        "blockchain",
        "symbol",
        "tx_type",
        "amount",
        "amount_usd",
        "from_owner",
        "to_owner",
        "transaction_hash",
        "event_time",
        "text",
    )
    return [dict(zip(columns, row)) for row in rows]


def write_onchain_flow(flow: dict, db_path: Path | None = None) -> int:
    """Store a normalized tracked-wallet on-chain movement."""
    path = init_db(db_path)
    required = (
        "source",
        "event_id",
        "chain",
        "asset",
        "direction",
        "entity",
        "amount",
        "transaction_hash",
        "event_time",
    )
    missing = [key for key in required if flow.get(key) in (None, "")]
    if missing:
        raise ValueError(f"On-chain flow is missing fields: {', '.join(missing)}")
    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        result = conn.execute(
            """
            INSERT OR IGNORE INTO onchain_flows (
                source, event_id, chain, asset, direction, entity, amount,
                amount_usd, transaction_hash, event_time, scope_note,
                raw_output, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flow["source"],
                flow["event_id"],
                flow["chain"],
                str(flow["asset"]).upper(),
                flow["direction"],
                flow["entity"],
                float(flow["amount"]),
                flow.get("amount_usd"),
                flow["transaction_hash"],
                flow["event_time"],
                flow.get("scope_note"),
                flow.get("raw_output"),
                created_at,
            ),
        )
    return result.rowcount


def get_recent_onchain_flows(
    hours: int = 6, source: str | None = None, db_path: Path | None = None
) -> list[dict]:
    """Return recent normalized tracked-wallet flows, optionally by provider."""
    path = db_path or DB_PATH
    if not path.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conditions = ["event_time >= ?"]
    params: list[str] = [cutoff]
    if source:
        conditions.append("source = ?")
        params.append(source)
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT source, chain, asset, direction, entity, amount, amount_usd,
                   transaction_hash, event_time, scope_note
            FROM onchain_flows
            WHERE {" AND ".join(conditions)}
            ORDER BY event_time DESC
            LIMIT 100
            """,
            params,
        ).fetchall()
    columns = (
        "source",
        "chain",
        "asset",
        "direction",
        "entity",
        "amount",
        "amount_usd",
        "transaction_hash",
        "event_time",
        "scope_note",
    )
    return [dict(zip(columns, row)) for row in rows]


def write_public_whale_item(item: dict, db_path: Path | None = None) -> int:
    """Store one official public Whale Alert Telegram/story feed item."""
    path = init_db(db_path)
    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        result = conn.execute(
            """
            INSERT INTO public_whale_feed (
                post_id, item_type, asset, chain, amount, amount_usd,
                direction, from_owner, to_owner, title, url, summary,
                sentiment, impact, event_time, raw_text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                title=excluded.title, url=excluded.url, summary=excluded.summary,
                sentiment=excluded.sentiment, impact=excluded.impact,
                raw_text=excluded.raw_text
            """,
            (
                item["post_id"],
                item["item_type"],
                item.get("asset"),
                item.get("chain"),
                item.get("amount"),
                item.get("amount_usd"),
                item.get("direction"),
                item.get("from_owner"),
                item.get("to_owner"),
                item.get("title"),
                item.get("url"),
                item.get("summary"),
                item.get("sentiment"),
                item.get("impact"),
                item["event_time"],
                item["raw_text"],
                created_at,
            ),
        )
    return result.rowcount


def get_recent_public_whale_items(
    hours: int = 48, db_path: Path | None = None
) -> list[dict]:
    """Return recently observed public Whale Alert transfer and analysis posts."""
    path = db_path or DB_PATH
    if not path.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            """
            SELECT post_id, item_type, asset, chain, amount, amount_usd,
                   direction, from_owner, to_owner, title, url, summary,
                   sentiment, impact, event_time, raw_text
            FROM public_whale_feed
            WHERE event_time >= ?
            ORDER BY event_time DESC
            LIMIT 50
            """,
            (cutoff,),
        ).fetchall()
    columns = (
        "post_id",
        "item_type",
        "asset",
        "chain",
        "amount",
        "amount_usd",
        "direction",
        "from_owner",
        "to_owner",
        "title",
        "url",
        "summary",
        "sentiment",
        "impact",
        "event_time",
        "raw_text",
    )
    return [dict(zip(columns, row)) for row in rows]


if __name__ == "__main__":
    location = init_db()
    print(f"Initialized signal database at {location}")
