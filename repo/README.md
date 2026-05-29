# TraceTradeLab

AI-powered crypto trading research dashboard. Kết hợp [TradingAgents](https://github.com/TauricResearch/TradingAgents) + Freqtrade + feedback loop để phân tích và học từ kết quả giao dịch thực.

## Kiến trúc

```
TradingAgents (DeepSeek AI)
       ↓ signal (BUY/SELL/HOLD)
  signal_bridge/signals.db
       ↓ read
  Freqtrade (dry-run / live)
       ↓ outcome (PnL, W/L)
  feedback_collector.py
       ↓ past_context
  agent_runner_v2.py (next run)
```

## Yêu cầu

- Ubuntu 22.04+ / Debian 12
- Python 3.11+
- Docker (cho Freqtrade)
- DeepSeek API key

## Deploy nhanh

```bash
git clone https://github.com/YOUR_USERNAME/TraceTradeLab.git
cd TraceTradeLab
bash setup.sh
```

## Cấu hình

```bash
nano /opt/TraceTradeLab/.env
# Điền DEEPSEEK_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

## Khởi động API

```bash
cd /opt/TraceTradeLab/dashboard
/opt/TraceTradeLab/.venv/bin/uvicorn api_v2:app --host 0.0.0.0 --port 8888 &
```

Dashboard: http://localhost:8888
