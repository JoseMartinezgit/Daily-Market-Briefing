"""
RSS feed aggregator — works without any API keys.
Parses standard Atom/RSS feeds and returns normalized dicts.
"""
import logging
import hashlib
import datetime
from typing import Optional
import feedparser
import httpx
from src.config import cfg

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    {
        "name": "Reuters Business",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "key": "rss_reuters",
        "category_hint": "Macro",
    },
    {
        "name": "Reuters Markets",
        "url": "https://feeds.reuters.com/reuters/marketsNews",
        "key": "rss_reuters",
        "category_hint": "Macro",
    },
    {
        "name": "CNBC Markets",
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "key": "rss_cnbc",
        "category_hint": "Macro",
    },
    {
        "name": "CNBC Finance",
        "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        "key": "rss_cnbc",
        "category_hint": "Earnings",
    },
    {
        "name": "MarketWatch",
        "url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
        "key": "rss_marketwatch",
        "category_hint": "Macro",
    },
    {
        "name": "MarketWatch Top Stories",
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "key": "rss_marketwatch",
        "category_hint": "Macro",
    },
    {
        "name": "Yahoo Finance",
        "url": "https://finance.yahoo.com/rss/topstories",
        "key": "rss_yahoo_finance",
        "category_hint": "General",
    },
    {
        "name": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "key": "rss_fed",
        "category_hint": "Macro",
    },
    {
        "name": "SEC Press Releases",
        "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=20&output=atom",
        "key": "rss_sec",
        "category_hint": "Deals",
    },
    {
        "name": "Seeking Alpha",
        "url": "https://seekingalpha.com/market_currents.xml",
        "key": "rss_seeking_alpha",
        "category_hint": "Earnings",
    },
    {
        "name": "Investopedia News",
        "url": "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline",
        "key": "rss_yahoo_finance",
        "category_hint": "Macro",
    },
    {
        "name": "The Wall Street Journal Markets",
        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "key": "rss_marketwatch",
        "category_hint": "Macro",
    },
]


def _parse_date(entry) -> Optional[datetime.datetime]:
    """Try multiple date fields to get a publish time."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                return datetime.datetime(*val[:6])
            except Exception:
                pass
    return datetime.datetime.utcnow()


def _summarize(text: str, max_chars: int = 300) -> str:
    """Truncate and clean a summary string."""
    if not text:
        return ""
    # Strip HTML tags crudely
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def fetch_rss_feed(feed_info: dict, timeout: int = 10) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns list of article dicts."""
    if not cfg.source_enabled(feed_info["key"]):
        return []

    articles = []
    try:
        # feedparser can fetch directly but doesn't set a User-Agent header
        # Use httpx to fetch raw content for better compatibility
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MarketBriefingBot/1.0)"
        }
        response = httpx.get(feed_info["url"], timeout=timeout, headers=headers, follow_redirects=True)
        response.raise_for_status()
        parsed = feedparser.parse(response.text)
    except Exception as exc:
        logger.warning("RSS %s failed: %s", feed_info["name"], exc)
        # Try feedparser directly as fallback
        try:
            parsed = feedparser.parse(feed_info["url"])
        except Exception:
            return []

    for entry in parsed.entries:
        url = getattr(entry, "link", "") or getattr(entry, "id", "")
        if not url:
            continue

        title = getattr(entry, "title", "").strip()
        if not title:
            continue

        # Get summary from multiple possible fields
        summary_raw = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or getattr(entry, "content", [{}])[0].get("value", "") if hasattr(entry, "content") and entry.content else ""
        )

        articles.append({
            "title": title,
            "url": url,
            "source": feed_info["name"],
            "published_at": _parse_date(entry),
            "summary": _summarize(summary_raw),
            "category_hint": feed_info["category_hint"],
        })

    return articles


def fetch_all_rss(today_only: bool = True) -> tuple[list[dict], list[str]]:
    """
    Fetch all configured RSS feeds in sequence.
    Returns (articles, failed_source_names).
    """
    all_articles: list[dict] = []
    failed: list[str] = []
    today = datetime.datetime.utcnow().date()

    for feed_info in RSS_FEEDS:
        if not cfg.source_enabled(feed_info["key"]):
            continue
        try:
            articles = fetch_rss_feed(feed_info)
            if today_only:
                # Keep articles from today and yesterday (handle late-evening posts)
                cutoff = datetime.datetime.combine(today - datetime.timedelta(days=1), datetime.time.min)
                articles = [
                    a for a in articles
                    if a.get("published_at") and a["published_at"] >= cutoff
                ]
            all_articles.extend(articles)
            logger.info("RSS %s: %d articles", feed_info["name"], len(articles))
        except Exception as exc:
            logger.error("RSS %s unexpected error: %s", feed_info["name"], exc)
            failed.append(feed_info["name"])

    return all_articles, failed
