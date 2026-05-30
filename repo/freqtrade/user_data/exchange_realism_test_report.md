# Binance realistic gap-time report

Mục tiêu: kiểm tra thêm các case gần môi trường thật cho Binance trước khi chạy 200-300 vòng dry-test.

Giả định test gồm: thời gian AI chạy, retry/rate-limit, độ trễ OHLCV/CCXT/Freqtrade, và việc signal có bị ghi sang candle sau hay không.

## Tổng hợp case

| Case | Sàn | Market | TF | TTL | AI+retry | Guard | Gap tới eval | Candle sau source | Status |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| BN-SPOT-4H-NORMAL | binance | spot | 240m | 300m | 8m | 180m | 232m | 1 | OK |
| BN-SPOT-4H-SLOW-AI | binance | spot | 240m | 300m | 120m | 180m | 120m | 1 | OK |
| BN-SPOT-4H-RATE-LIMIT | binance | spot | 240m | 300m | 265m | 180m | 216m | 2 | BLOCKED_BY_RUNTIME_GUARD |
| BN-FUT-1H-NORMAL | binance | futures | 60m | 90m | 6m | 45m | 54m | 1 | OK |
| BN-FUT-1H-SLOW-AI | binance | futures | 60m | 90m | 55m | 45m | 5m | 1 | BLOCKED_BY_RUNTIME_GUARD |
| BN-FUT-1H-RETRY | binance | futures | 60m | 90m | 75m | 45m | 46m | 2 | BLOCKED_BY_RUNTIME_GUARD |

## Chi tiết

### BN-SPOT-4H-NORMAL

- Binance spot 4h, AI hoàn tất nhanh, Freqtrade/CCXT lag nhẹ.
- Agent start delay: `2m`, AI runtime: `8m`, retry delay: `0m`.
- Runtime guard: `180m`; guarded runtime: `8m`; blocked: `False`.
- Signal ghi sau candle close: `10m`; Freqtrade eval lag: `2m`.
- Gap tới eval kế tiếp: `232m`; TTL `300m`; status `OK`.

### BN-SPOT-4H-SLOW-AI

- Binance spot 4h, AI chạy chậm nhưng vẫn trong cùng candle window.
- Agent start delay: `2m`, AI runtime: `120m`, retry delay: `0m`.
- Runtime guard: `180m`; guarded runtime: `120m`; blocked: `False`.
- Signal ghi sau candle close: `122m`; Freqtrade eval lag: `2m`.
- Gap tới eval kế tiếp: `120m`; TTL `300m`; status `OK`.

### BN-SPOT-4H-RATE-LIMIT

- Binance spot 4h, API retry/rate-limit làm signal ghi sau candle kế tiếp.
- Agent start delay: `2m`, AI runtime: `95m`, retry delay: `170m`.
- Runtime guard: `180m`; guarded runtime: `265m`; blocked: `True`.
- Signal ghi sau candle close: `267m`; Freqtrade eval lag: `3m`.
- Gap tới eval kế tiếp: `216m`; TTL `300m`; status `BLOCKED_BY_RUNTIME_GUARD`.

### BN-FUT-1H-NORMAL

- Binance futures 1h, điều kiện bình thường.
- Agent start delay: `2m`, AI runtime: `6m`, retry delay: `0m`.
- Runtime guard: `45m`; guarded runtime: `6m`; blocked: `False`.
- Signal ghi sau candle close: `8m`; Freqtrade eval lag: `2m`.
- Gap tới eval kế tiếp: `54m`; TTL `90m`; status `OK`.

### BN-FUT-1H-SLOW-AI

- Binance futures 1h, AI gần chạm biên 1 candle.
- Agent start delay: `2m`, AI runtime: `55m`, retry delay: `0m`.
- Runtime guard: `45m`; guarded runtime: `55m`; blocked: `True`.
- Signal ghi sau candle close: `57m`; Freqtrade eval lag: `2m`.
- Gap tới eval kế tiếp: `5m`; TTL `90m`; status `BLOCKED_BY_RUNTIME_GUARD`.

### BN-FUT-1H-RETRY

- Binance futures 1h, retry làm signal qua candle sau.
- Agent start delay: `2m`, AI runtime: `35m`, retry delay: `40m`.
- Runtime guard: `45m`; guarded runtime: `75m`; blocked: `True`.
- Signal ghi sau candle close: `77m`; Freqtrade eval lag: `3m`.
- Gap tới eval kế tiếp: `46m`; TTL `90m`; status `BLOCKED_BY_RUNTIME_GUARD`.

## Những điểm lo ngại

- `BN-SPOT-4H-RATE-LIMIT`: đã được chặn bởi runtime guard. Guard `180m`, runtime thực tế `265m`.
- `BN-FUT-1H-SLOW-AI`: đã được chặn bởi runtime guard. Guard `45m`, runtime thực tế `55m`.
- `BN-FUT-1H-RETRY`: đã được chặn bởi runtime guard. Guard `45m`, runtime thực tế `75m`.

## Khuyến nghị kiểm soát trước dry-test dài

- Ghi thêm metric `agent_runtime_seconds`, `signal_created_at`, `freqtrade_eval_at`, `signal_age_at_entry` vào dashboard để đo thực tế thay vì đoán.
- Guard đã được mô phỏng: futures 1h chặn khi `AI runtime + retry > 45m`; spot 4h chặn khi vượt `180m`.
- Các case bị block nên được ghi nhận là HOLD/BLOCK trong dashboard thay vì tạo lệnh.
- Khi quay lại Bybit, chạy bộ realistic case riêng thay vì trộn vào báo cáo Binance.
- Không dùng kết quả Binance để suy ra latency/fill behavior của Bybit futures.
