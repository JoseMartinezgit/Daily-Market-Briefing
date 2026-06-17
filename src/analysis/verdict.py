"""
Day verdict computation: BULLISH / BEARISH / VOLATILE / MIXED.

Combines:
- Major index % changes (S&P, Nasdaq, Dow, Russell)
- VIX level and rate of change
- Aggregate news sentiment
- Sector ETF breadth (dispersion)
"""
import logging
import datetime
import statistics
from typing import Optional
from src.config import cfg

logger = logging.getLogger(__name__)


def _clamp(val: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _normalize_pct_change(pct: float, scale: float = 2.0) -> float:
    """Map a % change to [-1, 1] range. scale=2% → ±1."""
    return _clamp(pct / scale)


def _vix_to_signal(vix: float, vix_change_pct: float) -> tuple[float, str]:
    """
    Convert VIX level + change to a directional signal.
    Returns (signal in [-1,1], description).
    """
    # VIX level signal
    if vix >= 35:
        level_signal = -1.0
        level_desc = f"VIX extreme fear ({vix:.1f})"
    elif vix >= 25:
        level_signal = -0.6
        level_desc = f"VIX elevated ({vix:.1f})"
    elif vix >= 20:
        level_signal = -0.2
        level_desc = f"VIX slightly elevated ({vix:.1f})"
    elif vix <= 12:
        level_signal = 0.5
        level_desc = f"VIX very low ({vix:.1f}), complacency"
    elif vix <= 15:
        level_signal = 0.3
        level_desc = f"VIX low ({vix:.1f})"
    else:
        level_signal = 0.0
        level_desc = f"VIX normal ({vix:.1f})"

    # VIX change signal (spiking VIX → bearish signal regardless of direction)
    if vix_change_pct >= 20:
        change_signal = -1.0
        change_desc = f"VIX spiking +{vix_change_pct:.0f}%"
    elif vix_change_pct >= 10:
        change_signal = -0.6
        change_desc = f"VIX rising +{vix_change_pct:.0f}%"
    elif vix_change_pct <= -20:
        change_signal = 0.8
        change_desc = f"VIX collapsing {vix_change_pct:.0f}%"
    elif vix_change_pct <= -10:
        change_signal = 0.5
        change_desc = f"VIX falling {vix_change_pct:.0f}%"
    else:
        change_signal = 0.0
        change_desc = ""

    combined = 0.6 * level_signal + 0.4 * change_signal
    desc = level_desc + (f", {change_desc}" if change_desc else "")
    return _clamp(combined), desc


def compute_breadth_signal(sector_changes: list[float]) -> tuple[float, str]:
    """
    Measure sector ETF dispersion.
    High dispersion with mixed signs → VOLATILE.
    Returns (signal, description).
    """
    if not sector_changes:
        return 0.0, "no sector data"

    pos = sum(1 for c in sector_changes if c > 0.2)
    neg = sum(1 for c in sector_changes if c < -0.2)
    total = len(sector_changes)
    avg = statistics.mean(sector_changes) if sector_changes else 0.0

    pct_up = pos / total
    pct_down = neg / total

    if pct_up > 0.75:
        signal = 0.8
        desc = f"{pos}/{total} sectors advancing"
    elif pct_down > 0.75:
        signal = -0.8
        desc = f"{neg}/{total} sectors declining"
    elif abs(pct_up - pct_down) < 0.2:
        signal = _normalize_pct_change(avg)
        desc = f"mixed breadth ({pos} up, {neg} down of {total})"
    else:
        signal = _normalize_pct_change(avg)
        desc = f"{pos} sectors up, {neg} down"

    return _clamp(signal), desc


def generate_justification(
    verdict: str,
    index_changes: dict,
    vix_data: dict,
    news_sentiment: float,
    breadth_desc: str,
    premarket: bool = False,
) -> str:
    """Build a plain-English one-paragraph justification for the verdict."""
    parts = []

    sp500 = index_changes.get("sp500", 0.0)
    nasdaq = index_changes.get("nasdaq", 0.0)
    vix = vix_data.get("vix", 20.0)
    vix_change = vix_data.get("vix_change", 0.0)

    # Index moves
    if abs(sp500) >= 0.5:
        direction = "up" if sp500 > 0 else "down"
        parts.append(f"S&P 500 {direction} {abs(sp500):.1f}%")
    if abs(nasdaq) >= 0.5:
        direction = "up" if nasdaq > 0 else "down"
        parts.append(f"Nasdaq {direction} {abs(nasdaq):.1f}%")

    # VIX
    if abs(vix_change) >= 5:
        direction = "up" if vix_change > 0 else "down"
        parts.append(f"VIX {direction} {abs(vix_change):.0f}% to {vix:.1f}")
    elif vix >= 25:
        parts.append(f"VIX elevated at {vix:.1f}")

    # News sentiment
    if news_sentiment > 0.2:
        parts.append("news tone broadly positive")
    elif news_sentiment < -0.2:
        parts.append("news tone broadly negative")
    elif abs(news_sentiment) < 0.05:
        parts.append("news sentiment neutral")

    # Breadth
    if breadth_desc and "mixed" not in breadth_desc:
        parts.append(breadth_desc)

    if premarket:
        parts.append("(pre-market assessment using futures)")

    if not parts:
        return f"Markets appear {verdict.lower()} based on available data."

    return f"Markets {verdict.lower()}: {'; '.join(parts)}."


def compute_verdict(
    snapshot: list[dict],
    vix_data: dict,
    news_items: list[dict],
    sector_heatmap: list[dict],
) -> dict:
    """
    Main verdict computation.

    Returns dict with:
    - verdict: BULLISH / BEARISH / VOLATILE / MIXED
    - confidence: 0–100
    - justification: plain English string
    - component scores
    """
    weights = cfg.sentiment_weights

    # --- Market indices component ---
    index_changes: dict[str, float] = {}
    for item in snapshot:
        sym = item.get("symbol", "")
        chg = item.get("change_pct", 0.0) or 0.0
        if sym == "^GSPC":
            index_changes["sp500"] = chg
        elif sym == "^IXIC":
            index_changes["nasdaq"] = chg
        elif sym == "^DJI":
            index_changes["dow"] = chg
        elif sym == "^RUT":
            index_changes["russell"] = chg

    market_score = _clamp(
        0.40 * _normalize_pct_change(index_changes.get("sp500", 0)) +
        0.30 * _normalize_pct_change(index_changes.get("nasdaq", 0)) +
        0.20 * _normalize_pct_change(index_changes.get("dow", 0)) +
        0.10 * _normalize_pct_change(index_changes.get("russell", 0))
    )

    # --- VIX component ---
    vix_val = vix_data.get("vix", 20.0)
    vix_change_pct = vix_data.get("vix_change", 0.0)
    vix_signal, vix_desc = _vix_to_signal(vix_val, vix_change_pct)

    # --- News sentiment component ---
    scores = [item.get("sentiment_score", 0.0) for item in news_items if item.get("sentiment_score") is not None]
    news_sentiment = statistics.mean(scores) if scores else 0.0
    news_signal = _clamp(news_sentiment * 2)  # amplify: 0.5 → 1.0

    # --- Breadth component ---
    sector_changes = [item.get("change_pct", 0.0) for item in sector_heatmap if item.get("change_pct") is not None]
    breadth_signal, breadth_desc = compute_breadth_signal(sector_changes)

    # --- Weighted combined score ---
    w = weights
    w_market = float(w.get("market_indices", 0.40))
    w_vix = float(w.get("vix", 0.20))
    w_news = float(w.get("news_sentiment", 0.30))
    w_breadth = float(w.get("breadth", 0.10))

    combined = _clamp(
        w_market * market_score +
        w_vix * vix_signal +
        w_news * news_signal +
        w_breadth * breadth_signal
    )

    # --- Volatile override: high VIX + large moves in either direction ---
    sp500_abs = abs(index_changes.get("sp500", 0))
    is_volatile = vix_val >= 25 and sp500_abs >= 1.5

    # --- Determine verdict ---
    if is_volatile:
        verdict = "VOLATILE"
    elif combined >= 0.25:
        verdict = "BULLISH"
    elif combined <= -0.25:
        verdict = "BEARISH"
    else:
        verdict = "MIXED"

    # --- Confidence: distance from thresholds, scaled to 50-100 ---
    if verdict == "VOLATILE":
        confidence = min(100, 60 + int(vix_val - 25) * 2)
    else:
        confidence = min(100, 55 + int(abs(combined) * 50))

    # Pre-market check: if before 9:30 AM ET (8:30 CT), note futures
    now_ct_hour = datetime.datetime.now().hour
    premarket = now_ct_hour < 8 or now_ct_hour >= 21

    justification = generate_justification(
        verdict,
        index_changes,
        vix_data,
        news_sentiment,
        breadth_desc,
        premarket=premarket,
    )

    return {
        "verdict": verdict,
        "confidence": confidence,
        "justification": justification,
        "combined_score": round(combined, 3),
        "market_score": round(market_score, 3),
        "vix_signal": round(vix_signal, 3),
        "news_signal": round(news_signal, 3),
        "breadth_signal": round(breadth_signal, 3),
        "news_sentiment_avg": round(news_sentiment, 3),
        "sp500_change": index_changes.get("sp500"),
        "nasdaq_change": index_changes.get("nasdaq"),
        "dow_change": index_changes.get("dow"),
        "russell2k_change": index_changes.get("russell"),
        "vix": vix_val,
        "vix_change": vix_change_pct,
        "is_premarket": premarket,
        "computed_at": datetime.datetime.utcnow().isoformat(),
    }
