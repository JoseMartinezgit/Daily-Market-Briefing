"""
Economic and market event calendar.

Sources (in priority order):
1. FRED API release calendar (if key set)
2. Algorithmically-computed recurring schedule (NFP, CPI, jobless claims, etc.)
3. yfinance earnings calendar for watchlist tickers
4. Hardcoded FOMC / OPEX schedule
"""
import logging
import datetime
import json
import calendar
import pytz
from typing import Optional
import httpx
import yfinance as yf
from src.config import cfg

logger = logging.getLogger(__name__)

CT = pytz.timezone("America/Chicago")
ET = pytz.timezone("America/New_York")


# ---------------------------------------------------------------------------
# FOMC schedule (manually maintained — update annually)
# ---------------------------------------------------------------------------
FOMC_DATES_2025 = [
    "2025-01-29", "2025-03-19", "2025-05-07",
    "2025-06-18", "2025-07-30", "2025-09-17",
    "2025-10-29", "2025-12-10",
]
FOMC_DATES_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-09",
]
FOMC_ALL = set(FOMC_DATES_2025 + FOMC_DATES_2026)

# Options expiration: 3rd Friday of each month
def get_opex_dates(year: int, months: list[int]) -> list[str]:
    dates = []
    for month in months:
        # 3rd Friday
        c = calendar.monthcalendar(year, month)
        fridays = [week[calendar.FRIDAY] for week in c if week[calendar.FRIDAY] != 0]
        if len(fridays) >= 3:
            dates.append(f"{year}-{month:02d}-{fridays[2]:02d}")
    return dates


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> Optional[datetime.date]:
    """Return the nth occurrence (1-based) of weekday in the given month."""
    count = 0
    d = datetime.date(year, month, 1)
    while d.month == month:
        if d.weekday() == weekday:  # Mon=0, Fri=4
            count += 1
            if count == n:
                return d
        d += datetime.timedelta(days=1)
    return None


def _first_business_day(year: int, month: int) -> datetime.date:
    d = datetime.date(year, month, 1)
    while d.weekday() >= 5:  # skip weekends
        d += datetime.timedelta(days=1)
    return d


def _et_to_ct(et_time_str: str) -> str:
    """Convert 'HH:MM AM/PM ET' string to CT. Simple 1-hour offset."""
    # ET is UTC-5 (EST) or UTC-4 (EDT); CT is UTC-6/UTC-5
    # Simplified: CT = ET - 1h
    try:
        t = datetime.datetime.strptime(et_time_str, "%I:%M %p")
        t_ct = t - datetime.timedelta(hours=1)
        return t_ct.strftime("%I:%M %p CT")
    except Exception:
        return et_time_str


def get_recurring_events(target_date: datetime.date) -> list[dict]:
    """
    Compute algorithmically which recurring economic events fall on or near target_date.
    All times are Central Time.
    """
    year, month = target_date.year, target_date.month
    events = []

    # --- Jobs Report (NFP): first Friday of each month ---
    nfp_date = _nth_weekday(year, month, 4, 1)  # 4 = Friday
    if nfp_date and abs((nfp_date - target_date).days) <= 1:
        events.append({
            "event_date": str(nfp_date),
            "event_time_ct": "7:30 AM CT",
            "title": "Non-Farm Payrolls (Jobs Report)",
            "description": "Monthly employment situation report from BLS. NFP + unemployment rate.",
            "impact_level": "High",
            "affected_assets": ["SPY", "TLT", "GLD", "XLF", "XLY"],
            "category": "Economic",
        })

    # --- Jobless Claims: every Thursday ---
    if target_date.weekday() == 3:  # Thursday
        events.append({
            "event_date": str(target_date),
            "event_time_ct": "7:30 AM CT",
            "title": "Initial Jobless Claims",
            "description": "Weekly unemployment insurance claims — proxy for labor market health.",
            "impact_level": "Medium",
            "affected_assets": ["SPY", "TLT", "XLF"],
            "category": "Economic",
        })

    # --- CPI: ~2nd Tuesday/Wednesday of each month ---
    # Typically released 8:30 AM ET on the 10th-15th
    # We approximate as 2nd Wednesday
    cpi_approx = _nth_weekday(year, month, 2, 2)  # 2nd Wednesday
    if cpi_approx and abs((cpi_approx - target_date).days) <= 2:
        events.append({
            "event_date": str(cpi_approx),
            "event_time_ct": "7:30 AM CT",
            "title": "Consumer Price Index (CPI)",
            "description": "Inflation measure. Core CPI (ex-food/energy) is the Fed's key focus.",
            "impact_level": "High",
            "affected_assets": ["TLT", "GLD", "XLK", "XLF", "XLRE"],
            "category": "Economic",
        })

    # --- PPI: day after CPI approx ---
    ppi_approx = cpi_approx + datetime.timedelta(days=1) if cpi_approx else None
    if ppi_approx and abs((ppi_approx - target_date).days) <= 1:
        events.append({
            "event_date": str(ppi_approx),
            "event_time_ct": "7:30 AM CT",
            "title": "Producer Price Index (PPI)",
            "description": "Upstream inflation — leads CPI by 1-3 months.",
            "impact_level": "Medium",
            "affected_assets": ["TLT", "XLE", "XLB"],
            "category": "Economic",
        })

    # --- PCE: last Friday of each month ---
    pce_date = _nth_weekday(year, month, 4, 4)  # 4th Friday (approx last)
    if not pce_date:
        pce_date = _nth_weekday(year, month, 4, 3)
    if pce_date and abs((pce_date - target_date).days) <= 2:
        events.append({
            "event_date": str(pce_date),
            "event_time_ct": "7:30 AM CT",
            "title": "PCE Price Index (Fed's Preferred Inflation Gauge)",
            "description": "Personal Consumption Expenditures index — the Fed's primary inflation benchmark.",
            "impact_level": "High",
            "affected_assets": ["TLT", "SPY", "GLD"],
            "category": "Economic",
        })

    # --- GDP: last week of Jan, Apr, Jul, Oct ---
    gdp_months = {1, 4, 7, 10}
    if month in gdp_months:
        gdp_approx = _nth_weekday(year, month, 3, 4)  # 4th Thursday
        if not gdp_approx:
            gdp_approx = _nth_weekday(year, month, 3, 3)
        if gdp_approx and abs((gdp_approx - target_date).days) <= 3:
            events.append({
                "event_date": str(gdp_approx),
                "event_time_ct": "7:30 AM CT",
                "title": "GDP (Advance / Preliminary Estimate)",
                "description": "Quarterly GDP growth rate — defines recession/expansion.",
                "impact_level": "High",
                "affected_assets": ["SPY", "TLT", "DXY"],
                "category": "Economic",
            })

    # --- Retail Sales: ~mid-month, usually 2nd Wednesday ---
    retail_approx = _nth_weekday(year, month, 2, 2)  # 2nd Wednesday
    if retail_approx and abs((retail_approx - target_date).days) <= 3:
        events.append({
            "event_date": str(retail_approx),
            "event_time_ct": "7:30 AM CT",
            "title": "Retail Sales",
            "description": "Monthly consumer spending on goods — 70% of GDP is consumer-driven.",
            "impact_level": "Medium",
            "affected_assets": ["XLY", "XLP", "SPY"],
            "category": "Economic",
        })

    # --- ISM Manufacturing: first business day ---
    ism_mfg = _first_business_day(year, month)
    if abs((ism_mfg - target_date).days) <= 1:
        events.append({
            "event_date": str(ism_mfg),
            "event_time_ct": "9:00 AM CT",
            "title": "ISM Manufacturing PMI",
            "description": "Purchasing managers index for manufacturing. Above 50 = expansion.",
            "impact_level": "Medium",
            "affected_assets": ["XLI", "XLB", "SPY"],
            "category": "Economic",
        })

    # --- ISM Services: ~3rd business day ---
    ism_svc_approx = _first_business_day(year, month) + datetime.timedelta(days=2)
    if abs((ism_svc_approx - target_date).days) <= 1:
        events.append({
            "event_date": str(ism_svc_approx),
            "event_time_ct": "9:00 AM CT",
            "title": "ISM Services PMI",
            "description": "Services sector is 80% of US economy — this number often moves markets more than manufacturing.",
            "impact_level": "Medium",
            "affected_assets": ["SPY", "XLF", "XLY"],
            "category": "Economic",
        })

    # --- Michigan Consumer Sentiment: 2nd Friday ---
    mich_date = _nth_weekday(year, month, 4, 2)  # 2nd Friday
    if mich_date and abs((mich_date - target_date).days) <= 1:
        events.append({
            "event_date": str(mich_date),
            "event_time_ct": "9:00 AM CT",
            "title": "Michigan Consumer Sentiment",
            "description": "Survey of consumer confidence and inflation expectations.",
            "impact_level": "Low",
            "affected_assets": ["XLY", "SPY"],
            "category": "Economic",
        })

    # --- FOMC Decision ---
    if str(target_date) in FOMC_ALL:
        events.append({
            "event_date": str(target_date),
            "event_time_ct": "1:00 PM CT",
            "title": "FOMC Interest Rate Decision",
            "description": "Federal Reserve rate decision + statement. Press conference 1:30 PM CT.",
            "impact_level": "High",
            "affected_assets": ["SPY", "TLT", "GLD", "XLF", "XLRE", "UUP"],
            "category": "FOMC",
        })

    # --- Options Expiration (3rd Friday) ---
    opex_friday = _nth_weekday(year, month, 4, 3)  # 3rd Friday
    if opex_friday and target_date == opex_friday:
        events.append({
            "event_date": str(target_date),
            "event_time_ct": "3:00 PM CT",
            "title": "Options Expiration (Monthly OPEX)",
            "description": "Monthly options expiration — increased volume and potential pinning/volatility near large open interest strikes.",
            "impact_level": "Medium",
            "affected_assets": ["SPY", "QQQ", "IWM"],
            "category": "OPEX",
        })

    # --- Triple Witching: 3rd Friday of Mar, Jun, Sep, Dec ---
    if month in {3, 6, 9, 12} and opex_friday and target_date == opex_friday:
        # Replace medium with high
        for e in events:
            if "OPEX" in e.get("category", ""):
                e["title"] = "Triple Witching (OPEX + Futures + Index Options)"
                e["impact_level"] = "High"
                e["description"] = "Simultaneous expiration of stock options, index options, and futures. Heavy institutional rebalancing causes elevated volume."

    return events


def fetch_earnings_events(tickers: list[str], target_date: datetime.date) -> list[dict]:
    """Check upcoming/recent earnings for configured watchlist tickers."""
    events = []
    window_start = target_date - datetime.timedelta(days=1)
    window_end = target_date + datetime.timedelta(days=7)

    for sym in tickers[:25]:  # cap to avoid rate limits
        try:
            tk = yf.Ticker(sym)
            cal = tk.get_earnings_dates(limit=4)
            if cal is None or cal.empty:
                continue
            for idx, row in cal.iterrows():
                try:
                    d = idx.date() if hasattr(idx, "date") else None
                    if d is None or not (window_start <= d <= window_end):
                        continue
                    eps_est = row.get("EPS Estimate")
                    eps_actual = row.get("Reported EPS")

                    if d < target_date or (d == target_date and idx.hour < 9):
                        timing = "Before Open"
                    elif idx.hour >= 16:
                        timing = "After Close"
                    else:
                        timing = "During Market"

                    # Get company name
                    try:
                        name = tk.info.get("shortName", sym)
                    except Exception:
                        name = sym

                    events.append({
                        "event_date": str(d),
                        "event_time_ct": "Before Open" if timing == "Before Open" else "After Close",
                        "title": f"Earnings: {name} ({sym}) — {timing}",
                        "description": f"Q earnings report. Est. EPS: {eps_est:.2f}" if eps_est == eps_est and eps_est is not None else "Q earnings report.",
                        "impact_level": "High" if sym in ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM"] else "Medium",
                        "affected_assets": [sym],
                        "category": "Earnings",
                        "ticker": sym,
                        "eps_estimate": float(eps_est) if eps_est is not None and eps_est == eps_est else None,
                        "eps_actual": float(eps_actual) if eps_actual is not None and eps_actual == eps_actual else None,
                    })
                    break
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Earnings calendar for %s: %s", sym, exc)

    return events


def fetch_fred_releases(target_date: datetime.date) -> list[dict]:
    """Fetch upcoming economic data releases from FRED API."""
    if not cfg.fred_key:
        return []

    try:
        url = "https://api.stlouisfed.org/fred/releases/dates"
        start = (target_date - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        end = (target_date + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        params = {
            "realtime_start": start,
            "realtime_end": end,
            "api_key": cfg.fred_key,
            "file_type": "json",
        }
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        events = []
        for rd in data.get("release_dates", []):
            events.append({
                "event_date": rd.get("date", str(target_date)),
                "event_time_ct": "7:30 AM CT",
                "title": f"FRED Release: {rd.get('release_name', 'Economic Data')}",
                "description": f"FRED series update: {rd.get('release_name', '')}",
                "impact_level": "Medium",
                "affected_assets": ["SPY", "TLT"],
                "category": "Economic",
            })
        return events
    except Exception as exc:
        logger.debug("FRED releases failed: %s", exc)
        return []


def get_all_events(target_date: Optional[datetime.date] = None) -> list[dict]:
    """
    Aggregate all event sources and return a time-sorted list for the week.
    Mark past events (with results if available).
    """
    if target_date is None:
        target_date = datetime.datetime.now(CT).date()

    now_ct = datetime.datetime.now(CT)
    all_events: list[dict] = []

    # Recurring economic events
    for offset in range(-1, 8):
        d = target_date + datetime.timedelta(days=offset)
        all_events.extend(get_recurring_events(d))

    # FRED releases (if key available)
    all_events.extend(fetch_fred_releases(target_date))

    # Earnings
    all_events.extend(fetch_earnings_events(cfg.earnings_watchlist, target_date))

    # Deduplicate by title + date
    seen = set()
    deduped = []
    for e in all_events:
        key = (e["event_date"], e["title"][:50])
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    # Mark past events
    for e in deduped:
        try:
            event_dt_str = f"{e['event_date']} {e.get('event_time_ct', '12:00 PM CT')}"
            # Simple check: if date is before today it's past
            event_date = datetime.date.fromisoformat(e["event_date"])
            e["is_past"] = event_date < target_date or (
                event_date == target_date and
                e.get("event_time_ct", "11:59 PM CT").startswith(("7:30", "8:00", "9:00", "10:00")) and
                now_ct.hour >= 12
            )
        except Exception:
            e["is_past"] = False

        # Add an "id" for frontend keying
        import hashlib
        e["id"] = int(hashlib.md5(f"{e['event_date']}{e['title']}".encode()).hexdigest()[:8], 16)

    # Sort by date then time
    def sort_key(e):
        try:
            d = e["event_date"]
            t = e.get("event_time_ct", "12:00 PM CT").replace(" CT", "")
            return f"{d} {t}"
        except Exception:
            return e["event_date"]

    deduped.sort(key=sort_key)
    return deduped
