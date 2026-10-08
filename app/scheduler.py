"""Penjadwal: posting terjadwal, draf harian otomatis, dan perpanjangan token Threads."""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, db, generator, publishers

log = logging.getLogger("scheduler")
TZ = ZoneInfo(config.TIMEZONE)
_sched: BackgroundScheduler | None = None


def next_free_slot(after: datetime | None = None) -> datetime:
    """Slot jam posting berikutnya (POST_TIMES) yang belum terisi. Hasil dalam UTC."""
    after = after or datetime.now(timezone.utc)
    taken = db.scheduled_times()
    local = after.astimezone(TZ)
    for day in range(0, 60):
        d = (local + timedelta(days=day)).date()
        for t in sorted(config.POST_TIMES):
            hh, mm = (int(x) for x in t.split(":"))
            slot = datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ).astimezone(timezone.utc)
            if slot > after and slot.replace(microsecond=0).isoformat() not in taken:
                return slot
    return after + timedelta(hours=1)


def run_due_posts():
    now = db.now_utc()
    for post in db.due_posts(now):
        log.info("Memposting post #%s", post["id"])
        publishers.publish_post(post["id"])


def auto_generate():
    for _ in range(config.AUTO_GENERATE_COUNT):
        try:
            pid = generator.create_post(pillar=generator.next_pillar())
            log.info("Draf otomatis dibuat: #%s", pid)
        except Exception:
            log.exception("Gagal membuat draf otomatis")


def start():
    global _sched
    if _sched:
        return _sched
    _sched = BackgroundScheduler(timezone=TZ)
    _sched.add_job(run_due_posts, "interval", minutes=1, id="due_posts", max_instances=1, coalesce=True)
    if config.AUTO_GENERATE_DAILY:
        hh, mm = (int(x) for x in config.AUTO_GENERATE_TIME.split(":"))
        _sched.add_job(auto_generate, "cron", hour=hh, minute=mm, id="auto_generate", max_instances=1)
    _sched.add_job(publishers.refresh_threads_token, "interval", days=7, id="threads_refresh")
    _sched.start()
    log.info("Penjadwal berjalan (zona waktu %s)", config.TIMEZONE)
    return _sched


def stop():
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None
