"""
M2 — Historical price data via yfinance + data availability rules.

Availability flags (PRD §6.1):
  ok          ≥252 days → full window
  reduced     21-251 days → use actual, flag [{N}d window]
  excluded    <21 days → skip from VaR/vol calcs, include in NAV/P&L
  unavailable fetch failed
  not_found   ticker not on Yahoo Finance
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

FX_TICKERS = {
    "USDEUR": "EURUSD=X",   # invert
    "GBPEUR": "EURGBP=X",   # invert
    "AUDEUR": "EURAUD=X",   # invert
}

@dataclass
class PriceData:
    ticker:     str
    closes:     Optional[pd.Series]     # daily closing prices
    returns:    Optional[pd.Series]     # daily log/pct returns
    days:       int = 0
    status:     str = "ok"              # ok | reduced | excluded | unavailable | not_found
    flag:       str = ""                # shown in report footer


def load_prices(tickers: list[str], days: int = 252) -> dict[str, PriceData]:
    results: dict[str, PriceData] = {}
    end   = datetime.now()
    start = end - timedelta(days=int(days * 1.5))  # extra buffer for trading day gaps

    for ticker in tickers:
        results[ticker] = _fetch_one(ticker, start, end, days)

    return results


def _fetch_one(ticker: str, start: datetime, end: datetime, target_days: int) -> PriceData:
    try:
        df = yf.Ticker(ticker).history(start=start, end=end)
        if df is None or df.empty:
            logger.warning(f"{ticker}: not found on yfinance")
            return PriceData(ticker=ticker, closes=None, returns=None,
                             status="not_found", flag="[Ticker not found — manual check needed]")

        closes  = df["Close"].dropna()
        n       = len(closes)
        returns = closes.pct_change().dropna()

        if n < 21:
            logger.warning(f"{ticker}: only {n} days — excluded from VaR")
            return PriceData(ticker=ticker, closes=closes, returns=returns, days=n,
                             status="excluded", flag="[Excluded from VaR]")
        elif n < target_days:
            flag = f"[{n}d window]"
            logger.info(f"{ticker}: {n} days available (reduced window)")
            return PriceData(ticker=ticker, closes=closes.tail(n), returns=returns.tail(n),
                             days=n, status="reduced", flag=flag)
        else:
            closes_  = closes.tail(target_days)
            returns_ = returns.tail(target_days)
            return PriceData(ticker=ticker, closes=closes_, returns=returns_,
                             days=len(closes_), status="ok")

    except Exception as e:
        logger.error(f"{ticker}: price fetch failed — {e}")
        return PriceData(ticker=ticker, closes=None, returns=None,
                         status="unavailable", flag="[Price data unavailable]")


def load_fx_rates() -> dict[str, float]:
    """Return {USDEUR: float, GBPEUR: float, ...} — all rates to EUR."""
    rates = {"USDEUR": 0.92, "GBPEUR": 1.17, "AUDEUR": 0.59}  # fallback defaults
    for pair, yticker in FX_TICKERS.items():
        try:
            df = yf.Ticker(yticker).history(period="2d")
            if not df.empty:
                raw = float(df["Close"].iloc[-1])
                # These tickers are EUR/XXX so we need to invert to get XXX/EUR
                rates[pair] = 1.0 / raw
        except Exception as e:
            logger.warning(f"FX {pair}: fetch failed, using default {rates[pair]:.4f} — {e}")
    return rates
