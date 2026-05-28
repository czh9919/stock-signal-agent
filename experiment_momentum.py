"""
One-off experiment — momentum-factor evaluation (does NOT touch production code).

Compares the 5-factor (FF5) vs 6-factor (FF5 + Carhart momentum) specifications
on the SAME aligned sample. v2 hardens the methodology after the first pass showed
only noise-level differences — the suspicion (correctly) was that the test itself
was too thin: only ~8 months were actually evaluated and IC-on-alpha is structurally
insensitive to a priced factor's contribution.

v2 changes:
  - History extended to ~2010 (multi-regime) via explicit Ken French `start`
    (production fetch_ff5 defaults to only the last ~5 years).
  - IC evaluation grid widened: n_periods 8 → ~45, so evaluation walks back across
    ~4 years instead of only the last 8 months.
  - Universe expanded to watchlist + up to 100 cross-sector S&P 500 names.
  - NEW direct test of "does momentum belong in the model": per-stock ΔR² (6f vs 5f)
    and the fraction of stocks whose momentum loading is significant (|t_Mom| > 2).
    This is the natural lens — IC-on-alpha can stay flat even when momentum is priced,
    because adding it strips momentum OUT of alpha.
  - Verdict tightened: requires the direct coefficient/ΔR² evidence (primary), with
    IC only counted when the baseline IC is itself significant.

Metrics reuse production cross-sectional functions where applicable so numbers are
comparable to the live pipeline:
  - Cross-sectional IC      ic_analysis.compute_universe_ic  (toggling FACTORS)
  - Alpha-sign hit rate     ic_analysis.compute_alpha_decay
  - Top-K signal turnover   local loop, reuses ic_analysis._roll_alpha
  - ΔR² / |t_Mom|           local OLS (self-contained, no production internals)

Caches Ken French data to EXPERIMENT-ONLY files (cache/exp_*.pkl) so the production
FF5 cache (cache/ff5_daily.pkl, used by the live 5-year pipeline) is never clobbered.

Run:
  python experiment_momentum.py
  python experiment_momentum.py --max-sp500 120 --nperiods 45 --topk 8
"""
import argparse
import csv
import datetime as dt
import logging
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import strategy.ic_analysis as ica
from data.spy_universe import load_universe

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("experiment_momentum")

FF5 = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
FF6 = FF5 + ["Mom"]

_FF5_EXP_CACHE = Path("cache/exp_ff5_full.pkl")
_MOM_EXP_CACHE = Path("cache/exp_mom_full.pkl")


def _tz_naive(idx: pd.Index) -> pd.Index:
    return idx.tz_localize(None) if idx.tz is not None else idx


def _kf_fetch(dataset: str, cache: Path, start: dt.datetime) -> pd.DataFrame | None:
    """Generic Ken French daily fetch with explicit start; cached 20 h (experiment-only)."""
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 72000:
        try:
            with open(cache, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    try:
        import pandas_datareader.data as web
        raw = web.DataReader(dataset, "famafrench", start=start)[0]
        raw.columns = [c.strip() for c in raw.columns]
        df = raw / 100.0
        df.index = _tz_naive(df.index)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(cache, "wb") as fh:
            pickle.dump(df, fh)
        logger.info("%s: %d rows (%s – %s)", dataset, len(df),
                    df.index[0].date(), df.index[-1].date())
        return df
    except Exception as exc:
        logger.error("%s fetch failed: %s", dataset, exc)
        return None


def fetch_ff5_full(start: dt.datetime) -> pd.DataFrame | None:
    return _kf_fetch("F-F_Research_Data_5_Factors_2x3_daily", _FF5_EXP_CACHE, start)


def fetch_momentum_full(start: dt.datetime) -> pd.DataFrame | None:
    df = _kf_fetch("F-F_Momentum_Factor_daily", _MOM_EXP_CACHE, start)
    if df is None:
        return None
    col = "Mom" if "Mom" in df.columns else df.columns[0]
    return df[[col]].rename(columns={col: "Mom"})


def load_watchlist_equities(path: str = "config/watchlist.csv") -> list[str]:
    out: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("asset_class") or "").strip() == "equity":
                t = (row.get("ticker") or "").strip()
                if t and t not in ("SPY", "QQQ"):   # index ETFs have ~zero alpha dispersion
                    out.append(t)
    return out


# ── Metric 1: cross-sectional IC + Metric 2: hit rate (production functions) ──────

def _hit_rate_at(price_data: dict, ff_df: pd.DataFrame, horizon: int = 21) -> tuple[float, int]:
    rates = []
    for tk, pdo in price_data.items():
        rets = getattr(pdo, "returns", None)
        if rets is None:
            continue
        d = ica.compute_alpha_decay(tk, rets, ff_df, horizons=(horizon,))
        if d and d["decay"].get(horizon) is not None:
            rates.append(d["decay"][horizon])
    return (float(np.mean(rates)) if rates else float("nan"), len(rates))


# ── Metric 3: top-K signal turnover (mirrors the IC eval grid) ────────────────────

def _topk_turnover(price_data: dict, ff_df: pd.DataFrame, k: int,
                   roll_train: int, forward: int, n_periods: int) -> tuple[float, int]:
    avail = [f for f in ica.FACTORS if f in ff_df.columns]
    aligned: dict[str, pd.DataFrame] = {}
    for tk, pdo in price_data.items():
        rets = getattr(pdo, "returns", None)
        if rets is None:
            continue
        r = rets.copy()
        r.index = _tz_naive(r.index)
        m = r.to_frame("ret").join(ff_df[avail + ["RF"]], how="inner")
        if len(m) >= roll_train + forward + 5:
            aligned[tk] = m
    if len(aligned) < k + 1:
        return float("nan"), 0

    # reference index = the longest available series (avoids a short stock truncating grid)
    ref_idx = max((d.index for d in aligned.values()), key=len)
    if len(ref_idx) < roll_train + n_periods * forward:
        return float("nan"), 0
    positions = [len(ref_idx) - (n_periods - j) * forward for j in range(n_periods)]
    positions = [p for p in positions if p >= roll_train and p + forward <= len(ref_idx)]

    prev_set: set | None = None
    turnovers: list[float] = []
    for pos in positions:
        eval_date = ref_idx[pos]
        preds: dict[str, float] = {}
        for tk, df in aligned.items():
            loc = df.index.searchsorted(eval_date)
            if loc < roll_train or loc + forward > len(df):
                continue
            a = ica._roll_alpha(df.iloc[loc - roll_train:loc])
            if a is not None:
                preds[tk] = a
        if len(preds) < k:
            continue
        topk = set(sorted(preds, key=preds.get, reverse=True)[:k])
        if prev_set is not None:
            turnovers.append(1.0 - len(topk & prev_set) / k)
        prev_set = topk
    return (float(np.mean(turnovers)) if turnovers else float("nan"), len(turnovers))


# ── Metric 4 (NEW): direct "does momentum belong" test — ΔR² and |t_Mom| ─────────

def _ols_r2_and_t(y: np.ndarray, X: np.ndarray) -> tuple[float, np.ndarray]:
    """Return (R², t-stats) for OLS y ~ X (X already includes an intercept column)."""
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    n, kk = X.shape
    dof = n - kk
    sigma2 = float(resid @ resid) / dof if dof > 0 else float("nan")
    se = np.sqrt(np.diag(sigma2 * XtX_inv))
    tstat = beta / se
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - rss / tss if tss > 1e-12 else float("nan")
    return r2, tstat


def momentum_significance(price_data: dict, ff6: pd.DataFrame, min_obs: int = 252) -> dict:
    """
    Per-stock full-sample OLS: regress excess return on FF5 vs FF6.
    Reports mean ΔR² (6f − 5f) and the fraction of stocks with a significant
    momentum loading (|t_Mom| > 2). This is the direct test of whether the
    momentum factor *belongs* in the model — independent of the alpha-IC lens.
    """
    dr2, t_moms = [], []
    for tk, pdo in price_data.items():
        rets = getattr(pdo, "returns", None)
        if rets is None:
            continue
        r = rets.copy()
        r.index = _tz_naive(r.index)
        m = r.to_frame("ret").join(ff6[FF6 + ["RF"]], how="inner").dropna()
        if len(m) < min_obs:
            continue
        y = (m["ret"] - m["RF"]).values
        ones = np.ones(len(m))
        X5 = np.column_stack([ones, m[FF5].values])
        X6 = np.column_stack([ones, m[FF6].values])   # Mom is last column
        try:
            r2_5, _ = _ols_r2_and_t(y, X5)
            r2_6, t6 = _ols_r2_and_t(y, X6)
        except np.linalg.LinAlgError:
            continue
        if r2_5 == r2_5 and r2_6 == r2_6:
            dr2.append(r2_6 - r2_5)
            t_moms.append(abs(float(t6[-1])))
    n = len(dr2)
    return {
        "n":             n,
        "mean_dr2":      float(np.mean(dr2)) if n else float("nan"),
        "median_dr2":    float(np.median(dr2)) if n else float("nan"),
        "frac_t_gt2":    float(np.mean([t > 2 for t in t_moms])) if n else float("nan"),
        "mean_abs_tmom": float(np.mean(t_moms)) if n else float("nan"),
    }


def run_ic_spec(label: str, factors: list[str], price_data: dict, ff_df: pd.DataFrame,
                k: int, roll_train: int, forward: int, n_periods: int) -> dict:
    orig = ica.FACTORS
    ica.FACTORS = factors
    try:
        ic = ica.compute_universe_ic(price_data, ff_df, roll_train=roll_train,
                                     forward=forward, n_periods=n_periods) or {}
        hit, n_hit = _hit_rate_at(price_data, ff_df, 21)
        turn, _ = _topk_turnover(price_data, ff_df, k, roll_train, forward, n_periods)
    finally:
        ica.FACTORS = orig
    return {
        "label": label, "ic_mean": ic.get("ic_mean", float("nan")),
        "icir": ic.get("icir", float("nan")), "pvalue": ic.get("pvalue", float("nan")),
        "ic_nobs": ic.get("n_obs", 0), "hit": hit, "n_hit": n_hit, "turnover": turn,
    }


def _fmt(x: float, pct: bool = False) -> str:
    if x != x:
        return "   n/a"
    return f"{x*100:7.1f}%" if pct else f"{x:7.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--max-sp500", type=int, default=100)
    ap.add_argument("--nperiods", type=int, default=45)
    ap.add_argument("--forward", type=int, default=21)
    ap.add_argument("--roll-train", type=int, default=126)
    ap.add_argument("--start", default="2009-01-01", help="Ken French history start")
    args = ap.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m-%d")
    # trading days needed to cover [start, now] with a margin for the eval grid
    days = int((dt.datetime.now() - start).days / 365.25 * 252) + 60

    watch = load_watchlist_equities()
    print(f"Loading universe: {len(watch)} watchlist equities + up to {args.max_sp500} S&P500 "
          f"({args.start} → now, ~{days} trading days) …")
    price_data = load_universe(watch, days=days, max_sp500=args.max_sp500)

    need = args.roll_train + args.nperiods * args.forward
    usable = {t: p for t, p in price_data.items()
              if p is not None and getattr(p, "returns", None) is not None
              and getattr(p, "status", "") in ("ok", "reduced")
              and len(p.returns) >= need}
    print(f"Usable series with ≥{need}d history: {len(usable)}")
    if len(usable) < 10:
        print("ERROR: too few usable stocks for a credible cross-section — aborting.")
        return 1

    print("Fetching FF5 + momentum (full history) …")
    ff5 = fetch_ff5_full(start)
    mom = fetch_momentum_full(start)
    if ff5 is None or mom is None:
        print("ERROR: factor data unavailable — aborting.")
        return 1
    ff6 = ff5.join(mom[["Mom"]], how="inner")
    print(f"Factor coverage: FF5 {len(ff5)} rows ({ff5.index[0].date()}–{ff5.index[-1].date()}) | "
          f"FF5∩Mom {len(ff6)} rows (lost {len(ff5) - len(ff6)} to alignment)")

    res5 = run_ic_spec("FF5", FF5, usable, ff6, args.topk, args.roll_train, args.forward, args.nperiods)
    res6 = run_ic_spec("FF6", FF6, usable, ff6, args.topk, args.roll_train, args.forward, args.nperiods)
    sig  = momentum_significance(usable, ff6)

    print("\n" + "=" * 66)
    print(f"  5-factor vs 6-factor   ({res5['ic_nobs']} IC windows, ~{args.nperiods*args.forward}d span)")
    print("=" * 66)
    print(f"  {'Metric':<24}{'FF5':>11}{'FF6':>11}{'Δ':>13}")
    print(f"  {'-'*24}{'-'*11}{'-'*11}{'-'*13}")

    def row(name, key, pct=False, higher_better=True):
        a, b = res5[key], res6[key]
        d = (b - a) if (a == a and b == b) else float("nan")
        arrow = ""
        if d == d and abs(d) > 1e-9:
            arrow = "  ↑better" if (d > 0) == higher_better else "  ↓worse"
        print(f"  {name:<24}{_fmt(a, pct):>11}{_fmt(b, pct):>11}{_fmt(d, pct):>13}{arrow}")

    row("Cross-sec IC mean", "ic_mean")
    row("IC-IR (ann.)", "icir")
    row("IC p-value", "pvalue", higher_better=False)
    row("Hit rate @21d", "hit", pct=True)
    row(f"Top-{args.topk} turnover", "turnover", pct=True, higher_better=False)
    print("=" * 66)
    print("  DIRECT TEST — does momentum belong in the model?")
    print(f"    stocks tested (≥252d):      {sig['n']}")
    print(f"    mean ΔR² (6f − 5f):         {_fmt(sig['mean_dr2'], pct=True)}")
    print(f"    median ΔR²:                 {_fmt(sig['median_dr2'], pct=True)}")
    print(f"    mean |t_Mom|:               {_fmt(sig['mean_abs_tmom'])}")
    print(f"    stocks with |t_Mom| > 2:    {_fmt(sig['frac_t_gt2'], pct=True)}")
    print("=" * 66)

    # ── Tightened verdict ─────────────────────────────────────────────────────
    baseline_ic_sig = (res5["pvalue"] == res5["pvalue"]) and res5["pvalue"] < 0.05
    ic_helps = baseline_ic_sig and (res6["ic_mean"] - res5["ic_mean"] > 0.01)
    coef_strong = (sig["frac_t_gt2"] == sig["frac_t_gt2"]) and sig["frac_t_gt2"] > 0.30 \
                  and (sig["mean_dr2"] == sig["mean_dr2"]) and sig["mean_dr2"] > 0.005
    turn_blowup = (res6["turnover"] == res6["turnover"] and res5["turnover"] == res5["turnover"]
                   and res6["turnover"] > res5["turnover"] * 1.5)

    print("\n  VERDICT")
    if coef_strong and not turn_blowup:
        print("  → Momentum is broadly priced (significant loadings + real ΔR²) and")
        print("    turnover is contained. Integration is justified.")
        if not baseline_ic_sig:
            print("    (Note: alpha-IC lens stays flat by construction — that's expected,")
            print("     not a counter-argument; the coefficient test is the right one.)")
    elif coef_strong and turn_blowup:
        print("  → Momentum is priced BUT turnover rises >50%. Integrate only with")
        print("    trading-cost controls / signal smoothing.")
    elif ic_helps:
        print("  → Weak case: alpha-IC improves on a significant baseline, but the")
        print("    direct coefficient/ΔR² test is not strong. Treat as marginal.")
    else:
        print("  → Evidence still does NOT justify integration: momentum loadings are")
        print("    not broadly significant and ΔR² is negligible on this universe.")
    print("  (Reminder: survivorship bias — current S&P 500 members only.)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
