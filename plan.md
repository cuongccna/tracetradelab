# Plan: TraceTrader Lab V2 — Phased Implementation

## TL;DR
All 7 core files in the workspace are COMPLETE. The sole missing piece is `feedback_collector.py` (not in workspace, referenced by api_v2 + agent_runner_v2). Execution plan = (0) create feedback_collector.py → (1-5) deploy each phase to /opt/tracetrader/dashboard/ → run test gates → report after each phase.

---

## Phase 0 — Create feedback_collector.py (PREREQUISITE, BLOCKING)
Must be done before any deployment can pass test gates.

Required exports (from import analysis):
- `ensure_feedback_schema()` — init feedback tables if needed
- `get_accuracy_stats()` → dict with win_rate, total, etc.
- `get_past_context(symbol, n=5)` → str summary of recent outcomes
- `run_feedback_collection()` — poll FT closed trades, save feedback_events, update signal_outcomes

Design:
- Read FREQTRADE_PASS from config.json at runtime (not hard-coded)
- Use db_v2 functions: save_feedback_event, create_execution, update_execution, get_signal_history
- Try/except around all FT API calls with timeout=5.0
- Standalone executable (if __name__ == "__main__": run_feedback_collection())

**Files to create:** `feedback_collector.py` in workspace

---

## Phase 1 — Database Layer (P1-T1, P1-T2)

Steps:
1. Backup existing db.py on server: `cp db.py db.py.v1.bak`
2. Copy db_v2.py to /opt/tracetrader/dashboard/db_v2.py
3. Run migration: `python db_v2.py`
4. Run test gate P1-T1 (9 tables + required columns)
5. Run test gate P1-T2 (all query helpers with mock data)

**Files to copy:** `db_v2.py`

---

## Phase 2 — Backend Modules (P2-T1 through P2-T6)

### P2-T1: market_regime.py
- Copy to server
- pip install ccxt pandas numpy if needed
- Run test gate (mock OHLCV → compute_indicators → classify_regime)

### P2-T2: agent_bias_extractor.py
- Copy to server
- Run test gate (5 extraction cases, 4/5 min pass)

### P2-T3: signal_lifecycle.py
- Copy to server
- Update FREQTRADE_PASS from config.json
- Run test gate (validate_signal + match_signal_to_ft_trade)

### P2-T4: feedback_collector.py
- Copy to server (created in Phase 0)
- Update FREQTRADE_PASS from config.json
- Run test gate (ensure_feedback_schema, get_accuracy_stats, get_past_context)

### P2-T5: api_v2.py
- Copy to server
- Update FREQTRADE_PASS from config.json
- Test import + verify all 10 routes registered

### P2-T6: Update systemd service
- Update ExecStart to use api_v2:app (NOT auto-restart)
- Write PENDING_HUMAN note for systemctl restart

**Files to copy:** market_regime.py, agent_bias_extractor.py, signal_lifecycle.py, feedback_collector.py, api_v2.py, agent_runner_v2.py

---

## Phase 3 — Integration (after human restarts service)

### P3-T1: API smoke tests (10 endpoints)
- Run curl suite against localhost:8888
- Target: 10/10, acceptable 8/10

### P3-T2: Manual agent run
- Run agent_runner_v2.py for BTC/USDT
- Verify messages + signal + regime in DB

### P3-T3: Frontend smoke test
- HTTP 200 for /
- Verify 8-tab HTML structure served

**Prerequisite:** HUMAN must run `systemctl daemon-reload && systemctl restart tracetrader-api`

---

## Phase 4 — Crontab (READ ONLY, then human action)
- Print 4 cron job spec for human to add
- AI does NOT modify crontab

---

## Phase 5 — 30-Day Validation
- Copy monthly_report.py to server
- Verify it runs without error
- Human reviews metrics after 30 days

---

## Key Files

| File | Location | Status | Action |
|------|----------|--------|--------|
| db_v2.py | workspace | ✅ Complete | Copy to server |
| api_v2.py | workspace | ✅ Complete (needs password) | Copy + patch |
| agent_runner_v2.py | workspace | ✅ Complete | Copy to server |
| market_regime.py | workspace | ✅ Complete | Copy to server |
| agent_bias_extractor.py | workspace | ✅ Complete | Copy to server |
| signal_lifecycle.py | workspace | ✅ Complete (needs password) | Copy + patch |
| index_v2.html | workspace | ✅ Complete | Copy as static/index.html |
| feedback_collector.py | MISSING | ❌ Not in workspace | CREATE then copy |

## Decisions
- feedback_collector.py must be created fresh (not in workspace)
- FREQTRADE_PASS read from config.json at runtime, not hard-coded
- Phase 3 requires human restart — AI reports and waits
- Crontab changes always require human action
- Each phase reports PASS/FAIL to user before proceeding
