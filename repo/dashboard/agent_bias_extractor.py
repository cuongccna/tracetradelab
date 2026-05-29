"""
agent_bias_extractor.py — Parse agent_bias, agent_confidence, agent_recommendation
từ raw text content của từng agent message.

Input:  raw content string từ agent_messages.content
Output: {agent_bias, agent_confidence, agent_recommendation}

Dùng regex + keyword scoring — không cần LLM thêm.

Path: /opt/TraceTradeLab/dashboard/agent_bias_extractor.py
"""

import re
import logging

log = logging.getLogger(__name__)

# ─── Keyword maps ─────────────────────────────────────────────────

BULLISH_KEYWORDS = [
    "bullish", "buy", "long", "uptrend", "breakout", "accumulation",
    "positive", "upside", "rally", "higher", "support holds",
    "strong demand", "outflow", "etf inflow", "institutional buying",
    "oversold", "above ema", "golden cross", "higher high",
]

BEARISH_KEYWORDS = [
    "bearish", "sell", "short", "downtrend", "breakdown", "distribution",
    "negative", "downside", "decline", "lower", "resistance",
    "weak demand", "inflow", "etf outflow", "institutional selling",
    "overbought", "below ema", "death cross", "lower low",
    "stop loss", "sl triggered", "risk off", "liquidation",
]

NEUTRAL_KEYWORDS = [
    "hold", "neutral", "sideways", "wait", "unclear", "mixed",
    "inconclusive", "conflicting", "balanced", "range",
]

RECOMMENDATION_PATTERNS = {
    "BUY": [
        r"\bbuy\b", r"\blong\b", r"\benter\s+long\b",
        r"\bspot\s+buy\b", r"\baccumulate\b",
        r"recommended?\s+(action|spot)?\s*:?\s*buy",
        r"final\s+(trade\s+)?plan[:\s]+buy",
        r"action\s*[:\-]\s*buy",
    ],
    "SELL": [
        r"\bsell\b", r"\bexit\b", r"\bshort\b",
        r"recommended?\s+(action)?\s*:?\s*sell",
        r"action\s*[:\-]\s*sell",
    ],
    "HOLD": [
        r"\bhold\b", r"\bwait\b", r"\bdo\s+not\s+(enter|buy)\b",
        r"\bhold\s+cash\b", r"\bno\s+trade\b",
        r"recommended?\s+(action)?\s*:?\s*hold",
        r"action\s*[:\-]\s*hold",
    ],
    "REDUCE_SIZE": [
        r"reduce[_\s]size",
        r"reduce\s+(size|position|exposure)",
        r"recommend\w*\s*[:\-]\s*reduce[_\s]size",
        r"smaller\s+(position|size|allocation)",
        r"half\s+(position|size)",
        r"underweight",
    ],
    "BLOCK": [
        r"\bblock\b", r"\breject\b", r"\babort\b",
        r"do\s+not\s+trade", r"avoid\s+(this\s+)?trade",
        r"too\s+risky", r"pass\s+on",
    ],
}

CONFIDENCE_PATTERNS = [
    r"confidence\s*[:\-=]\s*(0?\.\d+|\d+%)",
    r"conf(?:idence)?\s+(0?\.\d+|\d+%)",
    r"(0?\.\d{2})\s+confidence",
]

BIAS_PATTERNS = {
    "bullish": [
        r"bias[:\s]+(bullish|bull)",
        r"(bullish|bull)\s+bias",
        r"overall[:\s]+(bullish|bull)",
        r"(bullish|bull)\s+signal",
        r"action\s*[:\-]\s*buy",
        r"\bapprove\b",
    ],
    "bearish": [
        r"bias[:\s]+(bearish|bear)",
        r"(bearish|bear)\s+bias",
        r"overall[:\s]+(bearish|bear)",
        r"(bearish|bear)\s+signal",
    ],
    "neutral": [
        r"bias[:\s]+neutral",
        r"neutral\s+bias",
        r"overall[:\s]+neutral",
        r"mixed\s+signal",
    ],
}


def extract_bias(content: str) -> str | None:
    """Parse agent_bias từ content."""
    text = content.lower()

    # 1. Tìm explicit bias pattern
    for bias, patterns in BIAS_PATTERNS.items():
        for p in patterns:
            if re.search(p, text):
                return bias

    # 2. Keyword scoring
    bull_score = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
    bear_score = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
    neut_score = sum(1 for kw in NEUTRAL_KEYWORDS if kw in text)

    total = bull_score + bear_score + neut_score
    if total == 0:
        return None

    # Cần ít nhất 2 keywords và có dominance rõ
    if total < 2:
        return None

    if bull_score > bear_score * 1.5 and bull_score > neut_score:
        return "bullish"
    elif bear_score > bull_score * 1.5 and bear_score > neut_score:
        return "bearish"
    elif neut_score >= bull_score and neut_score >= bear_score:
        return "neutral"
    else:
        return "mixed"


def extract_confidence(content: str) -> float | None:
    """Parse agent_confidence từ content."""
    for pattern in CONFIDENCE_PATTERNS:
        m = re.search(pattern, content.lower())
        if m:
            val_str = m.group(1)
            try:
                if "%" in val_str:
                    return round(float(val_str.replace("%", "")) / 100, 3)
                val = float(val_str)
                if 0 <= val <= 1:
                    return round(val, 3)
                if 1 < val <= 100:
                    return round(val / 100, 3)
            except Exception:
                pass
    return None


def extract_recommendation(content: str, layer: str = "") -> str | None:
    """Parse agent_recommendation từ content."""
    text = content.lower()

    # Risk management layer — check for BLOCK/REDUCE first
    if layer in ("risk_mgmt",):
        for rec in ("BLOCK", "REDUCE_SIZE"):
            for p in RECOMMENDATION_PATTERNS[rec]:
                if re.search(p, text):
                    return rec

    # Check all patterns in priority order
    for rec in ("BUY", "SELL", "HOLD", "REDUCE_SIZE", "BLOCK"):
        for p in RECOMMENDATION_PATTERNS[rec]:
            if re.search(p, text):
                return rec

    return None


def extract_all(content: str, layer: str = "") -> dict:
    """
    Extract tất cả 3 fields từ một agent message.
    Trả về dict với các key: agent_bias, agent_confidence, agent_recommendation
    """
    if not content or len(content) < 20:
        return {"agent_bias": None, "agent_confidence": None, "agent_recommendation": None}

    return {
        "agent_bias":          extract_bias(content),
        "agent_confidence":    extract_confidence(content),
        "agent_recommendation": extract_recommendation(content, layer),
    }


def extract_position_size(content: str) -> float | None:
    """Extract position size % từ Trader/PM output."""
    patterns = [
        r"position[_\s]size[_\s]pct[:\s]+([\d.]+)",
        r"position\s*:?\s*([\d.]+)%\s+of\s+portfolio",
        r"([\d.]+)%\s+of\s+portfolio",
        r"stake\s*:?\s*([\d.]+)%",
        r"size\s*[:\-]\s*([\d.]+)%",
        r"allocat\w+\s+([\d.]+)%",
    ]
    for p in patterns:
        m = re.search(p, content.lower())
        if m:
            try:
                val = float(m.group(1))
                if 0 < val <= 100:
                    return round(val, 2)
            except Exception:
                pass
    return None


def enrich_message_batch(messages: list[dict]) -> list[dict]:
    """
    Enrich một batch messages với bias/confidence/recommendation.
    messages: list of dicts với keys: content, layer, agent_name
    Trả về messages với 3 fields được thêm vào.
    """
    enriched = []
    for msg in messages:
        content = msg.get("content", "")
        layer   = msg.get("layer", "")
        extracted = extract_all(content, layer)
        enriched.append({**msg, **extracted})
    return enriched


# ─── Batch update DB ─────────────────────────────────────────────

def backfill_agent_biases(limit: int = 500):
    """
    Backfill agent_bias, agent_confidence, agent_recommendation
    cho các messages chưa có trong DB.
    Chạy một lần sau khi update schema.
    """
    import sqlite3
    from db_v2 import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, content, layer FROM agent_messages
        WHERE agent_bias IS NULL AND layer != 'system'
        LIMIT ?
    """, (limit,)).fetchall()

    updated = 0
    for row in rows:
        extracted = extract_all(row["content"], row["layer"])
        if any(v for v in extracted.values()):
            conn.execute("""
                UPDATE agent_messages
                SET agent_bias=?, agent_confidence=?, agent_recommendation=?
                WHERE id=?
            """, (extracted["agent_bias"], extracted["agent_confidence"],
                  extracted["agent_recommendation"], row["id"]))
            updated += 1

    conn.commit()
    conn.close()
    log.info(f"Backfilled {updated}/{len(rows)} agent messages with bias/confidence")
    return updated


if __name__ == "__main__":
    # Quick test
    samples = [
        ("Technical Analyst: RSI=58, EMA aligned bullish, MACD positive. Bias: BULLISH. Confidence: 0.74. Recommendation: BUY.", "analysts"),
        ("Bear Researcher: RSI overbought, funding elevated. SELL signal. Confidence 65%. Recommend: DO NOT ENTER SPOT.", "researchers"),
        ("Neutral Debater: Mixed signals. HOLD position. Confidence 0.55. Action: HOLD.", "risk_mgmt"),
        ("Conservative Analyst: Red flags: funding extreme. Recommend: REDUCE_SIZE or BLOCK.", "risk_mgmt"),
    ]
    print("=== Agent Bias Extractor Test ===")
    for text, layer in samples:
        result = extract_all(text, layer)
        print(f"\nLayer: {layer}")
        print(f"  Bias: {result['agent_bias']}")
        print(f"  Confidence: {result['agent_confidence']}")
        print(f"  Recommendation: {result['agent_recommendation']}")
