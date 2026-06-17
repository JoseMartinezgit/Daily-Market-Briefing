import os
import yaml
from dotenv import load_dotenv
from pathlib import Path

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def _load_yaml() -> dict:
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    return {}


_cfg = _load_yaml()


class Config:
    # Paths
    ROOT = ROOT
    DB_DIR = ROOT / "db"
    REPORTS_DIR = ROOT / "reports"
    DATA_DIR = ROOT / "data"
    STATIC_DIR = ROOT / "src" / "static"

    # Timezone
    timezone: str = _cfg.get("timezone", "America/Chicago")

    # API keys (from .env)
    finnhub_key: str = os.getenv("FINNHUB_API_KEY", "")
    newsapi_key: str = os.getenv("NEWS_API_KEY", "")
    alpha_vantage_key: str = os.getenv("ALPHA_VANTAGE_KEY", "")
    marketaux_key: str = os.getenv("MARKETAUX_API_KEY", "")
    fred_key: str = os.getenv("FRED_API_KEY", "")
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Sources toggle
    sources: dict = _cfg.get("sources", {})

    # Cache TTLs
    cache_cfg: dict = _cfg.get("cache", {})
    market_data_ttl: int = cache_cfg.get("market_data_ttl", 900)
    news_ttl: int = cache_cfg.get("news_ttl", 900)
    events_ttl: int = cache_cfg.get("events_ttl", 3600)

    # Sentiment weights
    sentiment_weights: dict = _cfg.get("sentiment_weights", {
        "market_indices": 0.40,
        "vix": 0.20,
        "news_sentiment": 0.30,
        "breadth": 0.10,
    })

    # Watchlists
    watchlist: list = _cfg.get("watchlist", [])
    earnings_watchlist: list = _cfg.get("earnings_watchlist", [])
    sector_etfs: list = _cfg.get("sector_etfs", [])
    market_symbols: dict = _cfg.get("market_symbols", {})
    keyword_alerts: list = [k.lower() for k in _cfg.get("keyword_alerts", [])]

    # Schedule
    report_schedule: str = _cfg.get("report_schedule", "")

    @classmethod
    def source_enabled(cls, key: str) -> bool:
        return cls.sources.get(key, True)


cfg = Config()
