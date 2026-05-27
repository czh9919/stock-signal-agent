"""
Paper trading engine.

Pipeline:
  1. expand_universe()      — permanent watchlist + random S&P 500 fills to target_size
  2. screen_universe()      — FF5 regression on all tickers
  3. promote_to_watchlist() — candidates with BUY/SELL signal appended to watchlist.csv
  4. generate_orders()      — compute + execute Alpaca paper trades

Entry point: run_paper_trade_pipeline(config)
"""
import csv
import logging
import os
import random
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = Path("config/watchlist.csv")


# ── Universe expansion ────────────────────────────────────────────────────────

def expand_universe(target: int = 50) -> tuple[list[str], list[str]]:
    """
    Returns (permanent_equity_tickers, candidate_tickers).

    Permanent = current watchlist equity tickers.
    Candidates = random S&P 500 sample (seeded by date, reproducible within a day)
                 that fills universe up to `target` total tickers.
    """
    from data.spy_universe import get_sp500_tickers
    from main import load_watchlist

    watchlist = load_watchlist()
    permanent = [w["ticker"] for w in watchlist if w.get("asset_class", "equity") == "equity"]
    perm_set  = set(permanent)

    sp500      = [t for t in get_sp500_tickers() if t not in perm_set]
    rng        = random.Random(int(date.today().strftime("%Y%m%d")))
    rng.shuffle(sp500)

    fill_n     = max(0, target - len(permanent))
    candidates = sp500[:fill_n]

    logger.info(
        f"Universe: {len(permanent)} permanent + {len(candidates)} S&P 500 candidates "
        f"= {len(permanent) + len(candidates)} total"
    )
    return permanent, candidates


# ── FF5 screening ─────────────────────────────────────────────────────────────

def screen_universe(
    tickers:     list[str],
    price_data:  dict,
    ff5,
    n_strategies: int,
) -> list[dict]:
    """
    Run FF5 regression + robust signal on each ticker.
    Returns list of result dicts sorted by IR descending.
    """
    from strategy.factor_model import run_factor_regression, compute_robust_signal

    results = []
    for ticker in tickers:
        pd_obj = price_data.get(ticker)
        if pd_obj is None or pd_obj.returns is None or pd_obj.status not in ("ok", "reduced"):
            continue

        reg = run_factor_regression(ticker, pd_obj.returns, ff5)
        if not reg:
            continue

        robust = compute_robust_signal(ticker, pd_obj.returns, ff5, n_strategies=n_strategies)
        if robust:
            for key in ("psr", "ir_deflated", "robust_signal",
                        "alpha_consistency", "oos_hit_rate"):
                if key in robust:
                    reg[key] = robust[key]

        if pd_obj.closes is not None and not pd_obj.closes.empty:
            reg["price"] = float(pd_obj.closes.iloc[-1])

        results.append(reg)

    results.sort(key=lambda x: x.get("ir") or float("-inf"), reverse=True)
    logger.info(f"FF5 screen: {len(results)}/{len(tickers)} tickers processed")
    return results


# ── Watchlist promotion ───────────────────────────────────────────────────────

def promote_to_watchlist(
    candidate_results: list[dict],
    watchlist_path: Path = _WATCHLIST_PATH,
) -> list[str]:
    """
    Append candidate tickers that have a BUY or SELL signal to watchlist.csv.
    Idempotent: skips tickers already in the watchlist.
    Returns list of newly added tickers.
    """
    from main import load_watchlist

    existing = {w["ticker"] for w in load_watchlist(str(watchlist_path))}

    to_add = [
        r for r in candidate_results
        if r.get("signal") in ("BUY", "SELL")
        and r["ticker"] not in existing
    ]
    if not to_add:
        logger.info("Watchlist promotion: no new candidates to promote")
        return []

    with open(watchlist_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["ticker", "weight", "notes", "asset_class", "currency"]
        )
        for r in to_add:
            t_alpha = r.get("t_alpha", 0) or 0
            writer.writerow({
                "ticker":      r["ticker"],
                "weight":      "1.0",
                "notes":       (
                    f"FF5 signal={r['signal']} "
                    f"t={t_alpha:.1f} "
                    f"IR={r.get('ir', 0) or 0:.2f} "
                    f"[{date.today()}]"
                ),
                "asset_class": "equity",
                "currency":    "USD",
            })

    promoted = [r["ticker"] for r in to_add]
    logger.info(f"Promoted to permanent watchlist: {promoted}")
    return promoted


# ── Order generation + execution ──────────────────────────────────────────────

def generate_orders(
    results:     list[dict],
    positions:   dict,
    equity:      float,
    price_data:  dict,
    max_pos_pct: float = 0.15,
    dry_run:     bool  = False,
) -> list[dict]:
    """
    Compute and (unless dry_run) execute Alpaca paper orders.

      BUY signal  + not currently held → market buy
      SELL signal + currently held     → close position
      Everything else                  → no action

    Position sizing: equal allocation among BUY signals, capped at max_pos_pct.
    """
    from brokers.alpaca import place_order, close_position

    buy_signals  = [r for r in results if r.get("signal") == "BUY"]
    sell_signals = [r for r in results if r.get("signal") == "SELL"]

    orders: list[dict] = []

    # ── Close SELL-signaled positions ─────────────────────────────────────────
    for r in sell_signals:
        ticker = r["ticker"]
        if ticker not in positions:
            continue
        orders.append({
            "ticker": ticker, "side": "sell",
            "reason": f"SELL signal  t={r.get('t_alpha', 0) or 0:.1f}  IR={r.get('ir', 0) or 0:.2f}",
        })
        if not dry_run:
            try:
                close_position(ticker)
                logger.info(f"Paper: CLOSED {ticker}")
            except Exception as e:
                logger.warning(f"Paper: close {ticker} failed — {e}")

    # ── Buy BUY-signaled tickers not already held ─────────────────────────────
    new_buys    = [r for r in buy_signals if r["ticker"] not in positions]
    n_new       = len(new_buys)
    target_val  = min(equity * max_pos_pct,
                      equity / n_new if n_new else equity)

    for r in new_buys:
        ticker = r["ticker"]
        # Use yfinance close as price estimate (Alpaca market order fills at market)
        pd_obj = price_data.get(ticker)
        if pd_obj is None or pd_obj.closes is None or pd_obj.closes.empty:
            logger.warning(f"Paper: {ticker} BUY skipped — no price data")
            continue

        price = float(pd_obj.closes.iloc[-1])
        qty   = max(1, int(target_val / price))
        orders.append({
            "ticker":    ticker, "side": "buy", "qty": qty,
            "price_est": price,  "value_usd": qty * price,
            "reason":    f"BUY signal  t={r.get('t_alpha', 0) or 0:.1f}  IR={r.get('ir', 0) or 0:.2f}",
        })
        if not dry_run:
            try:
                place_order(ticker, qty, "buy")
                logger.info(f"Paper: BUY {qty}x {ticker} @ ~${price:.2f}")
            except Exception as e:
                logger.warning(f"Paper: buy {ticker} failed — {e}")

    return orders


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_paper_trade_pipeline(config: dict) -> dict:
    """
    Orchestrates the full paper trading pipeline. Called from main.py
    when RUN_MODE=paper_trade.
    """
    from data.price_loader import load_prices
    from risk.optimizer    import fetch_ff5
    from brokers.alpaca    import is_configured, get_account, get_positions

    paper_cfg   = config.get("paper_trade", {})
    target_size = int(paper_cfg.get("universe_size", 50))
    max_pos_pct = float(paper_cfg.get("max_position_pct", 0.15))
    ff_days     = int(config.get("settings", {}).get("ff5_window", 252))
    dry_run     = os.environ.get("PAPER_DRY_RUN", "false").lower() == "true"

    logger.info(
        f"=== Paper Trade Pipeline  universe={target_size}  "
        f"max_pos={max_pos_pct*100:.0f}%  dry_run={dry_run} ==="
    )

    # 1. Expand universe
    permanent, candidates = expand_universe(target_size)
    all_tickers = permanent + candidates

    # 2. Load prices (252-day window)
    logger.info(f"Loading {len(all_tickers)} tickers …")
    price_data = load_prices(all_tickers, days=ff_days)

    # 3. FF5 data (uses 20h cache from daily_run if available)
    ff5 = fetch_ff5()
    if ff5 is None:
        logger.error("FF5 data unavailable — aborting paper trade pipeline")
        return {}

    n_strategies = len(all_tickers)

    # 4. Screen permanent watchlist
    logger.info("Screening permanent watchlist …")
    perm_results = screen_universe(permanent, price_data, ff5, n_strategies)

    # 5. Screen S&P 500 candidates
    logger.info("Screening S&P 500 candidates …")
    cand_results = screen_universe(candidates, price_data, ff5, n_strategies)

    # 6. Promote candidates with signal to permanent watchlist
    promoted = promote_to_watchlist(cand_results)

    all_results = perm_results + cand_results

    # 7. Alpaca paper trading
    orders: list[dict] = []
    account_info: dict = {}

    if is_configured():
        try:
            account  = get_account()
            equity   = float(account.get("equity", 100_000))
            positions = get_positions()
            account_info = {
                "equity":      equity,
                "cash":        float(account.get("cash", 0)),
                "n_positions": len(positions),
                "buying_power": float(account.get("buying_power", 0)),
            }
            logger.info(
                f"Alpaca paper account: equity=${equity:,.0f}  "
                f"cash=${account_info['cash']:,.0f}  "
                f"positions={account_info['n_positions']}"
            )
            orders = generate_orders(
                all_results, positions, equity, price_data,
                max_pos_pct=max_pos_pct, dry_run=dry_run,
            )
        except Exception as e:
            logger.error(f"Alpaca connection error: {e}")
    else:
        logger.warning("ALPACA_API_KEY not set — screening only, no paper trades placed")

    buys  = sum(1 for o in orders if o["side"] == "buy")
    sells = sum(1 for o in orders if o["side"] == "sell")
    logger.info(
        f"=== Paper Trade complete: {buys} buys  {sells} sells  "
        f"{len(promoted)} promoted ==="
    )

    return {
        "perm_results":      perm_results,
        "candidate_results": cand_results,
        "all_results":       all_results,
        "promoted":          promoted,
        "orders":            orders,
        "account":           account_info,
    }
