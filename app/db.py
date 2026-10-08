"""Database SQLite sederhana."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    pillar TEXT,
    topic TEXT,
    headline TEXT,
    image_prompt TEXT,
    image_file TEXT,
    fb_caption TEXT,
    ig_caption TEXT,
    threads_text TEXT,
    platforms TEXT DEFAULT 'facebook,instagram,threads',
    status TEXT NOT NULL DEFAULT 'draft',      -- draft | approved | publishing | published | partial | failed | rejected
    scheduled_at TEXT,                          -- UTC ISO
    published_at TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    ok INTEGER NOT NULL,
    remote_id TEXT,
    message TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        # WAL: penjadwal dan dashboard bisa membaca/menulis bersamaan tanpa saling mengunci
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def recover_stuck_publishing() -> int:
    """Post yang tertinggal di status 'publishing' (server mati saat memposting) dijadikan 'failed'
    agar bisa dicoba lagi dari dashboard. Platform yang sudah sukses tidak akan diulang."""
    with conn() as c:
        cur = c.execute(
            "UPDATE posts SET status = 'failed', "
            "error = 'Proses posting terhenti (server restart). Cek riwayat, lalu klik Coba posting lagi.' "
            "WHERE status = 'publishing'"
        )
        return cur.rowcount


def insert_post(**fields) -> int:
    fields.setdefault("created_at", now_utc())
    keys = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with conn() as c:
        cur = c.execute(f"INSERT INTO posts ({keys}) VALUES ({marks})", tuple(fields.values()))
        return cur.lastrowid


def update_post(post_id: int, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE posts SET {sets} WHERE id = ?", (*fields.values(), post_id))


def get_post(post_id: int):
    with conn() as c:
        return c.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


def list_posts(status: str | None = None, limit: int = 200):
    with conn() as c:
        if status:
            return c.execute(
                "SELECT * FROM posts WHERE status = ? ORDER BY COALESCE(scheduled_at, created_at) DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return c.execute(
            "SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def due_posts(now_iso: str):
    with conn() as c:
        return c.execute(
            "SELECT * FROM posts WHERE status = 'approved' AND scheduled_at <= ? ORDER BY scheduled_at",
            (now_iso,),
        ).fetchall()


def scheduled_times() -> set[str]:
    with conn() as c:
        rows = c.execute(
            "SELECT scheduled_at FROM posts WHERE status IN ('approved','publishing') AND scheduled_at IS NOT NULL"
        ).fetchall()
    return {r["scheduled_at"] for r in rows}


def add_result(post_id: int, platform: str, ok: bool, remote_id: str | None, message: str):
    with conn() as c:
        c.execute(
            "INSERT INTO results (post_id, platform, ok, remote_id, message, at) VALUES (?,?,?,?,?,?)",
            (post_id, platform, 1 if ok else 0, remote_id, message[:2000], now_utc()),
        )


def get_results(post_id: int):
    with conn() as c:
        return c.execute(
            "SELECT * FROM results WHERE post_id = ? ORDER BY id DESC", (post_id,)
        ).fetchall()


def count_by_status() -> dict:
    with conn() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM posts GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def get_setting(key: str, default: str | None = None) -> str | None:
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with conn() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def referenced_images() -> set[str]:
    with conn() as c:
        rows = c.execute("SELECT image_file FROM posts WHERE image_file IS NOT NULL").fetchall()
    return {r["image_file"] for r in rows}


def old_rejected_posts(before_iso: str):
    """Post ditolak yang dibuat sebelum waktu tertentu dan masih punya gambar."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM posts WHERE status = 'rejected' AND image_file IS NOT NULL AND created_at < ?",
            (before_iso,),
        ).fetchall()
