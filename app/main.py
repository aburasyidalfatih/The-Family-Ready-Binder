"""Dashboard web: buat draf, review, setujui, dan jadwalkan posting."""
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
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
    db.init()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Auto Post", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=config.BASE_DIR / "app" / "templates")
security = HTTPBasic()


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
templates.env.globals.update(config=config, PLATFORMS=PLATFORMS)


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


# ---------- pekerjaan latar ----------
def _job_generate(kind: str, **kw):
    with _jobs_lock:
        _jobs["running"] += 1
    try:
        if kind == "ideas":
            for _ in range(kw["count"]):
                pillar = kw["pillar"] or generator.next_pillar()
                generator.create_post(pillar=pillar, topic=kw.get("topic"))
        elif kind == "prompts":
            for p in kw["prompts"]:
                generator.create_post(pillar=kw["pillar"] or None, image_prompt=p)
        _jobs["last_error"] = None
    except Exception as e:
        log.exception("Generate gagal")
        _jobs["last_error"] = str(e)
    finally:
        with _jobs_lock:
            _jobs["running"] -= 1


def _job_regen_image(post_id: int):
    _jobs["busy_posts"].add(post_id)
    try:
        post = db.get_post(post_id)
        fname = generator.generate_image(post["image_prompt"], label=post["headline"] or "")
        db.update_post(post_id, image_file=fname, error=None)
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
            threads_text=c.get("threads_text", "")[:500],
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
        threads_text=(form.get("threads_text", "") or "")[:500],
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
            local = datetime.strptime(form["scheduled_at"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
            when = utc_iso(local)
        else:
            when = utc_iso(scheduler.next_free_slot())
        db.update_post(post_id, status="approved", scheduled_at=when, error=None)
        return redirect("/?status=draft")
    elif action == "post_now":
        if not db.get_post(post_id)["image_file"]:
            db.update_post(post_id, error="Belum ada gambar")
            return redirect(f"/post/{post_id}")
        db.update_post(post_id, status="publishing", scheduled_at=utc_iso(datetime.now(timezone.utc)))
        bg.add_task(_job_publish, post_id)
    elif action == "retry":
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
