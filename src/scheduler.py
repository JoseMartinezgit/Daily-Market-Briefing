"""
Optional scheduler: auto-generates the daily report at a configured time.
Usage: python run.py --schedule "08:00"
"""
import logging
import datetime
import time
from src.config import cfg

logger = logging.getLogger(__name__)


def run_scheduler(schedule_time: str, use_llm: bool = False):
    """
    Block and wait until the scheduled time each day, then generate the report.
    schedule_time: "HH:MM" in CT.
    """
    try:
        hour, minute = [int(x) for x in schedule_time.split(":")]
    except ValueError:
        logger.error("Invalid schedule time: %s — use HH:MM format", schedule_time)
        return

    logger.info("Scheduler active — will generate report daily at %02d:%02d CT", hour, minute)
    print(f"[Scheduler] Report will auto-generate at {schedule_time} CT each day. Ctrl+C to stop.")

    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info("Next report in %.0f minutes", wait_seconds / 60)
        print(f"[Scheduler] Next report in {wait_seconds / 60:.0f} min at {target.strftime('%Y-%m-%d %H:%M CT')}")

        try:
            time.sleep(wait_seconds)
        except KeyboardInterrupt:
            print("\n[Scheduler] Stopped.")
            return

        # Generate report
        try:
            from src.report import generate_report
            print(f"[Scheduler] Generating report at {datetime.datetime.now().strftime('%H:%M CT')}")
            report = generate_report(use_llm=use_llm, save=True)
            print(f"[Scheduler] Report saved. Length: {len(report)} chars.")
        except Exception as exc:
            logger.error("Scheduled report failed: %s", exc)
