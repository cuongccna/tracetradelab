# Real API dry-test report

Ngày test: 2026-05-30  
Sàn ưu tiên: Binance  
Chế độ: dry-run, API AI thật, không force entry

## Mục tiêu kiểm tra

- Spot: TradingAgents gọi AI thật, ghi signal vào bridge, Freqtrade chỉ vào lệnh nếu đủ điều kiện.
- Futures: chạy 4 vòng với luồng long/short mới, kiểm tra SELL có đi tới bridge và Freqtrade futures không.
- Feedback/outcome: chỉ ghi dữ liệu học lại khi Freqtrade có trade đóng thật trong dry-run.

## Môi trường

Spot:

- Exchange: Binance spot
- Strategy: `AISignalStrategy`
- Timeframe: `4h`
- TTL: `300` phút
- Runtime guard: `180` phút
- Short signal: tắt
- DB: `tradesv3.dryrun.sqlite`

Futures:

- Exchange: Binance futures
- Strategy: `AISignalLongShortStrategy`
- Timeframe: `1h`
- TTL: `90` phút
- Runtime guard: `45` phút
- Short signal: bật
- Leverage: `1x`
- DB: `tradesv3.futures.dryrun.sqlite`

## Kết quả spot

| Run | Kết quả AI | Bridge signal | Freqtrade order/trade | Outcome học lại |
|---|---|---|---|---|
| 15 | `HOLD`, confidence `0.50` | Có ghi `HOLD` | Không có lệnh | Không có outcome mới |

Kết luận spot:

- API AI thật hoạt động.
- Bridge ghi signal được.
- Không có lệnh spot vì tín hiệu cuối là `HOLD`, đúng với cơ chế bảo vệ.
- Feedback collector đọc được closed trades cũ, nhưng không ghi outcome mới vì vòng này không có trade đóng mới.

## Kết quả futures 4 vòng

| Vòng | Run | Kết quả AI | Bridge signal | TTL | Freqtrade order/trade | Ghi chú |
|---|---:|---|---|---:|---|---|
| 1 | 16 | `HOLD`, confidence `0.50` | id `26`, `HOLD` | 90m | 0 order, 0 trade | Confidence dưới ngưỡng |
| 2 | 17 | `SELL`, confidence `0.69` | id `27`, `SELL` | 90m | 0 order, 0 trade | Trước khi vá lifecycle, dashboard hiểu sai SELL như spot exit |
| 3 | 19 | `HOLD`, confidence `0.50` | id `28`, `HOLD` | 90m | 0 order, 0 trade | Confidence dưới ngưỡng |
| 4 | 20 | `SELL`, confidence `0.71` | id `29`, `SELL` | 90m | 0 order, 0 trade | Signal còn TTL qua nến kế tiếp nhưng bị strategy chặn bởi volume mỏng |

Run `18` bị hủy chủ động để sửa lỗi auth/lifecycle trước khi tiếp tục test, không tính vào 4 vòng.

## Đối chiếu gap time thực tế

Tín hiệu futures quan trọng:

- Signal id `29`
- Action: `SELL`
- Created: `2026-05-30T03:20:29Z`
- Expires: `2026-05-30T04:50:29Z`
- Nến kế tiếp cần xét: `2026-05-30T04:00:00Z`

Kết quả:

- Signal vẫn còn TTL khi qua nến `04:00Z`.
- Freqtrade vẫn chạy `futures`, `1h`, `AISignalLongShortStrategy`.
- Futures DB sau nến kế tiếp: `0 trades`, `0 orders`.

Nguyên nhân chặn entry:

| Pair | Candle | Điều kiện EMA50 | Volume ratio vs mean20 | Kết luận |
|---|---|---|---:|---|
| `BTC/USDT:USDT` | `03:00Z` | Close dưới EMA50 | `0.37x` | Bị chặn vì volume < `0.65x` |
| `BTC/USDT:USDT` | `04:00Z` | Close dưới EMA50 | `0.09x` | Bị chặn vì volume < `0.65x` |
| `ETH/USDT:USDT` | `04:00Z` | Close dưới EMA50 | `0.11x` | Bị chặn vì volume < `0.65x` |
| `SOL/USDT:USDT` | `04:00Z` | Close trên EMA50 | `0.12x` | Bị chặn bởi EMA và volume |

Kết luận gap-time:

- TTL `90m` đủ cho futures 1h trong test này.
- Vấn đề không phải gap-time, mà là market filter phòng thủ đã chặn entry.
- Đây là hành vi hợp lý cho bảo vệ tài khoản, đặc biệt khi volume nến mới quá mỏng.

## Fix đã thực hiện sau test

- `signal_lifecycle.py`: phân biệt spot/futures.
- Spot: `SELL` vẫn được map thành `EXIT`.
- Futures: `SELL` được chấp nhận như short candidate, không còn bị đánh dấu `REJECTED_NO_OPEN_TRADE` khi chưa có lệnh mở.
- `AISignalLongShortStrategy.py`: ghi log rõ khi signal bị chặn bởi:
  - Volume mỏng.
  - EMA50 không xác nhận hướng.
  - Khung giờ UTC thanh khoản thấp.
- Sửa kiểm tra giờ thanh khoản thấp để dùng cột `date` của Freqtrade thay vì index dataframe.
- Đã sync bản sửa sang `/opt/tracetrader` và restart Freqtrade futures thành công.

## Điểm lo ngại

1. Futures chưa có order/trade sau 4 vòng, nên chưa thể đánh giá thắng/thua hoặc expectancy.
2. Cơ chế feedback chỉ học được khi có trade đóng; hiện chưa có outcome mới cho futures.
3. AI đã tạo SELL hợp lệ 2 lần, nhưng bộ lọc phòng thủ làm hệ thống không vào lệnh. Điều này tốt cho an toàn, nhưng cần thêm nhiều vòng để biết bộ lọc có quá chặt không.
4. Trước khi vá, dashboard lifecycle hiểu sai futures SELL như spot exit. Lỗi này có thể làm báo cáo sai nếu không đồng bộ bản sửa ở deployment.
5. Freqtrade 2026.4 không còn dùng config-level protections theo cách cũ; phòng thủ hiện nằm ở agent runner + strategy + sizing, cần tiếp tục audit.
6. Một số nguồn dữ liệu xã hội có thể lỗi hoặc thiếu dữ liệu, nhưng AI provider chính vẫn gọi được.

## Khuyến nghị vòng tiếp theo

- Tiếp tục futures dry-run 20-30 vòng nữa với logging mới để thống kê lý do không vào lệnh.
- Không nới volume filter ngay. Chỉ cân nhắc giảm từ `0.65x` xuống `0.50x` nếu sau 30+ vòng có nhiều SELL/BUY đúng hướng nhưng không có entry nào.
- Thêm báo cáo `signal_age_at_entry`, `entry_reject_reason`, `volume_ratio`, `ema50_confirmed` vào dashboard.
- Chỉ đánh giá feedback/outback sau khi có ít nhất một trade futures đóng trong dry-run.

## Update sau kiểm tra env và guard

- Đã phát hiện `/opt/tracetrader/.env` bị thiếu trong deployment, trong khi cron agent mặc định đọc file này.
- Đã tạo lại `/opt/tracetrader/.env` từ `repo/.env`.
- Đã thêm `TRACE_MIN_RUN_INTERVAL_MINUTES=50` để chống chạy agent liên tục theo cùng symbol.
- Đã chỉnh `--force` để chỉ bỏ qua check trong test thủ công nhưng vẫn ghi timestamp last-run, tránh cron kế tiếp chạy quá dày.
- Đã hạ futures volume filter từ hard-code `0.65x` xuống biến `TRACE_MIN_VOLUME_RATIO=0.50`.
- Đã thêm biến này vào `/opt/tracetrader/freqtrade/.env` và recreate container để env thật được áp dụng.
- Đã xác nhận container đọc `TRACE_MIN_VOLUME_RATIO=0.50` và strategy đọc `MIN_VOLUME_RATIO=0.5`.

## Re-test sau khi hạ volume filter xuống 0.50x

| Run | Kết quả AI | Bridge signal | Lifecycle | Freqtrade tức thời | Ghi chú |
|---:|---|---|---|---|---|
| 22 | `SELL`, confidence `0.75` | id `31`, `SELL` | `ACCEPTED` | `0 orders`, `0 trades` | Signal tạo lúc `2026-05-30T04:21:39Z`, expires `05:51:39Z` |

Tại thời điểm kiểm tra ngay sau run:

- Freqtrade đang chạy Binance futures `1h`, strategy `AISignalLongShortStrategy`.
- Latest BTC futures candle `04:00Z` có close dưới EMA50, phù hợp short.
- Volume ratio tại thời điểm `04:22Z` mới khoảng `0.25x`, vẫn dưới ngưỡng mới `0.50x`.
- Vì `process_only_new_candles=True`, kết quả order/trade cần theo dõi qua mốc nến kế tiếp `05:00Z`.

Sau mốc `05:00Z`:

- Freqtrade đã nhận signal và bắt đầu kiểm tra depth of market cho `BTC/USDT:USDT`.
- Nhiều lần order book thỏa điều kiện short.
- Trade vẫn bị bỏ qua vì `stake_amount=40` quá nhỏ; Freqtrade tính stake tối thiểu khoảng `77 USDT` và bỏ qua vì mức điều chỉnh vượt quá 30% so với stake mong muốn.
- Điều chỉnh cần thiết: tăng futures `stake_amount` lên `80 USDT` cho Binance futures dry-run.

Sau khi tăng `stake_amount` lên `80 USDT`:

- Freqtrade restart với `stake_amount=80`.
- Signal `SELL 0.75` vẫn còn TTL.
- Freqtrade mở short dry-run thành công lúc `2026-05-30T05:05:24Z`.
- Order fill lúc `2026-05-30T05:05:29Z`.
- Trade id: `1`.
- Pair: `BTC/USDT:USDT`.
- Direction: short.
- Entry tag: `AI_SHORT_0.75`.
- Open rate: `73571.4`.
- Amount: `0.001 BTC`.
- Actual stake after precision: `73.5714 USDT`.
- Leverage: `1x`.
- Dashboard lifecycle đã được sửa để match open trades từ `/status`; signal dashboard id `21` đã chuyển thành `EXECUTED`, execution status `TRADE_OPENED`, `ft_trade_id=1`.
- Cron kế tiếp lúc `12:02` giờ Việt Nam bị interval guard chặn đúng: đã chạy cách manual test `38.9m`, cần thêm `11.1m` để đủ `TRACE_MIN_RUN_INTERVAL_MINUTES=50`.

Kết luận re-test:

- Full flow futures đã chạy qua: AI thật -> bridge SELL -> Freqtrade nhận -> guard kiểm tra -> mở short dry-run -> dashboard lifecycle match trade.
- Chưa có outback thắng/thua vì trade đang mở. Feedback collector chỉ học lại sau khi trade đóng.
