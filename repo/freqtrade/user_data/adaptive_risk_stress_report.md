# Adaptive risk stress report

Không gọi AI, không đặt lệnh. Test chỉ kiểm tra guard quản trị vốn/rủi ro.

| Case | Balance | Drawdown % | Status | Stake | Max trades | Median PnL | Expectancy | Loss streak | Flags |
|---|---:|---:|---|---:|---:|---:|---:|---:|---|
| BALANCE_DRAWDOWN_NORMALIZATION | 975.00 | 2.50 | BLOCKED | 32.18 | 0 | -0.1350 | 0.4000 | 3 | SMALL_SAMPLE, LOSS_STREAK_REDUCTION, SMALL_SAMPLE_REQUIRE_MIN_RISK, LOSS_STREAK_PAUSE |
| MIN_NOTIONAL_BLOCK_SMALL_ACCOUNT | 300.00 | 0.00 | BLOCKED | 0.00 | 0 | 0.0750 | 0.0458 | 0 | MIN_NOTIONAL_EXCEEDS_EXPOSURE_CAP |
| SKEWED_PNL_MEDIAN_GUARD | 1000.00 | 0.00 | REDUCED_SAFE | 80.00 | 1 | -0.4000 | 0.6250 | 0 | NEGATIVE_MEDIAN_PNL, RECENT_LOSS_RATE_HIGH, NEGATIVE_MEDIAN_REQUIRE_REDUCTION, MIN_NOTIONAL_RAISED_STAKE |
| LOSS_STREAK_PAUSE | 990.00 | 1.00 | BLOCKED | 21.38 | 0 | 0.0000 | 0.0583 | 3 | NEGATIVE_MEDIAN_PNL, RECENT_LOSS_RATE_HIGH, LOSS_STREAK_REDUCTION, NEGATIVE_MEDIAN_REQUIRE_REDUCTION, LOSS_STREAK_PAUSE |

Kết luận:

- Balance dùng `dry_run_wallet + realized PnL` khi chưa có equity trực tiếp.
- Drawdown ratio từ Freqtrade được đổi sang phần trăm.
- Nếu Binance futures min-notional vượt trần exposure, proposal bị `BLOCKED`.
- Median/expectancy/loss-streak guard chặn trường hợp avg PnL bị một lệnh thắng lớn làm lệch.
