"""
Claude API integration.
Sends pre-computed technical indicators + news to Claude and parses the JSON response.

Output JSON schema:
{
  "recommendation": "BUY" | "HOLD" | "SELL",
  "confidence": 0.0..1.0,
  "key_levels": {"support": [float, ...], "resistance": [float, ...]},
  "risk_factors": ["string", ...],
  "feasibility": "string",
  "sentiment_score": -1.0..1.0,
  "key_events": ["string", ...],
  "stale_news": bool
}
"""
import json
import logging
import os
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a quantitative stock analyst. You will receive pre-computed technical
indicators and recent news for a stock. Your job is to synthesize this information into a structured
investment recommendation.

IMPORTANT RULES:
- You must return ONLY a valid JSON object — no markdown, no explanation, no extra text.
- Never estimate or recalculate any numerical indicator — use only the provided values.
- Base confidence on signal agreement and news clarity.
- Flag stale_news=true if news items appear to be already priced in (≥5 days old with no new follow-up).
"""

USER_PROMPT_TEMPLATE = """Analyze {ticker} and return a JSON recommendation.

=== TECHNICAL INDICATORS (pre-computed, do not recalculate) ===
Current Price : {price}
SMA20 / SMA50 / SMA200 : {sma20} / {sma50} / {sma200}
EMA20         : {ema20}
MACD          : {macd}  |  Signal: {macd_signal}  |  Histogram: {macd_hist}
RSI (14)      : {rsi14}
Bollinger Bands — Upper: {bb_upper} / Mid: {bb_mid} / Lower: {bb_lower}
ATR (14)      : {atr14}
Volume        : {volume}  |  10d Average: {volume_sma10}
Signal Score  : {signal_score:.2f}  (bullish signals: {bullish} / bearish: {bearish})

=== LAST 30 DAYS CLOSING PRICES ===
{price_series}

=== RECENT NEWS (last 7 days, up to 10 articles) ===
{news_text}

=== REQUIRED JSON OUTPUT ===
{{
  "recommendation": "BUY" or "HOLD" or "SELL",
  "confidence": <0.0 to 1.0>,
  "key_levels": {{"support": [<float>], "resistance": [<float>]}},
  "risk_factors": ["<string>"],
  "feasibility": "<one sentence explanation>",
  "sentiment_score": <-1.0 to 1.0>,
  "key_events": ["<string>"],
  "stale_news": <true or false>
}}
"""


class AIAnalyst:
    def __init__(self, model: str = "claude-sonnet-4-6", max_tokens: int = 1024):
        self.model = model
        self.max_tokens = max_tokens
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        self.client = anthropic.Anthropic(api_key=api_key)

    def analyze(
        self,
        ticker: str,
        indicators: dict,
        signals,
        news: list[dict],
    ) -> tuple[Optional[dict], str]:
        """
        Call Claude API and return (parsed_dict, raw_response_text).
        Returns (None, raw) if parsing fails or API errors out.
        """
        news_text = self._format_news(news)
        prompt = USER_PROMPT_TEMPLATE.format(
            ticker=ticker,
            price=indicators.get("price"),
            sma20=indicators.get("sma20"),
            sma50=indicators.get("sma50"),
            sma200=indicators.get("sma200"),
            ema20=indicators.get("ema20"),
            macd=indicators.get("macd"),
            macd_signal=indicators.get("macd_signal"),
            macd_hist=indicators.get("macd_hist"),
            rsi14=indicators.get("rsi14"),
            bb_upper=indicators.get("bb_upper"),
            bb_mid=indicators.get("bb_mid"),
            bb_lower=indicators.get("bb_lower"),
            atr14=indicators.get("atr14"),
            volume=indicators.get("volume"),
            volume_sma10=indicators.get("volume_sma10"),
            signal_score=signals.score,
            bullish=", ".join(signals.bullish) or "none",
            bearish=", ".join(signals.bearish) or "none",
            price_series=indicators.get("price_series_30d", []),
            news_text=news_text or "No news available.",
        )

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
        except anthropic.APITimeoutError:
            logger.error(f"{ticker}: Claude API timed out")
            return None, ""
        except anthropic.APIError as e:
            logger.error(f"{ticker}: Claude API error — {e}")
            return None, ""

        # Parse JSON
        try:
            # Strip markdown code fences if present
            text = raw
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())
            return parsed, raw
        except json.JSONDecodeError as e:
            logger.error(f"{ticker}: JSON parse failed — {e}\nRaw: {raw[:200]}")
            return None, raw

    @staticmethod
    def _format_news(articles: list[dict]) -> str:
        if not articles:
            return ""
        lines = []
        for i, a in enumerate(articles, 1):
            title = a.get("title", "")
            desc  = a.get("description", "")
            src   = a.get("source", "")
            pub   = a.get("published_at", "")[:10]
            lines.append(f"{i}. [{pub}] {src}: {title}")
            if desc:
                lines.append(f"   {desc[:150]}")
        return "\n".join(lines)
