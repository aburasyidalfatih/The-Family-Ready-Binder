"""Memposting ke Facebook Page, Instagram, dan Threads lewat API resmi Meta."""
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import config, db

log = logging.getLogger("publishers")
GRAPH = f"https://graph.facebook.com/{config.GRAPH_VERSION}"
THREADS = "https://graph.threads.net/v1.0"
TIMEOUT = httpx.Timeout(60.0)


THREADS_MAX_CHARS = 500
THREADS_REFRESH_EVERY = timedelta(days=7)


class PublishError(Exception):
    pass


def clip(text: str | None, limit: int = THREADS_MAX_CHARS) -> str:
    """Potong teks agar muat batas karakter, di batas kata terakhir (bukan di tengah kata)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def _check(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        raise PublishError(f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise PublishError(f"{err.get('message', data)} (code {err.get('code', r.status_code)})")
    return data


# Nilai dari halaman Pengaturan (database) diutamakan, lalu dari .env
def fb_page_id() -> str:
    return db.get_setting("fb_page_id") or config.FB_PAGE_ID


def fb_token() -> str:
    return db.get_setting("fb_page_token") or config.FB_PAGE_ACCESS_TOKEN


def ig_user_id() -> str:
    return db.get_setting("ig_user_id") or config.IG_USER_ID


def threads_user_id() -> str:
    return db.get_setting("threads_user_id") or config.THREADS_USER_ID


def threads_token() -> str:
    return db.get_setting("threads_token") or config.THREADS_ACCESS_TOKEN


def image_url(post) -> str:
    return f"{config.PUBLIC_BASE_URL}/media/{post['image_file']}"


# ---------- Facebook Page ----------
def publish_facebook(post) -> str:
    if not (fb_page_id() and fb_token()):
        raise PublishError("FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN belum diisi")
    r = httpx.post(
        f"{GRAPH}/{fb_page_id()}/photos",
        data={
            "url": image_url(post),
            "message": post["fb_caption"] or "",
            "access_token": fb_token(),
        },
        timeout=TIMEOUT,
    )
    data = _check(r)
    return data.get("post_id") or data.get("id")


# ---------- Instagram ----------
def _wait_ig_container(container_id: str, tries: int = 20):
    for _ in range(tries):
        r = httpx.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code,status", "access_token": fb_token()},
            timeout=TIMEOUT,
        )
        data = _check(r)
        code = data.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise PublishError(f"Container Instagram gagal: {data.get('status')}")
        time.sleep(3)
    raise PublishError("Container Instagram tidak selesai diproses")


def publish_instagram(post) -> str:
    if not (ig_user_id() and fb_token()):
        raise PublishError("IG_USER_ID / FB_PAGE_ACCESS_TOKEN belum diisi")
    r = httpx.post(
        f"{GRAPH}/{ig_user_id()}/media",
        data={
            "image_url": image_url(post),
            "caption": post["ig_caption"] or "",
            "access_token": fb_token(),
        },
        timeout=TIMEOUT,
    )
    container_id = _check(r)["id"]
    _wait_ig_container(container_id)
    r = httpx.post(
        f"{GRAPH}/{ig_user_id()}/media_publish",
        data={"creation_id": container_id, "access_token": fb_token()},
        timeout=TIMEOUT,
    )
    return _check(r)["id"]


# ---------- Threads ----------


def _wait_threads_container(container_id: str, token: str, tries: int = 20):
    for _ in range(tries):
        r = httpx.get(
            f"{THREADS}/{container_id}",
            params={"fields": "status,error_message", "access_token": token},
            timeout=TIMEOUT,
        )
        data = _check(r)
        status = data.get("status")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"Container Threads gagal: {data.get('error_message')}")
        time.sleep(3)
    raise PublishError("Container Threads tidak selesai diproses")


def publish_threads(post) -> str:
    token = threads_token()
    if not (threads_user_id() and token):
        raise PublishError("THREADS_USER_ID / THREADS_ACCESS_TOKEN belum diisi")
    r = httpx.post(
        f"{THREADS}/{threads_user_id()}/threads",
        data={
            "media_type": "IMAGE",
            "image_url": image_url(post),
            "text": clip(post["threads_text"]),
            "access_token": token,
        },
        timeout=TIMEOUT,
    )
    container_id = _check(r)["id"]
    _wait_threads_container(container_id, token)
    r = httpx.post(
        f"{THREADS}/{threads_user_id()}/threads_publish",
        data={"creation_id": container_id, "access_token": token},
        timeout=TIMEOUT,
    )
    return _check(r)["id"]


def refresh_threads_token(force: bool = False) -> bool:
    """Token Threads berlaku 60 hari. Dicek tiap hari dan diperpanjang bila sudah >= 7 hari
    sejak perpanjangan terakhir. Tanggal disimpan di database agar tidak ter-reset saat redeploy."""
    token = threads_token()
    if not token or config.DRY_RUN:
        return False
    last = db.get_setting("threads_token_refreshed_at")
    now = datetime.now(timezone.utc)
    if not force and last and datetime.fromisoformat(last) > now - THREADS_REFRESH_EVERY:
        return False
    try:
        r = httpx.get(
            "https://graph.threads.net/refresh_access_token",
            params={"grant_type": "th_refresh_token", "access_token": token},
            timeout=TIMEOUT,
        )
        data = _check(r)
    except (PublishError, httpx.HTTPError) as e:
        log.error("Gagal memperpanjang token Threads: %s", e)
        db.set_setting("threads_token_refresh_error", f"{db.now_utc()}: {e}")
        return False
    db.set_setting("threads_token", data["access_token"])
    db.set_setting("threads_token_refreshed_at", db.now_utc())
    db.set_setting("threads_token_refresh_error", "")
    log.info("Token Threads diperpanjang")
    return True


PUBLISHERS = {
    "facebook": publish_facebook,
    "instagram": publish_instagram,
    "threads": publish_threads,
}


def publish_post(post_id: int) -> str:
    """Posting ke semua platform yang dipilih. Platform yang sudah sukses tidak diulang."""
    post = db.get_post(post_id)
    if not post or not post["image_file"]:
        db.update_post(post_id, status="failed", error="Tidak ada gambar")
        return "failed"
    db.update_post(post_id, status="publishing")
    already_ok = {r["platform"] for r in db.get_results(post_id) if r["ok"]}
    platforms = [p for p in (post["platforms"] or "").split(",") if p in PUBLISHERS]
    errors = []
    ok_count = len([p for p in platforms if p in already_ok])
    for p in platforms:
        if p in already_ok:
            continue
        try:
            if config.DRY_RUN:
                remote_id = f"dryrun-{p}-{post_id}"
                log.info("[DRY_RUN] %s <- post %s (%s)", p, post_id, image_url(post))
            else:
                remote_id = PUBLISHERS[p](post)
            db.add_result(post_id, p, True, remote_id, "OK")
            ok_count += 1
        except Exception as e:
            log.exception("Gagal posting ke %s", p)
            db.add_result(post_id, p, False, None, str(e))
            errors.append(f"{p}: {e}")
    if not errors:
        status = "published"
    elif ok_count:
        status = "partial"
    else:
        status = "failed"
    db.update_post(
        post_id,
        status=status,
        published_at=db.now_utc() if ok_count else None,
        error="; ".join(errors) or None,
    )
    return status
