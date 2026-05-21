"""
Pipeline smoke tests — run each module and print results.
Usage: python test_pipeline.py
"""
import os
import sys
import json
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── helpers ──────────────────────────────────────────────────────────────────

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"

def section(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print('='*55)

def ok(msg):   print(f"  {PASS}  {msg}")
def err(msg):  print(f"  {FAIL}  {msg}")
def skip(msg): print(f"  {SKIP}  {msg}")

TICKER = "AAPL"

# ── Phase 1: Data layer ───────────────────────────────────────────────────────

section("Phase 1 — Data Layer")

from data.fetcher import StockFetcher
from data.cleaner import DataCleaner
from data.storage import Storage

fetcher = StockFetcher()
cleaner = DataCleaner()
storage = Storage("data/test_agent.db")

df = fetcher.fetch_price_history(TICKER, days=90)
if df is not None and len(df) > 0:
    ok(f"fetch_price_history: {len(df)} rows, latest close = {df['Close'].iloc[-1]:.2f}")
else:
    err("fetch_price_history returned None or empty")
    sys.exit(1)

df, warns = cleaner.validate_price_data(TICKER, df)
ok(f"validate_price_data: {len(df)} rows after clean, {len(warns)} warnings")
for w in warns:
    print(f"       warn: {w}")

storage.save_price_history(TICKER, df)
loaded = storage.load_price_history(TICKER, days=30)
if loaded is not None and len(loaded) > 0:
    ok(f"SQLite save/load: {len(loaded)} rows round-tripped")
else:
    err("SQLite round-trip failed")

news = fetcher.fetch_news(TICKER, limit=5)
if news:
    ok(f"fetch_news: {len(news)} articles — \"{news[0]['title'][:60]}...\"")
    storage.save_news(TICKER, news)
else:
    skip("fetch_news: no articles returned (NewsAPI may be rate-limited)")

price = fetcher.fetch_current_price(TICKER)
if price:
    ok(f"fetch_current_price: ${price:.2f}")
else:
    err("fetch_current_price failed")

# ── Phase 2: Strategy layer ───────────────────────────────────────────────────

section("Phase 2a — Technical Indicators")

from strategy.indicators import IndicatorCalculator
from strategy.signals import evaluate_signals

calc = IndicatorCalculator()
ind = calc.compute(df)

if ind:
    ok(f"Price:   ${ind['price']:.2f}")
    ok(f"SMA20:   {ind['sma20']:.2f}  |  SMA50: {ind['sma50'] or 'N/A'}  |  SMA200: {ind['sma200'] or 'N/A'}")
    ok(f"MACD:    {ind['macd']:.4f}  |  Signal: {ind['macd_signal']:.4f}")
    ok(f"RSI(14): {ind['rsi14']:.2f}")
    ok(f"BB:      upper={ind['bb_upper']:.2f}  lower={ind['bb_lower']:.2f}")
    ok(f"ATR(14): {ind['atr14']:.2f}")
    ok(f"Volume:  {ind['volume']:,.0f}  (10d avg {ind['volume_sma10']:,.0f})")
else:
    err("IndicatorCalculator.compute returned None")
    sys.exit(1)

section("Phase 2b — Signal Rules")

signals = evaluate_signals(ind)
ok(f"Signal score: {signals.score:.2f}")
for s in signals.bullish:
    ok(f"  BULL: {s}")
for s in signals.bearish:
    ok(f"  BEAR: {s}")
for s in signals.neutral:
    ok(f"  NEUT: {s}")

section("Phase 2c — Claude AI Analysis")

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    skip("ANTHROPIC_API_KEY not set — skipping AI analysis")
else:
    from strategy.ai_analyst import AIAnalyst
    analyst = AIAnalyst()
    suggestion, raw = analyst.analyze(TICKER, ind, signals, news or [])
    if suggestion:
        ok(f"Recommendation : {suggestion.get('recommendation')}")
        ok(f"Confidence     : {suggestion.get('confidence'):.2f}")
        ok(f"Sentiment score: {suggestion.get('sentiment_score')}")
        ok(f"Feasibility    : {suggestion.get('feasibility')}")
        ok(f"Risk factors   : {suggestion.get('risk_factors')}")
        storage.save_suggestion(TICKER, suggestion, price, raw)
        ok("Suggestion saved to DB")
    else:
        err("AI analysis returned None — check logs")
        print(f"  Raw response: {raw[:300]}")

# ── Phase 3: Notify layer ─────────────────────────────────────────────────────

section("Phase 3 — Report Rendering & Email")

from notify.renderer import ReportRenderer

dummy_result = {
    "ticker": TICKER,
    "price": price,
    "recommendation": (suggestion or {}).get("recommendation", "HOLD"),
    "confidence": (suggestion or {}).get("confidence", 0.7),
    "signal_score": signals.score,
    "sentiment_score": (suggestion or {}).get("sentiment_score", 0.0),
    "feasibility": (suggestion or {}).get("feasibility", "Test run"),
    "key_levels": (suggestion or {}).get("key_levels", {}),
    "risk_factors": (suggestion or {}).get("risk_factors", []),
    "key_events": (suggestion or {}).get("key_events", []),
    "bullish_signals": signals.bullish,
    "bearish_signals": signals.bearish,
    "sma20": f"{ind['sma20']:.2f}", "sma50": str(ind['sma50'] or "N/A"),
    "sma200": str(ind['sma200'] or "N/A"),
    "macd": f"{ind['macd']:.4f}", "macd_signal": f"{ind['macd_signal']:.4f}",
    "rsi14": f"{ind['rsi14']:.2f}",
    "bb_upper": f"{ind['bb_upper']:.2f}", "bb_lower": f"{ind['bb_lower']:.2f}",
    "atr14": f"{ind['atr14']:.2f}",
    "low_confidence": False, "consecutive_buy": False,
    "data_warning": bool(warns), "no_news": not news,
}

renderer = ReportRenderer()
html = renderer.render([dummy_result])
preview_path = Path("data/test_report.html")
preview_path.parent.mkdir(exist_ok=True)
preview_path.write_text(html, encoding="utf-8")
ok(f"HTML report rendered — {len(html):,} chars → {preview_path}")

sendgrid_key = os.environ.get("SENDGRID_API_KEY")
recipient    = os.environ.get("RECIPIENT_EMAIL")
if sendgrid_key and recipient:
    from notify.mailer import Mailer
    import yaml, re
    with open("config/settings.yaml", encoding="utf-8") as _f:
        _raw = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), _f.read())
    _cfg = yaml.safe_load(_raw)
    email_cfg = _cfg.get("email", {})
    email_cfg["subject_prefix"] = "[Stock Agent TEST]"
    mailer = Mailer(email_cfg)
    sent = mailer.send(html)
    if sent:
        ok(f"Email sent to {recipient}")
    else:
        err("Email send failed — check SendGrid key or see data/email_backup/")
else:
    skip("SENDGRID_API_KEY or RECIPIENT_EMAIL not set — skipping email send")

# ── Phase 4: Backtest tracker ─────────────────────────────────────────────────

section("Phase 4 — Backtest Tracker")

from backtest.tracker import SuggestionTracker
tracker = SuggestionTracker(storage, fetcher)
tracker.evaluate_pending()
accuracy = storage.get_accuracy_report()
ok(f"Accuracy report: {accuracy if accuracy else 'no evaluated suggestions yet (need 30-day history)'}")

# ── Cleanup ───────────────────────────────────────────────────────────────────

section("Summary")
print("  All phases completed. Check data/test_report.html for HTML preview.")
print("  Run main.py for the full production pipeline.\n")
