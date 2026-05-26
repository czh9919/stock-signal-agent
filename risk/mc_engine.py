"""
Monte Carlo price-path generator.

simulate_paths(ret_df, mu, n_paths, horizon, model) → np.ndarray
  shape: (n_paths, horizon, n_assets)  daily decimal returns

Primary: GARCH(1,1) per asset + Constant Correlation.
Fallback: GBM with historical covariance.
"""
import logging
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def simulate_paths(
    ret_df: pd.DataFrame,
    mu: np.ndarray,
    n_paths: int = 5000,
    horizon: int = 21,
    model: Literal["garch", "gbm"] = "garch",
) -> np.ndarray:
    """
    Generate correlated multivariate daily return paths.

    Parameters
    ----------
    ret_df  : historical daily decimal returns, shape (T, n_assets)
    mu      : annualised expected returns, shape (n_assets,)
    n_paths : number of Monte Carlo paths
    horizon : trading-day forecast horizon
    model   : "garch" (default) or "gbm"

    Returns
    -------
    np.ndarray shape (n_paths, horizon, n_assets) — daily decimal returns
    """
    mu_daily = mu / 252.0
    if model == "garch":
        try:
            return _garch_cc_paths(ret_df, mu_daily, n_paths, horizon)
        except Exception as e:
            logger.warning(f"GARCH-CC path gen failed ({e}) — falling back to GBM")
    return _gbm_paths(ret_df, mu_daily, n_paths, horizon)


def _gbm_paths(ret_df: pd.DataFrame, mu_daily: np.ndarray,
               n_paths: int, horizon: int) -> np.ndarray:
    """GBM with historical covariance."""
    n   = len(ret_df.columns)
    cov = ret_df.cov().values
    L   = _cholesky_psd(cov)
    rng = np.random.default_rng(42)
    z   = rng.standard_normal((n_paths, horizon, n))
    return z @ L.T + mu_daily   # broadcast: (n_paths, horizon, n)


def _garch_cc_paths(ret_df: pd.DataFrame, mu_daily: np.ndarray,
                    n_paths: int, horizon: int) -> np.ndarray:
    """GARCH(1,1) per asset + constant correlation matrix (CC-GARCH)."""
    from arch import arch_model

    n     = ret_df.shape[1]
    R_pct = ret_df.values * 100   # percent scale for arch numerical stability

    omega = np.zeros(n)
    alpha = np.zeros(n)
    beta  = np.zeros(n)
    h0    = np.zeros(n)           # last conditional variance, pct²
    std_resids: list[np.ndarray] = []

    for i in range(n):
        try:
            am  = arch_model(R_pct[:, i], vol="GARCH", p=1, q=1,
                             dist="normal", rescale=False)
            res = am.fit(disp="off", show_warning=False)
            omega[i] = float(res.params["omega"])
            alpha[i] = float(res.params["alpha[1]"])
            beta[i]  = float(res.params["beta[1]"])
            h0[i]    = float(res.conditional_volatility.iloc[-1]) ** 2
            std_resids.append(res.std_resid.dropna().values)
        except Exception as e:
            logger.debug(f"GARCH fit asset {i}: {e} — constant vol fallback")
            sig      = float(R_pct[:, i].std()) or 1.0
            omega[i] = sig ** 2 * 0.05
            alpha[i] = 0.10
            beta[i]  = 0.85
            h0[i]    = sig ** 2
            std_resids.append(R_pct[:, i] / sig)

    # Constant correlation from standardised residuals
    min_len = min(len(sr) for sr in std_resids)
    SR   = np.column_stack([sr[-min_len:] for sr in std_resids])
    corr = np.corrcoef(SR.T) if n > 1 else np.array([[1.0]])
    L    = _cholesky_psd(corr)

    rng   = np.random.default_rng(42)
    paths = np.zeros((n_paths, horizon, n))

    for p in range(n_paths):
        h = h0.copy()   # pct²
        for t in range(horizon):
            z     = rng.standard_normal(n)
            z_c   = L @ z                              # correlated
            sigma = np.sqrt(h)                         # pct
            r_pct = mu_daily * 100 + sigma * z_c
            paths[p, t, :] = r_pct / 100              # → decimal
            eps = sigma * z_c                          # pct innovation
            h   = omega + alpha * eps ** 2 + beta * h  # GARCH recursion
    return paths


def _cholesky_psd(M: np.ndarray) -> np.ndarray:
    """Lower Cholesky factor; nudges M toward PSD if needed."""
    n = M.shape[0]
    for delta in (0.0, 1e-8, 1e-6, 1e-4, 1e-2):
        try:
            return np.linalg.cholesky(M + delta * np.eye(n))
        except np.linalg.LinAlgError:
            continue
    vals, vecs = np.linalg.eigh(M)
    return np.linalg.cholesky(vecs @ np.diag(np.maximum(vals, 1e-6)) @ vecs.T)
