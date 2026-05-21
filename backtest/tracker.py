"""
Live suggestion tracker.
Records AI suggestions and evaluates accuracy 30 days later.
"""
import logging
from typing import Optional

from data.storage import Storage
from data.fetcher import StockFetcher

logger = logging.getLogger(__name__)

ACCURACY_THRESHOLD = 0.0  # A BUY is "accurate" if price increased; SELL if decreased


class SuggestionTracker:
    def __init__(self, storage: Storage, fetcher: StockFetcher):
        self.storage = storage
        self.fetcher = fetcher

    def evaluate_pending(self):
        """Fetch unevaluated suggestions ~30 days old and compute actual return."""
        pending = self.storage.get_unevaluated_suggestions(days_ago=30)
        if not pending:
            logger.info("No pending suggestions to evaluate")
            return

        logger.info(f"Evaluating {len(pending)} pending suggestions")
        for s in pending:
            ticker = s["ticker"]
            entry_price = s.get("price_at_suggestion")
            recommendation = s.get("recommendation", "").upper()
            if not entry_price:
                continue

            current_price = self.fetcher.fetch_current_price(ticker)
            if not current_price:
                logger.warning(f"{ticker}: could not fetch current price for evaluation")
                continue

            actual_return = (current_price - entry_price) / entry_price

            if recommendation == "BUY":
                accurate = actual_return > ACCURACY_THRESHOLD
            elif recommendation == "SELL":
                accurate = actual_return < ACCURACY_THRESHOLD
            else:  # HOLD
                accurate = abs(actual_return) < 0.05  # within ±5%

            self.storage.update_suggestion_accuracy(s["id"], actual_return, accurate)
            logger.info(
                f"{ticker} [{recommendation}] from {s['date']}: "
                f"actual return {actual_return:.2%} → {'✓' if accurate else '✗'}"
            )

    def get_accuracy_summary(self) -> dict:
        return self.storage.get_accuracy_report()

    def check_consecutive_buys(self, ticker: str, storage: Storage) -> bool:
        """Return True if the last 3+ suggestions for this ticker are all BUY."""
        recent = storage.load_recent_suggestions(ticker, days=90)
        if len(recent) < 3:
            return False
        last_three = [r["recommendation"] for r in recent[:3]]
        return all(r == "BUY" for r in last_three)
