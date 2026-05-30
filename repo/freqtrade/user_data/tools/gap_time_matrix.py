"""Dry-run gap-time matrix for TraceTrader.

This script does not call the AI API. It isolates the timing question:
after TradingAgents writes a signal, will Freqtrade still see it at the next
eligible candle evaluation?
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RoundConfig:
    name: str
    market: str
    timeframe_minutes: int
    write_offsets_minutes: tuple[int, ...]
    ttl_candidates_minutes: tuple[int, ...]
    eval_lag_minutes: int
    safety_margin_minutes: int
    target_note: str


@dataclass(frozen=True)
class RealWorldCase:
    case_id: str
    exchange: str
    market: str
    timeframe_minutes: int
    ttl_minutes: int
    agent_start_delay_minutes: int
    ai_runtime_minutes: int
    retry_delay_minutes: int
    eval_lag_minutes: int
    note: str


ROUNDS = (
    RoundConfig(
        name="S1 Spot 4h baseline",
        market="spot",
        timeframe_minutes=240,
        write_offsets_minutes=(2, 5, 10, 20, 45, 70, 120),
        ttl_candidates_minutes=(70, 180, 240, 300),
        eval_lag_minutes=0,
        safety_margin_minutes=30,
        target_note="Kiểm tra config spot 4h hiện tại với AI chạy sau candle close.",
    ),
    RoundConfig(
        name="S2 Spot 4h stress",
        market="spot",
        timeframe_minutes=240,
        write_offsets_minutes=(1, 2, 5, 15, 30, 60, 120, 180),
        ttl_candidates_minutes=(240, 270, 300, 360),
        eval_lag_minutes=3,
        safety_margin_minutes=30,
        target_note="Thêm độ trễ Freqtrade 3 phút và API AI hoàn tất rất sớm hoặc rất muộn.",
    ),
    RoundConfig(
        name="F1 Futures 1h baseline",
        market="futures",
        timeframe_minutes=60,
        write_offsets_minutes=(2, 5, 10, 20, 35, 45),
        ttl_candidates_minutes=(60, 70, 90, 120),
        eval_lag_minutes=0,
        safety_margin_minutes=15,
        target_note="Kiểm tra futures 1h khi agent chạy mỗi giờ.",
    ),
    RoundConfig(
        name="F2 Futures 2h economy",
        market="futures",
        timeframe_minutes=120,
        write_offsets_minutes=(2, 5, 15, 30, 60, 90),
        ttl_candidates_minutes=(90, 120, 150, 180),
        eval_lag_minutes=2,
        safety_margin_minutes=20,
        target_note="Kiểm tra futures 2h nếu muốn giảm chi phí API AI.",
    ),
)

REAL_WORLD_CASES = (
    RealWorldCase(
        case_id="BN-SPOT-4H-NORMAL",
        exchange="binance",
        market="spot",
        timeframe_minutes=240,
        ttl_minutes=300,
        agent_start_delay_minutes=2,
        ai_runtime_minutes=8,
        retry_delay_minutes=0,
        eval_lag_minutes=2,
        note="Binance spot 4h, AI hoàn tất nhanh, Freqtrade/CCXT lag nhẹ.",
    ),
    RealWorldCase(
        case_id="BN-SPOT-4H-SLOW-AI",
        exchange="binance",
        market="spot",
        timeframe_minutes=240,
        ttl_minutes=300,
        agent_start_delay_minutes=2,
        ai_runtime_minutes=120,
        retry_delay_minutes=0,
        eval_lag_minutes=2,
        note="Binance spot 4h, AI chạy chậm nhưng vẫn trong cùng candle window.",
    ),
    RealWorldCase(
        case_id="BN-SPOT-4H-RATE-LIMIT",
        exchange="binance",
        market="spot",
        timeframe_minutes=240,
        ttl_minutes=300,
        agent_start_delay_minutes=2,
        ai_runtime_minutes=95,
        retry_delay_minutes=170,
        eval_lag_minutes=3,
        note="Binance spot 4h, API retry/rate-limit làm signal ghi sau candle kế tiếp.",
    ),
    RealWorldCase(
        case_id="BN-FUT-1H-NORMAL",
        exchange="binance",
        market="futures",
        timeframe_minutes=60,
        ttl_minutes=90,
        agent_start_delay_minutes=2,
        ai_runtime_minutes=6,
        retry_delay_minutes=0,
        eval_lag_minutes=2,
        note="Binance futures 1h, điều kiện bình thường.",
    ),
    RealWorldCase(
        case_id="BN-FUT-1H-SLOW-AI",
        exchange="binance",
        market="futures",
        timeframe_minutes=60,
        ttl_minutes=90,
        agent_start_delay_minutes=2,
        ai_runtime_minutes=55,
        retry_delay_minutes=0,
        eval_lag_minutes=2,
        note="Binance futures 1h, AI gần chạm biên 1 candle.",
    ),
    RealWorldCase(
        case_id="BN-FUT-1H-RETRY",
        exchange="binance",
        market="futures",
        timeframe_minutes=60,
        ttl_minutes=90,
        agent_start_delay_minutes=2,
        ai_runtime_minutes=35,
        retry_delay_minutes=40,
        eval_lag_minutes=3,
        note="Binance futures 1h, retry làm signal qua candle sau.",
    ),
)


def gap_to_next_eval(timeframe: int, write_offset: int, eval_lag: int) -> int:
    """Return minutes from signal write to the next Freqtrade evaluation.

    If the signal is available before Freqtrade's evaluation lag for the same
    candle close, it can be consumed immediately. Otherwise it waits until the
    next candle closes.
    """
    if write_offset <= eval_lag:
        return eval_lag - write_offset
    return timeframe - write_offset + eval_lag


def gap_to_next_eval_realistic(timeframe: int, write_offset: int, eval_lag: int) -> tuple[int, int]:
    """Return gap and number of completed candles after the source candle.

    `write_offset` may exceed one timeframe when AI/retries are slow. The
    returned candle count helps distinguish an acceptable wait from a stale
    signal that is still valid only because TTL is long.
    """
    position = write_offset % timeframe
    if write_offset < timeframe and position <= eval_lag:
        eval_time = eval_lag
    else:
        eval_time = write_offset + (timeframe - position) + eval_lag
    gap = max(0, eval_time - write_offset)
    candles_after_source = eval_time // timeframe
    return gap, candles_after_source


def evaluate_round(cfg: RoundConfig) -> dict:
    gaps = {
        offset: gap_to_next_eval(cfg.timeframe_minutes, offset, cfg.eval_lag_minutes)
        for offset in cfg.write_offsets_minutes
    }
    max_required_ttl = max(gaps.values()) + 1
    recommended_ttl = max_required_ttl + cfg.safety_margin_minutes
    ttl_rows = []
    for ttl in cfg.ttl_candidates_minutes:
        passed_offsets = [offset for offset, gap in gaps.items() if ttl > gap]
        missed_offsets = [offset for offset, gap in gaps.items() if ttl <= gap]
        ttl_rows.append(
            {
                "ttl": ttl,
                "passed": len(passed_offsets),
                "total": len(gaps),
                "pass_rate": round(len(passed_offsets) / len(gaps) * 100, 1),
                "missed_offsets": missed_offsets,
            }
        )
    return {
        "config": cfg,
        "gaps": gaps,
        "max_required_ttl": max_required_ttl,
        "recommended_ttl": recommended_ttl,
        "ttl_rows": ttl_rows,
    }


def markdown_report(results: list[dict]) -> str:
    lines = [
        "# Gap-time dry-run test report",
        "",
        "Mục tiêu: tối ưu TTL và lịch chạy TradingAgents để Freqtrade không bỏ lỡ signal do lệch thời điểm ghi signal với candle evaluation.",
        "",
        "Quy ước: `write_offset` là số phút sau candle close khi AI ghi signal vào bridge DB. `gap_to_eval` là số phút signal phải sống đến lần Freqtrade có thể xét nó.",
        "",
        "## Kết quả tổng hợp",
        "",
        "| Vòng | Market | TF | Max gap cần sống | TTL tối thiểu | TTL khuyến nghị | Kết luận |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for result in results:
        cfg = result["config"]
        conclusion = "OK" if any(row["pass_rate"] == 100.0 for row in result["ttl_rows"]) else "Cần tăng TTL"
        lines.append(
            f"| {cfg.name} | {cfg.market} | {cfg.timeframe_minutes}m | "
            f"{max(result['gaps'].values())}m | {result['max_required_ttl']}m | "
            f"{result['recommended_ttl']}m | {conclusion} |"
        )

    for result in results:
        cfg = result["config"]
        lines.extend(
            [
                "",
                f"## {cfg.name}",
                "",
                cfg.target_note,
                "",
                "| write_offset | gap_to_eval |",
                "|---:|---:|",
            ]
        )
        for offset, gap in result["gaps"].items():
            lines.append(f"| {offset}m | {gap}m |")

        lines.extend(["", "| TTL | Pass rate | Missed write_offset |", "|---:|---:|---|"])
        for row in result["ttl_rows"]:
            missed = ", ".join(f"{offset}m" for offset in row["missed_offsets"]) or "-"
            lines.append(f"| {row['ttl']}m | {row['pass_rate']}% | {missed} |")
        lines.extend(
            [
                "",
                f"Điều chỉnh vòng tiếp theo: dùng TTL khoảng `{result['recommended_ttl']}` phút nếu muốn có margin thực tế.",
            ]
        )

    lines.extend(
        [
            "",
            "## Khuyến nghị sau 4 vòng",
            "",
            "- Spot 4h: giữ `SIGNAL_TTL_MINUTES=300`; nếu AI API thường hoàn tất trong 1-5 phút và Freqtrade có delay vài phút, `270` là ngưỡng tối thiểu hơn, nhưng `300` an toàn hơn.",
            "- Futures 1h: giữ `SIGNAL_TTL_MINUTES=90`; `70` có thể đủ khi Freqtrade không trễ, nhưng thiếu margin khi API/Freqtrade jitter.",
            "- Futures 2h tiết kiệm chi phí: dùng `SIGNAL_TTL_MINUTES=150` hoặc `180`; `90` không phù hợp cho 2h.",
            "- Không dùng TTL quá dài vượt nhiều candle nếu signal là market-sensitive. TTL nên đủ qua đúng candle kế tiếp, không biến tín hiệu cũ thành lệnh muộn.",
        ]
    )
    return "\n".join(lines) + "\n"


def evaluate_real_world_case(case: RealWorldCase) -> dict:
    write_offset = (
        case.agent_start_delay_minutes
        + case.ai_runtime_minutes
        + case.retry_delay_minutes
    )
    runtime_guard = 180 if case.market == "spot" else 45
    guarded_runtime = case.ai_runtime_minutes + case.retry_delay_minutes
    blocked_by_guard = guarded_runtime > runtime_guard
    gap, candles_after_source = gap_to_next_eval_realistic(
        case.timeframe_minutes,
        write_offset,
        case.eval_lag_minutes,
    )
    ttl_ok = case.ttl_minutes > gap
    stale = candles_after_source > 1
    if blocked_by_guard:
        status = "BLOCKED_BY_RUNTIME_GUARD"
    elif not ttl_ok:
        status = "MISS_TTL"
    elif stale:
        status = "STALE_BUT_VALID"
    else:
        status = "OK"
    return {
        "case": case,
        "write_offset": write_offset,
        "gap_to_eval": gap,
        "candles_after_source": candles_after_source,
        "runtime_guard": runtime_guard,
        "guarded_runtime": guarded_runtime,
        "blocked_by_guard": blocked_by_guard,
        "ttl_ok": ttl_ok,
        "stale": stale,
        "status": status,
    }


def realistic_report(results: list[dict]) -> str:
    concerns = [r for r in results if r["status"] != "OK"]
    lines = [
        "# Binance realistic gap-time report",
        "",
        "Mục tiêu: kiểm tra thêm các case gần môi trường thật cho Binance trước khi chạy 200-300 vòng dry-test.",
        "",
        "Giả định test gồm: thời gian AI chạy, retry/rate-limit, độ trễ OHLCV/CCXT/Freqtrade, và việc signal có bị ghi sang candle sau hay không.",
        "",
        "## Tổng hợp case",
        "",
        "| Case | Sàn | Market | TF | TTL | AI+retry | Guard | Gap tới eval | Candle sau source | Status |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        case = result["case"]
        lines.append(
            f"| {case.case_id} | {case.exchange} | {case.market} | "
            f"{case.timeframe_minutes}m | {case.ttl_minutes}m | "
            f"{result['guarded_runtime']}m | {result['runtime_guard']}m | "
            f"{result['gap_to_eval']}m | {result['candles_after_source']} | "
            f"{result['status']} |"
        )

    lines.extend(["", "## Chi tiết", ""])
    for result in results:
        case = result["case"]
        lines.extend(
            [
                f"### {case.case_id}",
                "",
                f"- {case.note}",
                f"- Agent start delay: `{case.agent_start_delay_minutes}m`, AI runtime: `{case.ai_runtime_minutes}m`, retry delay: `{case.retry_delay_minutes}m`.",
                f"- Runtime guard: `{result['runtime_guard']}m`; guarded runtime: `{result['guarded_runtime']}m`; blocked: `{result['blocked_by_guard']}`.",
                f"- Signal ghi sau candle close: `{result['write_offset']}m`; Freqtrade eval lag: `{case.eval_lag_minutes}m`.",
                f"- Gap tới eval kế tiếp: `{result['gap_to_eval']}m`; TTL `{case.ttl_minutes}m`; status `{result['status']}`.",
                "",
            ]
        )

    lines.extend(["## Những điểm lo ngại", ""])
    if not concerns:
        lines.append("- Chưa thấy case timing fail trong bộ giả định hiện tại, nhưng vẫn cần dry-test thật để đo latency thực tế.")
    else:
        for result in concerns:
            case = result["case"]
            if result["status"] == "BLOCKED_BY_RUNTIME_GUARD":
                lines.append(
                    f"- `{case.case_id}`: đã được chặn bởi runtime guard. "
                    f"Guard `{result['runtime_guard']}m`, runtime thực tế `{result['guarded_runtime']}m`."
                )
            elif result["status"] == "MISS_TTL":
                lines.append(
                    f"- `{case.case_id}`: signal có thể hết hạn trước khi Freqtrade xét. "
                    f"TTL `{case.ttl_minutes}m` nhỏ hơn/equal gap `{result['gap_to_eval']}m`."
                )
            elif result["status"] == "STALE_BUT_VALID":
                lines.append(
                    f"- `{case.case_id}`: signal vẫn còn TTL nhưng đã sang `{result['candles_after_source']}` candle sau source. "
                    "Đây là rủi ro nguy hiểm hơn miss TTL vì bot có thể vào lệnh bằng bối cảnh đã cũ."
                )

    lines.extend(
        [
            "",
            "## Khuyến nghị kiểm soát trước dry-test dài",
            "",
            "- Ghi thêm metric `agent_runtime_seconds`, `signal_created_at`, `freqtrade_eval_at`, `signal_age_at_entry` vào dashboard để đo thực tế thay vì đoán.",
            "- Guard đã được mô phỏng: futures 1h chặn khi `AI runtime + retry > 45m`; spot 4h chặn khi vượt `180m`.",
            "- Các case bị block nên được ghi nhận là HOLD/BLOCK trong dashboard thay vì tạo lệnh.",
            "- Khi quay lại Bybit, chạy bộ realistic case riêng thay vì trộn vào báo cáo Binance.",
            "- Không dùng kết quả Binance để suy ra latency/fill behavior của Bybit futures.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    results = [evaluate_round(cfg) for cfg in ROUNDS]
    report = markdown_report(results)
    output_path = Path(__file__).resolve().parents[1] / "gap_time_test_report.md"
    output_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"Wrote {output_path}")

    realistic_results = [evaluate_real_world_case(case) for case in REAL_WORLD_CASES]
    realistic = realistic_report(realistic_results)
    realistic_path = Path(__file__).resolve().parents[1] / "exchange_realism_test_report.md"
    realistic_path.write_text(realistic, encoding="utf-8")
    print(realistic)
    print(f"Wrote {realistic_path}")


if __name__ == "__main__":
    main()
