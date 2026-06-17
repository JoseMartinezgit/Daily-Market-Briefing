"""
Sentiment scoring for news headlines and summaries.

Mode A (default): keyword/rule-based finance lexicon + VADER tiebreaker.
Mode B (--llm flag): routes through Anthropic API for higher quality results.
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load lexicon
# ---------------------------------------------------------------------------
_LEXICON_PATH = Path(__file__).parent.parent.parent / "data" / "finance_lexicon.json"
try:
    with open(_LEXICON_PATH) as f:
        _LEXICON = json.load(f)
except Exception as e:
    logger.warning("Could not load finance lexicon: %s", e)
    _LEXICON = {"bullish": {"strong": {"phrases": []}, "moderate": {"phrases": []}, "weak": {"phrases": []}},
                "bearish": {"strong": {"phrases": []}, "moderate": {"phrases": []}, "weak": {"phrases": []}}}

# ---------------------------------------------------------------------------
# VADER init (lazy load)
# ---------------------------------------------------------------------------
_vader = None


def _get_vader():
    global _vader
    if _vader is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader = SentimentIntensityAnalyzer()
        except ImportError:
            logger.warning("vaderSentiment not installed; VADER scoring disabled")
    return _vader


# ---------------------------------------------------------------------------
# Core keyword scoring
# ---------------------------------------------------------------------------

def _keyword_score(text: str) -> float:
    """
    Score text using finance keyword lexicon.
    Returns a value in [-1.0, 1.0].
    """
    text_lower = text.lower()
    score = 0.0

    for direction in ("bullish", "bearish"):
        levels = _LEXICON.get(direction, {})
        multiplier = 1.0 if direction == "bullish" else -1.0
        if not isinstance(levels, dict):
            continue
        for level, data in levels.items():
            if not isinstance(data, dict):
                continue
            weight = data.get("weight", 0.0)
            for phrase in data.get("phrases", []):
                if phrase in text_lower:
                    score += multiplier * weight

    return max(-1.0, min(1.0, score))


def _vader_score(text: str) -> float:
    """Return VADER compound score in [-1, 1]."""
    vader = _get_vader()
    if vader is None:
        return 0.0
    try:
        scores = vader.polarity_scores(text)
        return scores["compound"]
    except Exception:
        return 0.0


def score_text_mode_a(title: str, summary: str = "") -> float:
    """
    Combined scoring: keyword lexicon (70%) + VADER (30%).
    Returns float in [-1.0, 1.0].
    """
    combined_text = f"{title} {summary}"
    kw_score = _keyword_score(combined_text)
    vader_s = _vader_score(combined_text)

    # If keyword score is strong (|score| > 0.3), trust it more
    if abs(kw_score) > 0.3:
        return 0.80 * kw_score + 0.20 * vader_s
    else:
        return 0.50 * kw_score + 0.50 * vader_s


# ---------------------------------------------------------------------------
# LLM mode (Mode B)
# ---------------------------------------------------------------------------

def score_batch_llm(items: list[dict], api_key: str) -> list[dict]:
    """
    Use Anthropic API to score a batch of news items.
    items: list of {"title": ..., "summary": ...}
    Returns list of {"score": float, "label": str, "reasoning": str, "impact": str}
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed; falling back to Mode A")
        return []

    client = anthropic.Anthropic(api_key=api_key)

    # Build a compact batch prompt
    batch_lines = []
    for i, item in enumerate(items):
        batch_lines.append(f"{i+1}. TITLE: {item['title'][:200]}")
        if item.get("summary"):
            batch_lines.append(f"   SUMMARY: {item['summary'][:300]}")

    prompt = """You are a financial analyst assistant. For each news item below, classify:
- sentiment: bullish, bearish, or neutral
- score: a number from -1.0 (very bearish) to +1.0 (very bullish)
- impact: High, Medium, or Low market-moving impact
- reason: one sentence explaining the classification

Respond ONLY with valid JSON array, one object per item, in order:
[{"sentiment":"...","score":0.0,"impact":"...","reason":"..."},...]

News items:
""" + "\n".join(batch_lines)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",  # cheap fast model for batch classification
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        # Extract JSON from response
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if json_match:
            results = json.loads(json_match.group())
            return results
    except Exception as exc:
        logger.error("LLM scoring failed: %s", exc)

    return []


# ---------------------------------------------------------------------------
# Impact level determination
# ---------------------------------------------------------------------------

def determine_impact(title: str, summary: str = "", sentiment_score: float = 0.0) -> str:
    """Determine impact level (High/Medium/Low) from text and score."""
    text = f"{title} {summary}".lower()

    impact_data = _LEXICON.get("impact_keywords", {})
    high_kw = impact_data.get("high", [])
    medium_kw = impact_data.get("medium", [])

    for kw in high_kw:
        if kw in text:
            return "High"

    for kw in medium_kw:
        if kw in text:
            return "Medium"

    # Fall back to score magnitude
    if abs(sentiment_score) >= 0.6:
        return "High"
    if abs(sentiment_score) >= 0.3:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

def detect_category(title: str, summary: str = "") -> str:
    """Classify news into: Macro / Political / Deals / Earnings / Sector"""
    text = f"{title} {summary}".lower()
    cat_kw = _LEXICON.get("category_keywords", {})

    # Check Earnings first (most specific)
    for kw in cat_kw.get("earnings", []):
        if kw in text:
            return "Earnings"

    # Deals
    for kw in cat_kw.get("deals", []):
        if kw in text:
            return "Deals"

    # Political
    for kw in cat_kw.get("political", []):
        if kw in text:
            return "Political"

    # Macro
    for kw in cat_kw.get("macro", []):
        if kw in text:
            return "Macro"

    return "Sector"


# ---------------------------------------------------------------------------
# Keyword alert detection
# ---------------------------------------------------------------------------

def check_keyword_alerts(title: str, summary: str, alert_keywords: list[str]) -> bool:
    """Return True if any configured keyword alert matches."""
    text = f"{title} {summary}".lower()
    return any(kw.lower() in text for kw in alert_keywords)


# ---------------------------------------------------------------------------
# Surprise detection for economic releases
# ---------------------------------------------------------------------------

def detect_surprise(title: str, actual: Optional[float], consensus: Optional[float]) -> Optional[str]:
    """
    Flag if an economic release significantly beat/missed consensus.
    Returns None if no data, 'BEAT', 'MISS', or 'IN-LINE'.
    """
    if actual is None or consensus is None:
        return None
    if consensus == 0:
        return None
    diff_pct = abs(actual - consensus) / abs(consensus) * 100
    if diff_pct < 5:
        return "IN-LINE"
    if actual > consensus:
        return "BEAT"
    return "MISS"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_article(
    title: str,
    summary: str = "",
    use_llm: bool = False,
    api_key: str = "",
) -> dict:
    """
    Score a single article. Returns dict with:
    {score, label, impact_level, category, is_pinned_hint}
    """
    score = score_text_mode_a(title, summary)
    impact = determine_impact(title, summary, score)
    category = detect_category(title, summary)

    label = "neutral"
    if score >= 0.15:
        label = "bullish"
    elif score <= -0.15:
        label = "bearish"

    return {
        "sentiment_score": round(score, 3),
        "sentiment_label": label,
        "impact_level": impact,
        "category": category,
    }


def score_articles_batch(
    articles: list[dict],
    use_llm: bool = False,
    api_key: str = "",
) -> list[dict]:
    """
    Score a list of article dicts in-place.
    Each dict should have 'title' and optionally 'summary'.
    Adds sentiment_score, sentiment_label, impact_level, category keys.
    """
    if use_llm and api_key:
        # Batch LLM scoring — chunk into groups of 20 to keep prompts manageable
        chunk_size = 20
        llm_results = []
        for i in range(0, len(articles), chunk_size):
            chunk = articles[i:i + chunk_size]
            results = score_batch_llm(chunk, api_key)
            if len(results) == len(chunk):
                llm_results.extend(results)
            else:
                # LLM failed for this chunk, fill with Mode A
                for art in chunk:
                    llm_results.append(None)

        for article, llm_result in zip(articles, llm_results):
            if llm_result:
                article["sentiment_score"] = round(float(llm_result.get("score", 0.0)), 3)
                article["sentiment_label"] = llm_result.get("sentiment", "neutral")
                article["impact_level"] = llm_result.get("impact", "Low")
                article["category"] = detect_category(article.get("title", ""), article.get("summary", ""))
                article["llm_reason"] = llm_result.get("reason", "")
            else:
                result = score_article(article.get("title", ""), article.get("summary", ""))
                article.update(result)
    else:
        for article in articles:
            result = score_article(article.get("title", ""), article.get("summary", ""))
            article.update(result)

    return articles
