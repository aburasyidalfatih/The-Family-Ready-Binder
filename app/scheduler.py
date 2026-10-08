"""Penjadwal: posting terjadwal, draf harian otomatis, perpanjangan token Threads, bersih-bersih gambar."""
import logging
import time
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
    now = datetime.now(timezone.utc)
    too_late = now - timedelta(hours=config.MAX_LATE_HOURS)
    for post in db.due_posts(db.now_utc()):
        try:
            when = datetime.fromisoformat(post["scheduled_at"])
            if config.MAX_LATE_HOURS > 0 and when < too_late:
                # mis. server mati berhari-hari: jangan posting konten basi sekaligus
                log.warning("Post #%s terlewat jadwal (%s), tidak diposting otomatis", post["id"], when)
                db.update_post(
                    post["id"],
                    status="failed",
                    error=(
                        f"Jadwal terlewat lebih dari {config.MAX_LATE_HOURS:g} jam "
                        f"({when.astimezone(TZ):%d %b %Y %H:%M}), jadi tidak diposting otomatis. "
                        "Setujui ulang dengan jadwal baru, atau klik Coba posting lagi."
                    ),
                )
                continue
            log.info("Memposting post #%s", post["id"])
            publishers.publish_post(post["id"])
        except Exception:
            log.exception("Gagal memproses post #%s", post["id"])


def auto_generate():
    for _ in range(config.AUTO_GENERATE_COUNT):
        try:
            pid = generator.create_post(pillar=generator.next_pillar())
            log.info("Draf otomatis dibuat: #%s", pid)
        except Exception:
            log.exception("Gagal membuat draf otomatis")


def cleanup_media():
    """Hapus gambar milik post ditolak yang sudah lama, dan file gambar yang tidak dipakai post mana pun."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.MEDIA_RETENTION_DAYS)
    removed = 0
    for post in db.old_rejected_posts(cutoff.replace(microsecond=0).isoformat()):
        (config.MEDIA_DIR / post["image_file"]).unlink(missing_ok=True)
        db.update_post(post["id"], image_file=None)
        removed += 1
    used = db.referenced_images()
    # file yatim dibiarkan 1 hari dulu supaya tidak menghapus gambar yang sedang dibuat
    orphan_before = time.time() - 86400
    for f in config.MEDIA_DIR.glob("*.jpg"):
        if f.name not in used and f.stat().st_mtime < orphan_before:
            f.unlink(missing_ok=True)
            removed += 1
    if removed:
        log.info("Membersihkan %s file gambar", removed)
    return removed


def start():
    global _sched
    if _sched:
        return _sched
    _sched = BackgroundScheduler(timezone=TZ)
    _sched.add_job(run_due_posts, "interval", minutes=1, id="due_posts", max_instances=1, coalesce=True)
    if config.AUTO_GENERATE_DAILY:
        hh, mm = (int(x) for x in config.AUTO_GENERATE_TIME.split(":"))
        _sched.add_job(auto_generate, "cron", hour=hh, minute=mm, id="auto_generate", max_instances=1)
    # dicek tiap hari (dan sekali saat start); perpanjangan sebenarnya hanya tiap 7 hari, lihat publishers
    _sched.add_job(publishers.refresh_threads_token, "interval", days=1, id="threads_refresh",
                   next_run_time=datetime.now(TZ) + timedelta(minutes=1))
    _sched.add_job(cleanup_media, "cron", hour=3, minute=17, id="cleanup_media", max_instances=1)
    _sched.start()
    log.info("Penjadwal berjalan (zona waktu %s)", config.TIMEZONE)
    return _sched


def stop():
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None
