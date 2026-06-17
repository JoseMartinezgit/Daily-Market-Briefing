"""
Ticker extraction and sector mapping.

Given a news article, produces:
- directly_affected: companies explicitly named
- indirectly_affected: related stocks via sector knowledge map
- sector_etfs: relevant sector ETFs with explanations
"""
import json
import re
import logging
from pathlib import Path
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load static sector map
# ---------------------------------------------------------------------------
_MAP_PATH = Path(__file__).parent.parent.parent / "data" / "sector_map.json"
try:
    with open(_MAP_PATH) as f:
        _SECTOR_MAP = json.load(f)
except Exception as e:
    logger.warning("Could not load sector map: %s", e)
    _SECTOR_MAP = {"macro_drivers": {}, "sector_etfs": {}, "company_to_sector": {}}

COMPANY_TO_SECTOR: dict = _SECTOR_MAP.get("company_to_sector", {})
MACRO_DRIVERS: dict = _SECTOR_MAP.get("macro_drivers", {})

# ---------------------------------------------------------------------------
# Known ticker → company name mapping (commonly mentioned tickers)
# ---------------------------------------------------------------------------
TICKER_NAMES: dict[str, str] = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "AMD": "AMD",
    "INTC": "Intel", "QCOM": "Qualcomm", "AVGO": "Broadcom", "TSM": "Taiwan Semi",
    "ASML": "ASML", "MU": "Micron", "AMAT": "Applied Materials", "KLAC": "KLA Corp",
    "JPM": "JPMorgan", "BAC": "Bank of America", "GS": "Goldman Sachs",
    "MS": "Morgan Stanley", "WFC": "Wells Fargo", "C": "Citigroup",
    "V": "Visa", "MA": "Mastercard", "AXP": "AmEx", "BRK-B": "Berkshire",
    "XOM": "ExxonMobil", "CVX": "Chevron", "COP": "ConocoPhillips",
    "UNH": "UnitedHealth", "JNJ": "Johnson & Johnson", "LLY": "Eli Lilly",
    "MRK": "Merck", "ABBV": "AbbVie", "PFE": "Pfizer",
    "CAT": "Caterpillar", "HON": "Honeywell", "UPS": "UPS", "BA": "Boeing",
    "AMZN": "Amazon", "TSLA": "Tesla", "HD": "Home Depot", "MCD": "McDonald's",
    "NKE": "Nike", "LOW": "Lowe's", "TGT": "Target", "SBUX": "Starbucks",
    "WMT": "Walmart", "PG": "Procter & Gamble", "KO": "Coca-Cola",
    "PEP": "PepsiCo", "COST": "Costco",
    "META": "Meta", "GOOGL": "Alphabet", "GOOG": "Alphabet", "NFLX": "Netflix",
    "DIS": "Disney", "CMCSA": "Comcast", "TMUS": "T-Mobile", "VZ": "Verizon",
    "CRM": "Salesforce", "ORCL": "Oracle", "IBM": "IBM", "NOW": "ServiceNow",
    "ADBE": "Adobe", "PYPL": "PayPal", "SQ": "Block", "COIN": "Coinbase",
    "UBER": "Uber", "LYFT": "Lyft", "ABNB": "Airbnb", "BKNG": "Booking",
    "RTX": "Raytheon", "LMT": "Lockheed Martin", "NOC": "Northrop Grumman",
    "GE": "GE Aerospace", "DE": "Deere",
    "DAL": "Delta Air Lines", "AAL": "American Airlines", "UAL": "United Airlines",
    "F": "Ford", "GM": "General Motors", "RIVN": "Rivian", "LCID": "Lucid",
    "NEM": "Newmont", "FCX": "Freeport-McMoRan",
    "AMT": "American Tower", "PLD": "Prologis", "EQIX": "Equinix",
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq ETF", "IWM": "Russell 2000 ETF",
    "TLT": "iShares 20Y Treasury", "GLD": "SPDR Gold", "USO": "Oil ETF",
    "UUP": "USD ETF", "EEM": "Emerging Markets ETF", "ARKK": "ARK Innovation",
    "SMH": "VanEck Semiconductors",
    "XLK": "Technology ETF", "XLF": "Financials ETF", "XLE": "Energy ETF",
    "XLV": "Healthcare ETF", "XLI": "Industrials ETF", "XLY": "Cons Disc ETF",
    "XLP": "Cons Staples ETF", "XLU": "Utilities ETF", "XLRE": "Real Estate ETF",
    "XLB": "Materials ETF", "XLC": "Comm Services ETF",
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
    "SOFI": "SoFi Technologies", "HOOD": "Robinhood", "AFRM": "Affirm",
    "PLTR": "Palantir", "PATH": "UiPath", "SNOW": "Snowflake", "DDOG": "Datadog",
    "NET": "Cloudflare", "CRWD": "CrowdStrike", "ZS": "Zscaler",
    "PANW": "Palo Alto Networks",
}

# Company name variants → ticker
_NAME_TO_TICKER: dict[str, str] = {}
for tkr, nm in TICKER_NAMES.items():
    _NAME_TO_TICKER[nm.lower()] = tkr
    # Also add individual words for partial matching
    for word in nm.lower().split():
        if len(word) > 4:
            _NAME_TO_TICKER[word] = tkr

# Regex for ticker patterns in text: $AAPL or (AAPL) or standalone AAPL
_TICKER_RE = re.compile(r'\$([A-Z]{1,5}(?:-[A-Z])?)\b|\b([A-Z]{2,5})\b')

# Words that look like tickers but aren't
_TICKER_BLACKLIST = {
    "A", "I", "IN", "IT", "TO", "AT", "AS", "AN", "BE", "BY", "DO", "GO",
    "HE", "IF", "IS", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "UP", "US",
    "WE", "CEO", "CFO", "CTO", "IPO", "AI", "US", "UK", "EU", "FED", "SEC",
    "DOJ", "FTC", "GDP", "CPI", "PPI", "VIX", "ETF", "S&P", "NYSE", "NASDAQ",
    "FOMC", "OPEC", "NATO", "IMF", "WTO", "G7", "G20", "ECB", "BOJ", "BOE",
    "USD", "EUR", "GBP", "JPY", "CNY", "BTC", "ETH", "NFT", "API", "SaaS",
    "ESG", "EPS", "P/E", "YOY", "QOQ", "MOM", "TTM", "EBITDA", "GAAP",
    "EST", "CT", "ET", "PM", "AM",
}


def extract_tickers_from_text(text: str) -> list[str]:
    """
    Extract likely ticker symbols from article text.
    Returns a deduplicated list of uppercase ticker strings.
    """
    found = set()
    text_clean = text.replace("'s", "").replace("'s", "")

    # 1. $TICKER pattern (most reliable)
    for m in _TICKER_RE.finditer(text_clean):
        dollar_match = m.group(1)
        if dollar_match:
            found.add(dollar_match.upper())

    # 2. Uppercase words that match known tickers
    for m in re.finditer(r'\b([A-Z]{1,5}(?:-[A-Z])?)\b', text_clean):
        candidate = m.group(1)
        if candidate in _TICKER_BLACKLIST:
            continue
        if candidate in TICKER_NAMES:
            found.add(candidate)

    # 3. Company name → ticker lookup
    text_lower = text.lower()
    for name, ticker in _NAME_TO_TICKER.items():
        if name in text_lower and ticker not in found:
            found.add(ticker)

    # Filter to known tickers only
    known = {t for t in found if t in TICKER_NAMES}
    return sorted(known)


def get_macro_impacts(title: str, summary: str) -> tuple[list[dict], list[dict]]:
    """
    Match macro driver patterns to get bullish/bearish sector impacts.
    Returns (bullish_tickers, bearish_tickers), each a list of ticker dicts.
    """
    text = f"{title} {summary}".lower()
    bullish: list[dict] = []
    bearish: list[dict] = []
    seen_bull: set = set()
    seen_bear: set = set()

    for driver_name, driver_data in MACRO_DRIVERS.items():
        keywords = driver_data.get("keywords", [])
        if not any(kw in text for kw in keywords):
            continue

        for item in driver_data.get("bullish", []):
            if item["ticker"] not in seen_bull:
                seen_bull.add(item["ticker"])
                bullish.append({
                    "ticker": item["ticker"],
                    "name": TICKER_NAMES.get(item["ticker"], item["name"]),
                    "direction": "up",
                    "reason": item["reason"],
                    "is_indirect": True,
                    "driver": driver_name,
                })

        for item in driver_data.get("bearish", []):
            if item["ticker"] not in seen_bear:
                seen_bear.add(item["ticker"])
                bearish.append({
                    "ticker": item["ticker"],
                    "name": TICKER_NAMES.get(item["ticker"], item["name"]),
                    "direction": "down",
                    "reason": item["reason"],
                    "is_indirect": True,
                    "driver": driver_name,
                })

    return bullish, bearish


def build_ticker_list(
    title: str,
    summary: str,
    sentiment_score: float = 0.0,
    preloaded_tickers: Optional[list] = None,
) -> tuple[list[dict], list[str]]:
    """
    Build the full ticker impact list for an article.

    Returns:
    - tickers: list of ticker dicts (direct + indirect)
    - sectors: list of sector ETF strings most relevant to the story
    """
    tickers: list[dict] = []
    seen: set = set()

    # 1. Pre-loaded tickers from API (highest confidence)
    if preloaded_tickers:
        for t in preloaded_tickers:
            sym = t.get("ticker", "").upper()
            if sym and sym not in seen:
                seen.add(sym)
                direction = t.get("direction", "neutral")
                if direction == "neutral" and sentiment_score > 0.1:
                    direction = "up"
                elif direction == "neutral" and sentiment_score < -0.1:
                    direction = "down"
                tickers.append({
                    "ticker": sym,
                    "name": TICKER_NAMES.get(sym, t.get("name", sym)),
                    "direction": direction,
                    "reason": "Named in article",
                    "is_indirect": False,
                })

    # 2. Extract tickers from text
    extracted = extract_tickers_from_text(f"{title} {summary}")
    for sym in extracted:
        if sym not in seen:
            seen.add(sym)
            direction = "up" if sentiment_score > 0.1 else ("down" if sentiment_score < -0.1 else "neutral")
            tickers.append({
                "ticker": sym,
                "name": TICKER_NAMES.get(sym, sym),
                "direction": direction,
                "reason": "Named in article",
                "is_indirect": False,
            })

    # 3. Macro driver indirect impacts
    bullish_indirect, bearish_indirect = get_macro_impacts(title, summary)
    for item in bullish_indirect:
        if item["ticker"] not in seen:
            seen.add(item["ticker"])
            tickers.append(item)
    for item in bearish_indirect:
        if item["ticker"] not in seen:
            seen.add(item["ticker"])
            tickers.append(item)

    # 4. Determine relevant sector ETFs
    sectors: list[str] = []
    for t in tickers:
        sym = t["ticker"]
        sector_etf = COMPANY_TO_SECTOR.get(sym)
        if sector_etf and sector_etf not in sectors:
            sectors.append(sector_etf)

    # Cap at 8 tickers to avoid noise
    tickers = tickers[:8]

    return tickers, sectors


# ---------------------------------------------------------------------------
# Watchlist aggregation
# ---------------------------------------------------------------------------

def build_watchlist(
    news_items: list[dict],
    base_tickers: list[str],
    prices: dict[str, dict],
) -> list[dict]:
    """
    Aggregate ticker mentions across all news to build Today's Watchlist.
    Returns top-10 tickers sorted by mention count + sentiment.
    """
    ticker_stats: dict[str, dict] = defaultdict(lambda: {
        "mentions": 0,
        "sentiment_sum": 0.0,
        "stories": [],
        "name": "",
    })

    # Count mentions across all news
    for item in news_items:
        for t in item.get("tickers", []):
            sym = t.get("ticker", "")
            if not sym:
                continue
            stats = ticker_stats[sym]
            stats["mentions"] += 1
            stats["sentiment_sum"] += item.get("sentiment_score", 0.0)
            stats["name"] = TICKER_NAMES.get(sym, t.get("name", sym))
            if item.get("title"):
                stats["stories"].append(item["title"][:80])

    # Also include configured watchlist tickers
    for sym in base_tickers:
        if sym not in ticker_stats:
            ticker_stats[sym]["name"] = TICKER_NAMES.get(sym, sym)
            ticker_stats[sym]["mentions"] = 0

    # Build result list
    result = []
    for sym, stats in ticker_stats.items():
        price_data = prices.get(sym, {})
        avg_sentiment = stats["sentiment_sum"] / max(stats["mentions"], 1)
        result.append({
            "ticker": sym,
            "name": stats["name"] or TICKER_NAMES.get(sym, sym),
            "mentions": stats["mentions"],
            "sentiment_avg": round(avg_sentiment, 3),
            "sentiment_label": "bullish" if avg_sentiment > 0.1 else ("bearish" if avg_sentiment < -0.1 else "neutral"),
            "price": price_data.get("price"),
            "change_pct": price_data.get("change_pct"),
            "change_abs": price_data.get("change_abs"),
            "recent_stories": stats["stories"][:3],
        })

    # Sort: pinned watchlist first by mention count, then by |sentiment|
    result.sort(key=lambda x: (-x["mentions"], -abs(x["sentiment_avg"])))
    return result[:10]
