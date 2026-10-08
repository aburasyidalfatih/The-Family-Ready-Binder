"""Dashboard web: buat draf, review, setujui, dan jadwalkan posting."""
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, connect, db, generator, publishers, scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
TZ = ZoneInfo(config.TIMEZONE)
PLATFORMS = ["facebook", "instagram", "threads"]

# pekerjaan generate yang sedang berjalan (untuk ditampilkan di dashboard)
_jobs_lock = threading.Lock()
_jobs = {"running": 0, "busy_posts": set(), "last_error": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    db.init()
    stuck = db.recover_stuck_publishing()
    if stuck:
        log.warning("%s post tertinggal di status 'publishing', diubah jadi 'failed'", stuck)
    for w in config.warnings():
        log.warning(w)
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Auto Post", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=config.BASE_DIR / "app" / "templates")
security = HTTPBasic()


def _same_origin(source: str, request: Request) -> bool:
    netloc = urlparse(source).netloc
    allowed = {request.headers.get("host", ""), urlparse(config.PUBLIC_BASE_URL).netloc}
    return bool(netloc) and netloc in allowed


@app.middleware("http")
async def block_cross_site_posts(request: Request, call_next):
    """Perlindungan CSRF: browser otomatis mengirim login Basic Auth, jadi form POST dari situs lain
    harus ditolak. Browser selalu mengirim Origin/Referer untuk POST lintas situs."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        source = request.headers.get("origin") or request.headers.get("referer")
        if source and not _same_origin(source, request):
            log.warning("POST lintas situs ditolak: %s -> %s", source, request.url.path)
            return PlainTextResponse("Permintaan ditolak: asal permintaan tidak cocok.", status_code=403)
    return await call_next(request)


def auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username.encode(), config.DASHBOARD_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), config.DASHBOARD_PASSWORD.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic"})


# ---------- helper tampilan ----------
def local_time(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d %b %Y %H:%M")


def local_input(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y-%m-%dT%H:%M")


templates.env.filters["local_time"] = local_time
templates.env.filters["local_input"] = local_input
templates.env.globals.update(config=config, PLATFORMS=PLATFORMS, config_warnings=config.warnings)


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


# ---------- pekerjaan latar ----------
def _job_generate(kind: str, **kw):
    with _jobs_lock:
        _jobs["running"] += 1
    if kind == "ideas":
        tasks = [lambda: generator.create_post(pillar=kw["pillar"] or generator.next_pillar(), topic=kw.get("topic"))
                 for _ in range(kw["count"])]
    else:
        tasks = [lambda p=p: generator.create_post(pillar=kw["pillar"] or None, image_prompt=p)
                 for p in kw["prompts"]]
    errors = []
    try:
        # satu konten gagal tidak menghentikan konten berikutnya
        for task in tasks:
            try:
                task()
            except Exception as e:
                log.exception("Generate gagal")
                errors.append(str(e))
        _jobs["last_error"] = (
            f"{len(errors)} dari {len(tasks)} konten gagal dibuat. Terakhir: {errors[-1]}" if errors else None
        )
    finally:
        with _jobs_lock:
            _jobs["running"] -= 1


def _job_regen_image(post_id: int):
    _jobs["busy_posts"].add(post_id)
    try:
        post = db.get_post(post_id)
        fname = generator.generate_image(post["image_prompt"], label=post["headline"] or "")
        db.update_post(post_id, image_file=fname, error=None)
        # hapus gambar lama agar folder media tidak terus membengkak
        if post["image_file"] and post["image_file"] != fname:
            (config.MEDIA_DIR / post["image_file"]).unlink(missing_ok=True)
    except Exception as e:
        log.exception("Regenerate gambar gagal")
        db.update_post(post_id, error=f"Gagal membuat gambar: {e}")
    finally:
        _jobs["busy_posts"].discard(post_id)


def _job_regen_captions(post_id: int):
    _jobs["busy_posts"].add(post_id)
    try:
        post = db.get_post(post_id)
        c = generator.captions_for_prompt(post["image_prompt"])
        db.update_post(
            post_id,
            fb_caption=c.get("fb_caption", ""),
            ig_caption=c.get("ig_caption", ""),
            threads_text=publishers.clip(c.get("threads_text", "")),
            error=None,
        )
    except Exception as e:
        log.exception("Regenerate caption gagal")
        db.update_post(post_id, error=f"Gagal membuat caption: {e}")
    finally:
        _jobs["busy_posts"].discard(post_id)


def _job_publish(post_id: int):
    _jobs["busy_posts"].add(post_id)
    try:
        publishers.publish_post(post_id)
    finally:
        _jobs["busy_posts"].discard(post_id)


# ---------- rute publik ----------
@app.get("/media/{filename}")
def media(filename: str):
    path = (config.MEDIA_DIR / filename).resolve()
    if path.parent != config.MEDIA_DIR.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- dashboard ----------
TABS = {
    "draft": "Menunggu review",
    "approved": "Terjadwal",
    "published": "Terposting",
    "publishing": "Sedang diposting",
    "partial": "Sebagian gagal",
    "failed": "Gagal",
    "rejected": "Ditolak",
}


@app.get("/", dependencies=[Depends(auth)])
def index(request: Request, status: str = "draft"):
    if status not in TABS:
        status = "draft"
    posts = db.list_posts(status)
    if status == "approved":
        posts = sorted(posts, key=lambda p: p["scheduled_at"] or "")
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "posts": posts,
            "status": status,
            "tabs": TABS,
            "counts": db.count_by_status(),
            "jobs": _jobs,
            "dry_run": config.DRY_RUN,
            "fake_ai": config.FAKE_AI,
        },
    )


@app.get("/new", dependencies=[Depends(auth)])
def new_form(request: Request):
    return templates.TemplateResponse(request, "new.html", {"pillars": config.PILLARS})


@app.post("/new/ideas", dependencies=[Depends(auth)])
def new_ideas(bg: BackgroundTasks, pillar: str = Form(""), topic: str = Form(""), count: int = Form(1)):
    count = max(1, min(count, 10))
    bg.add_task(_job_generate, "ideas", pillar=pillar or None, topic=topic.strip() or None, count=count)
    return redirect("/?status=draft&generating=1")


@app.post("/new/prompts", dependencies=[Depends(auth)])
def new_prompts(bg: BackgroundTasks, prompts: str = Form(...), pillar: str = Form("")):
    # beberapa prompt dipisahkan dengan baris berisi ===
    items = [p.strip() for p in prompts.split("\n===") if p.strip()]
    items = [p.lstrip("=").strip() for p in items][:30]
    if not items:
        return redirect("/new")
    bg.add_task(_job_generate, "prompts", prompts=items, pillar=pillar or None)
    return redirect("/?status=draft&generating=1")


@app.get("/post/{post_id}", dependencies=[Depends(auth)])
def post_detail(request: Request, post_id: int):
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "post.html",
        {
            "post": post,
            "results": db.get_results(post_id),
            "busy": post_id in _jobs["busy_posts"],
            "next_slot": local_input(utc_iso(scheduler.next_free_slot())),
            "pillars": config.PILLARS,
        },
    )


def _save_fields(post_id: int, form: dict):
    platforms = [p for p in PLATFORMS if form.get(f"pf_{p}")]
    db.update_post(
        post_id,
        fb_caption=form.get("fb_caption", ""),
        ig_caption=form.get("ig_caption", ""),
        threads_text=publishers.clip(form.get("threads_text", "")),
        image_prompt=form.get("image_prompt", ""),
        platforms=",".join(platforms),
    )


@app.post("/post/{post_id}/action", dependencies=[Depends(auth)])
async def post_action(request: Request, post_id: int, bg: BackgroundTasks):
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(404)
    form = dict(await request.form())
    action = form.get("action")
    if post["status"] in ("draft", "approved", "failed", "partial", "rejected"):
        _save_fields(post_id, form)

    if action == "save":
        pass
    elif action == "regen_image":
        bg.add_task(_job_regen_image, post_id)
    elif action == "regen_captions":
        bg.add_task(_job_regen_captions, post_id)
    elif action in ("approve_next", "approve_at"):
        if not db.get_post(post_id)["image_file"]:
            db.update_post(post_id, error="Belum ada gambar")
            return redirect(f"/post/{post_id}")
        if action == "approve_at" and form.get("scheduled_at"):
            try:
                local = datetime.strptime(form["scheduled_at"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
            except ValueError:
                db.update_post(post_id, error="Format waktu tidak valid")
                return redirect(f"/post/{post_id}")
            if local < datetime.now(timezone.utc) - timedelta(minutes=5):
                db.update_post(post_id, error="Waktu yang dipilih sudah lewat. Pilih waktu yang akan datang.")
                return redirect(f"/post/{post_id}")
            when = utc_iso(local)
        else:
            when = utc_iso(scheduler.next_free_slot())
        db.update_post(post_id, status="approved", scheduled_at=when, error=None)
        return redirect("/?status=draft")
    elif action == "post_now":
        if post["status"] in ("publishing", "published") or post_id in _jobs["busy_posts"]:
            return redirect(f"/post/{post_id}")
        if not db.get_post(post_id)["image_file"]:
            db.update_post(post_id, error="Belum ada gambar")
            return redirect(f"/post/{post_id}")
        db.update_post(post_id, status="publishing", scheduled_at=utc_iso(datetime.now(timezone.utc)))
        bg.add_task(_job_publish, post_id)
    elif action == "retry":
        # cegah posting ganda bila tombol diklik dua kali atau post sedang diproses
        if post["status"] not in ("failed", "partial") or post_id in _jobs["busy_posts"]:
            return redirect(f"/post/{post_id}")
        db.update_post(post_id, status="publishing")
        bg.add_task(_job_publish, post_id)
    elif action == "unschedule":
        db.update_post(post_id, status="draft", scheduled_at=None)
    elif action == "reject":
        db.update_post(post_id, status="rejected", scheduled_at=None)
        return redirect("/?status=draft")
    elif action == "restore":
        db.update_post(post_id, status="draft")
    return redirect(f"/post/{post_id}")


@app.post("/run-auto-generate", dependencies=[Depends(auth)])
def run_auto_generate(bg: BackgroundTasks):
    bg.add_task(_job_generate, "ideas", pillar=None, topic=None, count=config.AUTO_GENERATE_COUNT)
    return redirect("/?status=draft&generating=1")


# ---------- pengaturan & koneksi akun ----------
@app.get("/settings", dependencies=[Depends(auth)])
def settings_page(request: Request, msg: str = "", err: str = "", test: int = 0):
    import json
    pages = json.loads(db.get_setting("pending_pages", "[]") or "[]")
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "msg": msg,
            "err": err,
            "pages": pages,
            "status": connect.test_connections() if test else None,
            "threads_redirect": connect.THREADS_REDIRECT,
            "has_fb": bool(publishers.fb_page_id() and publishers.fb_token()),
            "has_ig": bool(publishers.ig_user_id()),
            "has_threads": bool(publishers.threads_user_id() and publishers.threads_token()),
            "threads_refreshed_at": db.get_setting("threads_token_refreshed_at"),
            "threads_refresh_error": db.get_setting("threads_token_refresh_error"),
        },
    )


@app.post("/settings/meta-token", dependencies=[Depends(auth)])
def settings_meta_token(user_token: str = Form(...)):
    from urllib.parse import quote
    try:
        pages = connect.meta_fetch_pages(user_token)
    except Exception as e:
        return redirect("/settings?err=" + quote(str(e)))
    if not pages:
        return redirect("/settings?err=" + quote("Tidak ada Page. Pastikan izin pages_show_list diberikan."))
    return redirect("/settings?msg=" + quote("Pilih Page di bawah."))


@app.post("/settings/meta-page", dependencies=[Depends(auth)])
def settings_meta_page(page_id: str = Form(...)):
    from urllib.parse import quote
    try:
        page = connect.meta_select_page(page_id)
    except Exception as e:
        return redirect("/settings?err=" + quote(str(e)))
    note = f"Terhubung ke Page {page['name']}"
    note += f" dan Instagram @{page['ig_username']}" if page["ig_id"] else " (Instagram belum terhubung ke Page ini)"
    return redirect("/settings?test=1&msg=" + quote(note))


@app.get("/threads/connect", dependencies=[Depends(auth)])
def threads_connect():
    from urllib.parse import quote
    try:
        return redirect(connect.threads_authorize_url())
    except Exception as e:
        return redirect("/settings?err=" + quote(str(e)))


@app.get("/threads/callback", dependencies=[Depends(auth)])
def threads_callback(code: str = "", state: str = "", error_description: str = ""):
    from urllib.parse import quote
    if not code:
        return redirect("/settings?err=" + quote(error_description or "Otorisasi Threads dibatalkan"))
    try:
        connect.threads_handle_callback(code, state)
    except Exception as e:
        return redirect("/settings?err=" + quote(str(e)))
    return redirect("/settings?test=1&msg=" + quote("Threads terhubung"))
