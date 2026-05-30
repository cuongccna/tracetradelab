# Gap-time dry-run test report

Mục tiêu: tối ưu TTL và lịch chạy TradingAgents để Freqtrade không bỏ lỡ signal do lệch thời điểm ghi signal với candle evaluation.

Quy ước: `write_offset` là số phút sau candle close khi AI ghi signal vào bridge DB. `gap_to_eval` là số phút signal phải sống đến lần Freqtrade có thể xét nó.

## Kết quả tổng hợp

| Vòng | Market | TF | Max gap cần sống | TTL tối thiểu | TTL khuyến nghị | Kết luận |
|---|---|---:|---:|---:|---:|---|
| S1 Spot 4h baseline | spot | 240m | 238m | 239m | 269m | OK |
| S2 Spot 4h stress | spot | 240m | 238m | 239m | 269m | OK |
| F1 Futures 1h baseline | futures | 60m | 58m | 59m | 74m | OK |
| F2 Futures 2h economy | futures | 120m | 117m | 118m | 138m | OK |

## S1 Spot 4h baseline

Kiểm tra config spot 4h hiện tại với AI chạy sau candle close.

| write_offset | gap_to_eval |
|---:|---:|
| 2m | 238m |
| 5m | 235m |
| 10m | 230m |
| 20m | 220m |
| 45m | 195m |
| 70m | 170m |
| 120m | 120m |

| TTL | Pass rate | Missed write_offset |
|---:|---:|---|
| 70m | 0.0% | 2m, 5m, 10m, 20m, 45m, 70m, 120m |
| 180m | 28.6% | 2m, 5m, 10m, 20m, 45m |
| 240m | 100.0% | - |
| 300m | 100.0% | - |

Điều chỉnh vòng tiếp theo: dùng TTL khoảng `269` phút nếu muốn có margin thực tế.

## S2 Spot 4h stress

Thêm độ trễ Freqtrade 3 phút và API AI hoàn tất rất sớm hoặc rất muộn.

| write_offset | gap_to_eval |
|---:|---:|
| 1m | 2m |
| 2m | 1m |
| 5m | 238m |
| 15m | 228m |
| 30m | 213m |
| 60m | 183m |
| 120m | 123m |
| 180m | 63m |

| TTL | Pass rate | Missed write_offset |
|---:|---:|---|
| 240m | 100.0% | - |
| 270m | 100.0% | - |
| 300m | 100.0% | - |
| 360m | 100.0% | - |

Điều chỉnh vòng tiếp theo: dùng TTL khoảng `269` phút nếu muốn có margin thực tế.

## F1 Futures 1h baseline

Kiểm tra futures 1h khi agent chạy mỗi giờ.

| write_offset | gap_to_eval |
|---:|---:|
| 2m | 58m |
| 5m | 55m |
| 10m | 50m |
| 20m | 40m |
| 35m | 25m |
| 45m | 15m |

| TTL | Pass rate | Missed write_offset |
|---:|---:|---|
| 60m | 100.0% | - |
| 70m | 100.0% | - |
| 90m | 100.0% | - |
| 120m | 100.0% | - |

Điều chỉnh vòng tiếp theo: dùng TTL khoảng `74` phút nếu muốn có margin thực tế.

## F2 Futures 2h economy

Kiểm tra futures 2h nếu muốn giảm chi phí API AI.

| write_offset | gap_to_eval |
|---:|---:|
| 2m | 0m |
| 5m | 117m |
| 15m | 107m |
| 30m | 92m |
| 60m | 62m |
| 90m | 32m |

| TTL | Pass rate | Missed write_offset |
|---:|---:|---|
| 90m | 50.0% | 5m, 15m, 30m |
| 120m | 100.0% | - |
| 150m | 100.0% | - |
| 180m | 100.0% | - |

Điều chỉnh vòng tiếp theo: dùng TTL khoảng `138` phút nếu muốn có margin thực tế.

## Khuyến nghị sau 4 vòng

- Spot 4h: giữ `SIGNAL_TTL_MINUTES=300`; nếu AI API thường hoàn tất trong 1-5 phút và Freqtrade có delay vài phút, `270` là ngưỡng tối thiểu hơn, nhưng `300` an toàn hơn.
- Futures 1h: giữ `SIGNAL_TTL_MINUTES=90`; `70` có thể đủ khi Freqtrade không trễ, nhưng thiếu margin khi API/Freqtrade jitter.
- Futures 2h tiết kiệm chi phí: dùng `SIGNAL_TTL_MINUTES=150` hoặc `180`; `90` không phù hợp cho 2h.
- Không dùng TTL quá dài vượt nhiều candle nếu signal là market-sensitive. TTL nên đủ qua đúng candle kế tiếp, không biến tín hiệu cũ thành lệnh muộn.

## Cập nhật sau dry-run futures thực tế

Lệnh futures short `AI_SHORT_0.75` đã đóng do `STALE_SIGNAL` sau khoảng `46m`, PnL `-0.05%`.
Nguyên nhân vận hành: strategy cũ dùng TTL của signal không chỉ để kiểm soát entry mà còn để đóng lệnh đang mở khi signal hết hạn.

Điều chỉnh đã áp dụng:

- TTL hiện được xem là guard cho entry.
- Lệnh đang mở không còn bị đóng chỉ vì signal hết hạn.
- Exit sẽ đến từ TP/SL/trailing hoặc signal đối nghịch/`EXIT`.
- Biến kiểm soát: `TRACE_EXIT_ON_STALE_SIGNAL=false`.

Nếu muốn quay lại hành vi cực kỳ bảo thủ, bật:

```bash
TRACE_EXIT_ON_STALE_SIGNAL=true
```

Khuyến nghị hiện tại:

- Futures 1h vẫn giữ `SIGNAL_TTL_MINUTES=90`.
- Không tăng TTL chỉ để giữ lệnh lâu hơn; việc giữ/đóng lệnh phải do strategy exit logic quyết định.
- Theo dõi riêng các exit reason `STALE_SIGNAL`, `AI_BUY_COVER`, `AI_SELL_FLAT`, `roi`, `stop_loss` trong 200-300 vòng dry-test.
