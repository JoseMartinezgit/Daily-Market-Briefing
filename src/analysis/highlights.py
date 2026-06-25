"""
Stock highlights: today's top Nasdaq-100 gainers, and a heuristic shortlist
of candidates that could see momentum into the next session.

"Tomorrow's Top Picks" is NOT a prediction model — it's a transparent
heuristic combining (a) bullish news mention/sentiment volume today and
(b) confirmed earnings catalysts landing before the next session opens.
Both lists exclude penny stocks (price < MIN_PRICE).
"""
import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)

MIN_PRICE = 5.0


def _find_news_reason(ticker: str, news_items: list[dict]) -> str:
    """Find the most relevant bullish headline mentioning this ticker."""
    best = None
    best_score = -2.0
    for item in news_items:
        for t in item.get("tickers", []):
            if t.get("ticker") == ticker:
                score = item.get("sentiment_score", 0.0)
                if score > best_score:
                    best_score = score
                    best = item
                break
    if best and best_score > 0:
        title = best.get("title", "")
        source = best.get("source", "")
        return f"“{title}” ({source})"
    return "No single news catalyst identified — move likely reflects broader sector/market trends today."


def compute_today_top_movers(
    snapshot: list[dict],
    news_items: list[dict],
    top_n: int = 3,
    min_price: float = MIN_PRICE,
) -> list[dict]:
    """Top N Nasdaq-100 gainers today, excluding penny stocks, with a news-based reason."""
    eligible = [s for s in snapshot if (s.get("price") or 0) >= min_price]
    eligible.sort(key=lambda s: s.get("change_pct", 0.0), reverse=True)
    top = eligible[:top_n]

    results = []
    for rank, s in enumerate(top, 1):
        results.append({
            "rank": rank,
            "ticker": s["ticker"],
            "name": s["name"],
            "price": s["price"],
            "change_pct": s["change_pct"],
            "reason": _find_news_reason(s["ticker"], news_items),
        })
    return results


def compute_tomorrow_candidates(
    snapshot: list[dict],
    news_items: list[dict],
    earnings_calendar: list[dict],
    top_n: int = 3,
    min_price: float = MIN_PRICE,
    exclude_tickers: Optional[set] = None,
) -> list[dict]:
    """
    Heuristic shortlist for the next session: bullish sentiment momentum
    today, boosted heavily for a confirmed earnings catalyst landing before
    the next open (AMC today or any-time tomorrow). Excludes penny stocks.
    """
    exclude_tickers = exclude_tickers or set()
    nasdaq100 = {s["ticker"]: s for s in snapshot if (s.get("price") or 0) >= min_price}

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    tomorrow_str = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    earnings_lookup: dict[str, dict] = {}
    for e in earnings_calendar:
        ticker = e.get("ticker")
        next_date = e.get("next_date")
        next_time = e.get("next_time")
        if ticker not in nasdaq100:
            continue
        if next_date == today_str and next_time == "AMC":
            earnings_lookup[ticker] = e
        elif next_date == tomorrow_str:
            earnings_lookup[ticker] = e

    mention_stats: dict[str, dict] = {}
    for item in news_items:
        sentiment = item.get("sentiment_score", 0.0)
        for t in item.get("tickers", []):
            ticker = t.get("ticker")
            if ticker not in nasdaq100:
                continue
            stats = mention_stats.setdefault(ticker, {"mentions": 0, "sentiment_sum": 0.0})
            stats["mentions"] += 1
            stats["sentiment_sum"] += sentiment

    candidates = []
    all_tickers = set(mention_stats.keys()) | set(earnings_lookup.keys())
    for ticker in all_tickers:
        if ticker in exclude_tickers:
            continue
        stats = mention_stats.get(ticker, {"mentions": 0, "sentiment_sum": 0.0})
        mentions = stats["mentions"]
        avg_sentiment = stats["sentiment_sum"] / mentions if mentions else 0.0
        has_catalyst = ticker in earnings_lookup

        score = mentions * max(avg_sentiment, 0.0) + (5.0 if has_catalyst else 0.0)
        if score <= 0:
            continue

        snap = nasdaq100[ticker]
        if has_catalyst:
            e = earnings_lookup[ticker]
            timing = "after today's close" if e.get("next_time") == "AMC" else "before the next open"
            avg_reaction = e.get("avg_reaction_pct")
            beat_rate = e.get("beat_rate")
            reason_parts = [f"Reports earnings {timing}"]
            if avg_reaction is not None:
                reason_parts.append(f"averages {avg_reaction:+.2f}% next-session reaction over its last reports")
            if beat_rate:
                reason_parts.append(f"beat estimates {beat_rate} times")
            reason = "; ".join(reason_parts) + "."
        else:
            reason = (
                f"Heavy bullish coverage today ({mentions} stories, avg sentiment "
                f"{avg_sentiment:+.2f}) — momentum may carry into the next session."
            )

        candidates.append({
            "ticker": ticker,
            "name": snap["name"],
            "price": snap["price"],
            "change_pct": snap["change_pct"],
            "score": round(score, 3),
            "has_catalyst": has_catalyst,
            "reason": reason,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    top = candidates[:top_n]
    for rank, c in enumerate(top, 1):
        c["rank"] = rank
    return top
