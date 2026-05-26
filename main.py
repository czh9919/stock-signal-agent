"""
Unified entry point.
RUN_MODE=stock       → stock selection pipeline only
RUN_MODE=portfolio   → portfolio risk pipeline only
RUN_MODE=full        → both (default)
RUN_MODE=alert_check → portfolio threshold check only (no report)
"""
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import yaml


def setup_logging(level: str = "INFO", log_file: str = "logs/agent.log"):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO), format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def load_config(path: str = "config/settings.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    raw = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), raw)
    return yaml.safe_load(raw)


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_watchlist(path: str = "config/watchlist.csv") -> list[dict]:
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Stock pipeline ─────────────────────────────────────────────────────────────

def run_stock_pipeline(config: dict):
    logger = logging.getLogger("stock")
    logger.info("=== Stock AI Agent — starting ===")

    limits     = config.get("limits", {})
    claude_cfg = config.get("claude", {})
    db_path    = config.get("database", {}).get("path", "data/stock_agent.db")

    from data.cleaner   import DataCleaner
    from data.fetcher   import StockFetcher
    from data.storage   import Storage
    from strategy.indicators import IndicatorCalculator
    from strategy.signals    import evaluate_signals
    from strategy.ai_analyst import AIAnalyst
    from backtest.tracker    import SuggestionTracker
    from notify.renderer     import ReportRenderer
    from notify.mailer       import Mailer

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
        logger.error(f"AIAnalyst init failed: {e}")
        analyst = None

    watchlist   = load_watchlist()
    max_stocks  = int(limits.get("max_stocks_per_day", 20))
    max_calls   = int(limits.get("max_claude_calls_per_day", 50))
    news_limit  = int(limits.get("news_per_stock", 10))
    conf_thresh = float(claude_cfg.get("confidence_warning_threshold", 0.5))

    claude_calls  = 0
    stock_results = []

    for entry in watchlist[:max_stocks]:
        ticker = entry["ticker"]
        logger.info(f"--- {ticker} ---")

        df = fetcher.fetch_price_history(ticker, days=int(limits.get("history_days", 730)))
        if df is None:
            storage.log_data_warning(ticker, "price data unavailable")
            stock_results.append(_stock_error(ticker, "price data unavailable"))
            continue

        df, price_warns = cleaner.validate_price_data(ticker, df)
        for w in price_warns:
            storage.log_data_warning(ticker, w)
        storage.save_price_history(ticker, df)

        news, _ = cleaner.validate_news(ticker, fetcher.fetch_news(ticker, limit=news_limit))
        if news:
            storage.save_news(ticker, news)

        current_price = fetcher.fetch_current_price(ticker)
        indicators    = indicator.compute(df)
        if indicators is None:
            stock_results.append(_stock_error(ticker, "insufficient data"))
            continue

        signals    = evaluate_signals(indicators)
        suggestion = None
        if analyst and claude_calls < max_calls:
            suggestion, raw = analyst.analyze(ticker, indicators, signals, news)
            claude_calls += 1
            if suggestion:
                storage.save_suggestion(ticker, suggestion, current_price, raw)

        low_conf = suggestion and suggestion.get("confidence", 1.0) < conf_thresh
        consec   = (suggestion and suggestion.get("recommendation") == "BUY" and
                    tracker.check_consecutive_buys(ticker, storage))

        def fmt(v):
            return f"{v:.2f}" if isinstance(v, float) else str(v) if v is not None else "N/A"

        stock_results.append({
            "ticker": ticker, "price": current_price,
            "recommendation": (suggestion or {}).get("recommendation", "N/A"),
            "confidence":     (suggestion or {}).get("confidence"),
            "signal_score":   signals.score,
            "sentiment_score":(suggestion or {}).get("sentiment_score"),
            "feasibility":    (suggestion or {}).get("feasibility", ""),
            "key_levels":     (suggestion or {}).get("key_levels", {}),
            "risk_factors":   (suggestion or {}).get("risk_factors", []),
            "key_events":     (suggestion or {}).get("key_events", []),
            "bullish_signals":signals.bullish, "bearish_signals": signals.bearish,
            "sma20": fmt(indicators.get("sma20")), "sma50": fmt(indicators.get("sma50")),
            "sma200":fmt(indicators.get("sma200")), "macd": fmt(indicators.get("macd")),
            "macd_signal":fmt(indicators.get("macd_signal")), "rsi14":fmt(indicators.get("rsi14")),
            "bb_upper":fmt(indicators.get("bb_upper")), "bb_lower":fmt(indicators.get("bb_lower")),
            "atr14":fmt(indicators.get("atr14")),
            "low_confidence": low_conf, "consecutive_buy": consec,
            "data_warning": bool(price_warns), "no_news": not news,
        })

    tracker.evaluate_pending()
    accuracy = storage.get_accuracy_report()

    email_cfg = config.get("email", {})
    renderer  = ReportRenderer()
    mailer    = Mailer(email_cfg)

    html_en, html_zh = renderer.render_both(stock_results, accuracy_report=accuracy)
    mailer.send(html_en, subject=f"[Stock Agent] Daily Report {date.today()}")
    mailer.send(html_zh, subject=f"[选股助手] 每日报告 {date.today()}")

    logger.info(f"=== Stock pipeline complete — {len(stock_results)} stocks ===")
    return stock_results


def _stock_error(ticker: str, reason: str) -> dict:
    return {
        "ticker": ticker, "price": None, "recommendation": "N/A",
        "confidence": None, "signal_score": None, "sentiment_score": None,
        "feasibility": reason, "key_levels": {}, "risk_factors": [], "key_events": [],
        "bullish_signals": [], "bearish_signals": [],
        "sma20":"N/A","sma50":"N/A","sma200":"N/A","macd":"N/A","macd_signal":"N/A",
        "rsi14":"N/A","bb_upper":"N/A","bb_lower":"N/A","atr14":"N/A",
        "low_confidence":False,"consecutive_buy":False,"data_warning":True,"no_news":True,
    }


# ── Portfolio pipeline ─────────────────────────────────────────────────────────

def run_portfolio_pipeline(run_mode: str = "full"):
    logger = logging.getLogger("portfolio")
    logger.info(f"=== Portfolio Risk System — run_mode={run_mode} ===")

    thresholds = load_yaml("config/thresholds.yaml")
    vol_cfg    = load_yaml("config/volatility.yaml")

    from data.price_loader   import load_fx_rates
    from data.fetch_holdings import fetch_all_holdings
    from data                import cache

    fx_rates = load_fx_rates()
    holdings = fetch_all_holdings(fx_rates)

    if not holdings:
        cached = cache.load_latest_snapshot()
        if cached:
            snap_data, snap_date = cached
            logger.warning(f"No live holdings — using cache from {snap_date}")
            holdings = snap_data.get("holdings", [])
        else:
            logger.error("No holdings and no cache — skipping portfolio pipeline")
            return

    from data.price_loader import load_prices
    # Bonds don't have yfinance price history — only pass equity tickers
    equity_tickers = list({h["ticker"] for h in holdings if h.get("asset_class", "equity") == "equity"})
    tickers    = equity_tickers + ["SPY"]
    price_data = load_prices(tickers, days=vol_cfg.get("windows", {}).get("fixed", 252))
    spy_pd     = price_data.pop("SPY", None)

    from risk.risk_engine import compute_all
    metrics = compute_all(holdings, price_data, spy_pd, vcfg=vol_cfg, thresholds=thresholds)
    logger.info(f"NAV: €{metrics['nav_eur']:,.0f} | VaR(95%): {metrics['var_95_ewma']*100:.1f}% | RAG: {metrics['overall_rag']}")

    from alert import check_and_alert
    check_and_alert(metrics, thresholds)

    if run_mode == "alert_check":
        logger.info("alert_check — skipping full report")
        return

    from risk.stress       import run_all as run_stress
    from risk.optimizer    import compute_frontier, rebalancing_suggestions, marginal_impact
    from notify.report_gen import build_report
    from notify.chart      import risk_return_png
    from notify            import mailer

    stress      = run_stress(holdings, price_data, metrics["nav_eur"], vol_cfg=vol_cfg)
    snapshot    = {"holdings": holdings, "metrics": {k:v for k,v in metrics.items() if k != "alerts"}}
    cache.save_snapshot(snapshot)
    last_week   = cache.load_week_ago_snapshot()

    rf          = thresholds.get("risk_free_rate", 0.035)
    frontier    = compute_frontier(price_data, holdings, rf=rf)
    suggestions = rebalancing_suggestions(holdings, frontier, nav_eur=metrics["nav_eur"])
    chart_en    = risk_return_png(frontier, "en")
    chart_zh    = risk_return_png(frontier, "zh")

    # ── Diversification candidates (bonds + gold from watchlist) ──────────────
    watchlist_all  = load_watchlist()
    wl_map         = {w["ticker"]: w for w in watchlist_all}
    cand_entries   = [w for w in watchlist_all if w.get("asset_class") in ("bond", "gold")]
    div_candidates = []
    if cand_entries:
        new_tickers   = [w["ticker"] for w in cand_entries if w["ticker"] not in price_data]
        cand_pd_extra = load_prices(new_tickers, days=vol_cfg.get("windows", {}).get("fixed", 252)) if new_tickers else {}
        for entry in cand_entries:
            tk     = entry["ticker"]
            pd_obj = cand_pd_extra.get(tk) or price_data.get(tk)
            impact = marginal_impact(tk, pd_obj, holdings, price_data, rf=rf)
            if impact:
                impact["currency"]    = entry.get("currency", "USD")
                impact["asset_class"] = entry.get("asset_class", "bond")
                impact["name"]        = entry.get("notes", tk)
                div_candidates.append(impact)
        div_candidates.sort(key=lambda x: x["delta_sharpe"], reverse=True)
        logger.info(f"Diversification candidates: {len(div_candidates)} computed")

    html_en, html_zh = build_report(
        metrics, stress, holdings, price_data, last_week=last_week,
        frontier=frontier, suggestions=suggestions,
        has_chart_en=bool(chart_en), has_chart_zh=bool(chart_zh),
        div_candidates=div_candidates, fx_rates=fx_rates,
    )
    mailer.send_report(html_en, html_zh, rag=metrics["overall_rag"],
                       chart_en=chart_en, chart_zh=chart_zh)
    logger.info("=== Portfolio pipeline complete ===")


# ── Backtest pipeline ──────────────────────────────────────────────────────────

def run_backtest_pipeline(config: dict):
    logger = logging.getLogger("backtest")
    logger.info("=== Walk-Forward Backtest — starting ===")

    vol_cfg    = load_yaml("config/volatility.yaml")
    thresholds = load_yaml("config/thresholds.yaml")
    rf         = thresholds.get("risk_free_rate", 0.035)

    watchlist     = load_watchlist()
    equity_tickers = [w["ticker"] for w in watchlist if w.get("asset_class", "equity") == "equity"]

    from data.spy_universe       import load_universe
    from backtest.walk_forward   import run_walk_forward, print_summary

    max_sp500 = int(config.get("backtest", {}).get("max_sp500", 100))
    days      = int(config.get("backtest", {}).get("history_days", 1260))

    price_data = load_universe(equity_tickers, days=days, max_sp500=max_sp500)

    results = run_walk_forward(
        price_data,
        rf=rf,
        train_days=int(config.get("backtest", {}).get("train_days", 756)),
        test_days=int(config.get("backtest", {}).get("test_days", 63)),
    )

    if results:
        print_summary(results["summary"])
    else:
        logger.warning("Walk-forward returned no results")

    logger.info("=== Walk-Forward Backtest complete ===")
    return results


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg      = load_config()
    log_cfg  = cfg.get("logging", {})
    setup_logging(log_cfg.get("level", "INFO"), log_cfg.get("file", "logs/agent.log"))

    mode = os.environ.get("RUN_MODE", "full")

    if mode in ("stock", "full"):
        run_stock_pipeline(cfg)

    if mode in ("portfolio", "full", "alert_check"):
        run_portfolio_pipeline(run_mode=mode)

    if mode == "backtest":
        run_backtest_pipeline(cfg)
