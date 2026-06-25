"""
FastAPI application — serves the dashboard UI and JSON API.

Startup: initialises DB, runs first data refresh.
/api/refresh: triggers a background refresh.
/api/dashboard: returns the full cached dataset.
"""
import logging
import datetime
import hashlib
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder

from src.config import cfg
from src.database import init_db, SessionLocal, NewsItem, DayVerdict, MarketSnapshot, EconomicEvent
from src.cache import cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Daily Market Briefing", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Refresh state (shared mutable — guarded by a simple lock)
_refresh_lock = threading.Lock()
_refresh_state = {
    "is_refreshing": False,
    "last_updated": None,
    "failed_sources": [],
    "error": None,
}

# ---------------------------------------------------------------------------
# Data refresh pipeline
# ---------------------------------------------------------------------------

def _dedup_articles(articles: list[dict]) -> list[dict]:
    """Remove near-duplicate articles by URL and title similarity."""
    seen_urls: set = set()
    seen_title_hashes: set = set()
    result = []
    for a in articles:
        url = a.get("url", "")
        title = a.get("title", "").lower().strip()
        # Normalise URL to remove utm params
        url_clean = url.split("?")[0].rstrip("/")
        title_hash = hashlib.md5(title[:60].encode()).hexdigest()

        if url_clean in seen_urls or title_hash in seen_title_hashes:
            continue
        seen_urls.add(url_clean)
        seen_title_hashes.add(title_hash)
        result.append(a)
    return result


def _refresh_all_data(use_llm: bool = False) -> dict:
    """
    Full refresh pipeline. Called on startup and on manual /api/refresh.
    Returns the dashboard data dict and also writes to DB + cache.
    """
    from src.aggregators.rss_feeds import fetch_all_rss
    from src.aggregators.news_api import fetch_all_api_news
    from src.aggregators.market_data import (
        fetch_market_snapshot, fetch_vix_data,
        fetch_sector_heatmap, fetch_watchlist_prices,
        fetch_earnings_reactions, fetch_earnings_calendar,
        fetch_nasdaq100_snapshot, fetch_stock_technicals,
    )
    from src.aggregators.events import get_all_events
    from src.analysis.sentiment import score_articles_batch, check_keyword_alerts
    from src.analysis.ticker_mapping import build_ticker_list, build_watchlist
    from src.analysis.verdict import compute_verdict
    from src.analysis.highlights import compute_today_top_movers, compute_tomorrow_candidates
    from src.report import generate_report

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    failed_sources: list[str] = []

    # ------------------------------------------------------------------
    # 1. Fetch news from all sources
    # ------------------------------------------------------------------
    rss_articles, rss_failed = fetch_all_rss(today_only=True)
    failed_sources.extend(rss_failed)

    api_articles, api_failed = fetch_all_api_news()
    failed_sources.extend(api_failed)

    all_raw = rss_articles + api_articles
    all_raw = _dedup_articles(all_raw)
    logger.info("Total unique articles after dedup: %d", len(all_raw))

    # ------------------------------------------------------------------
    # 2. Score sentiment + categorise + extract tickers
    # ------------------------------------------------------------------
    anthr_key = cfg.anthropic_key if use_llm else ""
    scored = score_articles_batch(all_raw, use_llm=use_llm, api_key=anthr_key)

    processed_news: list[dict] = []
    for art in scored:
        tickers, sectors = build_ticker_list(
            art.get("title", ""),
            art.get("summary", ""),
            art.get("sentiment_score", 0.0),
            art.get("_preloaded_tickers"),
        )
        is_pinned = check_keyword_alerts(
            art.get("title", ""),
            art.get("summary", ""),
            cfg.keyword_alerts,
        )
        processed_news.append({
            **art,
            "tickers": tickers,
            "sectors": sectors,
            "is_pinned": is_pinned,
            "date_str": today_str,
        })

    # Sort: pinned first, then by impact, then by time
    impact_order = {"High": 0, "Medium": 1, "Low": 2}
    processed_news.sort(key=lambda x: (
        0 if x.get("is_pinned") else 1,
        impact_order.get(x.get("impact_level", "Low"), 2),
        -(x.get("published_at").timestamp() if x.get("published_at") else 0),
    ))

    # ------------------------------------------------------------------
    # 3. Market data
    # ------------------------------------------------------------------
    snapshot, snap_failed = fetch_market_snapshot()
    failed_sources.extend(f"market:{s}" for s in snap_failed[:3])

    vix_data = fetch_vix_data()
    sector_heatmap = fetch_sector_heatmap()

    # ------------------------------------------------------------------
    # 4. Watchlist prices
    # ------------------------------------------------------------------
    # Two-pass: first determine which tickers will actually make the top-10
    # watchlist (ranked by mention count), THEN fetch prices only for those —
    # avoids truncating by news-scan order before ranking is known.
    provisional_watchlist = build_watchlist(processed_news, cfg.watchlist, {})
    candidate_tickers = list(cfg.watchlist)
    for w in provisional_watchlist:
        if w["ticker"] not in candidate_tickers:
            candidate_tickers.append(w["ticker"])

    watchlist_prices = fetch_watchlist_prices(candidate_tickers)
    watchlist = build_watchlist(processed_news, cfg.watchlist, watchlist_prices)

    # ------------------------------------------------------------------
    # 5. Day verdict
    # ------------------------------------------------------------------
    verdict_data = compute_verdict(snapshot, vix_data, processed_news, sector_heatmap)

    # ------------------------------------------------------------------
    # 6. Events calendar
    # ------------------------------------------------------------------
    today = datetime.datetime.now().date()
    events = get_all_events(today)

    # ------------------------------------------------------------------
    # 7. Earnings reactions (last 7 days)
    # ------------------------------------------------------------------
    earnings_reactions = []
    try:
        earnings_reactions = fetch_earnings_reactions(cfg.earnings_watchlist[:15])
    except Exception as exc:
        logger.warning("Earnings reactions failed: %s", exc)

    earnings_calendar = []
    try:
        earnings_calendar = fetch_earnings_calendar(cfg.earnings_watchlist)
    except Exception as exc:
        logger.warning("Earnings calendar failed: %s", exc)

    # ------------------------------------------------------------------
    # 7b. Stock highlights: today's top Nasdaq-100 gainers + tomorrow's
    #     momentum/catalyst candidates (heuristic, not a prediction)
    # ------------------------------------------------------------------
    today_top_movers = []
    tomorrow_candidates = []
    try:
        nasdaq100_snapshot = fetch_nasdaq100_snapshot()
        today_top_movers = compute_today_top_movers(nasdaq100_snapshot, processed_news)
        tomorrow_candidates = compute_tomorrow_candidates(
            nasdaq100_snapshot, processed_news, earnings_calendar,
            exclude_tickers={m["ticker"] for m in today_top_movers},
        )

        # Enrich just the handful of tickers shown (monthly RSI + analyst targets)
        highlight_tickers = [m["ticker"] for m in today_top_movers] + [p["ticker"] for p in tomorrow_candidates]
        technicals = fetch_stock_technicals(highlight_tickers)
        for entry in today_top_movers + tomorrow_candidates:
            entry.update(technicals.get(entry["ticker"], {}))
    except Exception as exc:
        logger.warning("Stock highlights failed: %s", exc)

    # ------------------------------------------------------------------
    # 8. Persist to DB
    # ------------------------------------------------------------------
    _persist_to_db(today_str, processed_news, verdict_data, snapshot, events)

    # ------------------------------------------------------------------
    # 9. Build sources status
    # ------------------------------------------------------------------
    sources_status = {
        "rss": len(rss_failed) < len([f for f in rss_failed]),
        "finnhub": "Finnhub" not in failed_sources,
        "newsapi": "NewsAPI" not in failed_sources,
        "alpha_vantage": "Alpha Vantage" not in failed_sources,
        "marketaux": "Marketaux" not in failed_sources,
        "yfinance": "market:" not in " ".join(failed_sources),
    }

    # ------------------------------------------------------------------
    # 10. Assemble dashboard payload
    # ------------------------------------------------------------------
    dashboard = {
        "verdict": verdict_data,
        "market_snapshot": [s for s in snapshot],
        "sector_heatmap": sector_heatmap,
        "news": [_news_to_dict(n) for n in processed_news],
        "events": events,
        "watchlist": watchlist,
        "earnings_reactions": earnings_reactions,
        "earnings_calendar": earnings_calendar,
        "today_top_movers": today_top_movers,
        "tomorrow_candidates": tomorrow_candidates,
        "sources_status": sources_status,
        "failed_sources": list(set(failed_sources)),
        "last_updated": datetime.datetime.now().isoformat(),
        "today_str": today_str,
    }

    # ------------------------------------------------------------------
    # 11. Generate and save markdown report
    # ------------------------------------------------------------------
    try:
        generate_report(dashboard_data=dashboard, save=True)
    except Exception as exc:
        logger.warning("Report generation failed: %s", exc)

    # Encode to plain JSON-safe types before caching (strips datetime, numpy, etc.)
    dashboard = jsonable_encoder(dashboard)
    cache.set("dashboard", dashboard, ttl=cfg.news_ttl)

    return dashboard


def _news_to_dict(n: dict) -> dict:
    pub = n.get("published_at")
    return {
        "title": n.get("title", ""),
        "url": n.get("url", ""),
        "source": n.get("source", ""),
        "published_at": pub.isoformat() if pub and hasattr(pub, "isoformat") else str(pub or ""),
        "summary": n.get("summary", ""),
        "sentiment_score": n.get("sentiment_score", 0.0),
        "sentiment_label": n.get("sentiment_label", "neutral"),
        "impact_level": n.get("impact_level", "Low"),
        "tickers": n.get("tickers", []),
        "sectors": n.get("sectors", []),
        "category": n.get("category", "General"),
        "is_pinned": n.get("is_pinned", False),
        "date_str": n.get("date_str", ""),
    }


def _persist_to_db(today_str: str, news: list[dict], verdict_data: dict, snapshot: list[dict], events: list[dict]):
    """Persist today's data to SQLite."""
    db = SessionLocal()
    try:
        # News items — skip any URL already in DB (constraint is global, not per-day)
        existing_urls = {row[0] for row in db.query(NewsItem.url).all()}
        new_items = []
        for n in news:
            url = n.get("url", "")
            if not url or url in existing_urls:
                continue
            pub = n.get("published_at")
            item = NewsItem(
                title=n.get("title", "")[:500],
                url=url[:1000],
                source=n.get("source", "")[:100],
                published_at=pub,
                summary=n.get("summary", ""),
                sentiment_score=n.get("sentiment_score", 0.0),
                sentiment_label=n.get("sentiment_label", "neutral"),
                impact_level=n.get("impact_level", "Low"),
                tickers_json=__import__("json").dumps(n.get("tickers", [])),
                sectors_json=__import__("json").dumps(n.get("sectors", [])),
                category=n.get("category", "General"),
                date_str=today_str,
                is_pinned=n.get("is_pinned", False),
            )
            new_items.append(item)
            existing_urls.add(url)
        db.add_all(new_items)

        # Day verdict — upsert
        existing_verdict = db.query(DayVerdict).filter(DayVerdict.date_str == today_str).first()
        if existing_verdict:
            existing_verdict.verdict = verdict_data.get("verdict", "MIXED")
            existing_verdict.confidence = verdict_data.get("confidence", 50)
            existing_verdict.justification = verdict_data.get("justification", "")
            existing_verdict.sp500_change = verdict_data.get("sp500_change")
            existing_verdict.nasdaq_change = verdict_data.get("nasdaq_change")
            existing_verdict.dow_change = verdict_data.get("dow_change")
            existing_verdict.russell2k_change = verdict_data.get("russell2k_change")
            existing_verdict.vix = verdict_data.get("vix")
            existing_verdict.vix_change = verdict_data.get("vix_change")
            existing_verdict.news_sentiment_avg = verdict_data.get("news_sentiment_avg")
        else:
            db.add(DayVerdict(
                date_str=today_str,
                verdict=verdict_data.get("verdict", "MIXED"),
                confidence=verdict_data.get("confidence", 50),
                justification=verdict_data.get("justification", ""),
                sp500_change=verdict_data.get("sp500_change"),
                nasdaq_change=verdict_data.get("nasdaq_change"),
                dow_change=verdict_data.get("dow_change"),
                russell2k_change=verdict_data.get("russell2k_change"),
                vix=verdict_data.get("vix"),
                vix_change=verdict_data.get("vix_change"),
                news_sentiment_avg=verdict_data.get("news_sentiment_avg"),
            ))

        # Market snapshot (latest only)
        db.query(MarketSnapshot).filter(MarketSnapshot.date_str == today_str).delete()
        for s in snapshot:
            ts = s.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.datetime.fromisoformat(ts)
            db.add(MarketSnapshot(
                date_str=today_str,
                timestamp=ts or datetime.datetime.utcnow(),
                symbol=s.get("symbol", ""),
                name=s.get("name", ""),
                price=s.get("price"),
                change_pct=s.get("change_pct"),
                change_abs=s.get("change_abs"),
                data_type=s.get("data_type", "index"),
            ))

        db.commit()
    except Exception as exc:
        logger.error("DB persist failed: %s", exc)
        db.rollback()
    finally:
        db.close()


def _do_background_refresh(use_llm: bool = False):
    """Background thread worker for data refresh."""
    global _refresh_state
    with _refresh_lock:
        if _refresh_state["is_refreshing"]:
            return
        _refresh_state["is_refreshing"] = True
        _refresh_state["error"] = None

    try:
        logger.info("Starting data refresh…")
        _refresh_all_data(use_llm=use_llm)
        with _refresh_lock:
            _refresh_state["last_updated"] = datetime.datetime.now().isoformat()
            logger.info("Refresh complete.")
    except Exception as exc:
        logger.error("Refresh failed: %s", exc, exc_info=True)
        with _refresh_lock:
            _refresh_state["error"] = str(exc)
    finally:
        with _refresh_lock:
            _refresh_state["is_refreshing"] = False


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    cfg.DB_DIR.mkdir(exist_ok=True)
    cfg.REPORTS_DIR.mkdir(exist_ok=True)
    init_db()
    # Kick off first refresh in a background thread (non-blocking)
    t = threading.Thread(target=_do_background_refresh, daemon=True)
    t.start()
    logger.info("Daily Market Briefing started. Refresh in background…")


# ---------------------------------------------------------------------------
# Static files + SPA
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"

@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.datetime.now().isoformat()}


@app.get("/api/status")
async def get_status():
    with _refresh_lock:
        state = dict(_refresh_state)
    return {**state, "cache_keys": cache.keys()}


@app.post("/api/refresh")
async def trigger_refresh(background_tasks: BackgroundTasks, llm: bool = False):
    with _refresh_lock:
        if _refresh_state["is_refreshing"]:
            return JSONResponse({"status": "already_refreshing"})
    background_tasks.add_task(_do_background_refresh, use_llm=llm)
    return {"status": "refresh_started"}


@app.get("/api/dashboard")
async def get_dashboard():
    data = cache.get("dashboard")
    if data is None:
        # Check if refresh in progress
        with _refresh_lock:
            refreshing = _refresh_state["is_refreshing"]
        if refreshing:
            return JSONResponse({"loading": True, "message": "Refresh in progress…"})
        # Try loading from DB for today
        data = _load_from_db()
        if data:
            cache.set("dashboard", data)
    if data is None:
        return JSONResponse({"loading": True, "message": "Fetching market data…"})
    return JSONResponse(jsonable_encoder(data))


def _load_from_db() -> Optional[dict]:
    """Load today's data from SQLite as a fallback."""
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        verdict_row = db.query(DayVerdict).filter(DayVerdict.date_str == today_str).first()
        news_rows = db.query(NewsItem).filter(NewsItem.date_str == today_str).limit(100).all()
        snapshot_rows = db.query(MarketSnapshot).filter(MarketSnapshot.date_str == today_str).all()

        if not verdict_row and not news_rows:
            return None

        verdict_data = verdict_row.to_dict() if verdict_row else {}
        news_data = [n.to_dict() for n in news_rows]
        snapshot_data = [s.to_dict() for s in snapshot_rows]

        payload = {
            "verdict": verdict_data,
            "market_snapshot": snapshot_data,
            "sector_heatmap": [],
            "news": news_data,
            "events": [],
            "watchlist": [],
            "earnings_reactions": [],
            "sources_status": {},
            "failed_sources": [],
            "last_updated": verdict_row.created_at.isoformat() if verdict_row and verdict_row.created_at else None,
            "today_str": today_str,
        }
        return jsonable_encoder(payload)
    except Exception as exc:
        logger.error("DB load failed: %s", exc)
        return None
    finally:
        db.close()


@app.get("/api/news")
async def get_news(category: Optional[str] = None, limit: int = 100, date: Optional[str] = None):
    data = cache.get("dashboard")
    if data:
        news = data.get("news", [])
        if category and category.lower() != "all":
            news = [n for n in news if n.get("category", "").lower() == category.lower()]
        return JSONResponse(news[:limit])

    today_str = date or datetime.datetime.now().strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        q = db.query(NewsItem).filter(NewsItem.date_str == today_str)
        if category and category.lower() != "all":
            q = q.filter(NewsItem.category == category)
        rows = q.limit(limit).all()
        return JSONResponse([r.to_dict() for r in rows])
    finally:
        db.close()


@app.get("/api/verdict")
async def get_verdict(date: Optional[str] = None):
    if not date:
        data = cache.get("dashboard")
        if data:
            return JSONResponse(data.get("verdict", {}))

    today_str = date or datetime.datetime.now().strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        row = db.query(DayVerdict).filter(DayVerdict.date_str == today_str).first()
        if row:
            return JSONResponse(row.to_dict())
        return JSONResponse({})
    finally:
        db.close()


@app.get("/api/events")
async def get_events():
    data = cache.get("dashboard")
    if data:
        return JSONResponse(data.get("events", []))
    from src.aggregators.events import get_all_events
    events = get_all_events()
    return JSONResponse(events)


@app.get("/api/history")
async def get_history(days: int = 30):
    """Return historical verdict + VIX data for the history chart."""
    db = SessionLocal()
    try:
        rows = db.query(DayVerdict).order_by(DayVerdict.date_str.desc()).limit(days).all()
        rows.reverse()
        return JSONResponse([r.to_dict() for r in rows])
    finally:
        db.close()


@app.get("/api/reports")
async def list_reports():
    """List all saved report files."""
    reports_dir = cfg.REPORTS_DIR
    reports_dir.mkdir(exist_ok=True)
    files = sorted(reports_dir.glob("*.md"), reverse=True)
    result = []
    for f in files:
        date_str = f.stem
        # Try to get verdict from DB
        db = SessionLocal()
        try:
            row = db.query(DayVerdict).filter(DayVerdict.date_str == date_str).first()
            verdict = row.verdict if row else "UNKNOWN"
        except Exception:
            verdict = "UNKNOWN"
        finally:
            db.close()
        result.append({"date_str": date_str, "filename": f.name, "verdict": verdict})
    return JSONResponse(result)


@app.get("/api/reports/{date_str}")
async def get_report(date_str: str):
    """Return a specific report as plain text markdown."""
    report_path = cfg.REPORTS_DIR / f"{date_str}.md"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return PlainTextResponse(report_path.read_text(encoding="utf-8"))


@app.get("/api/export/{date_str}")
async def export_report(date_str: str):
    """Download a report as a markdown file."""
    report_path = cfg.REPORTS_DIR / f"{date_str}.md"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(
        path=str(report_path),
        filename=f"market_briefing_{date_str}.md",
        media_type="text/markdown",
    )
