"""
api_v2.py — FastAPI v2 với đầy đủ 9 dashboard endpoints theo spec
Thay thế api.py

Path: /opt/TraceTradeLab/dashboard/api_v2.py
Chạy: uvicorn api_v2:app --host 0.0.0.0 --port 8888
"""

import asyncio, json, logging, os, subprocess, shlex
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import sys

DEFAULT_TRACE_ROOT = Path(__file__).resolve().parents[1]
TRACE_ROOT = Path(os.getenv("TRACE_ROOT", str(DEFAULT_TRACE_ROOT)))
DASHBOARD_DIR = Path(os.getenv("TRACE_DASHBOARD_DIR", str(TRACE_ROOT / "dashboard")))
LOG_DIR = Path(os.getenv("TRACE_LOG_DIR", str(TRACE_ROOT / "logs")))
VENV_PYTHON = os.getenv("TRACE_VENV_PYTHON", str(TRACE_ROOT / ".venv/bin/python"))
FREQTRADE_CONFIG_PATH = Path(
    os.getenv("FREQTRADE_CONFIG_PATH", str(TRACE_ROOT / "freqtrade/user_data/config.json"))
)
FREQTRADE_ENV_PATH = Path(
    os.getenv("FREQTRADE_ENV_PATH", str(TRACE_ROOT / "freqtrade/.env"))
)
FREQTRADE_COMPOSE_FILE = Path(
    os.getenv("FREQTRADE_COMPOSE_FILE", str(TRACE_ROOT / "freqtrade/docker-compose.yml"))
)
FREQTRADE_SERVICE = os.getenv("FREQTRADE_SERVICE", "freqtrade")
FREQTRADE_UI_URL = os.getenv("FREQTRADE_UI_URL", "http://127.0.0.1:8080")
VALID_TIMEFRAMES = (
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d",
)

sys.path.insert(0, str(DASHBOARD_DIR))

from db_v2 import (
    init_db, get_recent_runs, get_run_messages, get_signal_history,
    get_latest_valid_signal, get_latest_freqtrade_snapshot,
    save_freqtrade_snapshot, get_overview_stats, get_signal_behavior_stats,
    get_execution_stats, get_outcome_stats, get_agent_attribution_stats,
    get_regime_stats, get_feedback_learning_stats,
    get_run_trace, get_signal_lineage,
)

# Graceful imports
try:
    from feedback_collector import (
        ensure_feedback_schema, get_accuracy_stats,
        get_past_context, run_feedback_collection
    )
    FEEDBACK_OK = True
except ImportError:
    FEEDBACK_OK = False

try:
    from signal_lifecycle import run_lifecycle_sync
    LIFECYCLE_OK = True
except ImportError:
    LIFECYCLE_OK = False

try:
    from agent_bias_extractor import backfill_agent_biases
    BIAS_OK = True
except ImportError:
    BIAS_OK = False

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def _load_ft_pass() -> tuple[str, str, str]:
    """Read Freqtrade credentials. config.json may use 'overridden_by_environment',
    so fall back to freqtrade/.env for the actual password."""
    import json as _json
    _url = "http://127.0.0.1:8080/api/v1"
    _user = "admin"
    _pass = ""
    try:
        with open(FREQTRADE_CONFIG_PATH) as _f:
            _cfg = _json.load(_f)
        _api = _cfg.get("api_server", {})
        _url = f"http://127.0.0.1:{_api.get('listen_port', 8080)}/api/v1"
        _user = _api.get("username", "admin")
        _pass = _api.get("password", "")
    except Exception as _e:
        log.warning(f"Cannot load FT config.json: {_e}")
    # If password is a placeholder, read from freqtrade .env
    if not _pass or _pass == "overridden_by_environment":
        try:
            for line in FREQTRADE_ENV_PATH.read_text().splitlines():
                if line.startswith("FREQTRADE_API_PASSWORD="):
                    _pass = line.split("=", 1)[1].strip()
                    break
        except Exception as _e:
            log.warning(f"Cannot load FT .env: {_e}")
    return _url, _user, _pass

FREQTRADE_URL, FREQTRADE_USER, FREQTRADE_PASS = _load_ft_pass()

app = FastAPI(title="TraceTrader Lab API v2", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class FreqtradeRestartRequest(BaseModel):
    timeframe: str


# ─── WebSocket Manager ────────────────────────────────────────────

class WSManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, msg: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

mgr = WSManager()


# ─── Startup ──────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    if FEEDBACK_OK:
        ensure_feedback_schema()
    asyncio.create_task(freqtrade_poller())
    asyncio.create_task(db_broadcaster())
    asyncio.create_task(lifecycle_poller())
    log.info("TraceTrader Lab API v2 started")


# ─── Background tasks ─────────────────────────────────────────────

async def freqtrade_poller():
    """Poll Freqtrade mỗi 30s → lưu snapshot → broadcast."""
    while True:
        await asyncio.sleep(30)
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                auth = (FREQTRADE_USER, FREQTRADE_PASS)
                st, pr, tr = await asyncio.gather(
                    c.get(f"{FREQTRADE_URL}/status",      auth=auth),
                    c.get(f"{FREQTRADE_URL}/profit",      auth=auth),
                    c.get(f"{FREQTRADE_URL}/trades?limit=20", auth=auth),
                )
                cfg = await c.get(f"{FREQTRADE_URL}/show_config", auth=auth)

            save_freqtrade_snapshot(
                open_trades    = st.json()  if st.status_code  == 200 else [],
                closed_trades  = tr.json()  if tr.status_code  == 200 else {},
                profit_summary = pr.json()  if pr.status_code  == 200 else {},
                bot_status     = cfg.json() if cfg.status_code == 200 else {},
            )
            if mgr.active:
                await mgr.broadcast({
                    "type": "freqtrade_update",
                    "data": get_latest_freqtrade_snapshot()
                })
        except Exception as e:
            log.debug(f"FT poll: {e}")


async def db_broadcaster():
    """Push DB changes đến WS clients mỗi 3s."""
    last_run = None
    last_sig = None
    while True:
        await asyncio.sleep(3)
        try:
            runs = get_recent_runs(10)
            sigs = get_signal_history(limit=20)
            cur_run = runs[0]["id"] if runs else None
            cur_sig = sigs[0]["id"] if sigs else None

            if cur_run != last_run or cur_sig != last_sig:
                last_run, last_sig = cur_run, cur_sig
                if mgr.active:
                    await mgr.broadcast({
                        "type": "update",
                        "data": {
                            "runs": runs, "signals": sigs,
                            "freqtrade": get_latest_freqtrade_snapshot(),
                        }
                    })
                    # Push messages cho running run
                    if runs and runs[0]["status"] == "running":
                        msgs = get_run_messages(runs[0]["id"])
                        await mgr.broadcast({
                            "type": "agent_messages",
                            "run_id": runs[0]["id"],
                            "data": msgs[-15:],
                        })
        except Exception as e:
            log.debug(f"Broadcaster: {e}")


async def lifecycle_poller():
    """Run signal lifecycle sync mỗi 5 phút."""
    while True:
        await asyncio.sleep(300)
        if LIFECYCLE_OK:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, run_lifecycle_sync)
            except Exception as e:
                log.debug(f"Lifecycle sync: {e}")


# ─── WebSocket ────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await mgr.connect(websocket)
    try:
        await websocket.send_json({
            "type": "init",
            "data": {
                "runs":      get_recent_runs(10),
                "signals":   get_signal_history(limit=20),
                "freqtrade": get_latest_freqtrade_snapshot(),
                "overview":  get_overview_stats(),
            }
        })
    except Exception:
        pass
    try:
        while True:
            d = await websocket.receive_text()
            if d == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        mgr.disconnect(websocket)


# ─── Core endpoints ───────────────────────────────────────────────

@app.get("/api/status")
async def status():
    return {"status": "ok", "version": "2.0", "time": datetime.now(timezone.utc).isoformat()}

@app.get("/api/runs")
async def get_runs(limit: int = Query(20, le=100)):
    return get_recent_runs(limit)

@app.get("/api/runs/{run_id}")
async def get_run(run_id: int):
    runs = get_recent_runs(200)
    run = next((r for r in runs if r["id"] == run_id), None)
    if not run:
        raise HTTPException(404, "Run not found")
    return {"run": run, "messages": get_run_messages(run_id)}

@app.get("/api/signals")
async def get_signals(symbol: Optional[str] = None, limit: int = Query(50, le=200)):
    return get_signal_history(symbol, limit)

@app.get("/api/signals/latest/{symbol:path}")
async def latest_signal(symbol: str):
    symbol = symbol.replace("%2F", "/")
    sig = get_latest_valid_signal(symbol)
    return {"signal": sig}


# ─── 7 Dashboard tab endpoints ────────────────────────────────────

@app.get("/api/dashboard/overview")
async def dashboard_overview():
    """Tab 1 — Overview KPIs + funnel."""
    return get_overview_stats()


@app.get("/api/dashboard/signals")
async def dashboard_signals(
    symbol: Optional[str] = None,
    days: int = Query(30, le=90)
):
    """Tab 2 — Signal Behavior."""
    return get_signal_behavior_stats(symbol, days)


@app.get("/api/dashboard/execution")
async def dashboard_execution():
    """Tab 3 — Execution Compatibility."""
    return get_execution_stats()


@app.get("/api/dashboard/outcomes")
async def dashboard_outcomes(symbol: Optional[str] = None):
    """Tab 4 — Outcome Metrics."""
    return get_outcome_stats(symbol)


@app.get("/api/dashboard/agents")
async def dashboard_agents():
    """Tab 5 — Agent Attribution."""
    return get_agent_attribution_stats()


@app.get("/api/dashboard/regimes")
async def dashboard_regimes():
    """Tab 6 — Market Regime."""
    return get_regime_stats()


@app.get("/api/dashboard/feedback")
async def dashboard_feedback():
    """Tab 7 — Feedback Learning."""
    return get_feedback_learning_stats()


# ─── Trace / Lineage endpoints ────────────────────────────────────

@app.get("/api/runs/{run_id}/trace")
async def run_trace(run_id: int):
    """
    Run Trace Viewer — màn hình quan trọng nhất.
    Trả về: run → regime → agent_messages → signal → execution → outcome → feedback
    """
    trace = get_run_trace(run_id)
    if not trace:
        raise HTTPException(404, "Run not found")
    return trace


@app.get("/api/signals/{signal_id}/lineage")
async def signal_lineage(signal_id: int):
    """
    Signal Lineage:
    agent_reasoning → signal → execution → outcome → feedback lesson
    """
    lineage = get_signal_lineage(signal_id)
    if not lineage:
        raise HTTPException(404, "Signal not found")
    return lineage


# ─── Freqtrade proxy ─────────────────────────────────────────────

async def _ft(path: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(f"{FREQTRADE_URL}{path}", auth=(FREQTRADE_USER, FREQTRADE_PASS))
        r.raise_for_status()
        return r.json()

def _load_freqtrade_config() -> dict:
    if not FREQTRADE_CONFIG_PATH.exists():
        raise HTTPException(404, f"Freqtrade config not found: {FREQTRADE_CONFIG_PATH}")
    try:
        return json.loads(FREQTRADE_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"Invalid Freqtrade config JSON: {exc}") from exc

def _write_freqtrade_config(config: dict) -> str:
    FREQTRADE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup = FREQTRADE_CONFIG_PATH.with_suffix(
        FREQTRADE_CONFIG_PATH.suffix + f".bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    if FREQTRADE_CONFIG_PATH.exists():
        backup.write_text(FREQTRADE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    tmp = FREQTRADE_CONFIG_PATH.with_suffix(FREQTRADE_CONFIG_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(FREQTRADE_CONFIG_PATH)
    return str(backup)

def _restart_freqtrade_container() -> dict:
    if not FREQTRADE_COMPOSE_FILE.exists():
        raise HTTPException(500, f"Docker compose file not found: {FREQTRADE_COMPOSE_FILE}")
    cmd = ["docker", "compose", "-f", str(FREQTRADE_COMPOSE_FILE), "restart", FREQTRADE_SERVICE]
    result = subprocess.run(
        cmd,
        cwd=str(FREQTRADE_COMPOSE_FILE.parent),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        raise HTTPException(
            500,
            {
                "error": "Freqtrade restart failed",
                "cmd": " ".join(cmd),
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            },
        )
    return {
        "cmd": " ".join(cmd),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }

async def _freqtrade_live_config() -> dict:
    try:
        return await _ft("/show_config")
    except Exception:
        return {}

@app.get("/api/freqtrade/status")
async def ft_status():
    try: return await _ft("/status")
    except: return {"error": "Freqtrade offline"}

@app.get("/api/freqtrade/profit")
async def ft_profit():
    try: return await _ft("/profit")
    except: return {"error": "unavailable"}

@app.get("/api/freqtrade/trades")
async def ft_trades(limit: int = Query(50)):
    try: return await _ft(f"/trades?limit={limit}")
    except: return {"error": "unavailable"}

@app.get("/api/freqtrade/performance")
async def ft_performance():
    try: return await _ft("/performance")
    except: return {"error": "unavailable"}

@app.get("/api/freqtrade/daily")
async def ft_daily(days: int = Query(30)):
    try: return await _ft(f"/daily?timescale={days}")
    except: return {"error": "unavailable"}

@app.get("/api/freqtrade/config")
async def ft_config():
    local = _load_freqtrade_config()
    live = await _freqtrade_live_config()
    api = local.get("api_server", {})
    return {
        "timeframe": local.get("timeframe", "1h"),
        "dry_run": bool(local.get("dry_run", True)),
        "live_timeframe": live.get("timeframe"),
        "live_dry_run": live.get("dry_run"),
        "state": live.get("state"),
        "strategy": live.get("strategy"),
        "available_timeframes": list(VALID_TIMEFRAMES),
        "config_path": str(FREQTRADE_CONFIG_PATH),
        "compose_file": str(FREQTRADE_COMPOSE_FILE),
        "service": FREQTRADE_SERVICE,
        "ui_url": FREQTRADE_UI_URL,
        "api_url": FREQTRADE_URL,
        "api_user": api.get("username", FREQTRADE_USER),
    }

@app.post("/api/freqtrade/config/restart")
async def ft_config_restart(payload: FreqtradeRestartRequest):
    timeframe = payload.timeframe.strip()
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe: {timeframe}")

    config = _load_freqtrade_config()
    old_timeframe = config.get("timeframe")
    config["timeframe"] = timeframe
    config["dry_run"] = True
    backup = _write_freqtrade_config(config)
    restart = _restart_freqtrade_container()
    live = await _freqtrade_live_config()
    return {
        "status": "restarted",
        "old_timeframe": old_timeframe,
        "timeframe": timeframe,
        "dry_run": True,
        "backup": backup,
        "restart": restart,
        "live_timeframe": live.get("timeframe"),
        "live_dry_run": live.get("dry_run"),
        "state": live.get("state"),
    }


# ─── Accuracy / Memory ───────────────────────────────────────────

@app.get("/api/accuracy")
async def accuracy(symbol: Optional[str] = None):
    if not FEEDBACK_OK:
        return {"error": "feedback_collector not available"}
    return get_accuracy_stats(symbol)

@app.get("/api/memory/{symbol:path}")
async def memory(symbol: str):
    symbol = symbol.replace("%2F", "/")
    if not FEEDBACK_OK:
        return {"context": ""}
    return {"symbol": symbol, "context": get_past_context(symbol, n=10)}


# ─── Action triggers ──────────────────────────────────────────────

def _bg_run(cmd: str, log_file: str):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        shlex.split(cmd),
        stdout=open(log_file, "a"),
        stderr=subprocess.STDOUT,
        cwd=DASHBOARD_DIR,
    )

@app.post("/api/trigger-run")
async def trigger_run(background_tasks: BackgroundTasks, symbol: str = "BTC/USDT"):
    cmd = f"{VENV_PYTHON} {DASHBOARD_DIR}/agent_runner_v2.py --symbol '{symbol}'"
    background_tasks.add_task(_bg_run, cmd, str(LOG_DIR / "manual_run.log"))
    return {"status": "triggered", "symbol": symbol}

@app.post("/api/trigger-feedback")
async def trigger_feedback(background_tasks: BackgroundTasks):
    if not FEEDBACK_OK:
        return {"error": "feedback_collector not available"}
    background_tasks.add_task(run_feedback_collection)
    return {"status": "triggered"}

@app.post("/api/trigger-lifecycle")
async def trigger_lifecycle(background_tasks: BackgroundTasks):
    if not LIFECYCLE_OK:
        return {"error": "signal_lifecycle not available"}
    background_tasks.add_task(run_lifecycle_sync)
    return {"status": "triggered"}

@app.post("/api/trigger-backfill-bias")
async def trigger_backfill(background_tasks: BackgroundTasks):
    if not BIAS_OK:
        return {"error": "agent_bias_extractor not available"}
    background_tasks.add_task(backfill_agent_biases)
    return {"status": "triggered"}


# ─── Static files ─────────────────────────────────────────────────

STATIC = Path(DASHBOARD_DIR) / "static"
STATIC.mkdir(parents=True, exist_ok=True)

@app.get("/", response_class=HTMLResponse)
async def root():
    idx = STATIC / "index.html"
    return HTMLResponse(idx.read_text() if idx.exists() else f"<h1>Place index.html in {STATIC}</h1>")

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
