"""
Manual trigger entry point.
Run: python main.py
"""
import logging
import os
import sys
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import yaml

from data.cleaner import DataCleaner
from data.fetcher import StockFetcher
from data.storage import Storage
from strategy.indicators import IndicatorCalculator
from strategy.signals import evaluate_signals
from strategy.ai_analyst import AIAnalyst
from backtest.tracker import SuggestionTracker
from notify.renderer import ReportRenderer
from notify.mailer import Mailer


def setup_logging(level: str = "INFO", log_file: str = "logs/agent.log"):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=fmt, handlers=handlers)


def load_config(path: str = "config/settings.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    # Expand ${ENV_VAR} references
    import re
    def replacer(m):
        return os.environ.get(m.group(1), m.group(0))
    raw = re.sub(r"\$\{(\w+)\}", replacer, raw)
    return yaml.safe_load(raw)


def load_watchlist(path: str = "config/watchlist.csv") -> list[dict]:
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_daily_pipeline(config: dict):
    logger = logging.getLogger("main")
    logger.info("=== Stock AI Agent — daily run starting ===")

    limits    = config.get("limits", {})
    claude_cfg = config.get("claude", {})
    db_path   = config.get("database", {}).get("path", "data/stock_agent.db")

    storage   = Storage(db_path)
    fetcher   = StockFetcher()
    cleaner   = DataCleaner()
    indicator = IndicatorCalculator()
    tracker   = SuggestionTracker(storage, fetcher)

    try:
        analyst = AIAnalyst(
            model=claude_cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=int(claude_cfg.get("max_tokens", 1024)),
        )
    except ValueError as e:
        logger.error(f"Cannot initialise AIAnalyst: {e}")
        analyst = None

    watchlist = load_watchlist()
    max_stocks = int(limits.get("max_stocks_per_day", 20))
    max_calls  = int(limits.get("max_claude_calls_per_day", 50))
    news_limit = int(limits.get("news_per_stock", 10))
    conf_thresh = float(claude_cfg.get("confidence_warning_threshold", 0.5))
    consec_warn = int(claude_cfg.get("consecutive_buy_warning", 3))

    claude_calls = 0
    stock_results: list[dict] = []

    for entry in watchlist[:max_stocks]:
        ticker = entry["ticker"]
        logger.info(f"--- Processing {ticker} ---")

        # ── Data fetch ────────────────────────────────────────────────────
        df = fetcher.fetch_price_history(ticker, days=int(limits.get("history_days", 730)))
        if df is None:
            storage.log_data_warning(ticker, "price data unavailable")
            stock_results.append(_error_result(ticker, "price data unavailable"))
            continue

        df, price_warns = cleaner.validate_price_data(ticker, df)
        for w in price_warns:
            storage.log_data_warning(ticker, w)
        storage.save_price_history(ticker, df)

        news = fetcher.fetch_news(ticker, limit=news_limit)
        news, news_warns = cleaner.validate_news(ticker, news)
        if news:
            storage.save_news(ticker, news)

        current_price = fetcher.fetch_current_price(ticker)

        # ── Technical indicators ──────────────────────────────────────────
        indicators = indicator.compute(df)
        if indicators is None:
            storage.log_data_warning(ticker, "insufficient data for indicators")
            stock_results.append(_error_result(ticker, "insufficient data"))
            continue

        signals = evaluate_signals(indicators)

        # ── AI analysis ───────────────────────────────────────────────────
        suggestion: dict | None = None
        if analyst and claude_calls < max_calls:
            suggestion, raw = analyst.analyze(ticker, indicators, signals, news)
            claude_calls += 1
            if suggestion:
                storage.save_suggestion(ticker, suggestion, current_price, raw)
            else:
                logger.warning(f"{ticker}: AI analysis skipped (parse/API failure)")
        else:
            logger.info(f"{ticker}: AI analysis skipped (no analyst or call limit reached)")

        # ── Risk flags ────────────────────────────────────────────────────
        low_confidence    = suggestion and suggestion.get("confidence", 1.0) < conf_thresh
        consecutive_buy   = (
            suggestion and
            suggestion.get("recommendation") == "BUY" and
            tracker.check_consecutive_buys(ticker, storage)
        )

        # ── Build result dict for report ──────────────────────────────────
        def fmt(v):
            return f"{v:.2f}" if isinstance(v, float) else str(v) if v is not None else "N/A"

        result = {
            "ticker": ticker,
            "price": current_price,
            "recommendation": (suggestion or {}).get("recommendation", "N/A"),
            "confidence": (suggestion or {}).get("confidence"),
            "signal_score": signals.score,
            "sentiment_score": (suggestion or {}).get("sentiment_score"),
            "feasibility": (suggestion or {}).get("feasibility", ""),
            "key_levels": (suggestion or {}).get("key_levels", {}),
            "risk_factors": (suggestion or {}).get("risk_factors", []),
            "key_events": (suggestion or {}).get("key_events", []),
            "bullish_signals": signals.bullish,
            "bearish_signals": signals.bearish,
            # indicator display
            "sma20": fmt(indicators.get("sma20")),
            "sma50": fmt(indicators.get("sma50")),
            "sma200": fmt(indicators.get("sma200")),
            "macd": fmt(indicators.get("macd")),
            "macd_signal": fmt(indicators.get("macd_signal")),
            "rsi14": fmt(indicators.get("rsi14")),
            "bb_upper": fmt(indicators.get("bb_upper")),
            "bb_lower": fmt(indicators.get("bb_lower")),
            "atr14": fmt(indicators.get("atr14")),
            # warning flags
            "low_confidence": low_confidence,
            "consecutive_buy": consecutive_buy,
            "data_warning": bool(price_warns),
            "no_news": not news,
        }
        stock_results.append(result)

    # ── Evaluate pending suggestions ──────────────────────────────────────
    tracker.evaluate_pending()
    accuracy = storage.get_accuracy_report()

    # ── Render & send email ───────────────────────────────────────────────
    renderer = ReportRenderer()
    html = renderer.render(stock_results, accuracy_report=accuracy)

    email_cfg = config.get("email", {})
    mailer = Mailer(email_cfg)
    mailer.send(html)

    logger.info("=== Daily run complete ===")
    return html


def _error_result(ticker: str, reason: str) -> dict:
    return {
        "ticker": ticker,
        "price": None,
        "recommendation": "N/A",
        "confidence": None,
        "signal_score": None,
        "sentiment_score": None,
        "feasibility": reason,
        "key_levels": {},
        "risk_factors": [],
        "key_events": [],
        "bullish_signals": [],
        "bearish_signals": [],
        "sma20": "N/A", "sma50": "N/A", "sma200": "N/A",
        "macd": "N/A", "macd_signal": "N/A",
        "rsi14": "N/A", "bb_upper": "N/A", "bb_lower": "N/A", "atr14": "N/A",
        "low_confidence": False, "consecutive_buy": False,
        "data_warning": True, "no_news": True,
    }


if __name__ == "__main__":
    cfg = load_config()
    log_cfg = cfg.get("logging", {})
    setup_logging(log_cfg.get("level", "INFO"), log_cfg.get("file", "logs/agent.log"))
    run_daily_pipeline(cfg)
