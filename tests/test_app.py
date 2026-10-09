from datetime import datetime, timedelta, timezone

import pytest

from app import config, db, publishers, scheduler

from .conftest import AUTH


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def test_dashboard_requires_login(client):
    assert client.get("/").status_code == 401
    assert client.get("/", auth=AUTH).status_code == 200


def test_weak_password_refused(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "ganti-password-ini")
    with pytest.raises(RuntimeError):
        config.validate()


def test_localhost_base_url_warns(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://localhost:8000")
    assert config.warnings()


def test_cross_site_post_blocked(client, post_id):
    r = client.post(f"/post/{post_id}/action", auth=AUTH, data={"action": "post_now"},
                    headers={"Origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403
    assert db.get_post(post_id)["status"] == "draft"


def test_post_now_publishes(client, post_id):
    r = client.post(f"/post/{post_id}/action", auth=AUTH, follow_redirects=False,
                    headers={"Origin": "https://autopost.example.com"},
                    data={"action": "post_now", "pf_facebook": "1", "pf_threads": "1", "threads_text": "hi"})
    assert r.status_code == 303
    assert db.get_post(post_id)["status"] == "published"
    assert {r["platform"] for r in db.get_results(post_id)} == {"facebook", "threads"}


def test_stuck_publishing_recovered(post_id):
    db.update_post(post_id, status="publishing")
    assert db.recover_stuck_publishing() >= 1
    assert db.get_post(post_id)["status"] == "failed"


def test_retry_ignored_when_not_failed(client, post_id):
    db.update_post(post_id, status="approved", scheduled_at=iso(datetime.now(timezone.utc) + timedelta(days=1)))
    client.post(f"/post/{post_id}/action", auth=AUTH, data={"action": "retry", "pf_facebook": "1"})
    assert db.get_post(post_id)["status"] == "approved"
    assert db.get_results(post_id) == []


def test_approve_in_past_rejected(client, post_id):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).astimezone(scheduler.TZ)
    client.post(f"/post/{post_id}/action", auth=AUTH,
                data={"action": "approve_at", "scheduled_at": past.strftime("%Y-%m-%dT%H:%M")})
    post = db.get_post(post_id)
    assert post["status"] == "draft" and "lewat" in post["error"]


def test_due_post_published_and_late_post_skipped(post_id):
    from app import generator
    late_id = generator.create_post(pillar="Retirement & Next Chapter")
    now = datetime.now(timezone.utc)
    db.update_post(post_id, status="approved", scheduled_at=iso(now - timedelta(minutes=2)))
    db.update_post(late_id, status="approved",
                   scheduled_at=iso(now - timedelta(hours=config.MAX_LATE_HOURS + 1)))
    scheduler.run_due_posts()
    assert db.get_post(post_id)["status"] == "published"
    late = db.get_post(late_id)
    assert late["status"] == "failed" and "terlewat" in late["error"]


def test_generate_continues_after_error(monkeypatch):
    from app import generator, main
    calls = []

    def flaky(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return 0

    monkeypatch.setattr(generator, "create_post", flaky)
    main._job_generate("ideas", pillar="X", topic=None, count=3)
    assert len(calls) == 3
    assert "1 dari 3" in main._jobs["last_error"]


def test_clip_cuts_on_word_boundary():
    text = "word " * 200
    out = publishers.clip(text)
    assert len(out) <= 500 and out.endswith("…") and not out[:-1].endswith(" ")
    assert publishers.clip("short") == "short"


def test_threads_refresh_uses_stored_date(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    db.set_setting("threads_token", "tok")
    calls = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"access_token": "new-tok"}

    monkeypatch.setattr(publishers.httpx, "get", lambda *a, **k: calls.append(1) or Resp())
    db.set_setting("threads_token_refreshed_at", iso(datetime.now(timezone.utc) - timedelta(days=1)))
    assert publishers.refresh_threads_token() is False and not calls
    db.set_setting("threads_token_refreshed_at", iso(datetime.now(timezone.utc) - timedelta(days=8)))
    assert publishers.refresh_threads_token() is True
    assert db.get_setting("threads_token") == "new-tok"


def test_cleanup_media(post_id):
    import os
    import time
    post = db.get_post(post_id)
    db.update_post(post_id, status="rejected", created_at=iso(datetime.now(timezone.utc) - timedelta(days=60)))
    orphan = config.MEDIA_DIR / "orphan.jpg"
    orphan.write_bytes(b"x")
    old = time.time() - 2 * 86400
    os.utime(orphan, (old, old))
    scheduler.cleanup_media()
    assert not orphan.exists()
    assert not (config.MEDIA_DIR / post["image_file"]).exists()
    assert db.get_post(post_id)["image_file"] is None


def test_post_slots_follow_us_dst(monkeypatch):
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    monkeypatch.setattr(scheduler, "POST_TZ", ny)
    monkeypatch.setattr(config, "POST_TIMES", ["08:30"])
    summer = scheduler.next_free_slot(datetime(2026, 7, 1, tzinfo=timezone.utc))
    winter = scheduler.next_free_slot(datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert (summer.astimezone(ny).hour, summer.astimezone(ny).minute) == (8, 30)
    assert (winter.astimezone(ny).hour, winter.astimezone(ny).minute) == (8, 30)
    assert summer.hour != winter.hour  # jam UTC bergeser mengikuti DST


def test_cta_only_every_nth_post(monkeypatch):
    from app import generator
    monkeypatch.setattr(config, "PRODUCT_NAME", "The Family Ready Binder")
    monkeypatch.setattr(config, "CTA_EVERY", 3)
    db.set_setting("cta_idx", "0")
    lines = [generator.cta_line() for _ in range(6)]
    assert [line != generator.NO_CTA for line in lines] == [False, False, True, False, False, True]
    monkeypatch.setattr(config, "PRODUCT_NAME", "")
    assert generator.cta_line() == generator.NO_CTA


def test_prompts_render():
    from app import generator
    text = generator.IDEA_INSTRUCTIONS.format(pillar="P", topic_line="T", recent="- x",
                                              format_hint="story", cta_line=generator.NO_CTA)
    assert '"story"' in text and "Comment YES" in generator.SYSTEM
    assert generator.CAPTION_INSTRUCTIONS.format(image_prompt="x", cta_line="c")
    prompt = generator.build_image_prompt({"format": "story", "headline": "The recipe card in her handwriting"})
    assert "moment remembered" in prompt and "Checklist" not in prompt
