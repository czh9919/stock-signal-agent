import os
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

COMPANY_NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA",
    "TSLA": "Tesla", "AMZN": "Amazon", "GOOGL": "Alphabet",
    "META": "Meta Platforms", "SPY": "S&P 500", "QQQ": "Nasdaq 100",
    "NBIS": "Nebius Group", "AMD": "Advanced Micro Devices",
}


class StockFetcher:
    def __init__(self, news_api_key: Optional[str] = None):
        self.news_api_key = news_api_key or os.environ.get("NEWS_API_KEY")

    def fetch_price_history(self, ticker: str, days: int = 365) -> Optional[pd.DataFrame]:
        """Fetch OHLCV history. Returns None on failure."""
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            df = yf.Ticker(ticker).history(start=start, end=end)
            if df.empty:
                logger.warning(f"{ticker}: no price data returned")
                return None
            df.index = pd.to_datetime(df.index).tz_localize(None)
            logger.info(f"{ticker}: fetched {len(df)} trading days")
            return df
        except Exception as e:
            logger.error(f"{ticker}: price fetch failed — {e}")
            return None

    def fetch_news(self, ticker: str, limit: int = 10) -> list[dict]:
        """Fetch recent news articles. Returns [] on failure or missing key."""
        if not self.news_api_key:
            logger.warning("NEWS_API_KEY not set — skipping news fetch")
            return []
        try:
            from newsapi import NewsApiClient
            client = NewsApiClient(api_key=self.news_api_key)
            company = COMPANY_NAMES.get(ticker, ticker)
            from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            resp = client.get_everything(
                q=f'"{ticker}" OR "{company}"',
                language="en",
                sort_by="publishedAt",
                page_size=limit,
                from_param=from_date,
            )
            articles = []
            for a in resp.get("articles", [])[:limit]:
                articles.append({
                    "title": a.get("title") or "",
                    "description": a.get("description") or "",
                    "published_at": a.get("publishedAt") or "",
                    "source": (a.get("source") or {}).get("name", ""),
                })
            logger.info(f"{ticker}: fetched {len(articles)} news articles")
            return articles
        except Exception as e:
            logger.error(f"{ticker}: news fetch failed — {e}")
            return []

    def fetch_current_price(self, ticker: str) -> Optional[float]:
        """Return the most recent closing price."""
        try:
            info = yf.Ticker(ticker).fast_info
            price = getattr(info, "last_price", None)
            if price is None:
                df = self.fetch_price_history(ticker, days=5)
                if df is not None and not df.empty:
                    price = float(df["Close"].iloc[-1])
            return price
        except Exception as e:
            logger.error(f"{ticker}: current price fetch failed — {e}")
            return None
