# TraceTrader recommended dry-run plan

Đây là cấu hình thận trọng để nghiên cứu, không phải cam kết lợi nhuận hay lời khuyên tài chính. Chỉ nên chuyển live sau khi dry-run/backtest có tối thiểu 100-200 lệnh, qua nhiều chế độ thị trường, và vẫn còn ổn sau phí/slippage.

## Spot Binance

- File mẫu: `config.spot.example.json`
- Strategy: `AISignalStrategy`
- Cặp ưu tiên: `BTC/USDT`, `ETH/USDT`, sau đó mới thêm `SOL/USDT`, `BNB/USDT`
- Timeframe: `4h`
- Lịch TradingAgents: chạy ngay sau nến 4h đóng, ví dụ `00:02, 04:02, 08:02, 12:02, 16:02, 20:02 UTC`
- Signal TTL: `300` phút để tránh tín hiệu hết hạn trước khi Freqtrade xử lý nến kế tiếp
- Khối lượng: `stake_amount` 50 USDT với ví dry-run 1000 USDT, tối đa 2 lệnh mở
- Rủi ro: SL 2.5%, không DCA, không martingale
- Chốt lời: ROI 4.0% khi vào đúng nhịp, giảm còn 2.5% sau 12h, 1.2% sau 24h, hòa vốn sau 48h
- Điều kiện không vào: confidence thấp hơn `TRACE_MIN_CONFIDENCE`, volume nến mới thấp, hoặc vùng giờ UTC thanh khoản mỏng

## Futures long/short Binance

- File mẫu: `config.futures.example.json`
- Strategy: `AISignalLongShortStrategy`
- Cặp futures dùng hậu tố Freqtrade: `BTC/USDT:USDT`, `ETH/USDT:USDT`, `SOL/USDT:USDT`
- Timeframe: `1h`
- Lịch TradingAgents: chạy mỗi giờ tại phút `:02` nếu chấp nhận chi phí LLM; nếu muốn tiết kiệm, dùng `2h` và TTL 150 phút
- Signal TTL: `90` phút cho 1h, hoặc `150` phút cho 2h
- Leverage: bắt đầu `TRACE_LEVERAGE=1.0`; chỉ cân nhắc 2x sau dry-run ổn định
- Khối lượng: 40 USDT/lệnh với ví dry-run 1000 USDT, tối đa 2 lệnh mở
- Rủi ro: SL 1.8%, trailing chỉ kích hoạt sau khi lợi nhuận đạt 2.6%
- Chốt lời: ROI 3.5% ban đầu, 2.2% sau 3h, 1.2% sau 8h, thoát hòa vốn sau 16h
- Điều kiện long: tín hiệu `BUY`, confidence đủ cao, giá trên EMA50
- Điều kiện short: tín hiệu `SELL`, confidence đủ cao, giá dưới EMA50

## Biến môi trường đề xuất

Spot 4h:

```bash
TRACE_MIN_CONFIDENCE=0.62
TRACE_MAX_SIGNAL_RUNTIME_MINUTES=180
SIGNAL_TTL_MINUTES=300
TRACE_ENABLE_SHORT_SIGNALS=false
TRACE_LEVERAGE=1.0
FREQTRADE_STRATEGY=AISignalStrategy
```

Futures 1h:

```bash
TRACE_MIN_CONFIDENCE=0.68
TRACE_MAX_SIGNAL_RUNTIME_MINUTES=45
SIGNAL_TTL_MINUTES=90
TRACE_ENABLE_SHORT_SIGNALS=true
TRACE_LEVERAGE=1.0
FREQTRADE_STRATEGY=AISignalLongShortStrategy
```

Lưu ý: `TRACE_ENABLE_SHORT_SIGNALS=true` và `TRACE_MAX_SIGNAL_RUNTIME_MINUTES` phải nằm trong `.env` mà `agent_runner_v2.py` đọc được, không chỉ trong container Freqtrade. Nếu không bật short signal, TradingAgents `SELL` sẽ tiếp tục được đổi thành `EXIT` để bảo vệ mode spot.

## Gap time trong dry-run

Gap hay gặp nhất là lệch giữa lịch sinh tín hiệu và lịch nến của Freqtrade:

- Nếu agent chạy lúc `00:02` nhưng Freqtrade đã xử lý nến `00:00` trước đó, strategy có thể không xét lại tín hiệu cho đến nến `04:00`.
- Nếu TTL chỉ 70 phút trong khi timeframe là 4h, tín hiệu sẽ hết hạn trước nến kế tiếp và dashboard sẽ thấy tín hiệu hợp lệ nhưng Freqtrade không vào lệnh.
- Với spot 4h, dùng TTL khoảng 300 phút.
- Với futures 1h, dùng TTL 90 phút và chạy agent sau mỗi nến đóng 1-3 phút.
- Không nên dùng 5m/15m cho TradingAgents LLM nếu chưa có bộ lọc kỹ thuật riêng, vì chi phí cao và tín hiệu ngữ cảnh dễ chậm hơn biến động thực tế.

## Kịch bản test gap-time 4 vòng

Bộ test mô phỏng nằm tại `tools/gap_time_matrix.py`, báo cáo kết quả nằm tại `gap_time_test_report.md`. Test này không gọi API AI, chỉ đo câu hỏi timing: sau khi TradingAgents ghi signal, Freqtrade còn thấy signal tại lần xét candle kế tiếp không.

| Vòng | Mục tiêu | Timeframe | TTL test | Kết quả | Điều chỉnh |
|---|---|---:|---|---|---|
| S1 Spot baseline | AI ghi signal sau nến 4h đóng 2-120 phút | 4h | 70/180/240/300 | TTL 70 miss 100%, TTL 180 chỉ pass 28.6%, TTL 240/300 pass 100% | Giữ spot TTL 300 |
| S2 Spot stress | Thêm Freqtrade lag 3 phút và API AI rất nhanh/chậm | 4h | 240/270/300/360 | Tất cả pass 100%, TTL khuyến nghị theo margin là khoảng 269 phút | Không cần tăng quá 300 |
| F1 Futures baseline | AI chạy mỗi giờ sau nến 1h đóng | 1h | 60/70/90/120 | Tất cả pass 100%, TTL khuyến nghị theo margin là khoảng 74 phút | Giữ futures 1h TTL 90 |
| F2 Futures economy | Chạy futures 2h để giảm chi phí API AI | 2h | 90/120/150/180 | TTL 90 chỉ pass 50%, TTL 120+ pass 100%, margin khuyến nghị khoảng 138 phút | Nếu dùng 2h, đặt TTL 150 hoặc 180 |

Kết luận sau test:

- Spot 4h: `SIGNAL_TTL_MINUTES=300` là hợp lý. `240` vừa đủ trong mô phỏng, nhưng margin thấp nếu server/API/Freqtrade bị trễ.
- Futures 1h: `SIGNAL_TTL_MINUTES=90` là hợp lý. `60-70` có thể đủ về mặt toán học nhưng không đẹp khi có jitter.
- Futures 2h: không dùng TTL 90; dùng `150` nếu muốn cân bằng, hoặc `180` nếu API AI hay chậm.
- Không nên kéo TTL vượt nhiều candle, vì tín hiệu AI có thể cũ và vào lệnh muộn so với bối cảnh thị trường.

## Test thực tế Binance

Báo cáo chi tiết nằm tại `exchange_realism_test_report.md`. Bộ test thêm các biến thực tế hơn: AI chạy chậm, API retry/rate-limit, Freqtrade/CCXT nhận candle trễ, và trường hợp signal được ghi sau khi đã qua candle kế tiếp. Bybit tạm bỏ khỏi phase này để tập trung làm chắc Binance.

Kết quả đáng chú ý:

| Case | Sàn | Market | TF | Status | Ý nghĩa |
|---|---|---|---:|---|---|
| `BN-SPOT-4H-RATE-LIMIT` | Binance | spot | 4h | `BLOCKED_BY_RUNTIME_GUARD` | Runtime 265 phút vượt guard 180 phút |
| `BN-FUT-1H-SLOW-AI` | Binance | futures | 1h | `BLOCKED_BY_RUNTIME_GUARD` | Runtime 55 phút vượt guard 45 phút |
| `BN-FUT-1H-RETRY` | Binance | futures | 1h | `BLOCKED_BY_RUNTIME_GUARD` | Runtime 75 phút vượt guard 45 phút |

Điểm cần lo:

- TTL dài giải quyết lỗi miss signal, nhưng có thể tạo lỗi nguy hiểm hơn: signal cũ vẫn được Freqtrade dùng.
- Futures 1h không chịu được AI runtime + retry quá dài. Guard hiện tại chặn BUY/SELL nếu tổng thời gian này vượt 45 phút.
- Spot 4h dễ chịu hơn. Guard hiện tại chặn BUY/SELL nếu AI runtime + retry vượt 180 phút.
- Binance spot/futures nên dry-test tách database/log trong 200-300 vòng đầu để đo latency, fill, slippage, funding và symbol behavior riêng.
- Cơ chế phòng thủ chi tiết nằm tại `ACCOUNT_DEFENSE_PLAN.md`.

## Tiêu chí mới cân nhắc live

- Tối thiểu 100-200 lệnh dry-run, không chỉ vài lệnh thắng.
- Profit factor sau phí/slippage giả định vẫn trên 1.15.
- Max drawdown nằm trong mức chịu được, khuyến nghị dưới 8-10% ở dry-run.
- Không có một cặp duy nhất tạo toàn bộ lợi nhuận.
- Các lệnh thua không tập trung vào cùng một gap giờ hoặc cùng một trạng thái thị trường như SIDEWAYS/HIGH_VOLATILITY.
