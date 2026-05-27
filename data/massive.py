"""
Massive / Polygon-compatible market data client.

REST  — historical daily OHLC via /v2/aggs
WS    — real-time minute bars via wss://delayed.massive.com/stocks

Polygon AM event schema:
  ev  "AM"             aggregate minute
  sym  ticker
  o/h/l/c/v           OHLC + volume
  t   start timestamp  ms UTC
  e   end timestamp    ms UTC
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("MASSIVE_BASE_URL", "https://api.massive.com")
WS_URL   = os.environ.get("MASSIVE_WS_URL",   "wss://delayed.massive.com/stocks")


def _api_key() -> str:
    return os.environ.get("MASSIVE_API_KEY", "")


# ── REST: historical daily OHLC ───────────────────────────────────────────────

_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_BACKOFF  = (5, 15, 30)   # seconds to wait after each 429


def fetch_daily_closes(ticker: str, days: int = 400) -> Optional[pd.Series]:
    """
    Return a pd.Series of daily closing prices indexed by tz-naive datetime.
    Retries up to 3 times on 429 (rate-limit) with exponential backoff.
    Returns None on any other failure so callers can fall back to yfinance.
    """
    key = _api_key()
    if not key:
        return None

    end   = datetime.utcnow()
    start = end - timedelta(days=int(days * 1.5))   # buffer for weekends/holidays
    url   = (f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day"
             f"/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}")
    params = {"adjusted": "true", "sort": "asc", "limit": 1000, "apiKey": key}

    for attempt in range(_RATE_LIMIT_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=10)

            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", _RATE_LIMIT_BACKOFF[attempt]))
                logger.warning(f"Massive REST {ticker}: rate-limited, retrying in {wait}s "
                               f"(attempt {attempt + 1}/{_RATE_LIMIT_RETRIES})")
                time.sleep(wait)
                continue

            r.raise_for_status()
            data = r.json()

            if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
                logger.debug(f"Massive {ticker}: status={data.get('status')}, no results")
                return None

            df = pd.DataFrame(data["results"])
            df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_localize(None)
            series = df.set_index("date")["c"].rename(ticker)
            logger.info(f"Massive {ticker}: {len(series)} daily closes via REST")
            return series

        except requests.exceptions.HTTPError:
            raise   # re-raise non-429 HTTP errors so the outer except logs them
        except Exception as exc:
            logger.warning(f"Massive REST {ticker}: {exc}")
            return None

    logger.warning(f"Massive REST {ticker}: gave up after {_RATE_LIMIT_RETRIES} rate-limit retries")
    return None


def fetch_snapshots(tickers: list[str]) -> dict[str, float]:
    """
    Return {ticker: latest_price} for a list of tickers via snapshot endpoint.
    Used for initial price display before WS delivers first bars.
    """
    key = _api_key()
    if not key or not tickers:
        return {}

    joined = ",".join(tickers)
    url    = f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers"
    try:
        r = requests.get(url, params={"tickers": joined, "apiKey": key}, timeout=10)
        r.raise_for_status()
        data    = r.json()
        results = {}
        for item in data.get("tickers", []):
            day = item.get("day", {})
            c   = day.get("c") or item.get("lastTrade", {}).get("p")
            if c:
                results[item["ticker"]] = float(c)
        return results
    except Exception as exc:
        logger.warning(f"Massive snapshot: {exc}")
        return {}


# ── WebSocket: real-time minute bars ─────────────────────────────────────────

async def run_ws(
    tickers: list[str],
    on_bar: Callable[[dict], None],
    stop_event: Optional[asyncio.Event] = None,
):
    """
    Persistent WebSocket loop that reconnects on failure.
    Calls on_bar(event_dict) for every AM (aggregate minute) event.
    Subscribes to the tickers list; re-reads on each reconnect so
    changes to the watchlist take effect automatically.
    """
    try:
        import websockets
    except ImportError:
        logger.error("websockets not installed — run: pip install websockets")
        return

    key = _api_key()
    if not key:
        logger.error("MASSIVE_API_KEY not set — WebSocket disabled")
        return

    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                logger.info(f"Massive WS connected: {WS_URL}")

                # Wait for connected status
                await _expect_status(ws, "connected")

                # Authenticate
                await ws.send(json.dumps({"action": "auth", "params": key}))
                await _expect_status(ws, "auth_success")
                logger.info("Massive WS authenticated")

                # Subscribe to AM bars for all watchlist tickers
                params = ",".join(f"AM.{t}" for t in tickers)
                await ws.send(json.dumps({"action": "subscribe", "params": params}))
                logger.info(f"Massive WS subscribed: {params}")

                async for raw in ws:
                    if stop_event and stop_event.is_set():
                        return
                    try:
                        events = json.loads(raw)
                        for ev in events:
                            if ev.get("ev") == "AM":
                                on_bar(ev)
                    except Exception as exc:
                        logger.debug(f"Massive WS parse error: {exc}")

        except Exception as exc:
            logger.warning(f"Massive WS error: {exc} — reconnecting in 60s")
            await asyncio.sleep(60)


async def _expect_status(ws, expected: str, timeout: float = 10.0):
    """Read messages until a status event with the expected status is received."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        for ev in json.loads(raw):
            if ev.get("ev") == "status":
                if ev.get("status") == expected:
                    return
                if "error" in ev.get("status", ""):
                    raise RuntimeError(f"Massive WS status: {ev}")
    raise TimeoutError(f"Timed out waiting for status '{expected}'")
