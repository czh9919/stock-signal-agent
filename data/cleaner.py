import logging
from datetime import date
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

MAX_DAILY_MOVE   = 0.50  # 50% — triggers human review flag
MAX_MISSING_DAYS = 10    # >10 missing trading days (~2 weeks) flags a gap
STALE_PRICE_DAYS = 4     # last row must be ≤4 calendar days old (covers long weekends)


class DataCleaner:
    def validate_price_data(self, ticker: str, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        """
        Validate and clean price data.
        Returns (cleaned_df, warnings). warnings is non-empty when data quality is poor.
        """
        warnings: list[str] = []

        if df is None or df.empty:
            return df, [f"{ticker}: empty dataframe"]

        # Drop rows with all-NaN OHLC
        df = df.dropna(subset=["Open", "High", "Low", "Close"])

        # Check for excessive data gaps (calendar days → rough trading day count)
        date_range_days = (df.index[-1] - df.index[0]).days
        expected_trading_days = date_range_days * 5 / 7
        actual_trading_days = len(df)
        if expected_trading_days - actual_trading_days > MAX_MISSING_DAYS:
            msg = f"{ticker}: data gap detected ({expected_trading_days:.0f} expected vs {actual_trading_days} actual trading days)"
            logger.warning(msg)
            warnings.append(msg)

        # Detect abnormal single-day price moves
        daily_returns = df["Close"].pct_change().abs()
        anomalies = daily_returns[daily_returns > MAX_DAILY_MOVE]
        if not anomalies.empty:
            for dt, ret in anomalies.items():
                msg = f"{ticker}: abnormal price move {ret:.1%} on {pd.Timestamp(dt).date()} — manual review recommended"
                logger.warning(msg)
                warnings.append(msg)

        # Detect stale / frozen price — last row should be recent
        last_date  = pd.Timestamp(df.index[-1]).date()
        days_stale = (date.today() - last_date).days
        if days_stale > STALE_PRICE_DAYS:
            msg = (f"{ticker}: last price date is {last_date} "
                   f"({days_stale}d ago) — possible halt, delisting, or feed freeze")
            logger.warning(msg)
            warnings.append(msg)

        return df, warnings

    def validate_news(self, ticker: str, articles: list[dict]) -> tuple[list[dict], list[str]]:
        """Remove articles with empty title and description."""
        warnings: list[str] = []
        cleaned = [a for a in articles if a.get("title") or a.get("description")]
        if not cleaned and articles:
            warnings.append(f"{ticker}: all news articles had empty content")
        return cleaned, warnings
