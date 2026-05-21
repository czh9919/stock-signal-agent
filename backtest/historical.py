"""
vectorbt-based historical backtest.
Runs a simple MA-crossover + RSI strategy on 2 years of data.
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def run_backtest(ticker: str, df: pd.DataFrame) -> Optional[dict]:
    """
    Simple SMA-20/50 crossover strategy backtested with vectorbt.
    Returns metrics dict or None on failure.
    """
    if df is None or len(df) < 60:
        logger.warning(f"{ticker}: insufficient data for backtest (need ≥60 rows)")
        return None

    try:
        import vectorbt as vbt

        close = df["Close"].astype(float)

        fast_ma = close.rolling(20).mean()
        slow_ma = close.rolling(50).mean()

        entries = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
        exits   = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))

        portfolio = vbt.Portfolio.from_signals(
            close,
            entries,
            exits,
            init_cash=10_000,
            freq="D",
        )

        total_return = portfolio.total_return()
        n_years = len(close) / 252
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
        sharpe = portfolio.sharpe_ratio()
        max_dd = portfolio.max_drawdown()

        trades = portfolio.trades.records_readable
        if len(trades) > 0:
            win_rate = float((trades["PnL"] > 0).mean())
        else:
            win_rate = 0.0

        metrics = {
            "annual_return": float(annual_return),
            "sharpe_ratio": float(sharpe) if not np.isnan(sharpe) else 0.0,
            "max_drawdown": float(max_dd),
            "win_rate": win_rate,
        }
        logger.info(
            f"{ticker} backtest: annual={annual_return:.2%}, "
            f"sharpe={metrics['sharpe_ratio']:.2f}, "
            f"max_dd={max_dd:.2%}, win={win_rate:.2%}"
        )
        return metrics
    except ImportError:
        logger.warning("vectorbt not installed — skipping historical backtest")
        return None
    except Exception as e:
        logger.error(f"{ticker}: backtest failed — {e}", exc_info=True)
        return None
