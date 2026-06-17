"""
Market data fetcher using yfinance.
Returns current prices, % changes, VIX, sector ETFs, futures.

Handles yfinance 0.2.x where download() returns a MultiIndex DataFrame
for multi-ticker requests and single-level for single-ticker requests.
"""
import logging
import datetime
import json
from typing import Optional
import yfinance as yf
import pandas as pd
from src.config import cfg

logger = logging.getLogger(__name__)


def _safe_float(val, default=0.0) -> float:
    try:
        f = float(val)
        return f if f == f else default  # NaN check
    except (TypeError, ValueError):
        return default


def _pct_change(current: float, previous: float) -> float:
    if not previous or previous == 0:
        return 0.0
    return round((current - previous) / abs(previous) * 100, 2)


def _get_close_series(data: pd.DataFrame, symbol: str, single: bool) -> Optional[pd.Series]:
    """
    Safely extract the Close Series for one symbol from a yfinance download DataFrame.
    Handles both single-ticker (1-level columns) and multi-ticker (MultiIndex) DataFrames.
    """
    try:
        if data is None or data.empty:
            return None

        # Single-ticker download: columns are flat like ['Open','High','Low','Close','Volume']
        if single or not isinstance(data.columns, pd.MultiIndex):
            if "Close" in data.columns:
                s = data["Close"].dropna()
                return s if not s.empty else None
            return None

        # Multi-ticker download: columns are MultiIndex (Price, Ticker)
        # Level 0 = price type ('Close','Open',…), Level 1 = ticker symbol
        if "Close" in data.columns.get_level_values(0):
            close_df = data["Close"]
            # Try exact symbol match
            if symbol in close_df.columns:
                s = close_df[symbol].dropna()
                return s if not s.empty else None
            # Try case-insensitive match
            for col in close_df.columns:
                if str(col).upper() == symbol.upper():
                    s = close_df[col].dropna()
                    return s if not s.empty else None
        return None
    except Exception as exc:
        logger.debug("_get_close_series %s: %s", symbol, exc)
        return None


def _series_to_quote(closes: pd.Series) -> Optional[dict]:
    """Convert a Close price Series into a {price, prev_close, change_abs, change_pct} dict."""
    if closes is None or len(closes) == 0:
        return None
    try:
        price = _safe_float(closes.iloc[-1])
        prev = _safe_float(closes.iloc[-2]) if len(closes) >= 2 else price
        return {
            "price": round(price, 4),
            "prev_close": round(prev, 4),
            "change_abs": round(price - prev, 4),
            "change_pct": _pct_change(price, prev),
        }
    except Exception:
        return None


def _fetch_single_ticker(symbol: str) -> Optional[dict]:
    """Fallback: fetch a single ticker via Ticker.history()."""
    try:
        hist = yf.Ticker(symbol).history(period="2d", interval="1d", auto_adjust=True)
        if hist.empty:
            return None
        closes = hist["Close"].dropna()
        return _series_to_quote(closes)
    except Exception as exc:
        logger.debug("Single-ticker fetch %s: %s", symbol, exc)
        return None


def fetch_market_snapshot() -> tuple[list[dict], list[str]]:
    """
    Fetch all configured market symbols.
    Returns (snapshot_list, failed_symbols).
    """
    results: list[dict] = []
    failed: list[str] = []
    now = datetime.datetime.utcnow()

    # Build (symbol, display_name, data_type) triples
    all_symbols: list[tuple[str, str, str]] = []

    for item in cfg.market_symbols.get("indices", []):
        if isinstance(item, dict):
            all_symbols.append((item.get("symbol", ""), item.get("name", ""), "index"))

    for item in cfg.market_symbols.get("commodities", []):
        if isinstance(item, dict):
            all_symbols.append((item.get("symbol", ""), item.get("name", ""), "commodity"))

    for item in cfg.market_symbols.get("yields", []):
        if isinstance(item, dict):
            all_symbols.append((item.get("symbol", ""), item.get("name", ""), "yield"))

    # Load sector ETF names
    try:
        with open(cfg.DATA_DIR / "sector_map.json") as f:
            sm = json.load(f)
        sector_info = sm.get("sector_etfs", {})
    except Exception:
        sector_info = {}

    for etf in cfg.sector_etfs:
        name = sector_info.get(etf, {}).get("name", etf) if isinstance(sector_info.get(etf), dict) else etf
        all_symbols.append((etf, name, "sector_etf"))

    # Filter out empty symbols
    all_symbols = [(s, n, t) for s, n, t in all_symbols if s]

    symbol_list = [s for s, _, _ in all_symbols]
    if not symbol_list:
        return results, failed

    # Bulk download (fastest path)
    data = None
    try:
        data = yf.download(
            symbol_list,
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.warning("yfinance bulk download failed: %s", exc)

    single = len(symbol_list) == 1

    for symbol, name, data_type in all_symbols:
        try:
            quote = None

            # Try bulk data first
            if data is not None and not data.empty:
                closes = _get_close_series(data, symbol, single)
                quote = _series_to_quote(closes)

            # Fallback to individual fetch
            if quote is None:
                quote = _fetch_single_ticker(symbol)

            if quote:
                results.append({
                    "symbol": symbol,
                    "name": name,
                    "price": quote["price"],
                    "change_pct": quote["change_pct"],
                    "change_abs": quote["change_abs"],
                    "data_type": data_type,
                    "timestamp": now.isoformat(),
                })
            else:
                failed.append(symbol)
        except Exception as exc:
            logger.warning("Market data failed for %s: %s", symbol, exc)
            failed.append(symbol)

    return results, failed


def fetch_vix_data() -> dict:
    """Fetch VIX level and compute 5-day context."""
    try:
        hist = yf.Ticker("^VIX").history(period="5d", interval="1d")
        if hist.empty:
            raise ValueError("empty VIX history")
        closes = hist["Close"].dropna()
        current = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else current
        return {
            "vix": round(current, 2),
            "vix_change": round(_pct_change(current, prev), 2),
            "vix_5d_avg": round(float(closes.mean()), 2),
        }
    except Exception as exc:
        logger.warning("VIX fetch failed: %s", exc)
        return {"vix": 20.0, "vix_change": 0.0, "vix_5d_avg": 20.0}


def fetch_sector_heatmap() -> list[dict]:
    """Fetch today's % change for all sector ETFs."""
    etfs = cfg.sector_etfs
    if not etfs:
        return []

    try:
        with open(cfg.DATA_DIR / "sector_map.json") as f:
            sm = json.load(f)
        sector_info = sm.get("sector_etfs", {})
    except Exception:
        sector_info = {}

    results = []
    try:
        data = yf.download(etfs, period="2d", interval="1d", auto_adjust=True, progress=False)
        single = len(etfs) == 1
        for etf in etfs:
            try:
                closes = _get_close_series(data, etf, single)
                quote = _series_to_quote(closes)
                if quote is None:
                    quote = _fetch_single_ticker(etf) or {"price": 0.0, "change_pct": 0.0}

                info = sector_info.get(etf, {})
                if not isinstance(info, dict):
                    info = {}
                results.append({
                    "etf": etf,
                    "name": info.get("name", etf),
                    "change_pct": round(quote.get("change_pct", 0.0) or 0.0, 2),
                    "price": round(quote.get("price", 0.0) or 0.0, 2),
                    "color": info.get("color", "#6B7280"),
                })
            except Exception as exc:
                logger.debug("Sector ETF %s: %s", etf, exc)
    except Exception as exc:
        logger.warning("Sector heatmap download failed: %s", exc)

    return results


def fetch_watchlist_prices(tickers: list[str]) -> dict[str, dict]:
    """Fetch current price and % change for a list of tickers."""
    if not tickers:
        return {}

    results: dict[str, dict] = {}
    try:
        data = yf.download(tickers, period="2d", interval="1d", auto_adjust=True, progress=False)
        single = len(tickers) == 1
        for ticker in tickers:
            try:
                closes = _get_close_series(data, ticker, single)
                quote = _series_to_quote(closes)
                if quote is None:
                    quote = _fetch_single_ticker(ticker)
                if quote:
                    results[ticker] = {
                        "price": round(quote["price"], 2),
                        "change_pct": round(quote["change_pct"], 2),
                        "change_abs": round(quote["change_abs"], 2),
                    }
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Watchlist prices download failed: %s", exc)

    return results


def fetch_earnings_reactions(tickers: list[str]) -> list[dict]:
    """
    Check recent earnings (last 7 days) for notable reactions.
    Returns list of {ticker, name, date, eps_actual, eps_estimate, surprise_pct, price_reaction}.
    """
    reactions = []
    today = datetime.datetime.utcnow().date()
    lookback = today - datetime.timedelta(days=7)

    for ticker_sym in tickers[:15]:
        try:
            tk = yf.Ticker(ticker_sym)
            earnings_dates = tk.get_earnings_dates(limit=4)
            if earnings_dates is None or earnings_dates.empty:
                continue

            for idx, row in earnings_dates.iterrows():
                try:
                    date_val = idx.date() if hasattr(idx, "date") else None
                    if date_val is None or not (lookback <= date_val <= today):
                        continue

                    # Use .get() safely — pandas Series supports this
                    eps_actual_raw = row.get("Reported EPS") if hasattr(row, "get") else row.get("Reported EPS", None)
                    eps_est_raw = row.get("EPS Estimate") if hasattr(row, "get") else None
                    surprise_raw = row.get("Surprise(%)") if hasattr(row, "get") else None

                    def _to_float(v):
                        if v is None:
                            return None
                        try:
                            f = float(v)
                            return None if f != f else f  # NaN → None
                        except (TypeError, ValueError):
                            return None

                    eps_actual = _to_float(eps_actual_raw)
                    eps_estimate = _to_float(eps_est_raw)
                    surprise_pct = _to_float(surprise_raw)

                    # Price reaction: day-of open vs prior close
                    price_reaction = None
                    try:
                        price_data = tk.history(
                            start=date_val - datetime.timedelta(days=2),
                            end=date_val + datetime.timedelta(days=3),
                        )
                        if len(price_data) >= 2:
                            prev_close = float(price_data["Close"].iloc[0])
                            day_open = float(price_data["Open"].iloc[1])
                            if prev_close:
                                price_reaction = round(_pct_change(day_open, prev_close), 2)
                    except Exception:
                        pass

                    try:
                        name = tk.info.get("shortName", ticker_sym)
                    except Exception:
                        name = ticker_sym

                    reactions.append({
                        "ticker": ticker_sym,
                        "name": name,
                        "date": str(date_val),
                        "eps_actual": eps_actual,
                        "eps_estimate": eps_estimate,
                        "surprise_pct": surprise_pct,
                        "price_reaction": price_reaction,
                    })
                    break
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Earnings reaction %s: %s", ticker_sym, exc)

    return reactions
