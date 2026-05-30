# Environment runbook

Mục tiêu: tránh rối loạn `.env` giữa TradingAgents, dashboard và Freqtrade container.

## File đang được dùng thật khi test

Deployment đang chạy ở `/opt/tracetrader`, không phải trực tiếp từ thư mục workspace `repo`.

| File | Ai đọc | Mục đích | Trạng thái sau fix |
|---|---|---|---|
| `/opt/tracetrader/.env` | `agent_runner_v2.py`, cron agent, manual agent | API key AI, mode signal, TTL bridge, runtime guard, interval guard | Đã tạo lại từ `repo/.env` |
| `/opt/tracetrader/freqtrade/.env` | `docker compose` cho container Freqtrade | Secret API server, strategy, DB dry-run, biến strategy trong container | Đang dùng futures |
| `/opt/tracetrader/freqtrade/user_data/config.json` | Freqtrade container | Cấu hình exchange, market mode, pairlist, stake, dry-run | Đang là Binance futures |
| `/opt/tracetrader/tradingagents-src/.env` | TradingAgents package nếu nó tự load | Provider API keys phụ trợ | Không nên dùng làm nguồn chính cho runner |
| `repo/.env` | Workspace/dev | Bản nguồn để sync sang `/opt/tracetrader/.env` | Đã thêm futures guards |
| `repo/freqtrade/.env` | Workspace/dev compose | Bản nguồn tham khảo cho Freqtrade env | Đã thêm `TRACE_MIN_VOLUME_RATIO=0.50` |

## Luồng liên kết

1. Cron/manual gọi:

```bash
cd /opt/tracetrader/dashboard
TRACE_ROOT=/opt/tracetrader TRACE_ENV_FILE=/opt/tracetrader/.env \
  /opt/tracetrader/tradingagents-src/.venv/bin/python agent_runner_v2.py --symbol BTC/USDT
```

2. `agent_runner_v2.py` đọc `/opt/tracetrader/.env`.

3. Agent gọi DeepSeek, tạo quyết định `BUY`, `SELL`, `HOLD`, hoặc `EXIT`.

4. Agent ghi dashboard DB và bridge DB:

```text
/opt/tracetrader/dashboard/tracetrader.db
/opt/tracetrader/signal_bridge/signals.db
```

5. Freqtrade container mount bridge DB vào:

```text
/bridge/signals.db
```

6. Strategy trong Freqtrade đọc signal từ `/bridge/signals.db`, rồi tự quyết định có vào lệnh hay chặn vì guard.

## Biến cần điền ở `/opt/tracetrader/.env`

```bash
DEEPSEEK_API_KEY=...

TRACE_ENABLE_SHORT_SIGNALS=true
TRACE_MIN_CONFIDENCE=0.68
TRACE_MAX_SIGNAL_RUNTIME_MINUTES=45
SIGNAL_TTL_MINUTES=90
TRACE_MIN_RUN_INTERVAL_MINUTES=50
TRACE_MIN_VOLUME_RATIO=0.50
TRACE_EXIT_ON_STALE_SIGNAL=false
TRACE_FUTURES_MIN_NOTIONAL_USDT=80
```

Ý nghĩa:

- `DEEPSEEK_API_KEY`: key gọi AI thật.
- `TRACE_ENABLE_SHORT_SIGNALS=true`: agent giữ `SELL` là short signal cho futures. Nếu `false`, `SELL` bị đổi thành `EXIT` để bảo vệ spot.
- `TRACE_MIN_CONFIDENCE=0.68`: dưới ngưỡng này, agent chuyển signal thành `HOLD`.
- `TRACE_MAX_SIGNAL_RUNTIME_MINUTES=45`: nếu AI/retry chạy quá lâu, `BUY/SELL` bị chặn thành `HOLD`.
- `SIGNAL_TTL_MINUTES=90`: signal trong bridge còn hiệu lực 90 phút.
- `TRACE_MIN_RUN_INTERVAL_MINUTES=50`: chống chạy agent liên tục cho cùng symbol.
- `TRACE_MIN_VOLUME_RATIO=0.50`: ghi cùng giá trị với Freqtrade để dễ audit, nhưng Freqtrade mới là nơi dùng biến này để lọc volume.
- `TRACE_EXIT_ON_STALE_SIGNAL=false`: TTL chỉ kiểm soát entry; lệnh đang mở không bị đóng chỉ vì signal hết hạn.
- `TRACE_FUTURES_MIN_NOTIONAL_USDT=80`: ngưỡng stake tối thiểu thực tế cho futures Binance trong adaptive guard.

## Biến cần điền ở `/opt/tracetrader/freqtrade/.env`

```bash
FREQTRADE_JWT_SECRET=...
FREQTRADE_WS_TOKEN=...
FREQTRADE_API_PASSWORD=...

TRACE_ENABLE_SHORT_SIGNALS=true
TRACE_MAX_SIGNAL_RUNTIME_MINUTES=45
TRACE_MIN_CONFIDENCE=0.68
SIGNAL_TTL_MINUTES=90
TRACE_LEVERAGE=1.0
TRACE_MIN_VOLUME_RATIO=0.50
TRACE_EXIT_ON_STALE_SIGNAL=false
TRACE_FUTURES_MIN_NOTIONAL_USDT=80

FREQTRADE_STRATEGY=AISignalLongShortStrategy
FREQTRADE_DB_URL=sqlite:////freqtrade/user_data/tradesv3.futures.dryrun.sqlite
```

Ý nghĩa:

- `FREQTRADE_*SECRET/TOKEN/PASSWORD`: bảo vệ REST API/UI của Freqtrade.
- `TRACE_MIN_CONFIDENCE`: strategy cũng dùng để bỏ qua signal yếu.
- `TRACE_LEVERAGE=1.0`: futures chỉ chạy 1x trong giai đoạn dry-test.
- `TRACE_MIN_VOLUME_RATIO=0.50`: volume nến cuối phải >= `0.50x` trung bình 20 nến.
- `TRACE_EXIT_ON_STALE_SIGNAL=false`: tránh đóng lệnh vì TTL entry hết hạn; exit dùng TP/SL/trailing hoặc signal đối nghịch/EXIT.
- `TRACE_FUTURES_MIN_NOTIONAL_USDT=80`: adaptive sẽ block nếu tài khoản nhỏ đến mức `80 USDT` vượt trần exposure.
- `FREQTRADE_STRATEGY`: strategy container chạy.
- `FREQTRADE_DB_URL`: tách DB futures khỏi spot.

## Lưu ý quan trọng

- Sửa `/opt/tracetrader/freqtrade/.env` xong phải recreate container, không chỉ restart:

```bash
docker compose -f /opt/tracetrader/freqtrade/docker-compose.yml \
  --env-file /opt/tracetrader/freqtrade/.env up -d --force-recreate freqtrade
```

- Sửa `/opt/tracetrader/.env` thì agent cron/manual sẽ nhận ở lần chạy kế tiếp.
- Nếu `/opt/tracetrader/.env` thiếu, agent có thể vẫn gọi được AI nhờ env khác, nhưng các biến như `TRACE_ENABLE_SHORT_SIGNALS` có thể sai. Đây là nguyên nhân run `21` ghi `EXIT` thay vì `SELL`.

## Chống chạy liên tục

Hiện có 2 lớp:

- Lock file `agent_runner.lock`: chặn 2 process agent chạy cùng lúc.
- Interval guard `TRACE_MIN_RUN_INTERVAL_MINUTES`: chặn chạy nối tiếp quá gần cho cùng symbol.

Trong dry-run và live đều có tác dụng như nhau vì guard nằm ở agent runner, trước khi ghi signal vào bridge.

Với futures 1h:

```bash
TRACE_MIN_RUN_INTERVAL_MINUTES=50
TRACE_MIN_VOLUME_RATIO=0.50
```

Với spot 4h:

```bash
TRACE_MIN_RUN_INTERVAL_MINUTES=180
SIGNAL_TTL_MINUTES=300
TRACE_ENABLE_SHORT_SIGNALS=false
```

## Binance futures minimum stake

Trong test thật, `stake_amount=40` bị Freqtrade bỏ qua vì Binance futures cần notional tối thiểu khoảng `77 USDT` cho `BTC/USDT:USDT`.

Giai đoạn futures dry-run hiện dùng:

```json
"stake_amount": 80
```

Với ví dry-run `1000 USDT`, `max_open_trades=2`, leverage `1x`, exposure tối đa thực tế khoảng 16% nếu mở đủ 2 lệnh.

Khi test thủ công cần bỏ qua điều kiện chặn hiện tại nhưng vẫn ghi timestamp để cron kế tiếp biết vừa có run, dùng:

```bash
agent_runner_v2.py --symbol BTC/USDT --force
```
