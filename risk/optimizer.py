"""
Markowitz mean-variance optimizer.
  compute_frontier()         — Monte Carlo cloud + max-Sharpe portfolio
  rebalancing_suggestions()  — 3-tier rebalancing vs optimal weights
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


def _stats(w: np.ndarray, mu: np.ndarray, cov: np.ndarray,
           rf: float = 0.035) -> tuple[float, float, float]:
    ret = float(w @ mu)
    vol = float(np.sqrt(max(w @ cov @ w, 0.0)))
    sharpe = (ret - rf) / vol if vol > 1e-9 else -99.0
    return ret, vol, sharpe


def _max_sharpe(mu: np.ndarray, cov: np.ndarray,
                rf: float = 0.035,
                w_bounds: tuple = (0.005, 0.30)) -> Optional[np.ndarray]:
    n = len(mu)
    w0 = np.ones(n) / n
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    res = minimize(
        lambda w: -_stats(w, mu, cov, rf)[2],
        w0, method="SLSQP",
        bounds=[w_bounds] * n,
        constraints=cons,
        options={"maxiter": 600, "ftol": 1e-9},
    )
    if res.success:
        return res.x
    logger.warning(f"Max-Sharpe optimizer: {res.message}")
    return None


def compute_frontier(price_data: dict, holdings: list[dict],
                     rf: float = 0.035, n_mc: int = 1200) -> dict:
    """
    Build risk-return frontier data for visualisation + optimisation.
    Only equity holdings with ok/reduced price history are used.

    Returns dict:
      mc      : list of (vol, ret, sharpe) for n_mc random portfolios
      current : {vol, ret, sharpe, weights}
      assets  : [{ticker, vol, ret}, ...]
      optimal : {vol, ret, sharpe, weights} | None
    """
    valid = [
        h for h in holdings
        if h.get("asset_class", "equity") == "equity"
        and price_data.get(h["ticker"])
        and price_data[h["ticker"]].status in ("ok", "reduced")
        and price_data[h["ticker"]].returns is not None
    ]
    if len(valid) < 2:
        logger.info("Frontier: fewer than 2 equity positions with price history — skipping")
        return {}

    tickers = [h["ticker"] for h in valid]
    w_cur   = np.array([h["weight"] for h in valid])
    w_sum   = w_cur.sum()
    if w_sum > 1e-9:
        w_cur = w_cur / w_sum  # re-normalise to equity-only weights

    ret_df = pd.concat(
        {t: price_data[t].returns for t in tickers}, axis=1
    ).dropna()

    mu  = ret_df.mean().values * 252    # annualised expected return
    cov = ret_df.cov().values  * 252    # annualised covariance

    # Current portfolio
    cur_ret, cur_vol, cur_sharpe = _stats(w_cur, mu, cov, rf)

    # Per-asset stats
    assets = [
        {"ticker": t, "ret": float(mu[i]), "vol": float(np.sqrt(max(cov[i, i], 0.0)))}
        for i, t in enumerate(tickers)
    ]

    # Monte Carlo portfolios
    rng = np.random.default_rng(42)
    mc_rows: list[tuple[float, float, float]] = []
    for _ in range(n_mc):
        w = rng.dirichlet(np.ones(len(tickers)))
        mc_rows.append(_stats(w, mu, cov, rf))

    # Max-Sharpe portfolio
    opt_w = _max_sharpe(mu, cov, rf)
    optimal: Optional[dict] = None
    if opt_w is not None:
        o_ret, o_vol, o_sharpe = _stats(opt_w, mu, cov, rf)
        optimal = {
            "vol":     o_vol,
            "ret":     o_ret,
            "sharpe":  o_sharpe,
            "weights": {t: float(opt_w[i]) for i, t in enumerate(tickers)},
        }
    else:
        logger.warning("Max-Sharpe portfolio could not be computed")

    return {
        "mc":      mc_rows,
        "current": {
            "vol": cur_vol, "ret": cur_ret, "sharpe": cur_sharpe,
            "weights": {t: float(w_cur[i]) for i, t in enumerate(tickers)},
        },
        "assets":  assets,
        "optimal": optimal,
    }


def rebalancing_suggestions(holdings: list[dict], frontier: dict,
                             nav_eur: float) -> list[dict]:
    """
    Compare current weights to max-Sharpe optimal weights and generate
    3-tier rebalancing suggestions.

    Tier 1 — Act:   |delta| >= 5%, no Irish CGT obstacle
    Tier 2 — Watch: 2% <= |delta| < 5%, or reducing a winner (Irish CGT 33% applies)
    Tier 3 — Hold:  |delta| < 2% (trade cost likely exceeds benefit)
    """
    if not frontier or not frontier.get("optimal"):
        return []

    opt_w = frontier["optimal"]["weights"]
    h_map = {h["ticker"]: h for h in holdings}
    rows  = []

    for ticker, ow in opt_w.items():
        h   = h_map.get(ticker)
        if h is None:
            continue
        cw    = h.get("weight", 0.0)
        delta = ow - cw
        absd  = abs(delta)
        # Irish CGT flag (33%): reducing a position with unrealised gains
        cgt_flag = (delta < 0) and (h.get("unrealised_pnl_eur", 0) > 0)

        if absd < 0.02:
            tier, action, action_zh = 3, "Hold",  "维持"
        elif cgt_flag or absd < 0.05:
            tier, action, action_zh = 2, "Watch", "观察"
        else:
            tier, action, action_zh = 1, "Act",   "立即行动"

        rows.append({
            "ticker":      ticker,
            "cur_weight":  cw,
            "opt_weight":  ow,
            "delta":       delta,
            "eur_change":  absd * nav_eur,
            "tier":        tier,
            "action":      action,
            "action_zh":   action_zh,
            "direction":   "▲ Add" if delta > 0 else "▼ Cut",
            "direction_zh":"加仓"  if delta > 0 else "减仓",
            "cgt_flag":    cgt_flag,
        })

    return sorted(rows, key=lambda x: (x["tier"], -abs(x["delta"])))
