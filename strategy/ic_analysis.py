"""
Cross-sectional Information Coefficient (IC) analysis.
  compute_universe_ic()  — rolling IC, IC-IR, p-value
  compute_alpha_decay()  — alpha signal persistence across horizons
"""
import math
import numpy as np
import pandas as pd
from typing import Optional

FACTORS    = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
MIN_STOCKS = 5


def _tz_naive(idx: pd.Index) -> pd.Index:
    return idx.tz_localize(None) if idx.tz is not None else idx


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    n = len(x)
    if n < 3:
        return float("nan")
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    return 1.0 - 6.0 * float(np.sum((rx - ry) ** 2)) / (n * (n * n - 1))


def _roll_alpha(df_slice: pd.DataFrame) -> Optional[float]:
    # Only use columns that exist
    avail_factors = [f for f in FACTORS if f in df_slice.columns]
    if not avail_factors or "ret" not in df_slice.columns or "RF" not in df_slice.columns:
        return None
    exc = df_slice["ret"].values - df_slice["RF"].values
    X_c = np.column_stack([np.ones(len(exc)), df_slice[avail_factors].values])
    try:
        c, _, _, _ = np.linalg.lstsq(X_c, exc, rcond=None)
        return float(c[0])
    except Exception:
        return None


def compute_universe_ic(
    price_data: dict,
    ff5: pd.DataFrame,
    roll_train: int = 126,
    forward: int = 21,
    n_periods: int = 8,
    min_stocks: int = MIN_STOCKS,
) -> Optional[dict]:
    """
    Cross-sectional IC: tests whether alpha ranks predict return ranks out-of-sample.

    At each evaluation point:
      1. For each stock: estimate alpha via OLS on [t-roll_train : t]
      2. Rank stocks by predicted alpha
      3. IC = Spearman(alpha rank, realized {forward}-day excess return rank)

    Returns None when insufficient data.
    Keys: ic_series, ic_mean, ic_std, icir, t_stat, pvalue, n_obs, forward_days
    """
    if ff5 is None or price_data is None:
        return None

    ff5_n = ff5.copy()
    ff5_n.index = _tz_naive(ff5_n.index)

    avail_factors = [f for f in FACTORS if f in ff5_n.columns]
    if not avail_factors or "RF" not in ff5_n.columns:
        return None

    aligned: dict[str, pd.DataFrame] = {}
    for tk, pd_obj in price_data.items():
        if pd_obj is None or getattr(pd_obj, "returns", None) is None:
            continue
        ret = pd_obj.returns.copy()
        ret.index = _tz_naive(ret.index)
        m = ret.to_frame("ret").join(ff5_n[avail_factors + ["RF"]], how="inner")
        if len(m) >= roll_train + forward + 5:
            aligned[tk] = m

    if len(aligned) < min_stocks:
        return None

    # Reference index: intersection-safe reference
    ref = next(iter(aligned.values()))
    ref_idx = ref.index

    total_needed = roll_train + n_periods * forward
    if len(ref_idx) < total_needed:
        return None

    eval_positions = [
        len(ref_idx) - (n_periods - k) * forward
        for k in range(n_periods)
    ]
    eval_positions = [p for p in eval_positions if p >= roll_train and p + forward <= len(ref_idx)]

    ic_series: list[dict] = []
    for pos in eval_positions:
        eval_date = ref_idx[pos]
        pred: dict[str, float] = {}
        real: dict[str, float] = {}
        for tk, df in aligned.items():
            loc = df.index.searchsorted(eval_date)
            if loc < roll_train or loc + forward > len(df):
                continue
            alpha = _roll_alpha(df.iloc[loc - roll_train:loc])
            if alpha is None:
                continue
            fut = df.iloc[loc:loc + forward]
            pred[tk] = alpha
            real[tk] = float((fut["ret"] - fut["RF"]).sum())

        common = [tk for tk in pred if tk in real]
        if len(common) < min_stocks:
            continue

        ic = _spearman(
            np.array([pred[tk] for tk in common]),
            np.array([real[tk]  for tk in common]),
        )
        if ic == ic:  # not NaN
            ic_series.append({
                "date": str(eval_date.date() if hasattr(eval_date, "date") else eval_date),
                "ic": float(ic),
                "n_stocks": len(common),
            })

    if len(ic_series) < 2:
        return None

    ic_vals = np.array([e["ic"] for e in ic_series])
    ic_mean = float(np.mean(ic_vals))
    ic_std  = float(np.std(ic_vals, ddof=1))
    T       = len(ic_vals)
    t_stat  = ic_mean / (ic_std / math.sqrt(T)) if ic_std > 1e-12 else float("nan")
    icir    = ic_mean / ic_std * math.sqrt(252 / forward) if ic_std > 1e-12 else float("nan")
    # Two-tailed p-value (normal approximation)
    pvalue  = float(math.erfc(abs(t_stat) / math.sqrt(2.0))) if t_stat == t_stat else float("nan")

    return {
        "ic_series":    ic_series,
        "ic_mean":      ic_mean,
        "ic_std":       ic_std,
        "icir":         icir,
        "t_stat":       t_stat,
        "pvalue":       pvalue,
        "n_obs":        T,
        "forward_days": forward,
    }


def compute_alpha_decay(
    ticker: str,
    returns: pd.Series,
    ff5: pd.DataFrame,
    horizons: tuple = (5, 21, 63, 126),
    train: int = 252,
) -> Optional[dict]:
    """
    Tests whether the alpha signal (estimated over `train` days) persists
    across multiple forward horizons.  Returns hit rate per horizon.

    hit_rate[h] = P(sign(alpha_estimate) == sign(realized_excess_return over h days))
    estimated via rolling windows of step=h.
    """
    if ff5 is None or returns is None:
        return None

    ff5_n = ff5.copy()
    ff5_n.index = _tz_naive(ff5_n.index)
    ret = returns.copy()
    ret.index = _tz_naive(ret.index)

    avail_factors = [f for f in FACTORS if f in ff5_n.columns]
    if not avail_factors or "RF" not in ff5_n.columns:
        return None

    df = ret.to_frame("ret").join(ff5_n[avail_factors + ["RF"]], how="inner")

    results: dict[int, Optional[float]] = {}
    for h in horizons:
        if len(df) < train + 2 * h:
            results[h] = None
            continue
        hits = total = 0
        for i in range(train, len(df) - h + 1, h):
            alpha = _roll_alpha(df.iloc[i - train:i])
            if alpha is None:
                continue
            realized = float((df.iloc[i:i + h]["ret"] - df.iloc[i:i + h]["RF"]).mean())
            if (alpha > 0) == (realized > 0):
                hits += 1
            total += 1
        results[h] = hits / total if total >= 2 else None

    if all(v is None for v in results.values()):
        return None
    return {"ticker": ticker, "decay": results}
