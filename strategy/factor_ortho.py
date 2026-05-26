"""
Factor collinearity diagnostics.
  check_factor_collinearity()  — pairwise correlation, VIF, condition number
"""
import numpy as np
import pandas as pd
from typing import Optional

FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]


def check_factor_collinearity(ff5: pd.DataFrame, window: int = 252) -> dict:
    """
    Computes collinearity diagnostics for FF5 factor returns.

    Returns dict:
      corr_matrix:      pd.DataFrame  pairwise correlations
      vif:              {factor: float}  VIF (>5 concerning, >10 severe)
      condition_number: float  max/min eigenvalue of corr matrix
      high_pairs:       list of (fA, fB, corr) with |corr| > 0.4
      has_warning:      bool
      n_obs:            int
    """
    if ff5 is None:
        return {"has_warning": False, "n_obs": 0}

    # Keep only the columns that exist
    available = [f for f in FACTORS if f in ff5.columns]
    if len(available) < 2:
        return {"has_warning": False, "n_obs": 0}

    data = ff5[available].dropna().iloc[-window:]
    if len(data) < 30:
        return {"has_warning": False, "n_obs": len(data)}

    corr = data.corr()

    # VIF: regress each factor on all others
    X = data.values
    vif_dict: dict[str, float] = {}
    for i, fname in enumerate(available):
        y      = X[:, i]
        others = np.column_stack([np.ones(len(X))] + [X[:, j] for j in range(len(available)) if j != i])
        c, _, _, _ = np.linalg.lstsq(others, y, rcond=None)
        y_hat  = others @ c
        ss_tot = float(np.dot(y - y.mean(), y - y.mean()))
        ss_res = float(np.dot(y - y_hat, y - y_hat))
        r2     = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
        vif_dict[fname] = 1.0 / (1.0 - r2) if r2 < 0.9999 else float("inf")

    # Condition number
    eigs = np.linalg.eigvalsh(corr.values)
    eigs = np.maximum(eigs, 0.0)
    cond = float(eigs[-1] / eigs[0]) if eigs[0] > 1e-12 else float("inf")

    # High-correlation pairs (|corr| > 0.4)
    high_pairs = []
    for i in range(len(available)):
        for j in range(i + 1, len(available)):
            c_ij = float(corr.iloc[i, j])
            if abs(c_ij) > 0.4:
                high_pairs.append((available[i], available[j], round(c_ij, 3)))

    has_warning = (
        any(v > 5.0 for v in vif_dict.values()) or
        cond > 10.0 or
        bool(high_pairs)
    )

    return {
        "corr_matrix":      corr,
        "vif":              vif_dict,
        "condition_number": cond,
        "high_pairs":       high_pairs,
        "has_warning":      has_warning,
        "n_obs":            len(data),
    }
