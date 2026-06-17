#!/usr/bin/env python3
"""
Daily Market Briefing — entry point.

Usage:
    python run.py                    # Launch UI at http://localhost:8000
    python run.py --port 8080        # Custom port
    python run.py --report           # Print markdown report, no UI
    python run.py --report --llm     # Use Anthropic API for better sentiment
    python run.py --schedule 08:00   # Auto-generate report at 8 AM CT daily
    python run.py --schedule 08:00 --llm  # Scheduled with LLM mode
"""
import argparse
import logging
import sys
import os

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(
        description="Daily Market Briefing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--report", action="store_true",
                        help="Generate markdown report without launching the UI")
    parser.add_argument("--llm", action="store_true",
                        help="Use Anthropic API for higher-quality sentiment analysis")
    parser.add_argument("--schedule", metavar="HH:MM",
                        help="Auto-generate report at this time (CT) each day; implies --report mode")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port for the web server (default: 8000)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind host (default: 0.0.0.0 for LAN access)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not auto-open the browser on startup")
    args = parser.parse_args()

    if args.schedule:
        # Scheduler mode — generate report on a daily schedule
        from src.scheduler import run_scheduler
        run_scheduler(args.schedule, use_llm=args.llm)
        return

    if args.report:
        # Report-only mode — print markdown to stdout and save to /reports/
        import datetime
        from src.database import init_db
        from src.config import cfg

        cfg.DB_DIR.mkdir(exist_ok=True)
        cfg.REPORTS_DIR.mkdir(exist_ok=True)
        init_db()

        print(f"[Report] Fetching data{' (LLM mode)' if args.llm else ''}…")
        from src.main import _refresh_all_data
        from src.report import generate_report

        data = _refresh_all_data(use_llm=args.llm)
        report = generate_report(dashboard_data=data, use_llm=args.llm, save=True)

        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        report_path = cfg.REPORTS_DIR / f"{date_str}.md"
        print(f"\n{'='*70}")
        print(report)
        print(f"{'='*70}")
        print(f"\n[Report] Saved to: {report_path}")
        return

    # --- Default: web server mode ---
    import threading
    import time
    import webbrowser
    import uvicorn

    url = f"http://localhost:{args.port}"

    if not args.no_browser:
        def _open():
            time.sleep(1.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"""
╔══════════════════════════════════════════════════════╗
║          Daily Market Briefing  v1.0                 ║
║  Open: {url:<44}║
║  LAN:  http://YOUR_LAN_IP:{args.port:<27}║
║  Press Ctrl+C to stop                                ║
╚══════════════════════════════════════════════════════╝
""")

    uvicorn.run(
        "src.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
