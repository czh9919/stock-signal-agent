"""
M3 — Risk engine: EWMA + GARCH(1,1) + fixed window.
All amounts normalised to EUR. Returns risk metrics as a dict.
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)

Z95 = 1.6449
Z99 = 2.3263


# ── EWMA VaR ─────────────────────────────────────────────────────────────────

def ewma_variance_series(returns: pd.Series, lam: float = 0.94,
                         init_window: int = 21) -> pd.Series:
    """Compute EWMA variance for each day. Returns series aligned with returns."""
    r = returns.values
    var = np.empty(len(r))
    if len(r) < init_window:
        var[:] = np.var(r)
    else:
        var[0] = np.var(r[:init_window])
        for t in range(1, len(r)):
            var[t] = lam * var[t - 1] + (1 - lam) * r[t - 1] ** 2
    return pd.Series(var, index=returns.index)


def ewma_var(portfolio_returns: pd.Series, lam: float = 0.94,
             init_window: int = 21, confidence: float = 0.95) -> float:
    """One-day VaR as a fraction of portfolio value (positive = loss)."""
    if len(portfolio_returns) < init_window:
        return float("nan")
    var_series = ewma_variance_series(portfolio_returns, lam, init_window)
    sigma_t    = float(np.sqrt(var_series.iloc[-1]))
    mu         = float(portfolio_returns.mean())
    z          = Z95 if confidence == 0.95 else Z99
    return -mu + z * sigma_t


# ── Fixed-window CVaR ─────────────────────────────────────────────────────────

def fixed_var_cvar(portfolio_returns: pd.Series,
                   confidence: float = 0.95) -> tuple[float, float]:
    """Historical simulation VaR and CVaR (positive = loss)."""
    if portfolio_returns.empty:
        return float("nan"), float("nan")
    cutoff = np.percentile(portfolio_returns, (1 - confidence) * 100)
    cvar   = float(portfolio_returns[portfolio_returns <= cutoff].mean())
    return float(-cutoff), float(-cvar)


# ── GARCH(1,1) per asset ──────────────────────────────────────────────────────

def garch_variance(returns: pd.Series, omega: float = 1e-6,
                   alpha: float = 0.05, beta: float = 0.94) -> float:
    """
    Fit GARCH(1,1) via arch library.
    Falls back to EWMA on convergence failure.
    """
    try:
        from arch import arch_model
        am  = arch_model(returns * 100, vol="Garch", p=1, q=1, mean="Zero", dist="Normal")
        res = am.fit(disp="off", show_warning=False)
        fc  = res.forecast(horizon=1)
        return float(fc.variance.values[-1, 0]) / (100 ** 2)
    except Exception as e:
        logger.debug(f"GARCH failed ({e}), falling back to EWMA")
        var_s = ewma_variance_series(returns)
        return float(var_s.iloc[-1])


# ── Portfolio returns ─────────────────────────────────────────────────────────

def portfolio_returns(price_data: dict, holdings: list[dict],
                      window: int = 252) -> pd.Series:
    """
    Compute weighted portfolio daily returns.
    Only includes holdings with status in {ok, reduced}.
    """
    weights = {h["ticker"]: h["weight"] for h in holdings}
    frames  = []
    for ticker, pd_obj in price_data.items():
        if pd_obj.status not in ("ok", "reduced"):
            continue
        w = weights.get(ticker, 0.0)
        if w == 0 or pd_obj.returns is None:
            continue
        frames.append(pd_obj.returns.tail(window) * w)

    if not frames:
        return pd.Series(dtype=float)

    df = pd.concat(frames, axis=1).fillna(0)
    return df.sum(axis=1)


# ── Correlation matrix ────────────────────────────────────────────────────────

def rolling_correlation(price_data: dict, holdings: list[dict],
                        window: int = 63) -> Optional[pd.DataFrame]:
    valid_tickers = [
        h["ticker"] for h in holdings
        if price_data.get(h["ticker"]) and
           price_data[h["ticker"]].status in ("ok", "reduced") and
           price_data[h["ticker"]].returns is not None
    ]
    if len(valid_tickers) < 2:
        return None
    ret_df = pd.concat(
        {t: price_data[t].returns.tail(window) for t in valid_tickers}, axis=1
    ).dropna()
    return ret_df.corr()


# ── Portfolio σ (GARCH) ───────────────────────────────────────────────────────

def portfolio_sigma(price_data: dict, holdings: list[dict],
                    vcfg: dict = None) -> float:
    """Portfolio annualised volatility using per-asset GARCH variances + rolling correlation."""
    vcfg    = vcfg or {}
    omega   = vcfg.get("omega", 1e-6)
    alpha   = vcfg.get("alpha", 0.05)
    beta    = vcfg.get("beta",  0.94)
    corr_df = rolling_correlation(price_data, holdings)

    valid = [
        h for h in holdings
        if price_data.get(h["ticker"]) and
           price_data[h["ticker"]].status in ("ok", "reduced") and
           price_data[h["ticker"]].returns is not None
    ]
    if not valid:
        return float("nan")

    weights = np.array([h["weight"] for h in valid])
    tickers = [h["ticker"] for h in valid]
    var_vec = np.array([garch_variance(price_data[t].returns, omega, alpha, beta) for t in tickers])
    sigma_vec = np.sqrt(var_vec)

    if corr_df is not None:
        corr = corr_df.reindex(index=tickers, columns=tickers).fillna(0).values
        np.fill_diagonal(corr, 1.0)
    else:
        corr = np.eye(len(tickers))

    cov = np.outer(sigma_vec, sigma_vec) * corr
    port_var = float(weights @ cov @ weights)
    return float(np.sqrt(port_var * 252))  # annualised


# ── Sharpe ratio ──────────────────────────────────────────────────────────────

def sharpe_ratio(portfolio_ret: pd.Series, rf: float = 0.035) -> float:
    if len(portfolio_ret) < 21:
        return float("nan")
    ann_ret = float(portfolio_ret.mean() * 252)
    ann_vol = float(portfolio_ret.std() * np.sqrt(252))
    return (ann_ret - rf) / ann_vol if ann_vol else float("nan")


# ── Beta vs SPY ───────────────────────────────────────────────────────────────

def beta_vs_spy(portfolio_ret: pd.Series, spy_returns: pd.Series) -> float:
    aligned = pd.concat([portfolio_ret, spy_returns], axis=1).dropna()
    if len(aligned) < 21:
        return float("nan")
    p, s   = aligned.iloc[:, 0].values, aligned.iloc[:, 1].values
    cov    = np.cov(p, s)
    return float(cov[0, 1] / cov[1, 1]) if cov[1, 1] else float("nan")


# ── HHI ──────────────────────────────────────────────────────────────────────

def hhi(holdings: list[dict]) -> float:
    return float(sum(h["weight"] ** 2 for h in holdings))


# ── Max Drawdown ──────────────────────────────────────────────────────────────

def max_drawdown(portfolio_ret: pd.Series) -> float:
    if portfolio_ret.empty:
        return float("nan")
    cum   = (1 + portfolio_ret).cumprod()
    peak  = cum.cummax()
    dd    = (cum - peak) / peak
    return float(dd.min())


# ── Full metrics bundle ───────────────────────────────────────────────────────

def compute_all(holdings: list[dict], price_data: dict,
                spy_price_data, vcfg: dict = None,
                thresholds: dict = None) -> dict:
    """
    Run all 10 core metrics. Returns dict of metric_name → value (floats).
    spy_price_data: PriceData for SPY.
    """
    vcfg = vcfg or {}
    lam        = vcfg.get("ewma", {}).get("lambda", 0.94)
    init_win   = vcfg.get("ewma", {}).get("init_window", 21)
    fixed_win  = vcfg.get("windows", {}).get("fixed", 252)
    rf         = vcfg.get("risk_free_rate", 0.045)

    port_ret   = portfolio_returns(price_data, holdings, window=fixed_win)
    nav        = sum(h["market_value_eur"] for h in holdings)
    total_pnl  = sum(h["unrealised_pnl_eur"] for h in holdings)
    daily_ret  = port_ret.iloc[-1] if not port_ret.empty else float("nan")

    var_95_ewma = ewma_var(port_ret, lam=lam, init_window=init_win, confidence=0.95)
    var_99_ewma = ewma_var(port_ret, lam=lam, init_window=init_win, confidence=0.99)
    var_95_hist, cvar_95 = fixed_var_cvar(port_ret, 0.95)

    spy_ret = spy_price_data.returns if spy_price_data and spy_price_data.returns is not None else pd.Series(dtype=float)

    metrics = {
        "nav_eur":           nav,
        "total_pnl_eur":     total_pnl,
        "daily_return":      daily_ret,
        "var_95_ewma":       var_95_ewma,
        "var_99_ewma":       var_99_ewma,
        "var_95_hist":       var_95_hist,
        "cvar_95":           cvar_95,
        "cvar_var_ratio":    cvar_95 / var_95_hist if var_95_hist else float("nan"),
        "max_drawdown":      max_drawdown(port_ret),
        "sharpe":            sharpe_ratio(port_ret, rf=rf),
        "beta":              beta_vs_spy(port_ret, spy_ret),
        "hhi":               hhi(holdings),
        "max_position_wt":   max((h["weight"] for h in holdings), default=0.0),
        "port_sigma_annual": portfolio_sigma(price_data, holdings, vcfg.get("garch", {})),
    }

    # RAG status
    def rag(value, threshold, direction="above"):
        if value is None or value != value:  # nan
            return "GREY"
        breach = value > threshold if direction == "above" else value < threshold
        return "RED" if breach else "GREEN"

    alerts = {}
    if thresholds:
        t = thresholds.get("alerts", {})
        alerts["var_95"]      = rag(metrics["var_95_ewma"],    t.get("var_95_pct",         {}).get("threshold", 0.05))
        alerts["cvar_ratio"]  = rag(metrics["cvar_var_ratio"], t.get("cvar_var_ratio",      {}).get("threshold", 1.8))
        alerts["max_dd"]      = rag(abs(metrics["max_drawdown"]), t.get("max_drawdown",     {}).get("threshold", 0.20))
        alerts["max_pos"]     = rag(metrics["max_position_wt"], t.get("max_single_position",{}).get("threshold", 0.30))
        alerts["hhi"]         = rag(metrics["hhi"],             t.get("hhi",                {}).get("threshold", 0.25))
        alerts["beta"]        = rag(metrics["beta"],            t.get("beta",               {}).get("threshold", 1.5))
        alerts["sharpe"]      = rag(metrics["sharpe"],          t.get("sharpe",             {}).get("threshold", 0.5), direction="below")
        alerts["daily_loss"]  = rag(-metrics["daily_return"],  t.get("single_day_loss",     {}).get("threshold", 0.03))

    overall_rag = "RED" if "RED" in alerts.values() else ("AMBER" if "AMBER" in alerts.values() else "GREEN")
    metrics["alerts"]      = alerts
    metrics["overall_rag"] = overall_rag

    return metrics
