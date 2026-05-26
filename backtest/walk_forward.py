"""
Walk-forward validation: FF5-implied μ vs historical-mean μ in Markowitz.

For each rolling window:
  1. Fit FF5 betas on training slice  →  μ_ff5
  2. Compute historical mean           →  μ_hist  (baseline)
  3. Solve max-Sharpe portfolio with each μ (same covariance matrix)
  4. Measure out-of-sample Sharpe, max drawdown, turnover

Usage:
    from data.spy_universe import load_universe
    from backtest.walk_forward import run_walk_forward

    price_data = load_universe(watchlist_tickers, days=1260)
    results    = run_walk_forward(price_data, rf=0.035)
    print_summary(results["summary"])
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from risk.optimizer import _max_sharpe, ff_implied_mu   # _max_sharpe: internal use OK

logger = logging.getLogger(__name__)


# ── Per-window helpers ────────────────────────────────────────────────────────

def _oos_metrics(weights: np.ndarray, ret_matrix: np.ndarray, rf: float) -> dict:
    """Annualised Sharpe and max drawdown for an out-of-sample test window."""
    port   = ret_matrix @ weights
    ann_r  = float(port.mean() * 252)
    ann_v  = float(port.std() * np.sqrt(252))
    sharpe = (ann_r - rf) / ann_v if ann_v > 1e-9 else 0.0

    cum  = np.cumprod(1 + port)
    peak = np.maximum.accumulate(cum)
    mdd  = float(((cum - peak) / peak).min())

    return {"sharpe": sharpe, "ann_ret": ann_r, "ann_vol": ann_v, "max_dd": mdd}


def _turnover(prev: Optional[np.ndarray], curr: np.ndarray) -> float:
    """One-way turnover: 0.5 × Σ|w_new - w_old|.  First period = 100% buy-in."""
    if prev is None:
        return 1.0
    return float(np.abs(curr - prev).sum() / 2)


def _adaptive_bounds(n: int) -> tuple[float, float]:
    """
    Weight bounds that scale with universe size to keep SLSQP feasible.
    Equal weight (1/n) is always strictly between w_min and w_max.
    """
    w_min = max(0.001, 0.5 / n)
    w_max = min(0.40,  5.0 / n)
    w_max = max(w_max, 2 * w_min)   # guarantee w_max > w_min
    return w_min, w_max


# ── Main walk-forward ─────────────────────────────────────────────────────────

def run_walk_forward(
    price_data: dict,
    rf:          float = 0.035,
    train_days:  int   = 756,    # ~3 years
    test_days:   int   = 63,     # ~1 quarter per step
    min_history: int   = 252,    # min days an asset must have in a training window
) -> dict:
    """
    Compare FF5-implied μ vs historical mean μ in Markowitz across rolling windows.

    Returns:
      windows : list[dict] — per-window metrics
      summary : dict       — means across all windows + Δ(FF5 - hist)
    """
    # ── Survivorship-bias warning ─────────────────────────────────────────────
    # The universe is built from the *current* S&P 500 constituent list, which
    # means companies that were members but later delisted (bankruptcy, M&A, etc.)
    # are never fetched.  This overstates backtest returns vs live trading.
    # A point-in-time constituent database (CRSP/Compustat) would be required
    # to eliminate this bias entirely.
    logger.warning(
        "SURVIVORSHIP BIAS: universe contains only current S&P 500 members — "
        "delisted/bankrupt stocks are excluded, OOS returns will be inflated."
    )

    # ── Build aligned return matrix ───────────────────────────────────────────
    valid: dict[str, pd.Series] = {}
    for ticker, pd_obj in price_data.items():
        if pd_obj and pd_obj.status in ("ok", "reduced") and pd_obj.returns is not None:
            valid[ticker] = pd_obj.returns

    if len(valid) < 5:
        logger.warning("Walk-forward: fewer than 5 usable assets — aborting")
        return {}

    # Keep all dates — do NOT dropna globally.  A single recently-listed stock
    # would otherwise truncate the entire matrix to its IPO date.
    # Per-window asset selection (below) handles date alignment safely.
    ret_df = pd.concat(valid, axis=1)
    T      = len(ret_df)
    logger.info(
        f"Walk-forward universe: {len(ret_df.columns)} assets, {T} days "
        f"({ret_df.index[0].date()} – {ret_df.index[-1].date()})"
    )

    if T < train_days + test_days:
        logger.warning(f"Walk-forward: {T} days available, need {train_days + test_days} — aborting")
        return {}

    # ── Rolling windows ───────────────────────────────────────────────────────
    windows:      list[dict]            = []
    prev_w_ff5:   Optional[np.ndarray]  = None
    prev_w_hist:  Optional[np.ndarray]  = None

    starts = range(train_days, T - test_days + 1, test_days)

    for pos in starts:
        train_raw = ret_df.iloc[pos - train_days : pos]
        test_raw  = ret_df.iloc[pos             : pos + test_days]

        # Include only assets that were trading from the start of this training
        # window (first 5 rows have data) AND have sufficient history.
        # This prevents a recently-listed stock from truncating train.dropna()
        # and silently shrinking the covariance estimation window.
        good = [
            c for c in train_raw.columns
            if train_raw[c].count() >= min_history
            and not train_raw[c].iloc[:5].isna().all()
        ]
        if len(good) < 5:
            logger.debug(f"Window @{pos}: only {len(good)} assets with {min_history}d history — skipping")
            continue

        train = train_raw[good].dropna()
        # Test: carry forward last known price (handles intra-period halts);
        # assets delisted during the test window retain their last return then 0.
        test  = test_raw[good].ffill().fillna(0.0)
        n     = len(good)

        cov      = train.cov().values * 252
        w_bounds = _adaptive_bounds(n)
        ew       = np.ones(n) / n          # equal-weight fallback

        # FF5 path
        mu_ff5  = ff_implied_mu(train, rf)
        w_ff5   = _max_sharpe(mu_ff5, cov, rf, w_bounds=w_bounds) or ew

        # Historical mean baseline
        mu_hist = train.mean().values * 252
        w_hist  = _max_sharpe(mu_hist, cov, rf, w_bounds=w_bounds) or ew

        m_ff5  = _oos_metrics(w_ff5,  test.values, rf)
        m_hist = _oos_metrics(w_hist, test.values, rf)

        windows.append({
            "train_start":    train.index[0].date().isoformat(),
            "train_end":      train.index[-1].date().isoformat(),
            "test_start":     test.index[0].date().isoformat(),
            "test_end":       test.index[-1].date().isoformat(),
            "n_assets":       n,
            # FF5 path
            "ff5_sharpe":     m_ff5["sharpe"],
            "ff5_ann_ret":    m_ff5["ann_ret"],
            "ff5_ann_vol":    m_ff5["ann_vol"],
            "ff5_max_dd":     m_ff5["max_dd"],
            "ff5_turnover":   _turnover(prev_w_ff5,  w_ff5),
            # Historical baseline
            "hist_sharpe":    m_hist["sharpe"],
            "hist_ann_ret":   m_hist["ann_ret"],
            "hist_ann_vol":   m_hist["ann_vol"],
            "hist_max_dd":    m_hist["max_dd"],
            "hist_turnover":  _turnover(prev_w_hist, w_hist),
        })

        prev_w_ff5  = w_ff5
        prev_w_hist = w_hist

    if not windows:
        logger.warning("Walk-forward: no windows completed")
        return {}

    # ── Summary ───────────────────────────────────────────────────────────────
    def _mean(key: str) -> float:
        return float(np.mean([w[key] for w in windows]))

    summary = {
        "n_windows":           len(windows),
        "ff5_mean_sharpe":     _mean("ff5_sharpe"),
        "hist_mean_sharpe":    _mean("hist_sharpe"),
        "delta_sharpe":        _mean("ff5_sharpe")   - _mean("hist_sharpe"),
        "ff5_mean_max_dd":     _mean("ff5_max_dd"),
        "hist_mean_max_dd":    _mean("hist_max_dd"),
        "delta_max_dd":        _mean("ff5_max_dd")   - _mean("hist_max_dd"),
        "ff5_mean_turnover":   _mean("ff5_turnover"),
        "hist_mean_turnover":  _mean("hist_turnover"),
        "ff5_win_rate":        float(sum(
            w["ff5_sharpe"] > w["hist_sharpe"] for w in windows
        ) / len(windows)),
        # Bias flags — consumers should display these prominently
        "survivorship_bias":   True,   # universe = current S&P 500 members only
        "lookahead_free":      True,   # FF5 factor premiums restricted to training window
    }

    logger.info(
        f"Walk-forward: {len(windows)} windows | "
        f"FF5 Sharpe {summary['ff5_mean_sharpe']:.2f} vs "
        f"Hist {summary['hist_mean_sharpe']:.2f}  "
        f"Δ={summary['delta_sharpe']:+.2f}  "
        f"FF5 wins {summary['ff5_win_rate']*100:.0f}% of windows"
    )
    return {"windows": windows, "summary": summary}


# ── Pretty printer ────────────────────────────────────────────────────────────

def print_summary(summary: dict):
    if not summary:
        print("No walk-forward results.")
        return
    w = summary
    print("\n" + "=" * 58)
    print(f"  Walk-Forward Results  ({w['n_windows']} windows)")
    print("=" * 58)
    print(f"  {'Metric':<22}  {'FF5-μ':>8}  {'Hist-μ':>8}  {'Δ':>7}")
    print(f"  {'-'*22}  {'-'*8}  {'-'*8}  {'-'*7}")
    print(f"  {'Mean OOS Sharpe':<22}  {w['ff5_mean_sharpe']:>8.2f}  "
          f"{w['hist_mean_sharpe']:>8.2f}  {w['delta_sharpe']:>+7.2f}")
    print(f"  {'Mean Max Drawdown':<22}  {w['ff5_mean_max_dd']:>7.1%}  "
          f"{w['hist_mean_max_dd']:>7.1%}  {w['delta_max_dd']:>+6.1%}")
    print(f"  {'Mean Turnover':<22}  {w['ff5_mean_turnover']:>7.1%}  "
          f"{w['hist_mean_turnover']:>7.1%}")
    print(f"  {'FF5 wins':<22}  {w['ff5_win_rate']:>7.0%} of windows")
    print("=" * 58)
    if w.get("survivorship_bias"):
        print("  [!] SURVIVORSHIP BIAS: universe = current S&P 500 only.")
        print("      Delisted/bankrupt stocks excluded — returns overstated.")
    if w.get("lookahead_free"):
        print("  [OK] No look-ahead: FF5 premiums restricted to training window.")
    print()
