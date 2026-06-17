"""
API-based news aggregators (Finnhub, NewsAPI, Alpha Vantage, Marketaux).
All keys are optional — gracefully skip if not configured.
"""
import logging
import datetime
from typing import Optional
import httpx
from src.config import cfg

logger = logging.getLogger(__name__)

FINANCIAL_QUERY = "stock market earnings fed interest rate tariff inflation economy"


def _truncate(text: str, max_chars: int = 300) -> str:
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text.strip()


# ---------------------------------------------------------------------------
# Finnhub
# ---------------------------------------------------------------------------

def fetch_finnhub(category: str = "general") -> tuple[list[dict], Optional[str]]:
    """Fetch market news from Finnhub. Returns (articles, error_msg)."""
    if not cfg.finnhub_key or not cfg.source_enabled("api_finnhub"):
        return [], None

    url = "https://finnhub.io/api/v1/news"
    params = {"category": category, "token": cfg.finnhub_key}
    try:
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return [], f"Finnhub: {exc}"

    articles = []
    today = datetime.datetime.utcnow().date()
    for item in data:
        ts = item.get("datetime")
        if ts:
            pub = datetime.datetime.utcfromtimestamp(ts)
        else:
            pub = datetime.datetime.utcnow()

        # Only include today's and yesterday's news
        if pub.date() < today - datetime.timedelta(days=1):
            continue

        articles.append({
            "title": item.get("headline", "").strip(),
            "url": item.get("url", ""),
            "source": f"Finnhub / {item.get('source', 'Unknown')}",
            "published_at": pub,
            "summary": _truncate(item.get("summary", "")),
            "category_hint": "Macro",
            "image": item.get("image"),
        })

    return articles, None


# ---------------------------------------------------------------------------
# NewsAPI
# ---------------------------------------------------------------------------

def fetch_newsapi() -> tuple[list[dict], Optional[str]]:
    """Fetch financial news from NewsAPI."""
    if not cfg.newsapi_key or not cfg.source_enabled("api_newsapi"):
        return [], None

    url = "https://newsapi.org/v2/top-headlines"
    params = {
        "category": "business",
        "language": "en",
        "pageSize": 40,
        "apiKey": cfg.newsapi_key,
    }
    try:
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return [], f"NewsAPI: {exc}"

    if data.get("status") != "ok":
        return [], f"NewsAPI error: {data.get('message', 'unknown')}"

    articles = []
    for item in data.get("articles", []):
        published_at_str = item.get("publishedAt", "")
        try:
            pub = datetime.datetime.strptime(published_at_str, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pub = datetime.datetime.utcnow()

        url_val = item.get("url", "")
        title = (item.get("title") or "").replace(" - " + (item.get("source", {}).get("name") or ""), "").strip()
        if not title or not url_val:
            continue

        articles.append({
            "title": title,
            "url": url_val,
            "source": f"NewsAPI / {item.get('source', {}).get('name', 'Unknown')}",
            "published_at": pub,
            "summary": _truncate(item.get("description") or item.get("content") or ""),
            "category_hint": "Macro",
        })

    return articles, None


# ---------------------------------------------------------------------------
# Alpha Vantage
# ---------------------------------------------------------------------------

def fetch_alpha_vantage() -> tuple[list[dict], Optional[str]]:
    """Fetch market news and sentiment from Alpha Vantage News Sentiment API."""
    if not cfg.alpha_vantage_key or not cfg.source_enabled("api_alpha_vantage"):
        return [], None

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "topics": "financial_markets,economy_macro,economy_fiscal,financial_markets",
        "limit": 30,
        "apikey": cfg.alpha_vantage_key,
    }
    try:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return [], f"AlphaVantage: {exc}"

    if "feed" not in data:
        return [], f"AlphaVantage: {data.get('Note') or data.get('Information') or 'no feed'}"

    articles = []
    today = datetime.datetime.utcnow().date()
    for item in data.get("feed", []):
        time_published = item.get("time_published", "")
        try:
            pub = datetime.datetime.strptime(time_published, "%Y%m%dT%H%M%S")
        except Exception:
            pub = datetime.datetime.utcnow()

        if pub.date() < today - datetime.timedelta(days=1):
            continue

        articles.append({
            "title": item.get("title", "").strip(),
            "url": item.get("url", ""),
            "source": f"AlphaVantage / {item.get('source', 'Unknown')}",
            "published_at": pub,
            "summary": _truncate(item.get("summary", "")),
            "category_hint": "Macro",
            # Alpha Vantage provides its own sentiment; we'll override with ours
            "_av_sentiment": item.get("overall_sentiment_label", ""),
            "_av_score": item.get("overall_sentiment_score", 0),
        })

    return articles, None


# ---------------------------------------------------------------------------
# Marketaux
# ---------------------------------------------------------------------------

def fetch_marketaux() -> tuple[list[dict], Optional[str]]:
    """Fetch financial news from Marketaux API."""
    if not cfg.marketaux_key or not cfg.source_enabled("api_marketaux"):
        return [], None

    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    url = "https://api.marketaux.com/v1/news/all"
    params = {
        "filter_entities": "true",
        "language": "en",
        "published_after": f"{today_str}T00:00:00",
        "limit": 30,
        "api_token": cfg.marketaux_key,
    }
    try:
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return [], f"Marketaux: {exc}"

    articles = []
    for item in data.get("data", []):
        pub_str = item.get("published_at", "")
        try:
            pub = datetime.datetime.fromisoformat(pub_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pub = datetime.datetime.utcnow()

        # Extract tickers from entities
        entities = item.get("entities", [])
        tickers_from_api = [
            {"ticker": e["symbol"], "name": e.get("name", e["symbol"]), "direction": "neutral"}
            for e in entities if e.get("symbol") and e.get("type") == "equity"
        ]

        articles.append({
            "title": item.get("title", "").strip(),
            "url": item.get("url", ""),
            "source": f"Marketaux / {item.get('source', 'Unknown')}",
            "published_at": pub,
            "summary": _truncate(item.get("description", "")),
            "category_hint": "Macro",
            "_preloaded_tickers": tickers_from_api,
        })

    return articles, None


# ---------------------------------------------------------------------------
# Combined fetch
# ---------------------------------------------------------------------------

def fetch_all_api_news() -> tuple[list[dict], list[str]]:
    """Fetch from all configured news APIs. Returns (articles, failed_sources)."""
    all_articles: list[dict] = []
    failed: list[str] = []

    for name, fetcher in [
        ("Finnhub", fetch_finnhub),
        ("NewsAPI", fetch_newsapi),
        ("Alpha Vantage", fetch_alpha_vantage),
        ("Marketaux", fetch_marketaux),
    ]:
        try:
            articles, error = fetcher()
            if error:
                logger.warning(error)
                failed.append(name)
            else:
                all_articles.extend(articles)
                logger.info("%s: %d articles", name, len(articles))
        except Exception as exc:
            logger.error("%s unexpected error: %s", name, exc)
            failed.append(name)

    return all_articles, failed
