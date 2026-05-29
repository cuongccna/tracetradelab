## PHẦN 1 — QUY TẮC TOÀN CỤC

### 1.1 AI ĐƯỢC PHÉP làm gì

```
✅ Đọc toàn bộ file trong /opt/tracetrader/
✅ Tạo file mới trong /opt/tracetrader/dashboard/
✅ Chỉnh sửa file trong /opt/tracetrader/dashboard/ (trừ các file trong KHÔNG ĐƯỢC)
✅ Chạy pip install trong venv đã có
✅ Chạy python script để test
✅ Chạy curl để test API
✅ Đọc logs trong /opt/tracetrader/logs/
✅ Chạy sqlite3 để inspect DB
✅ Chạy crontab -l để xem (chỉ đọc)
✅ Chạy systemctl status để xem (chỉ đọc)
✅ Tạo file backup trước khi sửa
✅ Ghi vào memory khi hoàn thành một task
```

### 1.2 AI KHÔNG ĐƯỢC PHÉP làm gì

```
🚫 TUYỆT ĐỐI không chỉnh sửa:
   - /opt/tracetrader/tradingagents-src/**  (repo gốc)
   - /opt/tracetrader/freqtrade/user_data/config.json
   - /opt/tracetrader/freqtrade/docker-compose.yml
   - /etc/systemd/system/tracetrader-api.service

🚫 KHÔNG chạy:
   - docker compose down (không được tắt Freqtrade)
   - systemctl stop/restart (không được restart service đang chạy)
   - crontab -e hoặc crontab -r (không được sửa crontab)
   - rm -rf (không được xóa bất kỳ thứ gì)
   - apt install (không được cài package hệ thống)

🚫 KHÔNG làm:
   - Hard-code API key hay password vào file
   - Bỏ qua bước test gate
   - Đến phase tiếp theo khi phase hiện tại chưa pass
   - Gọi external API (OpenAI, DeepSeek) khi testing logic
   - Sửa file đã pass test gate

🚫 KHÔNG tự quyết định khi:
   - Gặp lỗi không rõ nguyên nhân sau 3 lần retry
   - Test gate fail sau 2 lần sửa
   - Cần thay đổi cấu trúc DB đã có data
   → DỪNG và báo cáo human
```

### 1.3 Nguyên tắc an toàn

```
RULE 1 — Backup trước khi sửa:
  cp <file> <file>.bak.$(date +%Y%m%d_%H%M%S)

RULE 2 — Test nhỏ trước khi integrate:
  Mỗi function phải chạy được độc lập trước

RULE 3 — Không break backward compat:
  ADD COLUMN thay vì ALTER TABLE
  Dùng .get() với default thay vì truy cập trực tiếp

RULE 4 — Fail safe:
  Mọi external call phải có try/except
  Khi lỗi → trả về default an toàn (HOLD, None, {})

RULE 5 — Log rõ ràng:
  Mỗi operation quan trọng phải có log.info()
  Lỗi phải có log.error() với traceback
```

---

## PHẦN 2 — MEMORY SYSTEM CHO AI AGENT

### 2.1 Format memory entry

Sau mỗi task hoàn thành, AI ghi vào memory:

```
[TASK_ID] [STATUS] [FILE_PATH] [TIMESTAMP]
Mô tả ngắn điều đã làm và kết quả test.
```

Ví dụ:
```
[P1-T1] DONE /opt/tracetrader/dashboard/db_v2.py 2026-05-28T10:30
Schema init OK. 9 tables created. All indexes created.
Test: python db_v2.py → "[DB v2] Schema initialized"

[P1-T2] FAIL /opt/tracetrader/dashboard/market_regime.py 2026-05-28T11:00
ccxt import error. Missing package.
Action: pip install ccxt pandas numpy → retry
```

### 2.2 Context window management

```
Khi context quá dài:
1. Đọc lại memory entries của phase hiện tại
2. Đọc test gate requirements của task tiếp theo
3. Chỉ load file cần thiết cho task đó
4. KHÔNG load toàn bộ codebase
```

### 2.3 State tracking

AI phải track state sau mỗi bước:

```python
# state.json — AI đọc/ghi sau mỗi task
{
  "current_phase": 1,
  "current_task": "P1-T3",
  "completed_tasks": ["P1-T1", "P1-T2"],
  "failed_tasks": [],
  "pending_human_review": [],
  "db_initialized": true,
  "service_running": true,
  "freqtrade_password": "LOADED_FROM_CONFIG",  # đọc từ config, không hard-code
  "last_updated": "2026-05-28T10:30:00Z"
}
```

---

## PHẦN 3 — SUBAGENT PHÂN CHIA

```
┌─────────────────────────────────────────────────────────┐
│                  ORCHESTRATOR AGENT                      │
│  - Đọc state.json                                        │
│  - Dispatch tasks đến subagents                          │
│  - Verify test gates                                     │
│  - Update memory                                         │
│  - Escalate khi fail                                     │
└──────────┬──────────────────────────────────────────────┘
           │ dispatches to
    ┌──────┴──────────────────────────────────┐
    │                                         │
┌───▼────┐ ┌─────────┐ ┌──────────┐ ┌───────▼─────┐
│ DB     │ │ Backend │ │ Module   │ │ Frontend    │
│ Agent  │ │ Agent   │ │ Agent    │ │ Agent       │
│        │ │         │ │          │ │             │
│Schema  │ │FastAPI  │ │market_   │ │index_v2     │
│Queries │ │endpoints│ │regime    │ │.html        │
│Migrate │ │WebSocket│ │bias_ext  │ │JS logic     │
│        │ │         │ │lifecycle │ │API calls    │
└───┬────┘ └────┬────┘ └────┬─────┘ └──────┬──────┘
    │           │           │              │
    └───────────┴───────────┴──────────────┘
                            │
                   ┌────────▼────────┐
                   │  TEST AGENT     │
                   │                 │
                   │ Verify gates    │
                   │ Run curl tests  │
                   │ Check DB rows   │
                   │ Smoke test UI   │
                   └─────────────────┘
```

### 3.1 DB Agent — Nhiệm vụ

```
Phụ trách: db_v2.py, migration scripts
Input: spec schema từ PHẦN 4
Output: /opt/tracetrader/dashboard/db_v2.py

Quy tắc riêng:
- KHÔNG DROP TABLE
- KHÔNG xóa column
- Dùng _safe_add_column() cho mọi ALTER
- Verify mỗi table bằng SELECT COUNT(*) sau khi tạo
- Test mỗi query helper với mock data trước khi push
```

### 3.2 Backend Agent — Nhiệm vụ

```
Phụ trách: api_v2.py, feedback_collector.py, agent_runner_v2.py
Input: endpoint spec từ PHẦN 5
Output: /opt/tracetrader/dashboard/api_v2.py

Quy tắc riêng:
- Mọi endpoint phải return JSON (không HTML)
- Mọi external call (FT API, CCXT) phải có timeout=5.0
- Mọi exception phải return {"error": str(e)}, không raise 500
- Test mỗi endpoint với curl trước khi mark done
- KHÔNG gọi LLM API trong test (mock data only)
```

### 3.3 Module Agent — Nhiệm vụ

```
Phụ trách: market_regime.py, agent_bias_extractor.py, signal_lifecycle.py
Input: spec từ PHẦN 6
Output: 3 files tương ứng

Quy tắc riêng:
- Mỗi module phải chạy standalone (python module.py)
- Mỗi module phải có graceful import (try/except ở top)
- Test với mock data, KHÔNG cần FT online
- Hàm chính phải return dict, không print trực tiếp
```

### 3.4 Frontend Agent — Nhiệm vụ

```
Phụ trách: index_v2.html
Input: 7-tab spec từ PHẦN 7
Output: /opt/tracetrader/dashboard/static/index.html

Quy tắc riêng:
- Toàn bộ trong 1 file HTML (CSS + JS inline)
- KHÔNG dùng framework nặng (React, Vue)
- KHÔNG dùng external CDN ngoài Google Fonts
- Mọi API call phải có error state
- Test với API trả về {} và [] (empty states)
- KHÔNG hard-code data
```

### 3.5 Test Agent — Nhiệm vụ

```
Phụ trách: verify test gates sau mỗi task
Input: test gate spec từ mỗi task
Output: PASS/FAIL report vào memory

Quy tắc riêng:
- Chạy ĐÚNG lệnh test trong spec, không improvise
- Report đầy đủ output (không truncate)
- Nếu FAIL: ghi rõ expected vs actual
- Không sửa code — chỉ report
```

---

## PHẦN 4 — PHASE 1: DATABASE LAYER

### Task P1-T1 — Deploy và migrate db_v2.py

**Subagent:** DB Agent  
**File:** `/opt/tracetrader/dashboard/db_v2.py`  
**Prerequisite:** db.py đã tồn tại (V1)

**Bước thực hiện:**

```bash
# B1: Backup db cũ
cp /opt/tracetrader/dashboard/db.py \
   /opt/tracetrader/dashboard/db.py.bak.$(date +%Y%m%d)

# B2: Deploy file mới
# AI paste nội dung db_v2.py

# B3: Chạy migration
source /opt/tracetrader/tradingagents-src/venv/bin/activate
cd /opt/tracetrader/dashboard
python db_v2.py
```

**Test gate P1-T1:** ✅ PHẢI PASS trước khi tiếp tục

```bash
python3 - << 'TEST'
import sys
sys.path.insert(0, '/opt/tracetrader/dashboard')
from db_v2 import get_conn, DB_PATH

errors = []
with get_conn() as conn:
    expected_tables = [
        'agent_runs', 'agent_messages', 'signals', 'executions',
        'signal_outcomes', 'market_regimes', 'feedback_events',
        'agent_memory', 'freqtrade_snapshots'
    ]
    for t in expected_tables:
        try:
            conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()
        except Exception as e:
            errors.append(f'MISSING TABLE: {t} — {e}')

    # Check new columns exist
    cols = [r[1] for r in conn.execute('PRAGMA table_info(agent_runs)').fetchall()]
    for c in ['market_regime','final_position_size_pct','past_context_injected']:
        if c not in cols:
            errors.append(f'MISSING COLUMN agent_runs.{c}')

    cols2 = [r[1] for r in conn.execute('PRAGMA table_info(agent_messages)').fetchall()]
    for c in ['agent_bias','agent_confidence','agent_recommendation']:
        if c not in cols2:
            errors.append(f'MISSING COLUMN agent_messages.{c}')

    cols3 = [r[1] for r in conn.execute('PRAGMA table_info(signals)').fetchall()]
    for c in ['signal_status','position_size_pct','entry_price']:
        if c not in cols3:
            errors.append(f'MISSING COLUMN signals.{c}')

if errors:
    print('FAIL')
    for e in errors: print(f'  ✗ {e}')
    sys.exit(1)
else:
    print('PASS — All 9 tables and required columns exist')
TEST
```

**Expected output:** `PASS — All 9 tables and required columns exist`

---

### Task P1-T2 — Verify query helpers

**Subagent:** DB Agent  
**File:** db_v2.py (thêm test data)

**Bước thực hiện:**

```bash
python3 - << 'TEST'
import sys
sys.path.insert(0, '/opt/tracetrader/dashboard')
from db_v2 import (
    create_run, finish_run, add_agent_message,
    write_signal, create_execution, save_regime,
    get_overview_stats, get_run_trace, get_signal_lineage
)

# Insert test data
run_id = create_run('BTC/USDT')
assert isinstance(run_id, int) and run_id > 0, f'create_run failed: {run_id}'

add_agent_message(run_id, 'Technical Analyst', 'Phân tích kỹ thuật',
    'analysts', 'RSI=58, bullish bias, confidence 0.74',
    agent_bias='bullish', agent_confidence=0.74, agent_recommendation='BUY')

sig_id = write_signal('BTC/USDT', 'BUY', 0.72,
    stop_loss=0.015, take_profit=0.03,
    position_size_pct=1.5, run_id=run_id)
assert isinstance(sig_id, int), f'write_signal failed: {sig_id}'

exec_id = create_execution(sig_id, 'ACCEPTED')
assert isinstance(exec_id, int), f'create_execution failed: {exec_id}'

save_regime('BTC/USDT', '2026-05-28T10:00:00+00:00',
    'TRENDING_UP', adx=28.5, atr=950.0, atr_pct=0.98,
    ema_fast=95000.0, ema_slow=92000.0, ema_aligned=True)

finish_run(run_id, 'BUY', 0.72, position_size=1.5, regime='TRENDING_UP',
    past_ctx_injected=False)

# Query overview
ov = get_overview_stats()
assert ov['total_runs'] >= 1, 'overview total_runs wrong'
assert ov['total_signals'] >= 1, 'overview total_signals wrong'

# Query trace
trace = get_run_trace(run_id)
assert trace['run']['id'] == run_id, 'run trace run id wrong'
assert len(trace['messages']) >= 1, 'run trace messages empty'
assert trace['signal'] is not None, 'run trace signal missing'
assert trace['execution'] is not None, 'run trace execution missing'
assert trace['regime'] is not None, 'run trace regime missing'

# Query lineage
lineage = get_signal_lineage(sig_id)
assert lineage['signal']['id'] == sig_id, 'lineage signal id wrong'
assert len(lineage['agent_reasoning']) >= 1, 'lineage reasoning empty'

print(f'PASS — DB queries OK. run_id={run_id}, sig_id={sig_id}')
TEST
```

**Expected output:** `PASS — DB queries OK. run_id=N, sig_id=N`

---

## PHẦN 5 — PHASE 2: BACKEND MODULES

### Task P2-T1 — market_regime.py

**Subagent:** Module Agent  
**File:** `/opt/tracetrader/dashboard/market_regime.py`

**Bước thực hiện:**

```bash
# B1: Cài dependencies
source /opt/tracetrader/tradingagents-src/venv/bin/activate
pip install ccxt pandas numpy --quiet

# B2: Deploy file

# B3: Test standalone
python market_regime.py
```

**Test gate P2-T1:**

```bash
python3 - << 'TEST'
import sys
sys.path.insert(0, '/opt/tracetrader/dashboard')
import pandas as pd
import numpy as np
from market_regime import compute_indicators, classify_regime

# Mock OHLCV data (không cần CCXT online)
np.random.seed(42)
dates = pd.date_range('2026-01-01', periods=100, freq='1h', tz='UTC')
close = 95000 + np.cumsum(np.random.randn(100) * 200)
df = pd.DataFrame({
    'open': close - 100, 'high': close + 300,
    'low': close - 300, 'close': close,
    'volume': np.random.randint(1000, 5000, 100).astype(float)
}, index=dates)

df = compute_indicators(df)

# Check indicators computed
required = ['ema20','ema50','ema200','atr14','atr_pct','adx','vol_zscore']
missing = [c for c in required if c not in df.columns]
assert not missing, f'Missing indicators: {missing}'

# Check classify_regime returns required fields
result = classify_regime(df)
required_keys = ['regime','adx','atr_pct','ema_fast','ema_slow','volume_zscore','close_price']
missing_keys = [k for k in required_keys if k not in result]
assert not missing_keys, f'Missing keys: {missing_keys}'

valid_regimes = ['TRENDING_UP','TRENDING_DOWN','SIDEWAYS','HIGH_VOLATILITY','LOW_VOLATILITY','UNKNOWN']
assert result['regime'] in valid_regimes, f'Invalid regime: {result["regime"]}'

print(f'PASS — Regime: {result["regime"]}, ADX: {result["adx"]}, ATR%: {result["atr_pct"]}')
TEST
```

**Expected output:** `PASS — Regime: <valid_regime>, ADX: <number>, ATR%: <number>`

---

### Task P2-T2 — agent_bias_extractor.py

**Subagent:** Module Agent  
**File:** `/opt/tracetrader/dashboard/agent_bias_extractor.py`

**Test gate P2-T2:**

```bash
python3 - << 'TEST'
import sys
sys.path.insert(0, '/opt/tracetrader/dashboard')
from agent_bias_extractor import extract_all, extract_position_size

cases = [
    # (content, layer, expected_bias, expected_rec)
    ("RSI=58, EMA aligned bullish. Bias: BULLISH. Confidence: 0.74. BUY signal.", "analysts", "bullish", "BUY"),
    ("Bearish breakdown, funding elevated. Confidence 65%. DO NOT ENTER SPOT.", "researchers", "bearish", "HOLD"),
    ("Mixed signals. HOLD position. Confidence 0.55.", "risk_mgmt", "neutral", "HOLD"),
    ("Red flags: funding extreme. Recommend: REDUCE_SIZE.", "risk_mgmt", None, "REDUCE_SIZE"),
    ("Portfolio Manager: APPROVE. Action: BUY. Confidence: 0.71.", "execution", "bullish", "BUY"),
]

failures = []
for content, layer, exp_bias, exp_rec in cases:
    result = extract_all(content, layer)
    if exp_bias and result['agent_bias'] != exp_bias:
        failures.append(f'bias: got {result["agent_bias"]}, expected {exp_bias} | "{content[:50]}"')
    if exp_rec and result['agent_recommendation'] != exp_rec:
        failures.append(f'rec: got {result["agent_recommendation"]}, expected {exp_rec} | "{content[:50]}"')

# Test position size extraction
size = extract_position_size("Position size: 1.5% of portfolio")
assert size is not None and 1.0 <= size <= 2.0, f'Position size failed: {size}'

if failures:
    print('FAIL')
    for f in failures: print(f'  ✗ {f}')
    sys.exit(1)
else:
    print(f'PASS — All {len(cases)} extraction cases correct')
TEST
```

**Expected output:** `PASS — All 5 extraction cases correct`

**Acceptable:** Tối thiểu 4/5 cases pass (extractor là heuristic, không phải 100%)

---

### Task P2-T3 — signal_lifecycle.py

**Subagent:** Module Agent  
**File:** `/opt/tracetrader/dashboard/signal_lifecycle.py`

**Test gate P2-T3:**

```bash
python3 - << 'TEST'
import sys
sys.path.insert(0, '/opt/tracetrader/dashboard')
from signal_lifecycle import validate_signal, match_signal_to_ft_trade

# Test validate_signal
cases = [
    ({"action":"BUY","confidence":0.72,"expires_at":None}, True, ""),
    ({"action":"BUY","confidence":0.55,"expires_at":None}, False, "REJECTED_LOW_CONFIDENCE"),
    ({"action":"HOLD","confidence":0.80,"expires_at":None}, False, "REJECTED_INVALID"),
    ({"action":"BUY","confidence":0.72,"expires_at":"2020-01-01T00:00:00+00:00"}, False, "REJECTED_EXPIRED"),
]
failures = []
for sig, exp_valid, exp_reason in cases:
    is_valid, reason = validate_signal(sig)
    if is_valid != exp_valid:
        failures.append(f'validate: got valid={is_valid}, expected {exp_valid} | {sig}')
    if not exp_valid and reason != exp_reason:
        failures.append(f'reason: got {reason}, expected {exp_reason}')

# Test match_signal_to_ft_trade
from datetime import datetime, timezone, timedelta
sig_time = datetime.now(timezone.utc) - timedelta(minutes=30)
signal = {"symbol":"BTC/USDT","created_at":sig_time.isoformat()}
trades = [
    {"pair":"BTC/USDT","trade_id":1,"open_date":(sig_time+timedelta(minutes=5)).isoformat()},
    {"pair":"ETH/USDT","trade_id":2,"open_date":(sig_time+timedelta(minutes=10)).isoformat()},
    {"pair":"BTC/USDT","trade_id":3,"open_date":(sig_time-timedelta(hours=5)).isoformat()},
]
matched = match_signal_to_ft_trade(signal, trades)
assert matched is not None, 'Should match trade 1'
assert matched['trade_id'] == 1, f'Wrong match: {matched["trade_id"]}'

# No match case
no_match = match_signal_to_ft_trade({"symbol":"SOL/USDT","created_at":sig_time.isoformat()}, trades)
assert no_match is None, 'SOL/USDT should not match'

if failures:
    print('FAIL')
    for f in failures: print(f'  ✗ {f}')
    sys.exit(1)
else:
    print(f'PASS — validate_signal OK, match_signal_to_ft_trade OK')
TEST
```

**Expected output:** `PASS — validate_signal OK, match_signal_to_ft_trade OK`

---

### Task P2-T4 — feedback_collector.py update

**Subagent:** Backend Agent  
**File:** `/opt/tracetrader/dashboard/feedback_collector.py`

**Việc cần làm:** Update `FREQTRADE_PASS` từ config.json (không hard-code)

```bash
# Đọc password từ config
FT_PASS=$(python3 -c "
import json
with open('/opt/tracetrader/freqtrade/user_data/config.json') as f:
    cfg = json.load(f)
print(cfg.get('api_server',{}).get('password',''))
")
echo "FT password loaded: ${#FT_PASS} chars"

# Update trong file (chỉ thay placeholder)
sed -i "s/FREQTRADE_PASS = \"your_password_here\"/FREQTRADE_PASS = \"$FT_PASS\"/" \
    /opt/tracetrader/dashboard/feedback_collector.py
```

**Test gate P2-T4:**

```bash
python3 - << 'TEST'
import sys
sys.path.insert(0, '/opt/tracetrader/dashboard')
from feedback_collector import ensure_feedback_schema, get_accuracy_stats, get_past_context

# Schema
ensure_feedback_schema()

# Stats (sẽ trả về empty nếu chưa có data — OK)
stats = get_accuracy_stats()
assert isinstance(stats, dict), f'stats not dict: {type(stats)}'

# Past context (OK nếu empty)
ctx = get_past_context('BTC/USDT', n=5)
assert isinstance(ctx, str), f'context not str: {type(ctx)}'

print(f'PASS — feedback_collector OK. Stats: {stats}')
TEST
```

---

### Task P2-T5 — api_v2.py deploy và test

**Subagent:** Backend Agent  
**File:** `/opt/tracetrader/dashboard/api_v2.py`

**Bước thực hiện:**

```bash
# B1: Update passwords trong api_v2.py
FT_PASS=$(python3 -c "
import json
with open('/opt/tracetrader/freqtrade/user_data/config.json') as f:
    cfg = json.load(f)
print(cfg.get('api_server',{}).get('password',''))
")
sed -i "s/FREQTRADE_PASS = \"your_password_here\"/FREQTRADE_PASS = \"$FT_PASS\"/" \
    /opt/tracetrader/dashboard/api_v2.py

# B2: Test import (không start server)
source /opt/tracetrader/tradingagents-src/venv/bin/activate
python3 -c "import api_v2; print('Import OK')"
```

**Test gate P2-T5 — Static import test:**

```bash
python3 - << 'TEST'
import sys, importlib
sys.path.insert(0, '/opt/tracetrader/dashboard')

# Test tất cả imports trong api_v2
try:
    import api_v2
    print('PASS — api_v2 imports OK')
except ImportError as e:
    print(f'FAIL — Missing dependency: {e}')
    sys.exit(1)
except Exception as e:
    print(f'FAIL — Import error: {e}')
    sys.exit(1)

# Verify all expected routes exist
from api_v2 import app
routes = [r.path for r in app.routes]
required_routes = [
    '/api/dashboard/overview',
    '/api/dashboard/signals',
    '/api/dashboard/execution',
    '/api/dashboard/outcomes',
    '/api/dashboard/agents',
    '/api/dashboard/regimes',
    '/api/dashboard/feedback',
    '/api/runs/{run_id}/trace',
    '/api/signals/{signal_id}/lineage',
    '/ws',
]
missing = [r for r in required_routes if r not in routes]
if missing:
    print(f'FAIL — Missing routes: {missing}')
    sys.exit(1)
print(f'PASS — All {len(required_routes)} required routes registered')
TEST
```

**Expected output:**
```
PASS — api_v2 imports OK
PASS — All 9 required routes registered
```

---

### Task P2-T6 — Update systemd service

**Subagent:** Backend Agent  
**Action:** Cập nhật ExecStart (KHÔNG restart nếu đang có traffic)

```bash
# Xem service file hiện tại
cat /etc/systemd/system/tracetrader-api.service | grep ExecStart

# Nếu đang dùng api:app → update sang api_v2:app
# KHÔNG restart — chỉ update file và daemon-reload
# Human sẽ restart vào thời điểm phù hợp
```

**AI GHI VÀO MEMORY:**
```
[P2-T6] PENDING_HUMAN /etc/systemd/system/tracetrader-api.service
Service file updated to use api_v2:app.
HUMAN ACTION REQUIRED: systemctl daemon-reload && systemctl restart tracetrader-api
Reason: Cannot restart service automatically (may interrupt active connections)
```

---

## PHẦN 6 — PHASE 3: INTEGRATION TEST

### Task P3-T1 — End-to-end smoke test (API live)

**Prerequisite:** Human đã restart service sau P2-T6

**Subagent:** Test Agent

```bash
BASE="http://localhost:8888"

run_test() {
    local name=$1
    local cmd=$2
    local expected=$3
    local result=$(eval $cmd 2>&1)
    if echo "$result" | grep -q "$expected"; then
        echo "✅ PASS: $name"
    else
        echo "❌ FAIL: $name"
        echo "   Expected: $expected"
        echo "   Got: ${result:0:200}"
        return 1
    fi
}

echo "=== API Smoke Tests ==="
run_test "Status"    "curl -s $BASE/api/status"                   '"status":"ok"'
run_test "Overview"  "curl -s $BASE/api/dashboard/overview"        '"total_runs"'
run_test "Signals"   "curl -s $BASE/api/dashboard/signals"         '"buy_rate"'
run_test "Execution" "curl -s $BASE/api/dashboard/execution"       '"total_signals"'
run_test "Outcomes"  "curl -s $BASE/api/dashboard/outcomes"        '"win_rate"'
run_test "Agents"    "curl -s $BASE/api/dashboard/agents"          '"agent_matrix"'
run_test "Regimes"   "curl -s $BASE/api/dashboard/regimes"         '"regime_performance"'
run_test "Feedback"  "curl -s $BASE/api/dashboard/feedback"        '"total_feedback_events"'
run_test "Runs"      "curl -s $BASE/api/runs?limit=5"              '\[\]'  # empty OK
run_test "Signals2"  "curl -s $BASE/api/signals?limit=5"           '\[\]'  # empty OK

echo "=== Done ==="
```

**Expected:** 10/10 PASS  
**Acceptable:** 8/10 (FT-related endpoints OK to fail jika FT offline)

---

### Task P3-T2 — Manual run test

**Prerequisite:** DeepSeek API key trong .env, FinnHub API key trong .env

**Subagent:** Test Agent (chỉ trigger, không đợi kết quả)

```bash
source /opt/tracetrader/tradingagents-src/venv/bin/activate
cd /opt/tracetrader/dashboard

# Chạy với --skip-feedback để test nhanh hơn
timeout 900 python agent_runner_v2.py --symbol BTC/USDT --skip-feedback
EXIT=$?

if [ $EXIT -eq 0 ]; then
    echo "PASS — agent_runner_v2 completed successfully"
else
    echo "FAIL or TIMEOUT — exit code: $EXIT"
    echo "Check logs: tail -50 /opt/tracetrader/logs/agent_runner.log"
fi
```

**Sau khi chạy xong, verify:**

```bash
python3 - << 'TEST'
import sys
sys.path.insert(0, '/opt/tracetrader/dashboard')
from db_v2 import get_recent_runs, get_run_messages, get_run_trace

runs = get_recent_runs(1)
assert runs, 'No runs found after agent_runner_v2'

run = runs[0]
print(f'Run: #{run["id"]} {run["symbol"]} status={run["status"]}')
assert run['status'] in ('done','error'), f'Unexpected status: {run["status"]}'

msgs = get_run_messages(run['id'])
print(f'Messages: {len(msgs)}')
assert len(msgs) >= 3, f'Too few messages: {len(msgs)}'

# Check at least some agents ran
layers = set(m['layer'] for m in msgs)
print(f'Layers: {layers}')
assert 'analysts' in layers, 'No analyst messages'

# Check regime was saved
if run['market_regime']:
    print(f'Regime: {run["market_regime"]}')
else:
    print('WARNING: No regime saved (market_regime.py may have failed)')

# Check signal was created
trace = get_run_trace(run['id'])
if trace['signal']:
    s = trace['signal']
    print(f'Signal: {s["action"]} conf={s["confidence"]} status={s["signal_status"]}')
else:
    print('WARNING: No signal created')

print('PASS — agent_runner_v2 integration test OK')
TEST
```

---

### Task P3-T3 — Frontend smoke test

**Subagent:** Test Agent

```bash
# Test static file được serve
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8888/)
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ PASS: Dashboard serves HTTP 200"
else
    echo "❌ FAIL: Dashboard returned HTTP $HTTP_CODE"
fi

# Test index.html có đúng tab structure
CONTENT=$(curl -s http://localhost:8888/)
check_content() {
    local name=$1
    local pattern=$2
    if echo "$CONTENT" | grep -q "$pattern"; then
        echo "✅ PASS: $name"
    else
        echo "❌ FAIL: $name — pattern '$pattern' not found"
    fi
}

check_content "Overview tab" "Overview"
check_content "Signal Behavior tab" "Signal Behavior"
check_content "Execution tab" "Execution"
check_content "Outcome tab" "Outcome"
check_content "Agent Attribution tab" "Agent Attribution"
check_content "Market Regime tab" "Market Regime"
check_content "Feedback Learning tab" "Feedback Learning"
check_content "Run Trace tab" "Run Trace"
check_content "WebSocket connect" "connectWS"
check_content "API calls" "/api/dashboard"
```

**Expected:** 10/10 PASS

---

## PHẦN 7 — PHASE 4: CRONTAB SETUP

### Task P4-T1 — Verify crontab (READ ONLY)

**AI CHỈ ĐỌC, KHÔNG SỬA crontab**

```bash
# Đọc crontab hiện tại
crontab -l

# In ra những gì cần thêm để human làm
cat << 'CRON_SPEC'
=== HUMAN ACTION REQUIRED ===
Chạy: crontab -e
Thêm 4 dòng sau:

# Job 1: TradingAgents (mỗi giờ phút :02)
2 * * * * cd /opt/tracetrader/dashboard && /opt/tracetrader/tradingagents-src/venv/bin/python agent_runner_v2.py --symbol BTC/USDT >> /opt/tracetrader/logs/cron.log 2>&1

# Job 2: Market regime update (mỗi giờ phút :05)
5 * * * * cd /opt/tracetrader/dashboard && /opt/tracetrader/tradingagents-src/venv/bin/python market_regime.py >> /opt/tracetrader/logs/regime.log 2>&1

# Job 3: Signal lifecycle sync (mỗi 5 phút)
*/5 * * * * cd /opt/tracetrader/dashboard && /opt/tracetrader/tradingagents-src/venv/bin/python signal_lifecycle.py >> /opt/tracetrader/logs/lifecycle.log 2>&1

# Job 4: Feedback standalone (mỗi 30 phút phút :32)
32 * * * * cd /opt/tracetrader/dashboard && /opt/tracetrader/tradingagents-src/venv/bin/python feedback_collector.py >> /opt/tracetrader/logs/feedback.log 2>&1

CRON_SPEC
```

---

## PHẦN 8 — PHASE 5: 30-DAY VALIDATION

### Metrics cần đo sau 30 ngày

**AI tạo script đo lường, không tự interpret kết quả:**

```bash
# Script này AI tạo và để human chạy
cat > /opt/tracetrader/dashboard/monthly_report.py << 'SCRIPT'
"""
monthly_report.py — Tổng hợp metrics sau 30 ngày dry-run
Chạy: python monthly_report.py
"""
import sys
sys.path.insert(0, '/opt/tracetrader/dashboard')
from db_v2 import (
    get_overview_stats, get_signal_behavior_stats,
    get_outcome_stats, get_regime_stats, get_feedback_learning_stats
)
from datetime import datetime

print(f"TraceTrader Lab — 30-Day Report")
print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 60)

ov = get_overview_stats()
print(f"\n[OVERVIEW]")
print(f"  Total Runs:          {ov['total_runs']}")
print(f"  Total Signals:       {ov['total_signals']}")
print(f"  Execution Rate:      {ov['execution_rate']}%")
print(f"  Win Rate:            {ov['win_rate']}%  (target: >52%)")
print(f"  Avg PnL:             {ov['avg_pnl']}%")
print(f"  Profit Factor:       {ov['profit_factor']}")
print(f"  Traceability Rate:   {ov['traceability_rate']}%  (target: >85%)")
print(f"  Feedback Active:     {ov['feedback_used_runs']} runs")

sb = get_signal_behavior_stats(days=30)
print(f"\n[SIGNAL BEHAVIOR]")
print(f"  BUY rate:            {sb['buy_rate']}%")
print(f"  HOLD rate:           {sb['hold_rate']}%")
print(f"  Avg Confidence:      {sb['avg_confidence']}")
print(f"  Overconfidence Rate: {sb['overconfidence_rate']}%")

out = get_outcome_stats()
print(f"\n[OUTCOMES]")
print(f"  Total Closed:        {out['total']}")
print(f"  Win Rate:            {out['win_rate']}%")
print(f"  SL Triggered:        {out['sl_triggered']}")
print(f"  Avg Duration:        {out['avg_duration']} min")

reg = get_regime_stats()
print(f"\n[REGIME PERFORMANCE]")
for r in reg['regime_performance']:
    print(f"  {r['regime']:20} WR={r['win_rate']}% PnL={r['avg_pnl']}% Runs={r['runs']}")

fb = get_feedback_learning_stats()
print(f"\n[FEEDBACK LEARNING]")
print(f"  Memory Usage Rate:   {fb['memory_usage_rate']}%")
print(f"  Lesson Applied Rate: {fb['lesson_applied_rate']}%")
print(f"  Repeat Mistake Rate: {fb['repeat_mistake_rate']}%")
print(f"  Before WR:           {fb['before_feedback']['win_rate']}%  ({fb['before_feedback']['total']} trades)")
print(f"  After WR:            {fb['after_feedback']['win_rate']}%  ({fb['after_feedback']['total']} trades)")

print("\n[VERDICT]")
verdicts = []
if ov['win_rate'] >= 52:
    verdicts.append("✓ Win rate above 52% target")
else:
    verdicts.append(f"✗ Win rate {ov['win_rate']}% below 52% target")

if ov['traceability_rate'] >= 85:
    verdicts.append("✓ Traceability rate adequate for research")
else:
    verdicts.append(f"⚠ Traceability {ov['traceability_rate']}% — pipeline has gaps")

if ov['profit_factor'] and float(ov['profit_factor']) > 1:
    verdicts.append(f"✓ Profit factor {ov['profit_factor']} > 1")
else:
    verdicts.append(f"✗ Profit factor {ov['profit_factor']} <= 1")

for v in verdicts:
    print(f"  {v}")
SCRIPT

python /opt/tracetrader/dashboard/monthly_report.py
```

---

## PHẦN 9 — ESCALATION PROTOCOL

### Khi nào AI phải dừng và báo human

```
STOP và báo ngay nếu:

1. Test gate fail sau 2 lần retry
   → Ghi rõ: expected, actual, command đã chạy

2. DB có data thực tế nhưng cần schema change
   → Không tự DROP/ALTER, đề xuất migration plan

3. Lỗi không có trong danh sách known errors
   → Paste full traceback

4. Service (Freqtrade/API) bị down
   → Không tự restart, báo ngay

5. File config bị sửa ngoài ý muốn
   → Stop toàn bộ, check git diff hoặc backup

6. LLM API key hết tiền hoặc rate limit
   → Dừng agent_runner, báo để top up
```

### Template báo cáo

```
=== AI AGENT ESCALATION REPORT ===
Task:     P2-T5
Time:     2026-05-28T14:30
Status:   BLOCKED

Problem:
  api_v2.py import fails với ModuleNotFoundError: feedback_collector

Steps tried:
  1. pip install → không giải quyết (feedback_collector là local module)
  2. Kiểm tra path → /opt/tracetrader/dashboard/feedback_collector.py exists
  3. Python path → sys.path check OK

Hypothesis:
  feedback_collector.py dùng db.py thay vì db_v2.py,
  gây circular import khi api_v2.py import cả hai

Required human action:
  Xem xét và quyết định migration order

Files affected:
  /opt/tracetrader/dashboard/api_v2.py
  /opt/tracetrader/dashboard/feedback_collector.py
===================================
```

---

## PHẦN 10 — CHECKLIST TỔNG QUAN

```
PHASE 1 — DATABASE
  [ ] P1-T1: db_v2.py deployed + 9 tables created
  [ ] P1-T2: All query helpers return correct data

PHASE 2 — BACKEND MODULES
  [ ] P2-T1: market_regime.py computes regime from mock data
  [ ] P2-T2: agent_bias_extractor.py 4/5 cases pass
  [ ] P2-T3: signal_lifecycle.py validate + match OK
  [ ] P2-T4: feedback_collector.py password updated, schema OK
  [ ] P2-T5: api_v2.py all 9 routes registered
  [ ] P2-T6: Service file updated (HUMAN restarts)

PHASE 3 — INTEGRATION
  [ ] P3-T1: 8/10 API smoke tests pass
  [ ] P3-T2: agent_runner_v2 completes with messages+signal
  [ ] P3-T3: Dashboard HTML serves with 7 tabs

PHASE 4 — CRONTAB
  [ ] P4-T1: Cron spec printed for human (HUMAN adds)

PHASE 5 — 30-DAY VALIDATION
  [ ] monthly_report.py created and runs without error
  [ ] Human reviews verdict after 30 days

HUMAN ACTIONS REQUIRED (AI không tự làm):
  [ ] systemctl restart tracetrader-api (sau P2-T6)
  [ ] crontab -e để thêm 4 jobs (sau P4-T1)
  [ ] Review monthly_report.py output sau 30 ngày
```

---

*Document version: 1.0 | Tạo cho TraceTrader Lab V2 | 2026-05-29*