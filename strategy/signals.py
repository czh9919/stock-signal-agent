"""
Hard-coded signal rules engine.
Converts raw indicator values into human-readable signal flags.
"""
from dataclasses import dataclass, field


@dataclass
class SignalResult:
    bullish: list[str] = field(default_factory=list)
    bearish: list[str] = field(default_factory=list)
    neutral: list[str] = field(default_factory=list)
    score: float = 0.0  # -1 (strongly bearish) to +1 (strongly bullish)


def evaluate_signals(indicators: dict) -> SignalResult:
    result = SignalResult()
    score = 0.0
    count = 0

    price    = indicators.get("price")
    sma20    = indicators.get("sma20")
    sma50    = indicators.get("sma50")
    sma200   = indicators.get("sma200")
    macd     = indicators.get("macd")
    macd_sig = indicators.get("macd_signal")
    rsi      = indicators.get("rsi14")
    bb_lower = indicators.get("bb_lower")
    bb_upper = indicators.get("bb_upper")
    volume   = indicators.get("volume")
    vol_avg  = indicators.get("volume_sma10")

    # Golden/Death Cross (SMA 50/200)
    if sma50 and sma200:
        if sma50 > sma200:
            result.bullish.append("Golden cross: SMA50 > SMA200")
            score += 1; count += 1
        else:
            result.bearish.append("Death cross: SMA50 < SMA200")
            score -= 1; count += 1

    # Price vs SMA20
    if price and sma20:
        if price > sma20:
            result.bullish.append(f"Price ({price:.2f}) above SMA20 ({sma20:.2f})")
            score += 0.5; count += 1
        else:
            result.bearish.append(f"Price ({price:.2f}) below SMA20 ({sma20:.2f})")
            score -= 0.5; count += 1

    # MACD
    if macd is not None and macd_sig is not None:
        if macd > macd_sig:
            result.bullish.append(f"MACD ({macd:.3f}) above signal ({macd_sig:.3f})")
            score += 1; count += 1
        else:
            result.bearish.append(f"MACD ({macd:.3f}) below signal ({macd_sig:.3f})")
            score -= 1; count += 1

    # RSI
    if rsi is not None:
        if rsi < 30:
            result.bullish.append(f"RSI {rsi:.1f} — oversold (potential rebound)")
            score += 1.5; count += 1
        elif rsi > 70:
            result.bearish.append(f"RSI {rsi:.1f} — overbought (caution)")
            score -= 1.5; count += 1
        else:
            result.neutral.append(f"RSI {rsi:.1f} — neutral")
            count += 1

    # Bollinger Bands
    if price and bb_lower and bb_upper:
        if price <= bb_lower:
            result.bullish.append(f"Price at/below BB lower band ({bb_lower:.2f}) — rebound signal")
            score += 1; count += 1
        elif price >= bb_upper:
            result.bearish.append(f"Price at/above BB upper band ({bb_upper:.2f}) — overextended")
            score -= 1; count += 1
        else:
            result.neutral.append("Price within Bollinger Bands")

    # Volume surge
    if volume and vol_avg and vol_avg > 0:
        ratio = volume / vol_avg
        if ratio > 1.5:
            result.bullish.append(f"Volume surge: {ratio:.1f}x 10-day average — confirms breakout")
            score += 0.5; count += 1

    result.score = score / count if count else 0.0
    return result
