"""
Monte Carlo portfolio simulator.

run_mc_portfolio(holdings, price_data, ...) → dict

Buy-and-hold over `horizon` trading days on `n_paths` paths.
Applies at horizon-end:
  - Trading costs:  commission + half-spread + √ market-impact
  - Irish CGT 33%:  on realised gains above €1,270 exemption
  - Margin monitor: IBKR RegT maintenance-margin breach flag

Returns:
  tail        VaR/CVaR/drawdown/loss-probability metrics
  decision    Sharpe, Sortino, Calmar, win_rate
  nav_paths   (n_paths, horizon+1) EUR NAV trajectories
  terminal_pnl (n_paths,) post-cost P&L in EUR
  costs       {'trading_eur', 'cgt_median_eur'}
  tickers     assets included in simulation
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from risk.mc_engine import simulate_paths
from risk.optimizer import ff_implied_mu

logger = logging.getLogger(__name__)

_DEFAULTS: dict = {
    # Trading costs (full liquidation at horizon end)
    "commission_eur":    5.0,       # fixed per trade leg, EUR
    "spread_bps":        5,         # half-spread in basis points
    "impact_coeff":      0.10,      # κ in √(trade/ADV) market-impact model
    "adv_proxy_eur":     500_000.0, # assumed ADV when volume unknown

    # Irish CGT on disposal gains
    "cgt_rate":          0.33,
    "cgt_exemption_eur": 1_270.0,

    # IBKR RegT margin monitoring
    "margin_maintenance": 0.25,     # maintenance equity ratio
    "margin_leverage":    0.50,     # assumed fraction of IBKR position on margin

    # Simulation defaults (overridden by caller)
    "n_paths":  5000,
    "horizon":  21,
    "rf":       0.035,
    "model":    "hawkes",
}


def run_mc_portfolio(
    holdings:   list[dict],
    price_data: dict,
    config:     Optional[dict] = None,
    n_paths:    int   = 5000,
    horizon:    int   = 21,
    rf:         float = 0.035,
) -> dict:
    """
    Full buy-and-hold Monte Carlo simulation with cost/tax/margin overlay.

    Parameters
    ----------
    holdings   : list of holding dicts — must include market_value_eur,
                 cost_basis_eur, weight, ticker, asset_class, platform
    price_data : dict  ticker → PriceData (with .returns Series)
    config     : cost/tax/margin parameter overrides
    n_paths    : simulation paths
    horizon    : trading-day forecast horizon
    rf         : risk-free rate (annualised)

    Returns
    -------
    dict or {} if fewer than 2 equity holdings have price data
    """
    cfg = {
        **_DEFAULTS,
        "n_paths": n_paths, "horizon": horizon, "rf": rf,
        **(config or {}),
    }

    # Filter: equity holdings with price return series
    valid = [
        h for h in holdings
        if h.get("asset_class", "equity") == "equity"
        and price_data.get(h["ticker"]) is not None
        and getattr(price_data[h["ticker"]], "returns", None) is not None
    ]
    if len(valid) < 2:
        logger.warning("MC portfolio: fewer than 2 equity positions with data — skipping")
        return {}

    tickers = [h["ticker"] for h in valid]
    mv_eur  = np.array([h.get("market_value_eur", 0.0) for h in valid], dtype=float)
    nav0    = float(mv_eur.sum())
    if nav0 <= 0:
        return {}

    w0 = mv_eur / nav0   # static portfolio weights

    # ── Build aligned returns DataFrame ──────────────────────────────────────
    ret_df = pd.concat(
        {t: price_data[t].returns for t in tickers}, axis=1
    ).dropna()
    if len(ret_df) < 60:
        logger.warning(f"MC portfolio: only {len(ret_df)} aligned days — skipping")
        return {}

    mu = ff_implied_mu(ret_df, cfg["rf"])

    # ── Generate price paths ──────────────────────────────────────────────────
    logger.info(
        f"MC simulation: {n_paths} paths × {horizon} days  "
        f"model={cfg['model']}  assets={len(tickers)}"
    )
    paths = simulate_paths(ret_df, mu, n_paths=n_paths, horizon=horizon,
                           model=cfg["model"])
    # paths: (n_paths, horizon, n_assets) — daily decimal returns

    # ── Buy-and-hold NAV paths ────────────────────────────────────────────────
    port_ret   = (paths * w0).sum(axis=2)           # (n_paths, horizon)
    cum_factor = np.cumprod(1.0 + port_ret, axis=1) # (n_paths, horizon)
    nav_paths  = np.empty((n_paths, horizon + 1), dtype=float)
    nav_paths[:, 0]  = nav0
    nav_paths[:, 1:] = nav0 * cum_factor            # (n_paths, horizon+1)

    # ── Overlay: liquidation costs at horizon end ─────────────────────────────
    terminal_nav = nav_paths[:, -1].copy()

    tc = _trading_cost(mv_eur, cfg)
    terminal_nav -= tc
    logger.debug(f"MC: liquidation trading cost = €{tc:,.0f}")

    cgt = _irish_cgt(valid, nav_paths, cfg)    # (n_paths,)
    terminal_nav -= cgt

    # ── Margin monitoring (path-level breach flag) ────────────────────────────
    ibkr_mv = float(sum(
        h.get("market_value_eur", 0.0) for h in valid
        if h.get("platform") == "IBKR"
    ))
    margin_breach = _margin_monitor(nav_paths, ibkr_mv, nav0, cfg)

    # ── Compute metrics ───────────────────────────────────────────────────────
    terminal_pnl = terminal_nav - nav0
    tail     = _tail_metrics(terminal_pnl, nav0, nav_paths, margin_breach, horizon)
    decision = _decision_metrics(port_ret, nav_paths, cfg["rf"], horizon)

    return {
        "nav_paths":     nav_paths,
        "terminal_pnl":  terminal_pnl,
        "tail":          tail,
        "decision":      decision,
        "costs": {
            "trading_eur":    float(tc),
            "cgt_median_eur": float(np.median(cgt)),
        },
        "tickers": tickers,
    }


# ── Cost models ───────────────────────────────────────────────────────────────

def _trading_cost(mv_eur: np.ndarray, cfg: dict) -> float:
    """Commission + half-spread + √ market-impact for full liquidation."""
    total = 0.0
    for mv in mv_eur:
        if mv <= 0:
            continue
        commission = cfg["commission_eur"]
        spread     = mv * cfg["spread_bps"] / 10_000.0
        # Square-root market-impact: κ · σ̄ · √(trade / ADV), σ̄ ≈ 1.5%
        impact = mv * cfg["impact_coeff"] * 0.015 * np.sqrt(mv / cfg["adv_proxy_eur"])
        total += commission + spread + impact
    return float(total)


def _irish_cgt(valid: list[dict], nav_paths: np.ndarray, cfg: dict) -> np.ndarray:
    """
    Path-dependent Irish CGT at liquidation.

    Realised gain per path = terminal NAV − total cost basis.
    Tax = 33% × max(0, gain − €1,270 annual exemption).
    """
    total_cost = float(sum(
        h.get("cost_basis_eur", h.get("market_value_eur", 0.0)) for h in valid
    ))
    terminal_nav  = nav_paths[:, -1]            # (n_paths,)
    realized_gain = terminal_nav - total_cost
    net_gain      = np.maximum(0.0, realized_gain - cfg["cgt_exemption_eur"])
    return net_gain * cfg["cgt_rate"]


def _margin_monitor(nav_paths: np.ndarray, ibkr_mv: float,
                    nav0: float, cfg: dict) -> np.ndarray:
    """
    Boolean array (n_paths,): True if IBKR maintenance margin breached on any day.
    Approximates IBKR sub-portfolio NAV by scaling total portfolio NAV proportionally.
    """
    if ibkr_mv <= 0 or nav0 <= 0:
        return np.zeros(nav_paths.shape[0], dtype=bool)

    ibkr_frac = ibkr_mv / nav0
    ibkr_nav  = nav_paths * ibkr_frac           # (n_paths, horizon+1)
    borrowed  = ibkr_mv * cfg["margin_leverage"]
    equity    = ibkr_nav - borrowed

    with np.errstate(divide="ignore", invalid="ignore"):
        eq_ratio = np.where(ibkr_nav > 0, equity / ibkr_nav, 1.0)

    return (eq_ratio < cfg["margin_maintenance"]).any(axis=1)


# ── Tail-risk metrics ─────────────────────────────────────────────────────────

def _tail_metrics(terminal_pnl: np.ndarray, nav0: float,
                  nav_paths: np.ndarray, margin_breach: np.ndarray,
                  horizon: int) -> dict:
    pnl_pct = terminal_pnl / nav0   # decimal fraction

    def _var_eur(alpha: float) -> float:
        return float(-np.percentile(terminal_pnl, alpha * 100))

    def _cvar_eur(alpha: float) -> float:
        threshold = np.percentile(terminal_pnl, alpha * 100)
        tail = terminal_pnl[terminal_pnl <= threshold]
        return float(-tail.mean()) if len(tail) > 0 else _var_eur(alpha)

    peak  = np.maximum.accumulate(nav_paths, axis=1)
    dd    = (nav_paths - peak) / np.maximum(peak, 1.0)
    max_dd_per_path = dd.min(axis=1)   # most negative value per path

    losses = terminal_pnl[terminal_pnl < 0]

    var_95_eur  = _var_eur(0.05)
    var_99_eur  = _var_eur(0.01)
    cvar_95_eur = _cvar_eur(0.05)
    cvar_99_eur = _cvar_eur(0.01)

    return {
        # EUR amounts (positive = loss)
        "var_95_eur":      var_95_eur,
        "var_99_eur":      var_99_eur,
        "cvar_95_eur":     cvar_95_eur,
        "cvar_99_eur":     cvar_99_eur,
        "expected_loss_eur": float(-losses.mean()) if len(losses) > 0 else 0.0,
        # Fraction of NAV (positive = loss)
        "var_95_pct":      var_95_eur  / nav0 if nav0 else 0.0,
        "var_99_pct":      var_99_eur  / nav0 if nav0 else 0.0,
        "cvar_95_pct":     cvar_95_eur / nav0 if nav0 else 0.0,
        "cvar_99_pct":     cvar_99_eur / nav0 if nav0 else 0.0,
        # Drawdown (positive = loss fraction)
        "max_dd_mean":     float(-max_dd_per_path.mean()),
        "max_dd_p95":      float(-np.percentile(max_dd_per_path, 5)),  # worst-5%
        # Loss probabilities
        "prob_loss_5pct":  float((pnl_pct < -0.05).mean()),
        "prob_loss_10pct": float((pnl_pct < -0.10).mean()),
        "prob_loss_20pct": float((pnl_pct < -0.20).mean()),
        # Margin
        "margin_call_prob": float(margin_breach.mean()),
        # Metadata
        "n_paths":     int(len(terminal_pnl)),
        "horizon_days": int(horizon),
    }


# ── Decision-quality metrics ──────────────────────────────────────────────────

def _decision_metrics(port_ret: np.ndarray, nav_paths: np.ndarray,
                      rf: float, horizon: int) -> dict:
    """Aggregate path-level Sharpe, Sortino, Calmar, win_rate."""
    mean_d  = port_ret.mean(axis=1)         # (n_paths,)
    std_d   = port_ret.std(axis=1)
    neg_d   = np.where(port_ret < 0, port_ret, 0.0)
    dstd_d  = np.sqrt((neg_d ** 2).mean(axis=1))

    ann_ret = mean_d * 252.0
    ann_vol = std_d  * np.sqrt(252.0)
    rf_d    = rf / 252.0

    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe  = np.where(ann_vol > 1e-9, (ann_ret - rf) / ann_vol, 0.0)
        sortino = np.where(
            dstd_d * np.sqrt(252.0) > 1e-9,
            (ann_ret - rf) / (dstd_d * np.sqrt(252.0)),
            0.0,
        )

    peak   = np.maximum.accumulate(nav_paths, axis=1)
    dd     = (nav_paths - peak) / np.maximum(peak, 1.0)
    max_dd = -dd.min(axis=1)

    # Horizon-annualised return for Calmar
    ann_ret_h = (nav_paths[:, -1] / nav_paths[:, 0] - 1.0) * (252.0 / horizon)
    with np.errstate(divide="ignore", invalid="ignore"):
        calmar = np.where(max_dd > 1e-6, ann_ret_h / max_dd, 0.0)

    win_rate = float((nav_paths[:, -1] > nav_paths[:, 0]).mean())

    return {
        "sharpe_median":  float(np.median(sharpe)),
        "sharpe_p10":     float(np.percentile(sharpe, 10)),
        "sortino_median": float(np.median(sortino)),
        "calmar_median":  float(np.median(calmar)),
        "win_rate":       win_rate,
        "ann_ret_median": float(np.median(ann_ret)),
        "ann_vol_median": float(np.median(ann_vol)),
    }
