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
    def fetch_price_history(self, ticker: str, days: int = 365) -> Optional[pd.DataFrame]:
        """Fetch OHLCV history. Returns None on failure."""
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            # auto_adjust=True: split- and dividend-adjusted closes
            df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
            if df.empty:
                logger.warning(f"{ticker}: no price data returned")
                return None
            df.index = pd.to_datetime(df.index).tz_localize(None)
            logger.info(f"{ticker}: fetched {len(df)} trading days")
            return df
        except Exception as e:
            logger.error(f"{ticker}: price fetch failed — {e}")
            return None

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
