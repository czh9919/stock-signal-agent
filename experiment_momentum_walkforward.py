"""
One-off experiment — FF5-μ vs FF6-μ walk-forward (does NOT touch production code).

The decisive economic test. Prior experiments measured *prediction/explanation*
(IC, ΔR², t_Mom). This one measures *portfolio P&L*: does feeding a momentum-augmented
expected-return vector into the Markowitz optimiser actually improve out-of-sample
Sharpe / drawdown — the only thing that matters for a portfolio tool?

Method (mirrors backtest/walk_forward.py exactly, but swaps the comparison):
  production walk_forward : FF5-μ  vs  historical-mean-μ
  this experiment         : FF5-μ  vs  FF6-μ  (FF5 + Carhart momentum)
Same covariance, same SLSQP bounds, same assets each window — ONLY μ differs, so any
Sharpe gap is attributable to the momentum factor in the expected-return estimate.

No look-ahead: factor premiums are computed only on each training slice (identical to
ff_implied_mu's restriction). Reuses production helpers: _oos_metrics, _turnover,
_adaptive_bounds (walk_forward) and _max_sharpe (optimizer). Factor data + universe are
pulled via the v2 experiment fetchers (same cache → no re-download).

Run:
  python experiment_momentum_walkforward.py
"""
import datetime as dt
import logging
import sys

import numpy as np
import pandas as pd

from backtest.walk_forward import _oos_metrics, _adaptive_bounds
from risk.optimizer import _max_sharpe
from data.spy_universe import load_universe
from experiment_momentum import (
    FF5, FF6, _tz_naive, fetch_ff5_full, fetch_momentum_full, load_watchlist_equities,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("experiment_mom_wf")


def ff_mu(ret_df: pd.DataFrame, ff_df: pd.DataFrame, factors: list[str], rf: float) -> np.ndarray:
    """
    Generalised FF-implied annualised μ — identical math to optimizer.ff_implied_mu
    but parameterised on the factor list (so FF5 path matches production exactly and
    FF6 just appends the momentum column). Premiums restricted to the training slice.
    """
    r = ret_df.copy()
    r.index = _tz_naive(r.index)
    f = ff_df.copy()
    f.index = _tz_naive(f.index)
    aligned = r.join(f[factors + ["RF"]], how="inner").dropna()
    if len(aligned) < 60:
        return ret_df.mean().values * 252
    prem_ann = aligned[factors].mean().values * 252
    X_c = np.column_stack([np.ones(len(aligned)), aligned[factors].values])
    out = np.empty(len(ret_df.columns))
    for i, col in enumerate(ret_df.columns):
        excess = aligned[col].values - aligned["RF"].values
        coeffs, *_ = np.linalg.lstsq(X_c, excess, rcond=None)
        out[i] = rf + coeffs[0] * 252 + coeffs[1:] @ prem_ann
    return out


def _turnover_by_name(prev: dict | None, w: np.ndarray, good: list[str]) -> tuple[float, dict]:
    """One-way turnover aligned by ticker (universe membership changes per window)."""
    cur = dict(zip(good, w))
    if prev is None:
        return 1.0, cur
    keys = set(prev) | set(cur)
    s = sum(abs(cur.get(k, 0.0) - prev.get(k, 0.0)) for k in keys)
    return s / 2.0, cur


def walk_forward_compare(price_data: dict, ff5: pd.DataFrame, ff6: pd.DataFrame,
                         rf: float = 0.035, train_days: int = 756,
                         test_days: int = 63, min_history: int = 252) -> dict:
    valid = {t: p.returns for t, p in price_data.items()
             if p is not None and getattr(p, "status", "") in ("ok", "reduced")
             and getattr(p, "returns", None) is not None}
    if len(valid) < 5:
        return {}

    ret_df = pd.concat(valid, axis=1)
    ret_df.index = _tz_naive(ret_df.index)
    T = len(ret_df)
    print(f"Walk-forward universe: {len(ret_df.columns)} assets, {T} days "
          f"({ret_df.index[0].date()} – {ret_df.index[-1].date()})")
    if T < train_days + test_days:
        print("Insufficient history — aborting.")
        return {}

    windows: list[dict] = []
    prev5 = prev6 = None
    for pos in range(train_days, T - test_days + 1, test_days):
        train_raw = ret_df.iloc[pos - train_days:pos]
        test_raw = ret_df.iloc[pos:pos + test_days]
        good = [c for c in train_raw.columns
                if train_raw[c].count() >= min_history and not train_raw[c].iloc[:5].isna().all()]
        if len(good) < 5:
            continue
        train = train_raw[good].dropna()
        test = test_raw[good].ffill().fillna(0.0)
        n = len(good)

        cov = train.cov().values * 252
        bnds = _adaptive_bounds(n)
        ew = np.ones(n) / n

        w5 = _max_sharpe(ff_mu(train, ff5, FF5, rf), cov, rf, w_bounds=bnds)
        w6 = _max_sharpe(ff_mu(train, ff6, FF6, rf), cov, rf, w_bounds=bnds)
        w5 = ew if w5 is None else w5
        w6 = ew if w6 is None else w6

        m5 = _oos_metrics(w5, test.values, rf)
        m6 = _oos_metrics(w6, test.values, rf)
        turn5, prev5 = _turnover_by_name(prev5, w5, good)
        turn6, prev6 = _turnover_by_name(prev6, w6, good)
        windows.append({
            "ff5_sharpe": m5["sharpe"], "ff5_max_dd": m5["max_dd"], "ff5_turnover": turn5,
            "ff6_sharpe": m6["sharpe"], "ff6_max_dd": m6["max_dd"], "ff6_turnover": turn6,
        })

    if not windows:
        return {}

    def mean(k): return float(np.mean([w[k] for w in windows]))
    return {
        "n_windows": len(windows),
        "ff5_sharpe": mean("ff5_sharpe"), "ff6_sharpe": mean("ff6_sharpe"),
        "ff5_max_dd": mean("ff5_max_dd"), "ff6_max_dd": mean("ff6_max_dd"),
        "ff5_turnover": mean("ff5_turnover"), "ff6_turnover": mean("ff6_turnover"),
        "ff6_win_rate": float(np.mean([w["ff6_sharpe"] > w["ff5_sharpe"] for w in windows])),
    }


def main():
    start = dt.datetime(2009, 1, 1)
    days = int((dt.datetime.now() - start).days / 365.25 * 252) + 60  # match v2 cache key
    watch = load_watchlist_equities()
    print(f"Loading universe (same key as v2 IC experiment → cache hit) …")
    price_data = load_universe(watch, days=days, max_sp500=100)

    ff5 = fetch_ff5_full(start)
    mom = fetch_momentum_full(start)
    if ff5 is None or mom is None:
        print("ERROR: factor data unavailable — aborting.")
        return 1
    ff6 = ff5.join(mom[["Mom"]], how="inner")

    res = walk_forward_compare(price_data, ff5, ff6)
    if not res:
        print("No walk-forward windows completed.")
        return 1

    print("\n" + "=" * 60)
    print(f"  FF5-μ vs FF6-μ  Walk-Forward  ({res['n_windows']} windows)")
    print("=" * 60)
    print(f"  {'Metric':<22}{'FF5-μ':>10}{'FF6-μ':>10}{'Δ':>12}")
    print(f"  {'-'*22}{'-'*10}{'-'*10}{'-'*12}")
    print(f"  {'Mean OOS Sharpe':<22}{res['ff5_sharpe']:>10.3f}{res['ff6_sharpe']:>10.3f}"
          f"{res['ff6_sharpe']-res['ff5_sharpe']:>+12.3f}")
    print(f"  {'Mean Max Drawdown':<22}{res['ff5_max_dd']:>9.1%}{res['ff6_max_dd']:>10.1%}"
          f"{res['ff6_max_dd']-res['ff5_max_dd']:>+11.1%}")
    print(f"  {'Mean Turnover':<22}{res['ff5_turnover']:>9.1%}{res['ff6_turnover']:>10.1%}"
          f"{res['ff6_turnover']-res['ff5_turnover']:>+11.1%}")
    print(f"  {'FF6 wins':<22}{res['ff6_win_rate']:>9.0%} of windows")
    print("=" * 60)

    d_sharpe = res["ff6_sharpe"] - res["ff5_sharpe"]
    turn_blowup = res["ff6_turnover"] > res["ff5_turnover"] * 1.5

    print("\n  VERDICT")
    if d_sharpe > 0.05 and res["ff6_win_rate"] > 0.5 and not turn_blowup:
        print("  → FF6-μ improves OOS Sharpe materially and wins a majority of windows.")
        print("    Economic case confirmed — integrating momentum is justified.")
    elif d_sharpe > 0.05 and turn_blowup:
        print("  → FF6-μ improves Sharpe but turnover blows up >50%; net of costs unclear.")
    elif abs(d_sharpe) <= 0.05:
        print("  → OOS Sharpe is statistically indistinguishable (|Δ| ≤ 0.05). This")
        print("    CONFIRMS the ΔR²≈0.3% finding: momentum is priced but does not move")
        print("    portfolio performance. Integrate only as model hygiene, not for returns.")
    else:
        print("  → FF6-μ is WORSE OOS. Do not integrate momentum into expected returns.")
    print("  (Reminder: survivorship bias — current S&P 500 members only.)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
