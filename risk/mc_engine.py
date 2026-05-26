"""
Monte Carlo price-path generator.

simulate_paths(ret_df, mu, n_paths, horizon, model) → np.ndarray
  shape: (n_paths, horizon, n_assets)  daily decimal returns

Models
------
"hawkes"  GARCH(1,1) + DCC dynamic correlations + self-exciting Hawkes jumps  (default)
"garch"   GARCH(1,1) + constant correlation, no jumps
"gbm"     GBM with historical covariance

Full model at each step t  (vectorised over all n_paths simultaneously)
-----------------------------------------------------------------------
  r_{t,i} = μ_i + σ_{t,i} · ε_{t,i}          GARCH-DCC diffusion
             + J_{t,i} · B_{t,i}               Hawkes jump (Bernoulli, valid λ≪1)

  Σ_t  = D_t · R_t · D_t                       time-varying covariance
  D_t  = diag(σ_{t,1}, …, σ_{t,n})             GARCH per-asset volatility
  R_t  = diag(Q_t)^{-½} · Q_t · diag(Q_t)^{-½} DCC correlation

  Q_{t+1} = (1−a−b)·Q̄ + a·ε̃_t·ε̃_t' + b·Q_t  DCC recursion
  ε̃_{t,i} = r_{t,i} / σ_{t,i}                 total standardised return

  λ_{t+1} = λ₀ + (λ_t − λ₀)·e^{−β}            Hawkes intensity decay
             + α · 1{|r_t| > 2σ_t}             self-excitation on extreme move
"""
import logging
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

EXTREME_K = 2.0   # Hawkes excitation threshold (multiples of GARCH σ)


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
                dcc = _fit_dcc(gp)
                hp  = _calibrate_hawkes(ret_df, gp)
                return _hawkes_dcc_paths(mu_daily, n_paths, horizon, gp, dcc, hp)
            return _garch_cc_paths(mu_daily, n_paths, horizon, gp)
        except Exception as e:
            logger.warning(f"{model.upper()} path gen failed ({e}) — falling back to GBM")

    return _gbm_paths(ret_df, mu_daily, n_paths, horizon)


# ── GARCH fitting ─────────────────────────────────────────────────────────────

def _fit_garch(ret_df: pd.DataFrame) -> dict:
    """
    Fit GARCH(1,1) per asset.

    Returns
    -------
    dict with keys:
      omega, alpha, beta  (n,) GARCH parameters
      h0                  (n,) last conditional variance in pct²
      L                   (n, n) Cholesky of constant correlation (CC fallback)
      std_resids          (T_min, n) aligned GARCH standardised residuals
    """
    from arch import arch_model

    n = ret_df.shape[1]
    R_pct = ret_df.values * 100

    omega = np.zeros(n)
    alpha = np.zeros(n)
    beta  = np.zeros(n)
    h0    = np.zeros(n)
    sr_list: list[np.ndarray] = []

    for i in range(n):
        try:
            am  = arch_model(R_pct[:, i], vol="GARCH", p=1, q=1,
                             dist="normal", rescale=False)
            res = am.fit(disp="off", show_warning=False)
            omega[i] = float(res.params["omega"])
            alpha[i] = float(res.params["alpha[1]"])
            beta[i]  = float(res.params["beta[1]"])
            h0[i]    = float(res.conditional_volatility.iloc[-1]) ** 2
            sr_list.append(res.std_resid.dropna().values)
        except Exception as e:
            logger.debug(f"GARCH fit asset {i}: {e} — constant vol fallback")
            sig      = float(R_pct[:, i].std()) or 1.0
            omega[i] = sig ** 2 * 0.05
            alpha[i] = 0.10
            beta[i]  = 0.85
            h0[i]    = sig ** 2
            sr_list.append(R_pct[:, i] / sig)

    min_len = min(len(s) for s in sr_list)
    SR   = np.column_stack([s[-min_len:] for s in sr_list])   # (T_min, n)
    corr = np.corrcoef(SR.T) if n > 1 else np.array([[1.0]])
    L    = _chol_psd(corr)

    return {
        "omega": omega, "alpha": alpha, "beta": beta,
        "h0": h0, "L": L, "std_resids": SR,
        "n": n,
    }


# ── DCC fitting ───────────────────────────────────────────────────────────────

def _fit_dcc(gp: dict) -> dict:
    """
    Fit DCC(1,1) via quasi-MLE on GARCH standardised residuals.

    Model:  Q_{t+1} = (1−a−b)·Q̄ + a·ε_t·ε_t' + b·Q_t
            R_t     = diag(Q_t)^{−½} Q_t diag(Q_t)^{−½}

    Returns dict: a, b, Q_bar (n×n), Q0 (last historical Q, n×n).
    """
    from scipy.optimize import minimize

    SR   = gp["std_resids"]   # (T, n)
    T, n = SR.shape

    Q_bar = np.corrcoef(SR.T) if n > 1 else np.array([[1.0]])
    Q_bar = _make_psd(Q_bar)

    def _neg_loglik(params: np.ndarray) -> float:
        a, b = float(params[0]), float(params[1])
        if a <= 0 or b <= 0 or a + b >= 0.9999:
            return 1e10
        Q  = Q_bar.copy()
        ll = 0.0
        for t in range(T):
            qd = np.diag(Q)
            if np.any(qd < 1e-12):
                return 1e10
            qi = 1.0 / np.sqrt(qd)
            R  = qi[:, None] * Q * qi[None, :]
            try:
                L_R     = np.linalg.cholesky(R + np.eye(n) * 1e-9)
                log_det = 2.0 * np.log(np.diag(L_R)).sum()
                v       = np.linalg.solve(L_R, SR[t])
                ll     += log_det + float(v @ v) - float(SR[t] @ SR[t])
            except np.linalg.LinAlgError:
                return 1e10
            Q = (1 - a - b) * Q_bar + a * np.outer(SR[t], SR[t]) + b * Q
        return ll   # minimise → quasi-negative log-likelihood

    a_fit, b_fit = 0.03, 0.93   # defaults if optimisation fails
    import warnings as _warnings
    try:
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            res = minimize(
                _neg_loglik, x0=[0.03, 0.93], method="SLSQP",
                bounds=[(1e-4, 0.20), (0.70, 0.9998)],
                constraints=[{"type": "ineq", "fun": lambda p: 0.9999 - p[0] - p[1]}],
                options={"maxiter": 300, "ftol": 1e-7},
            )
        if res.success:
            a_fit, b_fit = float(res.x[0]), float(res.x[1])
            logger.info(f"DCC fitted: a={a_fit:.4f}  b={b_fit:.4f}")
        else:
            logger.warning("DCC optimisation did not converge — using defaults a=0.03 b=0.93")
    except Exception as e:
        logger.warning(f"DCC fit error: {e} — using defaults")

    # Propagate Q to last historical value (Q_T = simulation starting point)
    Q = Q_bar.copy()
    for t in range(T):
        Q = (1 - a_fit - b_fit) * Q_bar + a_fit * np.outer(SR[t], SR[t]) + b_fit * Q

    return {"a": a_fit, "b": b_fit, "Q_bar": Q_bar, "Q0": Q}


# ── Hawkes calibration ────────────────────────────────────────────────────────

def _calibrate_hawkes(ret_df: pd.DataFrame, gp: dict) -> dict:
    """
    Calibrate Hawkes jump-process parameters per asset.

      λ₀  = empirical rate of |r_t| > EXTREME_K·σ_t events
      β   = 0.5 (half-life ≈ 1.4 days)
      α   = β × min(2 × lag-1 autocorr of extreme indicator, 0.80)
      μ_J, σ_J = mean / std of returns on extreme-event days
    """
    params = {}
    cols   = list(ret_df.columns)
    R      = ret_df.values
    T      = R.shape[0]
    h0_p2  = gp["h0"]   # pct²

    for i, col in enumerate(cols):
        r_pct = R[:, i] * 100
        lam   = 0.94
        sig2  = np.empty(T)
        sig2[0] = max(h0_p2[i], r_pct[:21].var())
        for t in range(1, T):
            sig2[t] = lam * sig2[t - 1] + (1 - lam) * r_pct[t - 1] ** 2
        sigma_pct = np.sqrt(sig2)

        extreme = np.abs(r_pct) > EXTREME_K * sigma_pct
        n_ext   = int(extreme.sum())
        lambda0 = max(n_ext / T, 1.0 / T)

        I    = extreme.astype(float)
        rho1 = max(0.0, float(np.corrcoef(I[:-1], I[1:])[0, 1])) if n_ext >= 5 else 0.2
        beta_h  = 0.5
        alpha_h = beta_h * min(2.0 * rho1, 0.80)

        ext_r  = R[:, i][extreme]
        mu_J   = float(ext_r.mean()) if len(ext_r) > 0 else 0.0
        sig_J  = float(ext_r.std())  if len(ext_r) > 1 else float(sigma_pct.mean()) / 100
        sig_J  = max(sig_J, float(sigma_pct.mean()) * EXTREME_K / 100)

        params[col] = {
            "lambda0": lambda0, "alpha": alpha_h, "beta": beta_h,
            "mu_J": mu_J, "sig_J": sig_J,
        }
        logger.debug(
            f"Hawkes [{col}]: λ₀={lambda0:.4f} α={alpha_h:.3f} β={beta_h:.3f} "
            f"μ_J={mu_J*100:.2f}% σ_J={sig_J*100:.2f}%"
        )
    return params


# ── Path simulators ───────────────────────────────────────────────────────────

def _hawkes_dcc_paths(
    mu_daily: np.ndarray,
    n_paths: int,
    horizon: int,
    gp: dict,
    dcc: dict,
    hp: dict,
) -> np.ndarray:
    """
    Fully vectorised simulation: GARCH(1,1) + DCC dynamic correlation + Hawkes jumps.

    All n_paths advance together at each time step t using batch numpy ops:

      State variables (path-level, updated each step)
      ─────────────────────────────────────────────────
      h    (n_paths, n)    GARCH conditional variance  [pct²]
      Q    (n_paths, n, n) DCC conditional Q matrix
      lam  (n_paths, n)    Hawkes jump intensity

      Step t
      ──────
      1. R_t   = diag(Q)^{-½} Q diag(Q)^{-½}          normalise Q → correlation
      2. L_t   = chol(R_t)                              batched Cholesky (n_paths, n, n)
      3. z_c   = L_t @ z,  z ~ N(0,I)                   correlated innovations
      4. r_diff = μ + σ · z_c                            GARCH diffusion
      5. B_t   ~ Bernoulli(λ_t),  J ~ N(μ_J, σ_J²)    Hawkes jump
      6. r_t   = r_diff + B_t · J
      7. h update: GARCH(1,1) on diffusion innovation
      8. Q update: DCC recursion on total standardised return
      9. λ update: Hawkes decay + self-excitation
    """
    n    = gp["n"]
    cols = list(hp.keys())

    # GARCH parameters (broadcast-ready)
    omega = gp["omega"]   # (n,)
    alpha = gp["alpha"]
    beta  = gp["beta"]

    # DCC parameters
    dcc_a   = dcc["a"]
    dcc_b   = dcc["b"]
    Q_bar   = dcc["Q_bar"]                         # (n, n)
    Q_coef  = (1.0 - dcc_a - dcc_b) * Q_bar       # constant term, (n, n)

    # Hawkes parameters (broadcast-ready)
    lam0   = np.array([hp[c]["lambda0"] for c in cols])   # (n,)
    h_alph = np.array([hp[c]["alpha"]   for c in cols])
    decay  = np.exp(-np.array([hp[c]["beta"] for c in cols]))
    mu_J   = np.array([hp[c]["mu_J"]    for c in cols])
    sig_J  = np.array([hp[c]["sig_J"]   for c in cols])

    # Initialise path-level state
    h   = np.tile(gp["h0"],  (n_paths, 1)).astype(float)  # (n_paths, n)
    Q   = np.tile(dcc["Q0"], (n_paths, 1, 1)).astype(float)  # (n_paths, n, n)
    lam = np.tile(lam0,      (n_paths, 1)).astype(float)  # (n_paths, n)

    rng   = np.random.default_rng(42)
    paths = np.zeros((n_paths, horizon, n), dtype=float)

    idx_n = np.arange(n)   # used for diagonal indexing

    for t in range(horizon):
        # ── DCC: correlation matrix R_t from Q ───────────────────────────────
        Q_diag  = Q[:, idx_n, idx_n]                        # (n_paths, n)
        q_isqrt = 1.0 / np.sqrt(np.maximum(Q_diag, 1e-10)) # (n_paths, n)
        R_t     = q_isqrt[:, :, None] * Q * q_isqrt[:, None, :]  # (n_paths, n, n)
        R_t    += np.eye(n) * 1e-7                          # nudge PSD

        # ── Batched Cholesky ─────────────────────────────────────────────────
        try:
            L_t = np.linalg.cholesky(R_t)                   # (n_paths, n, n)
        except np.linalg.LinAlgError:
            R_t += np.eye(n) * 1e-4
            L_t  = np.linalg.cholesky(R_t)

        # ── Correlated GARCH innovations ──────────────────────────────────────
        z   = rng.standard_normal((n_paths, n))             # (n_paths, n)
        z_c = np.einsum("pij,pj->pi", L_t, z)              # (n_paths, n)

        sigma  = np.sqrt(h)                                  # pct, (n_paths, n)
        r_diff = (mu_daily * 100.0 + sigma * z_c) / 100.0  # decimal

        # ── Hawkes jumps (Bernoulli approx; valid when λ ≪ 1) ────────────────
        jump_mask = rng.uniform(size=(n_paths, n)) < lam    # (n_paths, n) bool
        jump_size = rng.normal(mu_J, sig_J, (n_paths, n))  # (n_paths, n)
        jump_tot  = jump_mask * jump_size                   # zero where no jump

        r_total        = r_diff + jump_tot
        paths[:, t, :] = r_total

        # ── GARCH update ──────────────────────────────────────────────────────
        eps = sigma * z_c                                    # pct diffusion innovation
        h   = omega + alpha * eps ** 2 + beta * h

        # ── DCC Q update ──────────────────────────────────────────────────────
        # Use total standardised return (includes jumps, mirrors historical obs)
        sig_d    = np.maximum(sigma / 100.0, 1e-10)
        eps_norm = r_total / sig_d                           # (n_paths, n)
        outer    = np.einsum("pi,pj->pij", eps_norm, eps_norm)  # (n_paths, n, n)
        Q        = Q_coef + dcc_a * outer + dcc_b * Q

        # ── Hawkes intensity update ───────────────────────────────────────────
        extreme = np.abs(r_total) > EXTREME_K * sig_d       # (n_paths, n)
        lam     = lam0 + (lam - lam0) * decay + h_alph * extreme

    return paths


def _garch_cc_paths(
    mu_daily: np.ndarray,
    n_paths: int,
    horizon: int,
    gp: dict,
) -> np.ndarray:
    """Vectorised GARCH(1,1) + constant correlation, no jumps (fallback)."""
    n     = gp["n"]
    omega = gp["omega"]
    alpha = gp["alpha"]
    beta  = gp["beta"]
    L     = gp["L"]

    h   = np.tile(gp["h0"], (n_paths, 1)).astype(float)
    rng = np.random.default_rng(42)
    paths = np.zeros((n_paths, horizon, n))

    for t in range(horizon):
        z     = rng.standard_normal((n_paths, n))
        z_c   = z @ L.T                              # (n_paths, n) correlated
        sigma = np.sqrt(h)
        paths[:, t, :] = (mu_daily * 100.0 + sigma * z_c) / 100.0
        eps = sigma * z_c
        h   = omega + alpha * eps ** 2 + beta * h

    return paths


def _gbm_paths(
    ret_df: pd.DataFrame,
    mu_daily: np.ndarray,
    n_paths: int,
    horizon: int,
) -> np.ndarray:
    """Fully vectorised GBM with historical covariance."""
    n   = ret_df.shape[1]
    L   = _chol_psd(ret_df.cov().values)
    rng = np.random.default_rng(42)
    z   = rng.standard_normal((n_paths, horizon, n))
    return z @ L.T + mu_daily


# ── Utilities ─────────────────────────────────────────────────────────────────

def _chol_psd(M: np.ndarray) -> np.ndarray:
    """Lower Cholesky factor of M; nudges toward PSD if needed."""
    n = M.shape[0]
    for delta in (0.0, 1e-8, 1e-6, 1e-4, 1e-2):
        try:
            return np.linalg.cholesky(M + delta * np.eye(n))
        except np.linalg.LinAlgError:
            continue
    vals, vecs = np.linalg.eigh(M)
    return np.linalg.cholesky(vecs @ np.diag(np.maximum(vals, 1e-6)) @ vecs.T)


def _make_psd(M: np.ndarray) -> np.ndarray:
    """Nearest PSD matrix via eigenvalue clipping."""
    vals, vecs = np.linalg.eigh(M)
    return vecs @ np.diag(np.maximum(vals, 1e-8)) @ vecs.T
