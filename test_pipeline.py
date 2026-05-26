"""
Pipeline smoke tests — run each module and print results.
Usage: python test_pipeline.py
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"

def section(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print('='*55)

def ok(msg):   print(f"  {PASS}  {msg}")
def err(msg):  print(f"  {FAIL}  {msg}")
def skip(msg): print(f"  {SKIP}  {msg}")

TICKER = "AAPL"

# ── Phase 1: Data layer ───────────────────────────────────────────────────────

section("Phase 1 — Data Layer")

from data.fetcher import StockFetcher
from data.cleaner import DataCleaner
from data.storage import Storage

fetcher = StockFetcher()
cleaner = DataCleaner()
storage = Storage("data/test_agent.db")

df = fetcher.fetch_price_history(TICKER, days=90)
if df is not None and len(df) > 0:
    ok(f"fetch_price_history: {len(df)} rows, latest close = {df['Close'].iloc[-1]:.2f}")
else:
    err("fetch_price_history returned None or empty")
    sys.exit(1)

df, warns = cleaner.validate_price_data(TICKER, df)
ok(f"validate_price_data: {len(df)} rows after clean, {len(warns)} warnings")
for w in warns:
    print(f"       warn: {w}")

storage.save_price_history(TICKER, df)
loaded = storage.load_price_history(TICKER, days=30)
if loaded is not None and len(loaded) > 0:
    ok(f"SQLite save/load: {len(loaded)} rows round-tripped")
else:
    err("SQLite round-trip failed")

price = fetcher.fetch_current_price(TICKER)
if price:
    ok(f"fetch_current_price: ${price:.2f}")
else:
    err("fetch_current_price failed")

# ── Phase 2: Technical indicators ────────────────────────────────────────────

section("Phase 2 — Technical Indicators + Signals")

from strategy.indicators import IndicatorCalculator
from strategy.signals import evaluate_signals

calc = IndicatorCalculator()
ind = calc.compute(df)

if ind:
    ok(f"Price:   ${ind['price']:.2f}")
    ok(f"SMA20:   {ind['sma20']:.2f}  |  SMA50: {ind['sma50'] or 'N/A'}  |  SMA200: {ind['sma200'] or 'N/A'}")
    ok(f"MACD:    {ind['macd']:.4f}  |  Signal: {ind['macd_signal']:.4f}")
    ok(f"RSI(14): {ind['rsi14']:.2f}")
    ok(f"BB:      upper={ind['bb_upper']:.2f}  lower={ind['bb_lower']:.2f}")
    ok(f"ATR(14): {ind['atr14']:.2f}")
else:
    err("IndicatorCalculator.compute returned None")
    sys.exit(1)

signals = evaluate_signals(ind)
ok(f"Signal score: {signals.score:.2f}  |  "
   f"bull={len(signals.bullish)}  bear={len(signals.bearish)}")

# ── Phase 3: FF5 Factor model ─────────────────────────────────────────────────

section("Phase 3 — Fama-French 5-Factor Regression")

from strategy.factor_model import run_factor_regression, portfolio_factor_exposure

# Synthetic test with known beta
rng = np.random.default_rng(42)
n   = 300
idx = pd.date_range("2022-01-01", periods=n, freq="B")
cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
ff5_syn = pd.DataFrame(rng.normal(0, 0.01, (n, 6)), index=idx, columns=cols)
ff5_syn["RF"] = 0.00012
ret_syn = pd.Series(ff5_syn["Mkt-RF"] * 1.1 + 0.0003 + rng.normal(0, 0.008, n), index=idx)

res = run_factor_regression("SYNTHETIC", ret_syn, ff5_syn, window=252)
if res:
    ok(f"Regression: α={res['alpha_ann']*100:+.1f}%  β_MKT={res['beta_mkt']:.2f}  "
       f"t={res['t_alpha']:.2f}  IR={res['ir']:.3f}  R²={res['r_squared']:.2f}  signal={res['signal']}")
else:
    err("run_factor_regression returned None for 300-day series")

res_short = run_factor_regression("SHORT", ret_syn.iloc[:30], ff5_syn.iloc[:30])
if res_short is None:
    ok("Correctly returns None for <63 days")
else:
    err("Should return None for <63 days")

# Live FF5 fetch (optional — requires pandas_datareader + internet)
try:
    from risk.optimizer import fetch_ff5
    ff5_live = fetch_ff5()
    if ff5_live is not None:
        ok(f"FF5 live data: {len(ff5_live)} rows "
           f"({ff5_live.index[0].date()} – {ff5_live.index[-1].date()})")
        # Run regression with real AAPL data aligned to FF5
        from data.price_loader import load_prices
        aapl_pd = load_prices([TICKER], days=300).get(TICKER)
        if aapl_pd and aapl_pd.returns is not None:
            res_live = run_factor_regression(TICKER, aapl_pd.returns, ff5_live)
            if res_live:
                ok(f"{TICKER} live: α={res_live['alpha_ann']*100:+.1f}%  "
                   f"β_MKT={res_live['beta_mkt']:.2f}  IR={res_live['ir']:.3f}  "
                   f"signal={res_live['signal']}  n={res_live['n_days']}")
            else:
                skip(f"{TICKER}: too few aligned days")
    else:
        skip("FF5 fetch failed — check pandas_datareader / internet")
except Exception as e:
    skip(f"FF5 live test skipped: {e}")

# ── Phase 4: Report generation ────────────────────────────────────────────────

section("Phase 4 — Report Generation")

from notify.report_gen import build_report

metrics = {"overall_rag": "GREEN", "nav_eur": 50000,
           "total_pnl_eur": 2500, "var_95_ewma": 0.025,
           "var_95_cf": 0.028, "var_99_cf": 0.042, "var_99_evt": 0.045,
           "cvar_95": 0.035, "sharpe": 1.1, "beta": 1.05,
           "max_drawdown": 0.12, "hhi": 0.18, "max_position_wt": 0.25,
           "port_sigma_annual": 0.18, "alerts": {}}
holdings_test = [
    {"ticker": TICKER, "platform": "IBKR", "weight": 0.60,
     "market_value_eur": 30000, "cost_basis_eur": 25000,
     "unrealised_pnl_eur": 5000, "asset_class": "equity"},
]
stock_factors_test = [
    {"ticker": TICKER, "price": price, "signal": "BUY",
     "alpha_ann": 0.08, "beta_mkt": 1.15, "t_alpha": 2.1,
     "ir": 0.45, "r_squared": 0.72, "n_days": 252},
]
port_exposure_test = {
    "alpha_ann": 0.06, "beta_mkt": 1.12, "beta_smb": 0.18,
    "beta_hml": -0.08, "beta_rmw": 0.25, "beta_cma": -0.04,
}

html_en, html_zh = build_report(
    metrics, {}, holdings_test, {},
    stock_factors=stock_factors_test,
    port_exposure=port_exposure_test,
)
ok(f"EN report: {len(html_en):,} chars")
ok(f"ZH report: {len(html_zh):,} chars")

assert "Stock Factor Rankings" in html_en, "factor rankings missing from EN"
assert "个股因子排名" in html_zh, "factor rankings missing from ZH"
assert "Portfolio Factor Exposures" in html_en, "portfolio exposure missing from EN"
assert "BUY" in html_en
ok("Factor sections present in both languages")

preview_path = Path("data/test_report.html")
preview_path.parent.mkdir(exist_ok=True)
preview_path.write_text(html_en, encoding="utf-8")
ok(f"HTML preview → {preview_path}")

# ── Summary ───────────────────────────────────────────────────────────────────

section("Summary")
print("  All phases completed.")
print("  Open data/test_report.html to inspect the HTML report.\n")
