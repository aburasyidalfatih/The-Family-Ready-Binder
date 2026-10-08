"""Bantuan menghubungkan akun: Facebook Page + Instagram, dan Threads (OAuth)."""
import json
import secrets
from urllib.parse import urlencode

import httpx

from . import config, db
from .publishers import (GRAPH, THREADS, PublishError, _check, fb_page_id, fb_token, ig_user_id,
                         threads_token, threads_user_id)

THREADS_REDIRECT = f"{config.PUBLIC_BASE_URL}/threads/callback"
TIMEOUT = httpx.Timeout(30.0)


# ---------- Facebook Page + Instagram ----------
def meta_fetch_pages(user_token: str) -> list[dict]:
    """Tukar token user (dari Graph API Explorer) jadi long-lived, lalu ambil daftar Page.
    Token Page dari user token long-lived tidak kedaluwarsa."""
    if not (config.META_APP_ID and config.META_APP_SECRET):
        raise PublishError("META_APP_ID / META_APP_SECRET belum diisi di .env")
    r = httpx.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": config.META_APP_ID,
            "client_secret": config.META_APP_SECRET,
            "fb_exchange_token": user_token.strip(),
        },
        timeout=TIMEOUT,
    )
    long_token = _check(r)["access_token"]
    r = httpx.get(
        f"{GRAPH}/me/accounts",
        params={
            "fields": "id,name,access_token,instagram_business_account{id,username}",
            "access_token": long_token,
            "limit": 100,
        },
        timeout=TIMEOUT,
    )
    pages = []
    for p in _check(r).get("data", []):
        ig = p.get("instagram_business_account") or {}
        pages.append({
            "id": p["id"],
            "name": p.get("name", ""),
            "token": p["access_token"],
            "ig_id": ig.get("id", ""),
            "ig_username": ig.get("username", ""),
        })
    db.set_setting("pending_pages", json.dumps(pages))
    return pages


def meta_select_page(page_id: str) -> dict:
    pages = json.loads(db.get_setting("pending_pages", "[]") or "[]")
    page = next((p for p in pages if p["id"] == page_id), None)
    if not page:
        raise PublishError("Page tidak ditemukan. Ulangi langkah tempel token.")
    db.set_setting("fb_page_id", page["id"])
    db.set_setting("fb_page_token", page["token"])
    db.set_setting("ig_user_id", page["ig_id"])
    db.set_setting("pending_pages", "[]")
    return page


# ---------- Threads ----------
def threads_authorize_url() -> str:
    if not config.THREADS_APP_ID:
        raise PublishError("THREADS_APP_ID belum diisi di .env")
    state = secrets.token_urlsafe(16)
    db.set_setting("threads_oauth_state", state)
    return "https://threads.net/oauth/authorize?" + urlencode({
        "client_id": config.THREADS_APP_ID,
        "redirect_uri": THREADS_REDIRECT,
        "scope": "threads_basic,threads_content_publish",
        "response_type": "code",
        "state": state,
    })


def threads_handle_callback(code: str, state: str) -> str:
    if not state or state != db.get_setting("threads_oauth_state"):
        raise PublishError("State OAuth tidak cocok. Ulangi tombol Hubungkan Threads.")
    code = code.split("#")[0]
    r = httpx.post(
        "https://graph.threads.net/oauth/access_token",
        data={
            "client_id": config.THREADS_APP_ID,
            "client_secret": config.THREADS_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": THREADS_REDIRECT,
            "code": code,
        },
        timeout=TIMEOUT,
    )
    data = _check(r)
    short_token, user_id = data["access_token"], str(data["user_id"])
    r = httpx.get(
        "https://graph.threads.net/access_token",
        params={
            "grant_type": "th_exchange_token",
            "client_secret": config.THREADS_APP_SECRET,
            "access_token": short_token,
        },
        timeout=TIMEOUT,
    )
    long_token = _check(r)["access_token"]
    db.set_setting("threads_user_id", user_id)
    db.set_setting("threads_token", long_token)
    return user_id


# ---------- Tes koneksi ----------
def test_connections() -> dict:
    out = {}
    if fb_page_id() and fb_token():
        try:
            d = _check(httpx.get(f"{GRAPH}/{fb_page_id()}", params={"fields": "name", "access_token": fb_token()}, timeout=TIMEOUT))
            out["facebook"] = (True, f"Page: {d.get('name')}")
        except Exception as e:
            out["facebook"] = (False, str(e))
    else:
        out["facebook"] = (False, "Belum terhubung")
    if ig_user_id() and fb_token():
        try:
            d = _check(httpx.get(f"{GRAPH}/{ig_user_id()}", params={"fields": "username", "access_token": fb_token()}, timeout=TIMEOUT))
            out["instagram"] = (True, f"@{d.get('username')}")
        except Exception as e:
            out["instagram"] = (False, str(e))
    else:
        out["instagram"] = (False, "Belum terhubung (pastikan IG Business terhubung ke Page)")
    if threads_user_id() and threads_token():
        try:
            d = _check(httpx.get(f"{THREADS}/me", params={"fields": "username", "access_token": threads_token()}, timeout=TIMEOUT))
            out["threads"] = (True, f"@{d.get('username')}")
        except Exception as e:
            out["threads"] = (False, str(e))
    else:
        out["threads"] = (False, "Belum terhubung")
    return out
