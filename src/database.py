import os
import json
import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, Text, Boolean, event
)
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "market_briefing.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Enable WAL mode for better concurrent read performance
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class NewsItem(Base):
    __tablename__ = "news_items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    url = Column(String(1000), unique=True, nullable=False)
    source = Column(String(100))
    published_at = Column(DateTime)
    summary = Column(Text)
    sentiment_score = Column(Float, default=0.0)   # -1.0 to 1.0
    sentiment_label = Column(String(20), default="neutral")  # bullish/bearish/neutral
    impact_level = Column(String(10), default="Low")  # High/Medium/Low
    tickers_json = Column(Text, default="[]")  # JSON list of ticker dicts
    sectors_json = Column(Text, default="[]")  # JSON list of sector ETFs
    category = Column(String(50), default="General")  # Macro/Political/Deals/Earnings/Sector
    date_str = Column(String(10), index=True)  # YYYY-MM-DD
    is_pinned = Column(Boolean, default=False)  # keyword alert match
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    @property
    def tickers(self):
        return json.loads(self.tickers_json or "[]")

    @tickers.setter
    def tickers(self, value):
        self.tickers_json = json.dumps(value)

    @property
    def sectors(self):
        return json.loads(self.sectors_json or "[]")

    @sectors.setter
    def sectors(self, value):
        self.sectors_json = json.dumps(value)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "summary": self.summary,
            "sentiment_score": self.sentiment_score,
            "sentiment_label": self.sentiment_label,
            "impact_level": self.impact_level,
            "tickers": self.tickers,
            "sectors": self.sectors,
            "category": self.category,
            "date_str": self.date_str,
            "is_pinned": self.is_pinned,
        }


class DayVerdict(Base):
    __tablename__ = "day_verdicts"

    id = Column(Integer, primary_key=True, index=True)
    date_str = Column(String(10), unique=True, index=True)
    verdict = Column(String(20))     # BULLISH/BEARISH/VOLATILE/MIXED
    confidence = Column(Float)
    justification = Column(Text)
    sp500_change = Column(Float)
    nasdaq_change = Column(Float)
    dow_change = Column(Float)
    russell2k_change = Column(Float)
    vix = Column(Float)
    vix_change = Column(Float)
    news_sentiment_avg = Column(Float)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "date_str": self.date_str,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "justification": self.justification,
            "sp500_change": self.sp500_change,
            "nasdaq_change": self.nasdaq_change,
            "dow_change": self.dow_change,
            "russell2k_change": self.russell2k_change,
            "vix": self.vix,
            "vix_change": self.vix_change,
            "news_sentiment_avg": self.news_sentiment_avg,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    date_str = Column(String(10), index=True)
    timestamp = Column(DateTime)
    symbol = Column(String(20))
    name = Column(String(100))
    price = Column(Float)
    change_pct = Column(Float)
    change_abs = Column(Float)
    data_type = Column(String(20))  # index/commodity/crypto/yield/etf

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "name": self.name,
            "price": self.price,
            "change_pct": self.change_pct,
            "change_abs": self.change_abs,
            "data_type": self.data_type,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class EconomicEvent(Base):
    __tablename__ = "economic_events"

    id = Column(Integer, primary_key=True, index=True)
    event_date = Column(String(10), index=True)
    event_time_ct = Column(String(10))
    title = Column(String(300))
    description = Column(Text)
    impact_level = Column(String(10))
    affected_assets_json = Column(Text, default="[]")
    expected_value = Column(String(50))
    actual_value = Column(String(50))
    consensus = Column(String(50))
    prior_value = Column(String(50))
    is_past = Column(Boolean, default=False)
    category = Column(String(50))  # FOMC/Earnings/Economic/Political/OPEX
    ticker = Column(String(20))    # for earnings events

    @property
    def affected_assets(self):
        return json.loads(self.affected_assets_json or "[]")

    @affected_assets.setter
    def affected_assets(self, value):
        self.affected_assets_json = json.dumps(value)

    def to_dict(self):
        return {
            "id": self.id,
            "event_date": self.event_date,
            "event_time_ct": self.event_time_ct,
            "title": self.title,
            "description": self.description,
            "impact_level": self.impact_level,
            "affected_assets": self.affected_assets,
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
            "consensus": self.consensus,
            "prior_value": self.prior_value,
            "is_past": self.is_past,
            "category": self.category,
            "ticker": self.ticker,
        }


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id = Column(Integer, primary_key=True, index=True)
    date_str = Column(String(10), unique=True, index=True)
    content_md = Column(Text)
    verdict = Column(String(20))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "date_str": self.date_str,
            "verdict": self.verdict,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
