"""
M1 — Holdings consolidation from IBKR, Trading 212, eToro.

Each holding dict:
  ticker, platform, quantity, cost_basis_gbp, market_value_gbp,
  unrealised_pnl_gbp, currency, weight (filled later)
"""
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_RETRY = 3
_TIMEOUT = 15


def _get(url: str, headers: dict = None, retries: int = _RETRY) -> Optional[requests.Response]:
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers or {}, timeout=_TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as e:
            logger.warning(f"GET {url} attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None


def _parse_bond_symbol(symbol: str) -> dict:
    """Extract coupon rate and maturity from IBKR bond ticker strings.

    Handles formats like:
      'T 3 3/4 06/30/27'   → coupon=3.75,  maturity=06/30/27
      'T 4.5 12/31/28'     → coupon=4.5,   maturity=12/31/28
    """
    result = {}
    # Mixed fraction: "3 3/4 MM/DD/YY"
    m = re.search(r'(\d+)\s+(\d+)/(\d+)\s+(\d{2}/\d{2}/\d{2,4})', symbol)
    if m:
        result["coupon"]   = str(int(m.group(1)) + int(m.group(2)) / int(m.group(3)))
        result["maturity"] = m.group(4)
        return result
    # Decimal coupon: "4.5 MM/DD/YY"
    m2 = re.search(r'(\d+\.?\d*)\s+(\d{2}/\d{2}/\d{2,4})', symbol)
    if m2:
        result["coupon"]   = m2.group(1)
        result["maturity"] = m2.group(2)
    return result


# ── IBKR Flex Query ───────────────────────────────────────────────────────────

_IBKR_SEND = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
_IBKR_GET  = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"

def fetch_ibkr(fx_rates: dict) -> list[dict]:
    token    = os.environ.get("IBKR_FLEX_TOKEN")
    query_id = os.environ.get("IBKR_QUERY_ID")
    if not token or not query_id:
        logger.warning("IBKR_FLEX_TOKEN / IBKR_QUERY_ID not set — skipping IBKR")
        return []

    r = _get(f"{_IBKR_SEND}?t={token}&q={query_id}&v=3")
    if r is None:
        return []

    try:
        root = ET.fromstring(r.text)
        ref  = root.findtext(".//ReferenceCode")
        if not ref:
            logger.error("IBKR: no ReferenceCode in response")
            return []
    except ET.ParseError as e:
        logger.error(f"IBKR: XML parse error on SendRequest: {e}")
        return []

    time.sleep(3)
    r2 = _get(f"{_IBKR_GET}?t={token}&q={ref}&v=3")
    if r2 is None:
        return []

    holdings = []
    try:
        root2 = ET.fromstring(r2.text)
        for pos in root2.findall(".//OpenPosition"):
            asset_cat = pos.get("assetCategory", "")
            symbol    = pos.get("symbol", "")
            # If assetCategory absent, infer bond from ticker format (spaces/slashes = bond notation)
            is_bond   = (asset_cat == "BOND") or (
                            not asset_cat and (" " in symbol or "/" in symbol))

            # Skip options, futures, warrants, etc. — but keep BOND
            if asset_cat and asset_cat not in ("STK", "ETF", "FUND", "BOND"):
                logger.info(f"IBKR: skipping {symbol} (assetCategory={asset_cat})")
                continue
            # For equities only: skip malformed tickers (spaces/slashes indicate non-equity)
            if not is_bond and (" " in symbol or "/" in symbol):
                logger.info(f"IBKR: skipping {symbol} (non-equity ticker format)")
                continue

            currency   = pos.get("currency", "USD")
            rate       = fx_rates.get(f"{currency}GBP", 1.0)
            position   = float(pos.get("position", 0))
            cost       = float(pos.get("costBasisMoney", 0))

            # Prefer positionValue (direct market value) if the Flex Query includes it
            pos_value_str = pos.get("positionValue", "")
            if pos_value_str:
                mkt_val = float(pos_value_str)
            else:
                mark_price = float(pos.get("markPrice", 0))
                # Bonds: markPrice is clean price as % of par; position is face value
                mkt_val = (mark_price / 100) * position if is_bond else mark_price * position

            # Parse coupon & maturity from symbol string when XML fields are absent
            parsed = _parse_bond_symbol(symbol) if is_bond else {}
            maturity = pos.get("maturity") or parsed.get("maturity", "")
            coupon   = pos.get("coupon")   or parsed.get("coupon", "")

            holdings.append({
                "ticker":            symbol,
                "description":       pos.get("description", symbol),
                "asset_class":       "bond" if is_bond else "equity",
                "maturity":          maturity,
                "coupon":            coupon,
                "platform":          "IBKR",
                "quantity":          position,
                "cost_basis_gbp":    cost * rate,
                "market_value_gbp":  mkt_val * rate,
                "unrealised_pnl_gbp":(mkt_val - cost) * rate,
                "currency":          currency,
            })
    except Exception as e:
        logger.error(f"IBKR: parse positions failed: {e}")

    logger.info(f"IBKR: {len(holdings)} positions loaded")
    return holdings


# ── Trading 212 ───────────────────────────────────────────────────────────────

_T212_BASE = "https://live.trading212.com/api/v0"

_T212_TYPE_SUFFIXES = {"EQ", "ETF", "BOND", "CFD", "REIT"}

def _t212_ticker(raw: str) -> str:
    """Convert T212 ticker (e.g. AAPL_US_EQ) to a clean yfinance ticker (AAPL)."""
    parts = raw.rsplit("_", 2)
    if parts[-1].upper() in _T212_TYPE_SUFFIXES:
        parts = parts[:-1]
    if len(parts) > 1 and re.match(r'^[A-Z]{2,3}$', parts[-1]):
        parts = parts[:-1]
    return "_".join(parts)

def fetch_t212(fx_rates: dict) -> list[dict]:
    import base64
    api_key    = os.environ.get("T212_API_KEY")
    api_secret = os.environ.get("T212_API_SECRET")
    if not api_key or not api_secret:
        logger.warning("T212_API_KEY / T212_API_SECRET not set — skipping Trading 212")
        return []

    credentials = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    r = _get(f"{_T212_BASE}/equity/portfolio", headers={"Authorization": f"Basic {credentials}"})
    if r is None:
        return []

    holdings = []
    try:
        for pos in r.json():
            currency = pos.get("initialFillDate", "")  # T212 beta returns GBP natively
            mv    = float(pos.get("currentPrice", 0)) * float(pos.get("quantity", 0))
            cost  = float(pos.get("averagePrice", 0)) * float(pos.get("quantity", 0))
            holdings.append({
                "ticker":            _t212_ticker(pos.get("ticker", "")),
                "description":       _t212_ticker(pos.get("ticker", "")),
                "asset_class":       "equity",
                "platform":          "T212",
                "quantity":          float(pos.get("quantity", 0)),
                "cost_basis_gbp":    cost,
                "market_value_gbp":  mv,
                "unrealised_pnl_gbp":float(pos.get("ppl", 0)),
                "currency":          "GBP",
            })
    except Exception as e:
        logger.error(f"T212: parse failed: {e}")

    logger.info(f"T212: {len(holdings)} positions loaded")
    return holdings


# ── eToro ─────────────────────────────────────────────────────────────────────

_ETORO_BASE = "https://www.etoro.com/sapi/trade-data-real/live/public/portfolios"

def fetch_etoro(fx_rates: dict) -> list[dict]:
    key = os.environ.get("ETORO_API_KEY")
    if not key:
        logger.warning("ETORO_API_KEY not set — skipping eToro")
        return []

    r = _get(_ETORO_BASE, headers={"AccountType": "Real", "Authorization": f"Bearer {key}"})
    if r is None:
        return []

    holdings = []
    try:
        for pos in r.json().get("PublicPortfolio", {}).get("Positions", []):
            currency = "USD"
            rate     = fx_rates.get("USDGBP", 0.79)
            mv       = float(pos.get("NetValue", 0)) * rate
            cost     = float(pos.get("OpenRate", 0)) * float(pos.get("Units", 0)) * rate
            holdings.append({
                "ticker":            pos.get("Instrument", {}).get("SymbolFull", ""),
                "description":       pos.get("Instrument", {}).get("SymbolFull", ""),
                "asset_class":       "equity",
                "platform":          "eToro",
                "quantity":          float(pos.get("Units", 0)),
                "cost_basis_gbp":    cost,
                "market_value_gbp":  mv,
                "unrealised_pnl_gbp":mv - cost,
                "currency":          currency,
            })
    except Exception as e:
        logger.error(f"eToro: parse failed: {e}")

    logger.info(f"eToro: {len(holdings)} positions loaded")
    return holdings


# ── Merge ─────────────────────────────────────────────────────────────────────

def fetch_all_holdings(fx_rates: dict) -> list[dict]:
    """Fetch from all three platforms, merge, compute weights."""
    holdings = fetch_ibkr(fx_rates) + fetch_t212(fx_rates) + fetch_etoro(fx_rates)

    total_nav = sum(h["market_value_gbp"] for h in holdings)
    for h in holdings:
        h["weight"] = h["market_value_gbp"] / total_nav if total_nav else 0.0

    logger.info(f"Total holdings: {len(holdings)}, NAV: £{total_nav:,.2f}")
    return holdings
