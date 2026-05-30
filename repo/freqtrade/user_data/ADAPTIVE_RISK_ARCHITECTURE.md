# Adaptive risk architecture

Mục tiêu: chuyển cấu hình tĩnh sang adaptive nhưng vẫn đặt **bảo vệ tài khoản** lên trước lợi nhuận.

## Sơ đồ thư mục mới

```text
repo/
  dashboard/
    adaptive_risk.py                  # outback ingest, sliding window, debate, apply config
    api_v2.py                         # API endpoints adaptive/outback
    db_v2.py                          # schema outback + adaptive proposals
    static/index.html                 # tab Adaptive Risk
  tradingagents-src/tradingagents/agents/
    RiskAndMoneyManagementAgents/
      __init__.py
      prompts.py                      # system prompts Agent A, Agent B, Manager
  freqtrade/user_data/
    ADAPTIVE_RISK_ARCHITECTURE.md
```

## Schema Database

### `freqtrade_outback_events`

Lưu telemetry từ Freqtrade theo thời gian.

| Cột | Ý nghĩa |
|---|---|
| `source`, `payload_id` | Nguồn và id payload |
| `symbol`, `market`, `timeframe` | Ngữ cảnh giao dịch |
| `current_balance`, `available_balance`, `equity_peak` | Equity/balance |
| `drawdown_pct`, `max_drawdown_pct` | Drawdown hiện tại và lớn nhất |
| `volatility_atr_pct` | Volatility/ATR % |
| `total_profit_pct`, `total_profit_abs` | Tổng PnL |
| `open_trade_count`, `closed_trade_count` | Số lệnh mở/đóng |
| `trade_history`, `raw_payload` | JSON gốc để audit |
| `created_at` | Thời điểm ghi |

### `adaptive_risk_proposals`

Lưu đề xuất và phán quyết adaptive.

| Cột | Ý nghĩa |
|---|---|
| `old_config` | Config Freqtrade trước khi tối ưu |
| `quant_proposal` | Đề xuất của Agent A |
| `risk_critique` | Phản biện của Agent B |
| `final_config` | Phán quyết Manager sau clamp |
| `safety_status` | `APPROVED_SAFE`, `REDUCED_SAFE`, `BLOCKED` |
| `applied`, `applied_at`, `config_backup_path` | Audit khi áp dụng |

## API Endpoints

```http
POST /api/outback/freqtrade
```

Nhận payload outback từ Freqtrade hoặc automation.

```json
{
  "source": "freqtrade-webhook",
  "symbol": "BTC/USDT",
  "market": "futures",
  "timeframe": "1h",
  "current_balance": 1000,
  "drawdown_pct": 2.4,
  "volatility_atr_pct": 0.9,
  "trade_history": []
}
```

```http
POST /api/outback/freqtrade/collect
```

Dashboard tự kéo `/status`, `/profit`, `/trades` từ Freqtrade rồi ghi outback snapshot.

```http
POST /api/adaptive/risk/run
```

Chạy Agent A -> Agent B -> Manager.

```json
{
  "symbol": "BTC/USDT",
  "market": "futures",
  "apply_config": false,
  "restart": false
}
```

```http
POST /api/adaptive/risk/apply/{proposal_id}
```

Áp dụng proposal đã được Manager cho phép. Luôn ép `dry_run=true`.

## Sliding Window

Code chính: `dashboard/adaptive_risk.py`.

Thuật toán:

1. Lấy outback mới nhất theo `symbol + market`.
2. Lấy market regime mới nhất.
3. Lấy tối đa 30 outcomes gần nhất.
4. Nếu có đủ tối thiểu 5 outcomes cùng regime, chỉ dùng nhóm cùng regime.
5. Nếu chưa đủ mẫu, fallback về outcomes gần nhất nhưng Agent B sẽ giảm rủi ro.
6. Tính:
   - `win_rate`
   - `avg_pnl`
   - `profit_factor`
   - `loss_streak`
   - `max_loss`
   - `drawdown_pct`
   - `atr_pct`
7. Đề xuất:
   - `dynamic_stake_pct`
   - `stake_amount`
   - `stoploss_pct`
   - `take_profit_pct`
   - `max_open_trades`
   - `min_volume_ratio`

## Debate Loop

### Agent A: Quantitative Optimizer

Đề xuất thông số dựa trên sliding window. Nếu sample nhỏ, drawdown tăng hoặc loss streak, tự giảm stake.

### Agent B: Risk Critic

Phản biện đề xuất. Có quyền yêu cầu giảm stake, giảm max trades, siết SL hoặc block.

### Agent Manager: Safety Arbitrator

Chọn phương án an toàn hơn và hard clamp:

- Futures stake tối đa 10% equity.
- Spot stake tối đa 8% equity.
- Futures stoploss tối đa 2.5%.
- Spot stoploss tối đa 3.0%.
- `max_open_trades` tối đa 2.
- Nếu futures cần min notional, stake tối thiểu được nâng tới khoảng 80 USDT nhưng vẫn trong dry-run.
- Nếu `safety_status=BLOCKED`, không cho apply.

## Dashboard

Tab mới: `Adaptive Risk`.

Hiển thị:

- Current Balance.
- Drawdown.
- Stake đề xuất.
- Safety Status.
- So sánh config cũ/mới.
- Log proposal gần đây.
- Reasoning của Agent A, Agent B, Manager.
- Outback mới nhất.

## Cách chạy nhanh

```bash
curl -X POST http://127.0.0.1:8888/api/outback/freqtrade/collect
curl -X POST http://127.0.0.1:8888/api/adaptive/risk/run \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTC/USDT","market":"futures","apply_config":false}'
```

Chỉ áp dụng config sau khi review:

```bash
curl -X POST http://127.0.0.1:8888/api/adaptive/risk/apply/PROPOSAL_ID \
  -H 'Content-Type: application/json' \
  -d '{"restart":false}'
```

## Kết quả kiểm thử thực tế - 2026-05-30

Nguồn chạy:

- Dashboard API: `http://127.0.0.1:8888`
- Freqtrade API: `http://127.0.0.1:8080/api/v1`
- DB: `/opt/tracetrader/dashboard/tracetrader.db`
- Freqtrade config: `/opt/tracetrader/freqtrade/user_data/config.json`

Kết quả outback mới nhất:

- Outback snapshot id `3`.
- Market `futures`, timeframe `1h`, symbol `BTC/USDT`.
- Current balance đang đọc là `1000.0` từ `dry_run_wallet`.
- Open trades: `0`.
- Closed trades: `1`.
- Lệnh được thu về: `BTC/USDT:USDT`, entry tag `AI_SHORT_0.75`, short, stake `73.5714 USDT`.
- Lệnh đóng do `STALE_SIGNAL`, thời lượng `46m`, PnL `-0.05%` tương đương `-0.03793026 USDT`.

Kết quả feedback learning:

- Collector đã ghi outcome: signal `#21` ↔ Freqtrade trade `#1`.
- Action: `SELL`.
- Outcome: thua nhỏ `-0.05%`.
- Thống kê hiện tại: `4` outcomes, `1` win, `3` loss, win-rate `25.0%`.
- Avg PnL hiện là `+0.40%`, nhưng bị lệch bởi một lệnh thắng `+2.14%`; chưa đủ tin cậy.

Kết quả adaptive proposal:

- Proposal id `3`.
- Sample size: `4`, nên Agent B kích hoạt `SMALL_SAMPLE_REQUIRE_MIN_RISK`.
- Safety status: `REDUCED_SAFE`.
- Apply config: chưa áp dụng tự động.
- Final config đề xuất:
  - `dynamic_stake_pct`: `0.06`
  - `stake_amount`: `80.0 USDT`
  - `stoploss_pct`: `1.2`
  - `take_profit_pct`: `1.92`
  - `max_open_trades`: `1`
  - `min_volume_ratio`: `0.50`

Điểm lo ngại sau kiểm thử:

- Dữ liệu còn quá ít. `4` outcomes chỉ đủ test luồng, chưa đủ tối ưu thật.
- `current_balance` hiện vẫn ưu tiên `dry_run_wallet`; cần bổ sung nguồn equity/wallet tốt hơn trước khi live.
- Futures min-notional Binance khiến stake bị nâng từ `60` lên `80 USDT`; với tài khoản nhỏ, giới hạn này có thể làm exposure cao hơn mong muốn.
- Lệnh futures đóng vì `STALE_SIGNAL`, nghĩa là gap time/TTL vẫn là rủi ro vận hành cần theo dõi riêng.
- Avg PnL đang bị một lệnh thắng lớn che đi tỷ lệ thua; các vòng tiếp theo nên thêm median PnL, expectancy và loss-streak guard.
- Proposal chưa được apply vào config thật trong test này; cần review thủ công trước mỗi lần apply.

## Cập nhật vòng phòng thủ - 2026-05-30

Các lo ngại đã được xử lý thêm:

| Lo ngại | Giải pháp đã thêm | Kết quả test |
|---|---|---|
| Dữ liệu còn ít | Thêm stress test không gọi AI để kiểm tra guard trước khi có 200-300 dry-runs | `adaptive_risk_stress_report.md` tạo 4 case phòng thủ |
| Balance đang dùng `dry_run_wallet` | Nếu chưa có equity trực tiếp, balance = `dry_run_wallet + realized PnL` | Outback id `6`: balance `999.96206974`, profit_abs `-0.03793026` |
| Drawdown từ Freqtrade là ratio | Tự đổi ratio sang phần trăm | Outback id `6`: drawdown `0.010837%` |
| Binance futures min-notional | Nếu min-notional vượt exposure cap thì `BLOCKED`, không ép stake quá sức tài khoản | Case `MIN_NOTIONAL_BLOCK_SMALL_ACCOUNT` bị `BLOCKED` |
| `STALE_SIGNAL` đóng lệnh vì TTL | Futures strategy đổi policy: TTL chỉ kiểm soát entry; không đóng lệnh đang mở nếu signal hết hạn | `TRACE_EXIT_ON_STALE_SIGNAL=false` |
| Avg PnL bị lệch bởi lệnh thắng lớn | Thêm `median_pnl`, `expectancy_pct`, `recent_loss_rate`, `loss_streak` guard | Case `SKEWED_PNL_MEDIAN_GUARD` bị `REDUCED_SAFE`; case `LOSS_STREAK_PAUSE` bị `BLOCKED` |

Kết quả adaptive live sau cập nhật:

- Outback id `6`.
- Proposal id `6`.
- Safety status: `REDUCED_SAFE`.
- Sample size: `4`.
- Win-rate: `25.0%`.
- Median PnL: `-0.134%`.
- Recent loss-rate: `75.0%`.
- Final config đề xuất:
  - `stake_amount`: `80.0 USDT`
  - `dynamic_stake_pct`: `0.0800` sau min-notional
  - `stoploss_pct`: `1.2`
  - `take_profit_pct`: `1.92`
  - `max_open_trades`: `1`
  - Flags: `SMALL_SAMPLE`, `SMALL_SAMPLE_REQUIRE_MIN_RISK`, `MIN_NOTIONAL_RAISED_STAKE`

Chưa apply config tự động vì sample size vẫn quá nhỏ.
