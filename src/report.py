"""
Markdown report generator.
Produces a timestamped daily briefing and saves it to /reports/.
"""
import datetime
import os
import logging
from pathlib import Path
from typing import Optional
from src.config import cfg

logger = logging.getLogger(__name__)


def _verdict_emoji(verdict: str) -> str:
    return {"BULLISH": "🟢", "BEARISH": "🔴", "VOLATILE": "🟡", "MIXED": "⚪"}.get(verdict, "⚪")


def _dir_arrow(val: Optional[float]) -> str:
    if val is None:
        return ""
    return "▲" if val > 0 else ("▼" if val < 0 else "—")


def _fmt_pct(val) -> str:
    try:
        f = float(val)
        sign = "+" if f >= 0 else ""
        return f"{sign}{f:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_price(val) -> str:
    try:
        f = float(val)
        if f >= 1000:
            return f"{f:,.2f}"
        return f"{f:.4f}" if f < 1 else f"{f:.2f}"
    except (TypeError, ValueError):
        return "N/A"


def generate_report(
    dashboard_data: Optional[dict] = None,
    use_llm: bool = False,
    save: bool = True,
) -> str:
    """
    Generate a full markdown report.
    If dashboard_data is None, triggers a fresh data refresh.
    """
    if dashboard_data is None:
        from src.main import _refresh_all_data
        dashboard_data = _refresh_all_data(use_llm=use_llm)

    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%B %d, %Y at %I:%M %p CT")

    verdict_data = dashboard_data.get("verdict", {})
    verdict = verdict_data.get("verdict", "MIXED")
    emoji = _verdict_emoji(verdict)
    confidence = verdict_data.get("confidence", 0)
    justification = verdict_data.get("justification", "")

    snapshot = dashboard_data.get("market_snapshot", [])
    news_items = dashboard_data.get("news", [])
    events = dashboard_data.get("events", [])
    watchlist = dashboard_data.get("watchlist", [])
    sector_heatmap = dashboard_data.get("sector_heatmap", [])
    sources_status = dashboard_data.get("sources_status", {})

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    lines = [
        f"# Daily Market Briefing — {date_str}",
        f"> Generated: {ts_str}",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # Verdict Banner
    # -----------------------------------------------------------------------
    lines += [
        f"## {emoji} Day Verdict: **{verdict}** (Confidence: {confidence}%)",
        "",
        f"> {justification}",
        "",
    ]

    # -----------------------------------------------------------------------
    # Market Snapshot
    # -----------------------------------------------------------------------
    lines += ["## Market Snapshot", ""]

    indices = [s for s in snapshot if s.get("data_type") == "index"]
    commodities = [s for s in snapshot if s.get("data_type") in ("commodity", "yield")]

    if indices:
        lines.append("| Symbol | Name | Price | Change |")
        lines.append("|--------|------|-------|--------|")
        for s in indices:
            arrow = _dir_arrow(s.get("change_pct"))
            lines.append(
                f"| {s['symbol']} | {s['name']} | "
                f"{_fmt_price(s.get('price'))} | "
                f"{arrow} {_fmt_pct(s.get('change_pct'))} |"
            )
        lines.append("")

    if commodities:
        lines.append("| Symbol | Name | Price | Change |")
        lines.append("|--------|------|-------|--------|")
        for s in commodities:
            arrow = _dir_arrow(s.get("change_pct"))
            lines.append(
                f"| {s['symbol']} | {s['name']} | "
                f"{_fmt_price(s.get('price'))} | "
                f"{arrow} {_fmt_pct(s.get('change_pct'))} |"
            )
        lines.append("")

    # -----------------------------------------------------------------------
    # Sector Heatmap
    # -----------------------------------------------------------------------
    if sector_heatmap:
        lines += ["## Sector Heatmap", ""]
        lines.append("| ETF | Sector | Change |")
        lines.append("|-----|--------|--------|")
        for s in sorted(sector_heatmap, key=lambda x: x.get("change_pct", 0), reverse=True):
            arrow = _dir_arrow(s.get("change_pct"))
            lines.append(f"| {s['etf']} | {s['name']} | {arrow} {_fmt_pct(s.get('change_pct'))} |")
        lines.append("")

    # -----------------------------------------------------------------------
    # Today's Watchlist
    # -----------------------------------------------------------------------
    if watchlist:
        lines += ["## Today's Watchlist", ""]
        lines.append("| Ticker | Name | Price | Change | Mentions | Sentiment |")
        lines.append("|--------|------|-------|--------|----------|-----------|")
        for w in watchlist:
            arrow = _dir_arrow(w.get("change_pct"))
            label = w.get("sentiment_label", "neutral").upper()
            lines.append(
                f"| **{w['ticker']}** | {w.get('name', '')} | "
                f"{_fmt_price(w.get('price'))} | "
                f"{arrow} {_fmt_pct(w.get('change_pct'))} | "
                f"{w.get('mentions', 0)} | {label} |"
            )
        lines.append("")

    # -----------------------------------------------------------------------
    # Top 5 Stories
    # -----------------------------------------------------------------------
    high_impact = [n for n in news_items if n.get("impact_level") == "High"][:5]
    if high_impact:
        lines += ["## Top Stories", ""]
        for i, item in enumerate(high_impact, 1):
            sentiment = item.get("sentiment_label", "neutral").upper()
            tickers = ", ".join(
                f"**{t['ticker']}** {('▲' if t.get('direction') == 'up' else '▼' if t.get('direction') == 'down' else '—')}"
                for t in item.get("tickers", [])[:4]
            )
            lines += [
                f"### {i}. {item.get('title', '')}",
                f"*{item.get('source', '')} · {item.get('published_at', '')[:10]}* · "
                f"[Link]({item.get('url', '#')}) · Impact: **{item.get('impact_level', '')}** · {sentiment}",
            ]
            if item.get("summary"):
                lines.append(f"")
                lines.append(item["summary"])
            if tickers:
                lines.append(f"")
                lines.append(f"**Affected:** {tickers}")
            lines.append("")

    # -----------------------------------------------------------------------
    # Full News Feed by Category
    # -----------------------------------------------------------------------
    lines += ["## Full News Feed", ""]

    categories = ["Macro", "Political", "Deals", "Earnings", "Sector", "General"]
    for cat in categories:
        cat_items = [n for n in news_items if n.get("category", "General") == cat]
        if not cat_items:
            continue
        lines += [f"### {cat}", ""]
        for item in cat_items:
            sentiment = item.get("sentiment_label", "neutral")
            bullet = "🟢" if sentiment == "bullish" else ("🔴" if sentiment == "bearish" else "⚪")
            lines.append(
                f"- {bullet} **[{item.get('title', '')}]({item.get('url', '#')})** "
                f"— {item.get('source', '')} · {item.get('impact_level', '')} impact"
            )
            if item.get("summary"):
                lines.append(f"  > {item['summary'][:200]}")
        lines.append("")

    # -----------------------------------------------------------------------
    # Event Timeline
    # -----------------------------------------------------------------------
    if events:
        lines += ["## Event Timeline (This Week)", ""]
        today_str = date_str
        last_date = ""
        for e in events:
            edate = e.get("event_date", "")
            if edate != last_date:
                label = "**TODAY**" if edate == today_str else f"**{edate}**"
                lines.append(f"#### {label}")
                last_date = edate

            past_mark = "✅" if e.get("is_past") else "🔲"
            impact_badge = {"High": "🔴", "Medium": "🟡", "Low": "🔵"}.get(e.get("impact_level", "Low"), "🔵")
            assets = ", ".join(e.get("affected_assets", [])[:4])
            lines.append(
                f"- {past_mark} {impact_badge} **{e.get('event_time_ct', 'TBD')}** — "
                f"{e.get('title', '')} *(affects: {assets})*"
            )
            if e.get("description"):
                lines.append(f"  > {e['description']}")
        lines.append("")

    # -----------------------------------------------------------------------
    # Risks to Watch
    # -----------------------------------------------------------------------
    risks = []

    # VIX spike risk
    vix_val = verdict_data.get("vix", 0)
    if vix_val and vix_val > 20:
        risks.append(f"VIX at {vix_val:.1f} — elevated volatility; any negative surprise could accelerate selling.")

    # Upcoming high-impact events today
    today_events = [e for e in events if e.get("event_date") == date_str and not e.get("is_past")
                    and e.get("impact_level") == "High"]
    for e in today_events[:2]:
        risks.append(f"{e.get('title', '')} at {e.get('event_time_ct', 'TBD')} CT could reverse current direction.")

    # Bearish news concentration
    bearish_count = sum(1 for n in news_items if n.get("sentiment_label") == "bearish")
    if bearish_count > len(news_items) * 0.6:
        risks.append(f"News sentiment heavily bearish ({bearish_count}/{len(news_items)} stories). "
                     "Risk of negative feedback loop if indices break support.")

    if not risks:
        risks.append("No immediate high-probability risks identified from available data.")

    lines += ["## Risks to Watch", ""]
    for risk in risks[:3]:
        lines.append(f"- ⚠️ {risk}")
    lines.append("")

    # -----------------------------------------------------------------------
    # Sources status
    # -----------------------------------------------------------------------
    if sources_status:
        failed = [k for k, v in sources_status.items() if not v]
        if failed:
            lines += [
                "---",
                f"*⚠️ Failed sources: {', '.join(failed)}. Data may be incomplete.*",
                "",
            ]

    # -----------------------------------------------------------------------
    # Disclaimer
    # -----------------------------------------------------------------------
    lines += [
        "---",
        "*This report is for informational purposes only and does not constitute financial advice. "
        "Past sentiment patterns do not predict future market outcomes. "
        "Always conduct your own research before making investment decisions.*",
        "",
    ]

    report_md = "\n".join(lines)

    # Save to /reports/
    if save:
        reports_dir = cfg.REPORTS_DIR
        reports_dir.mkdir(exist_ok=True)
        report_path = reports_dir / f"{date_str}.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_md)
        logger.info("Report saved: %s", report_path)

    return report_md
