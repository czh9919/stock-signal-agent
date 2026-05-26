"""
Fama-French 5-factor regression per stock.
  run_factor_regression()      — OLS alpha/beta/IR for a single ticker
  portfolio_factor_exposure()  — market-value-weighted aggregate betas
  compute_attribution()        — decompose recent portfolio return into factor contributions
  enrich_suggestions()         — attach α-based "why" reason to each rebalancing suggestion
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FACTORS    = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
MIN_WINDOW = 63   # minimum aligned days for reliable OLS


def run_factor_regression(
    ticker: str,
    returns: pd.Series,
    ff5: pd.DataFrame,
    window: int = 252,
) -> Optional[dict]:
    """
    OLS regression of daily excess returns on FF5 factors.

    Uses the last min(window, available) aligned days; returns None
    when fewer than MIN_WINDOW days overlap with FF5 history.

    Returns dict:
      ticker, alpha_ann, t_alpha, ir, r_squared,
      beta_mkt/smb/hml/rmw/cma, signal ("BUY"/"SELL"/"HOLD"), n_days
    """
    def _tz_naive(idx: pd.Index) -> pd.Index:
        return idx.tz_localize(None) if idx.tz is not None else idx

    ret = returns.copy()
    ret.index = _tz_naive(ret.index)

    ff5_n = ff5.copy()
    ff5_n.index = _tz_naive(ff5_n.index)

    aligned = ret.to_frame("ret").join(ff5_n[FACTORS + ["RF"]], how="inner")

    if len(aligned) < MIN_WINDOW:
        logger.debug(f"{ticker}: only {len(aligned)} aligned days (<{MIN_WINDOW}) — skipping")
        return None

    n_use   = min(window, len(aligned))
    aligned = aligned.iloc[-n_use:]
    n       = len(aligned)

    excess = aligned["ret"].values - aligned["RF"].values
    X      = aligned[FACTORS].values
    X_c    = np.column_stack([np.ones(n), X])   # intercept + 5 factors

    coeffs, _, rank, _ = np.linalg.lstsq(X_c, excess, rcond=None)
    residuals  = excess - X_c @ coeffs
    alpha_daily = float(coeffs[0])
    betas       = coeffs[1:]

    # OLS standard error of alpha
    df_resid = n - X_c.shape[1]
    if df_resid > 0 and rank == X_c.shape[1]:
        s2 = float(np.dot(residuals, residuals) / df_resid)
        try:
            xxt_inv  = np.linalg.inv(X_c.T @ X_c)
            se_alpha = float(np.sqrt(max(s2 * xxt_inv[0, 0], 0.0)))
        except np.linalg.LinAlgError:
            se_alpha = float("nan")
    else:
        se_alpha = float("nan")

    t_alpha = (alpha_daily / se_alpha
               if se_alpha and se_alpha > 1e-12
               else float("nan"))

    # Information Ratio = (alpha_daily / σ_residuals) × √252
    sigma_res = float(np.std(residuals, ddof=1)) if n > 1 else float("nan")
    ir = ((alpha_daily / sigma_res) * np.sqrt(252)
          if sigma_res and sigma_res > 1e-12
          else float("nan"))

    # R²
    ss_tot = float(np.dot(excess - excess.mean(), excess - excess.mean()))
    r2     = 1.0 - float(np.dot(residuals, residuals)) / ss_tot if ss_tot > 1e-12 else 0.0

    # Signal driven by t_alpha (statistical significance of excess return)
    if t_alpha != t_alpha:   # NaN
        signal = "HOLD"
    elif t_alpha > 1.5:
        signal = "BUY"
    elif t_alpha < -1.5:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "ticker":    ticker,
        "alpha_ann": alpha_daily * 252,
        "t_alpha":   t_alpha,
        "ir":        ir,
        "r_squared": r2,
        "beta_mkt":  float(betas[0]),
        "beta_smb":  float(betas[1]),
        "beta_hml":  float(betas[2]),
        "beta_rmw":  float(betas[3]),
        "beta_cma":  float(betas[4]),
        "signal":    signal,
        "n_days":    n,
    }


def portfolio_factor_exposure(
    holdings: list[dict],
    price_data: dict,
    ff5: pd.DataFrame,
    window: int = 252,
) -> Optional[dict]:
    """
    Market-value-weighted aggregate factor betas for equity holdings.
    Returns None when no holdings have sufficient price history.
    """
    equity = [
        h for h in holdings
        if h.get("asset_class", "equity") == "equity"
        and price_data.get(h["ticker"])
        and price_data[h["ticker"]].returns is not None
    ]
    if not equity:
        return None

    total_mv = sum(h.get("market_value_eur", 0.0) for h in equity)
    if total_mv <= 0:
        return None

    agg  = {k: 0.0 for k in ("alpha_ann", "beta_mkt", "beta_smb", "beta_hml", "beta_rmw", "beta_cma")}
    used = 0.0

    for h in equity:
        ticker = h["ticker"]
        mv     = h.get("market_value_eur", 0.0)
        if mv <= 0:
            continue
        reg = run_factor_regression(ticker, price_data[ticker].returns, ff5, window)
        if reg is None:
            continue
        w = mv / total_mv
        used += w
        for key in agg:
            agg[key] += w * reg[key]

    if used < 1e-9:
        return None

    # Re-normalise if some holdings had no valid regression
    if used < 0.999:
        for key in agg:
            agg[key] /= used

    return agg


_FACTOR_LABELS = [
    ("Mkt-RF", "beta_mkt", "Market",       "市场"),
    ("SMB",    "beta_smb", "Size (SMB)",   "规模(SMB)"),
    ("HML",    "beta_hml", "Value (HML)",  "价值(HML)"),
    ("RMW",    "beta_rmw", "Profit (RMW)", "盈利(RMW)"),
    ("CMA",    "beta_cma", "Invest (CMA)", "投资(CMA)"),
]


def compute_attribution(
    holdings: list[dict],
    price_data: dict,
    ff5: pd.DataFrame,
    port_exposure: dict,
    window: int = 21,
) -> Optional[dict]:
    """
    Decomposes the realized equity portfolio return over the last `window`
    trading days into FF5 factor contributions + residual alpha.

    Uses market-value weights from holdings and betas from port_exposure.
    Returns None when insufficient price history is available.

    Keys in result:
      window, start, end, total_return, rf,
      contributions: {factor: {beta, factor_ret, contrib}},
      factor_total, alpha
    """
    def _tz_naive(idx: pd.Index) -> pd.Index:
        return idx.tz_localize(None) if idx.tz is not None else idx

    equity = [
        h for h in holdings
        if h.get("asset_class", "equity") == "equity"
        and price_data.get(h["ticker"])
        and price_data[h["ticker"]].returns is not None
    ]
    if not equity:
        return None

    total_mv = sum(h.get("market_value_eur", 0.0) for h in equity)
    if total_mv <= 0:
        return None

    # Build value-weighted portfolio return series
    port_ret: Optional[pd.Series] = None
    for h in equity:
        mv = h.get("market_value_eur", 0.0)
        if mv <= 0:
            continue
        w   = mv / total_mv
        ret = price_data[h["ticker"]].returns.copy()
        ret.index = _tz_naive(ret.index)
        port_ret  = ret * w if port_ret is None else port_ret.add(ret * w, fill_value=0.0)

    if port_ret is None or len(port_ret) < 5:
        return None

    ff5_n = ff5.copy()
    ff5_n.index = _tz_naive(ff5_n.index)

    aligned = port_ret.to_frame("port").join(ff5_n, how="inner").iloc[-window:]
    n = len(aligned)
    if n < 5:
        return None

    # Cumulative arithmetic return over the window (good approximation for ≤21 days)
    total_return = float(aligned["port"].sum())
    rf_total     = float(aligned["RF"].sum())

    contributions: dict = {}
    factor_total  = rf_total
    for fname, bkey, *_ in _FACTOR_LABELS:
        beta       = port_exposure.get(bkey, 0.0)
        factor_ret = float(aligned[fname].sum())
        contrib    = beta * factor_ret
        contributions[fname] = {"beta": beta, "factor_ret": factor_ret, "contrib": contrib}
        factor_total += contrib

    start = str(aligned.index[0].date()  if hasattr(aligned.index[0],  "date") else aligned.index[0])
    end   = str(aligned.index[-1].date() if hasattr(aligned.index[-1], "date") else aligned.index[-1])

    return {
        "window":        n,
        "start":         start,
        "end":           end,
        "total_return":  total_return,
        "rf":            rf_total,
        "contributions": contributions,
        "factor_total":  factor_total,
        "alpha":         total_return - factor_total,
    }


def enrich_suggestions(
    suggestions: list[dict],
    stock_factors: list[dict],
) -> list[dict]:
    """
    Adds 'reason' and 'reason_zh' fields to each rebalancing suggestion
    by cross-referencing the FF5 regression results.  Modifies in-place
    and also returns the list.
    """
    factor_map = {s["ticker"]: s for s in stock_factors}

    for sug in suggestions:
        reg   = factor_map.get(sug["ticker"])
        delta = sug.get("delta", 0.0)

        if reg is None:
            sug["reason"] = sug["reason_zh"] = ""
            continue

        alpha  = reg.get("alpha_ann", 0.0)
        t      = reg.get("t_alpha", float("nan"))
        signal = reg.get("signal", "HOLD")
        t_str  = f" t={t:+.1f}" if t == t else ""   # omit if NaN

        a_str    = f"α={alpha*100:+.1f}%{t_str}"
        cgt_flag = sug.get("cgt_flag", False)

        if delta > 0:
            if signal == "BUY":
                en = f"{a_str} → BUY signal; underweight"
                zh = f"{a_str} → 买入信号，当前低配"
            else:
                en = f"{a_str}; optimizer targets higher allocation"
                zh = f"{a_str}；优化器建议提高配置"
        else:
            if signal == "SELL":
                en = f"{a_str} → SELL signal; overweight"
                zh = f"{a_str} → 卖出信号，当前超配"
            elif cgt_flag:
                en = f"{a_str}; trim deferred — CGT 33% applies"
                zh = f"{a_str}；减仓受CGT 33%税务约束"
            else:
                en = f"{a_str}; trim to reduce concentration"
                zh = f"{a_str}；减仓以降低集中度"

        sug["reason"]    = en
        sug["reason_zh"] = zh

    return suggestions
