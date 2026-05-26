"""
Risk-constrained weight allocation from robust factor signals.
  compute_signal_weights()  — score → capped weights + factor exposure check
"""
import numpy as np
from typing import Optional

FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
_BKEYS  = {"Mkt-RF": "beta_mkt", "SMB": "beta_smb", "HML": "beta_hml",
            "RMW": "beta_rmw", "CMA": "beta_cma"}


def compute_signal_weights(
    stock_factors: list[dict],
    max_weight: float = 0.20,
    max_factor_exposure: float = 1.5,
    min_psr: float = 0.65,
) -> list[dict]:
    """
    Translates robust factor signals into portfolio weights.

    Eligible: robust_signal == "BUY", PSR >= min_psr, IR_deflated > 0
    Score: PSR × IR_deflated × consistency_boost
    Weights: score-proportional, capped at max_weight, renormalised.
    Flags portfolio-level factor exposures exceeding max_factor_exposure.

    Returns list sorted by weight descending, each entry:
      ticker, weight, score, psr, ir_deflated, alpha_consistency,
      factor_exposures, port_factor_exposures, exposure_warnings
    """
    if not stock_factors:
        return []

    candidates = [
        s for s in stock_factors
        if s.get("robust_signal") == "BUY"
        and (s.get("psr") or 0.0) >= min_psr
        and (s.get("ir_deflated") or 0.0) > 0
    ]
    if not candidates:
        return []

    def _score(s: dict) -> float:
        psr   = float(s.get("psr") or 0.5)
        ir_d  = max(float(s.get("ir_deflated") or 0.0), 0.0)
        cons  = int(s.get("alpha_consistency") or 0)
        nw    = int(s.get("n_windows") or 3)
        return psr * ir_d * (1.0 + 0.2 * cons / max(nw, 1))

    scores = [_score(s) for s in candidates]
    total  = sum(scores)
    if total < 1e-12:
        return []

    weights = [sc / total for sc in scores]

    # Iterative cap at max_weight
    for _ in range(30):
        excess = sum(max(w - max_weight, 0.0) for w in weights)
        if excess < 1e-7:
            break
        uncapped     = [i for i, w in enumerate(weights) if w < max_weight - 1e-9]
        spare        = sum(max_weight - weights[i] for i in uncapped)
        if spare < 1e-12:
            for i, w in enumerate(weights):
                weights[i] = min(w, max_weight)
            break
        for i in range(len(weights)):
            if weights[i] >= max_weight:
                weights[i] = max_weight
            else:
                weights[i] += excess * (max_weight - weights[i]) / spare

    total = sum(weights)
    if total < 1e-12:
        return []
    weights = [w / total for w in weights]

    result = []
    for s, w, sc in zip(candidates, weights, scores):
        result.append({
            "ticker":            s["ticker"],
            "weight":            round(w, 4),
            "score":             round(sc, 6),
            "psr":               s.get("psr"),
            "ir_deflated":       s.get("ir_deflated"),
            "alpha_consistency": s.get("alpha_consistency"),
            "factor_exposures":  {f: w * float(s.get(_BKEYS[f]) or 0.0) for f in FACTORS},
        })

    result.sort(key=lambda x: x["weight"], reverse=True)

    # Portfolio-level factor exposures
    port_exp = {f: sum(e["factor_exposures"][f] for e in result) for f in FACTORS}
    warnings = {f: abs(v) for f, v in port_exp.items() if abs(v) > max_factor_exposure}
    for e in result:
        e["port_factor_exposures"] = port_exp
        e["exposure_warnings"]     = warnings

    return result
