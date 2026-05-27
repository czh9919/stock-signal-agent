"""
S&P 500 constituent loader + bulk price downloader for the walk-forward universe.

get_sp500_tickers()  — scrape Wikipedia, cache 1 week
load_universe()      — bulk yfinance download for SP500 + watchlist tickers,
                       returns same PriceData dict format as price_loader.load_prices()
"""
import logging
import pickle
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from data.price_loader import PriceData

logger = logging.getLogger(__name__)

_SP500_CACHE      = Path("cache/sp500_tickers.pkl")
_SP500_COMPANIES  = Path("cache/sp500_companies.pkl")
_SP500_CACHE_TTL  = 7 * 24 * 3600   # 1 week
_PRICE_CACHE      = Path("cache/universe_prices.pkl")
_PRICE_CACHE_TTL  = 20 * 3600       # 20 h (same as FF5)


_WIKI_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-ai-bot/1.0)"}
_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _fetch_sp500_table() -> pd.DataFrame:
    """Scrape Wikipedia S&P 500 table; returns DataFrame with ticker/name/sector."""
    import requests, io
    html = requests.get(_WIKI_URL, headers=_WIKI_HEADERS, timeout=15).text
    tables = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})
    df = tables[0][["Symbol", "Security", "GICS Sector"]].copy()
    df.columns = ["ticker", "name", "sector"]
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    return df


def get_sp500_tickers() -> list[str]:
    """Return current S&P 500 ticker list (Wikipedia scrape, cached 1 week)."""
    if _SP500_CACHE.exists() and (time.time() - _SP500_CACHE.stat().st_mtime) < _SP500_CACHE_TTL:
        try:
            with open(_SP500_CACHE, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass

    try:
        df = _fetch_sp500_table()
        tickers = df["ticker"].tolist()
        _SP500_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SP500_CACHE, "wb") as f:
            pickle.dump(tickers, f)
        logger.info(f"S&P 500: {len(tickers)} tickers cached")
        return tickers
    except Exception as exc:
        logger.warning(f"SP500 list fetch failed: {exc}")
        return []


def get_sp500_companies() -> list[dict]:
    """Return [{ticker, name, sector}] for all S&P 500 constituents (cached 1 week)."""
    if _SP500_COMPANIES.exists() and (time.time() - _SP500_COMPANIES.stat().st_mtime) < _SP500_CACHE_TTL:
        try:
            with open(_SP500_COMPANIES, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    try:
        df = _fetch_sp500_table()
        companies = df.to_dict("records")
        _SP500_COMPANIES.parent.mkdir(parents=True, exist_ok=True)
        with open(_SP500_COMPANIES, "wb") as f:
            pickle.dump(companies, f)
        logger.info(f"S&P 500 companies: {len(companies)} entries cached")
        return companies
    except Exception as exc:
        logger.warning(f"SP500 companies fetch failed: {exc}")
        return []


def search_sp500(query: str, limit: int = 20) -> list[dict]:
    """Search S&P 500 by ticker or company name. Returns [{ticker, name, sector}]."""
    q = query.strip().lower()
    if not q:
        return []
    companies = get_sp500_companies()
    results = [
        c for c in companies
        if q in c["ticker"].lower() or q in c["name"].lower()
    ]
    return results[:limit]


def load_universe(
    watchlist_tickers: list[str],
    days: int = 1260,        # ~5 years; enough for 3y train + 2y test
    max_sp500: int = 100,    # number of SP500 tickers to add
) -> dict[str, PriceData]:
    """
    Build combined universe: watchlist equities + up to max_sp500 SP500 constituents.
    Uses a single bulk yfinance download for speed, then wraps into PriceData objects.
    Cache is refreshed every 20 h (aligned with FF5 cache TTL).
    """
    sp500    = get_sp500_tickers()
    extra    = [t for t in sp500 if t not in watchlist_tickers][:max_sp500]
    universe = list(dict.fromkeys(watchlist_tickers + extra))   # preserve order, dedup
    logger.info(f"Universe: {len(watchlist_tickers)} watchlist "
                f"+ {len(extra)} SP500 = {len(universe)} total tickers")

    # Check cache
    cache_key = (tuple(sorted(universe)), days)
    if _PRICE_CACHE.exists() and (time.time() - _PRICE_CACHE.stat().st_mtime) < _PRICE_CACHE_TTL:
        try:
            with open(_PRICE_CACHE, "rb") as f:
                cached_key, cached_data = pickle.load(f)
            if cached_key == cache_key:
                logger.info("Universe prices loaded from cache")
                return cached_data
        except Exception:
            pass

    result = _bulk_download(universe, days)

    try:
        _PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(_PRICE_CACHE, "wb") as f:
            pickle.dump((cache_key, result), f)
    except Exception as exc:
        logger.warning(f"Universe price cache write failed: {exc}")

    return result


def _bulk_download(tickers: list[str], days: int) -> dict[str, PriceData]:
    """Download all tickers in one yf.download() call; split into PriceData."""
    end   = datetime.now()
    start = end - timedelta(days=int(days * 1.5))

    logger.info(f"Bulk downloading {len(tickers)} tickers from yfinance …")
    try:
        raw = yf.download(
            tickers,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.error(f"Bulk download failed: {exc}")
        return {t: PriceData(ticker=t, closes=None, returns=None,
                             status="unavailable", flag="[bulk fetch failed]")
                for t in tickers}

    # yf.download with multiple tickers → MultiIndex columns (field, ticker)
    # With a single ticker it returns flat columns — handle both
    if isinstance(raw.columns, pd.MultiIndex):
        close_all = raw["Close"]
    else:
        close_all = raw[["Close"]].rename(columns={"Close": tickers[0]})

    result: dict[str, PriceData] = {}
    for ticker in tickers:
        if ticker not in close_all.columns:
            result[ticker] = PriceData(ticker=ticker, closes=None, returns=None,
                                       status="not_found", flag="[not on yfinance]")
            continue
        closes = close_all[ticker].dropna()
        n = len(closes)
        if n < 21:
            result[ticker] = PriceData(ticker=ticker, closes=closes,
                                       returns=closes.pct_change().dropna(),
                                       days=n, status="excluded",
                                       flag="[Excluded from VaR — <21d]")
        elif n < days:
            returns = closes.pct_change().dropna()
            result[ticker] = PriceData(ticker=ticker, closes=closes, returns=returns,
                                       days=n, status="reduced", flag=f"[{n}d window]")
        else:
            closes_  = closes.tail(days)
            returns_ = closes_.pct_change().dropna()
            result[ticker] = PriceData(ticker=ticker, closes=closes_,
                                       returns=returns_, days=len(closes_), status="ok")

    ok_count = sum(1 for p in result.values() if p.status in ("ok", "reduced"))
    logger.info(f"Universe download complete: {ok_count}/{len(tickers)} tickers usable")
    return result
