# Account defense plan

Mục tiêu ưu tiên là bảo vệ tài khoản. Lợi nhuận chỉ được xét sau khi hệ thống chứng minh được rằng nó biết dừng, biết bỏ lệnh, và không dùng tín hiệu cũ.

## Lớp phòng thủ chung

- Dry-run tối thiểu 200-300 vòng trước khi live.
- Không DCA, không martingale, không force entry.
- Mỗi market dùng config, database, log và báo cáo riêng để không trộn kết quả.
- Signal trễ bị chặn tại agent runner bằng `TRACE_MAX_SIGNAL_RUNTIME_MINUTES`.
- Signal confidence thấp bị chuyển thành `HOLD`.
- Freqtrade luôn bật `dry_run=true` trong dashboard restart flow.
- Có cooldown sau lệnh thua và StoplossGuard.
- Mọi lệnh phải có stoploss; nếu AI đưa SL quá rộng, strategy tự cap về ngưỡng tối đa.
- Khi dashboard ghi nhận lỗi API, retry dài, thiếu outcome hoặc traceability thấp, không tăng vốn.

## Spot Binance

Mục tiêu: sống sót qua thị trường xấu, ưu tiên BTC/ETH trước.

Thông số đề xuất:

```bash
TRACE_ENABLE_SHORT_SIGNALS=false
TRACE_MIN_CONFIDENCE=0.62
TRACE_MAX_SIGNAL_RUNTIME_MINUTES=180
SIGNAL_TTL_MINUTES=300
FREQTRADE_STRATEGY=AISignalStrategy
```

Quy tắc vốn:

- `dry_run_wallet`: 1000 USDT.
- `stake_amount`: 50 USDT/lệnh trong giai đoạn đầu.
- `max_open_trades`: 2.
- Tổng exposure khuyến nghị: tối đa 10% ví dry-run ở vòng đầu; chỉ tăng khi đủ thống kê.
- Chỉ trade `BTC/USDT`, `ETH/USDT` trong 100 vòng đầu; thêm `SOL/USDT`, `BNB/USDT` sau khi traceability ổn.

Quy tắc vào/ra:

- Chỉ BUY spot; `SELL` từ AI được hiểu là thoát/không vào thêm.
- Không vào lệnh nếu AI runtime + retry vượt 180 phút.
- Nếu runtime vượt 240 phút, bắt buộc chuyển signal thành `HOLD`.
- Không vào vùng volume thấp hoặc giờ UTC 22:00-01:00.
- SL mặc định 2.5%, AI SL bị cap tối đa 3%.
- TP theo ROI: 4.0% -> 2.5% -> 1.2% -> hòa vốn.

Điều kiện dừng spot dry-run:

- 2 stoploss liên tiếp trong 12 candle.
- Max drawdown dry-run vượt 8%.
- Profit factor dưới 1.0 sau 50+ lệnh.
- Nhiều hơn 10% lệnh vào bằng signal có cảnh báo stale/runtime.

## Futures Binance

Mục tiêu: học long/short với rủi ro nhỏ. Futures chỉ được live sau spot ổn định hơn và dry-run futures đủ mẫu.

Thông số đề xuất:

```bash
TRACE_ENABLE_SHORT_SIGNALS=true
TRACE_MIN_CONFIDENCE=0.68
TRACE_MAX_SIGNAL_RUNTIME_MINUTES=45
SIGNAL_TTL_MINUTES=90
TRACE_LEVERAGE=1.0
TRACE_MIN_RUN_INTERVAL_MINUTES=50
TRACE_MIN_VOLUME_RATIO=0.50
FREQTRADE_STRATEGY=AISignalLongShortStrategy
```

Quy tắc vốn:

- `dry_run_wallet`: 1000 USDT.
- `stake_amount`: 80 USDT/lệnh. Lý do: Binance futures/Freqtrade đang yêu cầu notional tối thiểu khoảng 77 USDT cho BTC perpetual; 40 USDT bị bỏ qua dù signal hợp lệ.
- `max_open_trades`: 2.
- `margin_mode`: isolated.
- Leverage giai đoạn đầu: 1x. Không tăng lên 2x trước khi có 200-300 vòng dry-test tốt.
- Tổng exposure futures khuyến nghị: khoảng 8-16% ví dry-run ở vòng đầu nếu `max_open_trades=1-2`.

Quy tắc vào/ra:

- BUY mới được long nếu giá trên EMA50.
- SELL mới được short nếu giá dưới EMA50.
- Không vào lệnh nếu AI runtime + retry vượt 45 phút.
- Nếu runtime vượt 60 phút, bắt buộc chuyển signal thành `HOLD`.
- Không vào lệnh nếu volume nến cuối thấp hơn `0.50x` trung bình 20 nến.
- SL mặc định 1.8%, AI SL bị cap tối đa 2.5%.
- Trailing chỉ bật sau khi lợi nhuận đạt 2.6%.
- TP theo ROI: 3.5% -> 2.2% -> 1.2% -> hòa vốn.

Điều kiện dừng futures dry-run:

- 2 stoploss liên tiếp trong 24 candle.
- Drawdown vượt 6-8%.
- Bất kỳ lỗi stale-but-valid nào tạo lệnh thật trong dry-run.
- Funding/slippage làm lệnh thắng nhỏ biến thành âm.
- Tỷ lệ thắng dương nhưng expectancy âm sau phí.

## Điểm cần đo trong 200-300 vòng dry-test

- `agent_runtime_seconds`: AI chạy mất bao lâu.
- `signal_created_at`: signal được ghi lúc nào.
- `signal_age_at_entry`: signal bao nhiêu phút tuổi khi Freqtrade vào lệnh.
- `entry_candle_delay`: vào cùng candle kế tiếp hay đã trễ hơn.
- `close_reason`: ROI, SL, trailing, stale signal hay AI exit.
- `slippage_estimate`: dry-run không phản ánh đủ fill thực, cần giả định phí/slippage khi đánh giá.
- `exchange`: hiện chỉ tập trung Binance; Bybit để phase sau.

## Quyết định hiện tại

- Binance spot 4h: có thể tiếp tục dry-run với guard runtime 180 phút.
- Binance futures 1h: chỉ dry-run, guard runtime 45 phút là bắt buộc.
- Bybit: tạm bỏ khỏi phase này.
