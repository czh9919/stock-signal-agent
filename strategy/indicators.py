"""
Technical indicator calculation using the 'ta' library.
All values are computed numerically — never estimated by AI.
"""
import logging
from typing import Optional

import pandas as pd
import ta
from ta.trend import SMAIndicator, EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange

logger = logging.getLogger(__name__)


class IndicatorCalculator:
    def compute(self, df: pd.DataFrame) -> Optional[dict]:
        """
        Compute all required technical indicators from OHLCV data.
        Returns a dict of the latest values, or None if data is insufficient.
        """
        if df is None or len(df) < 30:
            logger.warning("Insufficient data for indicator calculation (need ≥30 rows)")
            return None

        try:
            close  = df["Close"]
            high   = df["High"]
            low    = df["Low"]
            volume = df["Volume"]

            # Moving Averages
            sma20  = SMAIndicator(close=close, window=20).sma_indicator()
            sma50  = SMAIndicator(close=close, window=50).sma_indicator() if len(df) >= 50 else None
            sma200 = SMAIndicator(close=close, window=200).sma_indicator() if len(df) >= 200 else None
            ema20  = EMAIndicator(close=close, window=20).ema_indicator()

            # MACD (12, 26, 9)
            macd_ind  = MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
            macd_line = macd_ind.macd()
            macd_sig  = macd_ind.macd_signal()
            macd_hist = macd_ind.macd_diff()

            # RSI (14)
            rsi14 = RSIIndicator(close=close, window=14).rsi()

            # Bollinger Bands (20, 2σ)
            bb = BollingerBands(close=close, window=20, window_dev=2)
            bb_upper = bb.bollinger_hband()
            bb_mid   = bb.bollinger_mavg()
            bb_lower = bb.bollinger_lband()

            # ATR (14)
            atr14 = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

            # Volume 10-day SMA
            vol_sma10 = SMAIndicator(close=volume.astype(float), window=10).sma_indicator()

            def last(series):
                if series is None or (hasattr(series, "empty") and series.empty):
                    return None
                val = series.iloc[-1]
                return float(val) if pd.notna(val) else None

            return {
                "price": last(close),
                "sma20":  last(sma20),
                "sma50":  last(sma50),
                "sma200": last(sma200),
                "ema20":  last(ema20),
                "macd":        last(macd_line),
                "macd_signal": last(macd_sig),
                "macd_hist":   last(macd_hist),
                "rsi14":    last(rsi14),
                "bb_upper": last(bb_upper),
                "bb_mid":   last(bb_mid),
                "bb_lower": last(bb_lower),
                "atr14":    last(atr14),
                "volume":       last(volume),
                "volume_sma10": last(vol_sma10),
                "price_series_30d": close.tail(30).round(2).tolist(),
            }
        except Exception as e:
            logger.error(f"Indicator calculation failed: {e}", exc_info=True)
            return None
