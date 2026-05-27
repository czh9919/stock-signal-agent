"""
Background asyncio task: bridges Massive WebSocket → dashboard browser clients.

Flow:
  Massive WS  →  AM bar events  →  SQLite price_snapshots  →  browser via WSManager

Watchlist hot-reload:
  Call signal_reload() (e.g. from the watchlist PUT endpoint) to cancel the
  current WS session and reconnect with the updated ticker list immediately.
"""
import asyncio
import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = Path("config/watchlist.csv")

# Initialised by run_massive_ws_task; signal_reload() sets it from route handlers.
_reload_event: Optional[asyncio.Event] = None


def signal_reload() -> None:
    """
    Trigger immediate resubscription with the current watchlist.
    Safe to call from async FastAPI route handlers (same event loop).
    No-op if the WS task has not started yet.
    """
    if _reload_event is not None:
        _reload_event.set()


def _load_tickers() -> list[str]:
    if not _WATCHLIST_PATH.exists():
        return []
    with open(_WATCHLIST_PATH, newline="", encoding="utf-8") as f:
        return [row["ticker"] for row in csv.DictReader(f) if row.get("ticker")]


async def _wait_for_signal(stop_event: asyncio.Event,
                            reload_event: asyncio.Event) -> None:
    """Return as soon as either event is set."""
    t_stop   = asyncio.create_task(stop_event.wait())
    t_reload = asyncio.create_task(reload_event.wait())
    try:
        await asyncio.wait([t_stop, t_reload], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (t_stop, t_reload):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass


async def run_massive_ws_task(stop_event: asyncio.Event) -> None:
    """
    Long-running asyncio task launched from app lifespan.

    Each iteration:
      1. Load current tickers from watchlist.csv
      2. Connect + subscribe via Massive WS (cancel-able)
      3. Also wait for stop_event or reload_event
      4. Cancel the WS task; loop with fresh tickers if it was a reload
    """
    global _reload_event

    from data.massive import run_ws
    from data.storage import Storage
    from api.ws import manager

    if not os.environ.get("MASSIVE_API_KEY"):
        logger.info("MASSIVE_API_KEY not set — Massive WebSocket disabled")
        return

    # The Massive WebSocket (real-time minute bars) requires a real-time plan.
    # The Stocks Basic plan provides End of Day data only; attempting to connect
    # results in repeated auth failures.  Disable WS; yfinance polls handle
    # dashboard price updates instead.
    massive_plan = os.environ.get("MASSIVE_PLAN", "basic").lower()
    if massive_plan in ("basic", "eod", "starter"):
        logger.info(f"MASSIVE_PLAN={massive_plan} — WebSocket disabled (EOD plan, no real-time bars)")
        return

    _reload_event = asyncio.Event()
    db = Storage()

    def on_bar(event: dict) -> None:
        ticker = event.get("sym") or event.get("ticker", "")
        price  = event.get("c")          # close price of the minute bar
        if not ticker or price is None:
            return
        price      = float(price)
        updated_at = datetime.now(timezone.utc).isoformat()
        db.upsert_price_snapshot(ticker, price)
        manager.send_prices([{"ticker": ticker, "price": price,
                               "currency": "USD", "updated_at": updated_at}])

    while not stop_event.is_set():
        tickers = _load_tickers()
        if not tickers:
            logger.debug("Massive WS: watchlist empty, waiting 30s")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            continue

        _reload_event.clear()
        logger.info(f"Massive WS: subscribing to {len(tickers)} tickers")

        # Run WS loop and signal-waiter concurrently; first to finish wins.
        ws_task     = asyncio.create_task(run_ws(tickers, on_bar, stop_event))
        signal_task = asyncio.create_task(_wait_for_signal(stop_event, _reload_event))

        done, pending = await asyncio.wait(
            [ws_task, signal_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        if stop_event.is_set():
            break

        if _reload_event.is_set():
            logger.info("Massive WS: watchlist changed — resubscribing")
        # else: ws_task ended on its own (error handled inside run_ws)
