"""
Monte Carlo price-path generator.

simulate_paths(ret_df, mu, n_paths, horizon, model) → np.ndarray
  shape: (n_paths, horizon, n_assets)  daily decimal returns

Models
------
"hawkes"  GARCH(1,1)+CC diffusion + self-exciting Hawkes jump process  (default)
"garch"   GARCH(1,1)+CC diffusion only
"gbm"     GBM with historical covariance

Hawkes model
------------
Return at step t (per asset i):

  r_{t,i} = μ_i + σ_{t,i} · ε_{t,i}          # GARCH-CC diffusion
             + Σ_{k=1}^{N_{t,i}} J_{t,i,k}    # Hawkes jump sum

  N_{t,i} ~ Poisson(λ_{t,i})                   # jump count
  J_{t,i,k} ~ Normal(μ_J,i, σ_J,i²)           # jump size

  λ_{t+1,i} = λ_{0,i}
               + (λ_{t,i} − λ_{0,i}) · exp(−β_i)      # exponential decay
               + α_i · 1{|r_{t,i}| > c · σ_{t,i}}     # self-excitation on extreme move
"""
import logging
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Extreme-event threshold: moves > EXTREME_K × GARCH-σ excite the Hawkes intensity
EXTREME_K = 2.0


# ── Public API ────────────────────────────────────────────────────────────────

def simulate_paths(
    ret_df: pd.DataFrame,
    mu: np.ndarray,
    n_paths: int = 5000,
    horizon: int = 21,
    model: Literal["hawkes", "garch", "gbm"] = "hawkes",
) -> np.ndarray:
    """
    Generate correlated multivariate daily return paths.

    Parameters
    ----------
    ret_df  : historical daily decimal returns, shape (T, n_assets)
    mu      : annualised expected returns, shape (n_assets,)
    n_paths : number of Monte Carlo paths
    horizon : trading-day forecast horizon
    model   : "hawkes" (default), "garch", or "gbm"

    Returns
    -------
    np.ndarray shape (n_paths, horizon, n_assets) — daily decimal returns
    """
    mu_daily = mu / 252.0

    if model in ("hawkes", "garch"):
        try:
            gp = _fit_garch(ret_df)
            if model == "hawkes":
                hp = _calibrate_hawkes(ret_df, gp)
                return _hawkes_garch_paths(ret_df, mu_daily, n_paths, horizon, gp, hp)
            return _garch_cc_paths(ret_df, mu_daily, n_paths, horizon, gp)
        except Exception as e:
            logger.warning(f"{model.upper()} path gen failed ({e}) — falling back to GBM")

    return _gbm_paths(ret_df, mu_daily, n_paths, horizon)


# ── GARCH fitting (shared between "garch" and "hawkes" models) ────────────────

def _fit_garch(ret_df: pd.DataFrame) -> dict:
    """
    Fit GARCH(1,1) per asset.  Returns dict with per-asset arrays and
    the Cholesky factor L of the constant correlation matrix.
    """
    from arch import arch_model

    n = ret_df.shape[1]
    R_pct = ret_df.values * 100   # percent scale for arch numerical stability

    omega = np.zeros(n)
    alpha = np.zeros(n)
    beta  = np.zeros(n)
    h0    = np.zeros(n)            # last conditional variance, pct²
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

    return {"omega": omega, "alpha": alpha, "beta": beta, "h0": h0, "L": L}


# ── Hawkes calibration ────────────────────────────────────────────────────────

def _calibrate_hawkes(ret_df: pd.DataFrame, gp: dict) -> dict:
    """
    Calibrate Hawkes jump-process parameters from historical returns.

    For each asset:
      1. Compute EWMA conditional volatility σ_t.
      2. Identify extreme events: |r_t| > EXTREME_K × σ_t.
      3. λ₀  = empirical rate of extreme events (per day).
      4. β   = 0.5 (half-life ≈ 1.4 days; excitation decays quickly).
      5. α   = β × min(2 × lag-1 autocorrelation of extreme events, 0.80)
               (ensures stationarity: α < β).
      6. μ_J, σ_J = mean and std of extreme event returns.

    Returns dict: keys are column names, values are param dicts.
    """
    params = {}
    cols = list(ret_df.columns)
    R    = ret_df.values      # decimal, (T, n)
    T    = R.shape[0]

    # Use GARCH h0 as σ²₀ seed; propagate EWMA for calibration
    h0_pct2 = gp["h0"]       # pct²

    for i, col in enumerate(cols):
        r    = R[:, i]
        r_pct = r * 100

        # EWMA vol (simple, for threshold computation)
        lam = 0.94
        sig2 = np.empty(T)
        sig2[0] = max(h0_pct2[i], r_pct[:21].var())
        for t in range(1, T):
            sig2[t] = lam * sig2[t - 1] + (1 - lam) * r_pct[t - 1] ** 2
        sigma_pct = np.sqrt(sig2)                 # pct

        # Extreme event indicator
        extreme = np.abs(r_pct) > EXTREME_K * sigma_pct
        n_ext   = int(extreme.sum())
        lambda0 = max(n_ext / T, 1.0 / T)        # at least 1 event in history

        # Lag-1 autocorrelation of extreme-event indicator → α
        I = extreme.astype(float)
        if n_ext >= 5:
            rho1 = float(np.corrcoef(I[:-1], I[1:])[0, 1])
            rho1 = max(0.0, rho1)
        else:
            rho1 = 0.2   # default when too few events

        beta_h  = 0.5                             # fixed decay rate (per day)
        alpha_h = beta_h * min(2.0 * rho1, 0.80) # stability: α/β < 1

        # Jump size distribution from historical extreme returns
        ext_returns = r[extreme]
        mu_J   = float(ext_returns.mean()) if len(ext_returns) > 0 else 0.0
        # σ_J must be at least as large as the GARCH σ at extreme events
        sig_J  = float(ext_returns.std())  if len(ext_returns) > 1 else float(sigma_pct.mean()) / 100

        params[col] = {
            "lambda0": lambda0,
            "alpha":   alpha_h,
            "beta":    beta_h,
            "mu_J":    mu_J,
            "sig_J":   max(sig_J, float(sigma_pct.mean()) * EXTREME_K / 100),
        }
        logger.debug(
            f"Hawkes [{col}]: λ₀={lambda0:.4f}  α={alpha_h:.3f}  β={beta_h:.3f}"
            f"  μ_J={mu_J*100:.2f}%  σ_J={sig_J*100:.2f}%"
        )

    return params


# ── Path simulators ───────────────────────────────────────────────────────────

def _hawkes_garch_paths(ret_df: pd.DataFrame, mu_daily: np.ndarray,
                        n_paths: int, horizon: int,
                        gp: dict, hp: dict) -> np.ndarray:
    """
    GARCH(1,1)+CC diffusion with overlaid Hawkes self-exciting jump process.

    Simulation loop per path p, per day t:

      1. Draw correlated GARCH innovations  ε_t = L · z,  z ~ N(0,I)
      2. Diffusion return:  r_diff = μ + σ_t · ε_t
      3. Jump count:  N_t,i ~ Poisson(λ_t,i)
      4. Jump total:  jump_i = Σ_k J_{t,i,k},  J ~ N(μ_J,i, σ²_J,i)
      5. Total return: r_t,i = r_diff,i + jump_i
      6. GARCH update:  h_{t+1} = ω + α·ε²_t + β·h_t
      7. Hawkes update: λ_{t+1,i} = λ_{0,i}
                           + (λ_{t,i} − λ_{0,i})·exp(−β_i)
                           + α_i · 1{|r_{t,i}| > EXTREME_K · σ_{t,i}}
    """
    n    = ret_df.shape[1]
    cols = list(ret_df.columns)

    omega = gp["omega"]
    alpha = gp["alpha"]
    beta  = gp["beta"]
    h0    = gp["h0"]
    L     = gp["L"]

    lam0   = np.array([hp[c]["lambda0"] for c in cols])
    h_alph = np.array([hp[c]["alpha"]   for c in cols])
    h_beta = np.array([hp[c]["beta"]    for c in cols])
    mu_J   = np.array([hp[c]["mu_J"]    for c in cols])
    sig_J  = np.array([hp[c]["sig_J"]   for c in cols])
    decay  = np.exp(-h_beta)   # precompute exp(−β) per asset

    rng   = np.random.default_rng(42)
    paths = np.zeros((n_paths, horizon, n))

    for p in range(n_paths):
        h   = h0.copy()        # GARCH conditional variance, pct²
        lam = lam0.copy()      # Hawkes intensity per asset

        for t in range(horizon):
            # ── GARCH diffusion ───────────────────────────────────────────────
            z     = rng.standard_normal(n)
            z_c   = L @ z                              # correlated
            sigma = np.sqrt(h)                         # pct
            r_diff = (mu_daily * 100 + sigma * z_c) / 100   # decimal

            # ── Hawkes jumps ──────────────────────────────────────────────────
            n_jumps   = rng.poisson(lam)              # (n,) jump counts
            jump_tot  = np.zeros(n)
            for i in np.nonzero(n_jumps)[0]:          # only assets with ≥1 jump
                k = int(n_jumps[i])
                jump_tot[i] = rng.normal(mu_J[i], sig_J[i], k).sum()

            r_total          = r_diff + jump_tot
            paths[p, t, :]   = r_total

            # ── GARCH update ──────────────────────────────────────────────────
            eps = sigma * z_c                              # pct diffusion innovation
            h   = omega + alpha * eps ** 2 + beta * h

            # ── Hawkes intensity update ───────────────────────────────────────
            # Decay toward baseline
            lam = lam0 + (lam - lam0) * decay
            # Self-excitation: extreme move (relative to GARCH σ before jump)
            sigma_d   = sigma / 100.0                      # decimal σ
            extreme   = np.abs(r_total) > EXTREME_K * sigma_d
            lam       = lam + h_alph * extreme.astype(float)

    return paths


def _garch_cc_paths(ret_df: pd.DataFrame, mu_daily: np.ndarray,
                    n_paths: int, horizon: int, gp: dict) -> np.ndarray:
    """GARCH(1,1) + constant correlation, no jumps."""
    n     = ret_df.shape[1]
    omega = gp["omega"]
    alpha = gp["alpha"]
    beta  = gp["beta"]
    h0    = gp["h0"]
    L     = gp["L"]

    rng   = np.random.default_rng(42)
    paths = np.zeros((n_paths, horizon, n))

    for p in range(n_paths):
        h = h0.copy()
        for t in range(horizon):
            z     = rng.standard_normal(n)
            z_c   = L @ z
            sigma = np.sqrt(h)
            r_pct = mu_daily * 100 + sigma * z_c
            paths[p, t, :] = r_pct / 100
            eps = sigma * z_c
            h   = omega + alpha * eps ** 2 + beta * h
    return paths


def _gbm_paths(ret_df: pd.DataFrame, mu_daily: np.ndarray,
               n_paths: int, horizon: int) -> np.ndarray:
    """GBM with historical covariance."""
    n   = len(ret_df.columns)
    cov = ret_df.cov().values
    L   = _cholesky_psd(cov)
    rng = np.random.default_rng(42)
    z   = rng.standard_normal((n_paths, horizon, n))
    return z @ L.T + mu_daily


# ── Utility ───────────────────────────────────────────────────────────────────

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
