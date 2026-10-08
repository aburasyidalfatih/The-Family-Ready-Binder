"""Membuat ide konten, caption, dan gambar dengan OpenAI."""
import base64
import json
import logging
import random
import uuid

from . import config, db, imaging
from .publishers import clip

log = logging.getLogger("generator")

_client = None


def client():
    global _client
    if _client is None:
        from openai import OpenAI

        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY belum diisi di file .env")
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


SYSTEM = f"""You are the content strategist for the social media brand {config.BRAND_HANDLE}.
Audience: {config.BRAND_AUDIENCE}
Tone: warm, calm, practical, respectful. Never fear-based, morbid, or salesy.
Write in natural American English. Never give specific medical, legal, tax, or investment advice;
keep everything as general checklists and conversation starters.
Content is posted as an image (checklist, list, or quote) on Facebook, Instagram, and Threads.
Posts that get saved and shared win: practical checklists, gentle conversation starters, nostalgia.
Always answer with a single JSON object and nothing else."""

IDEA_INSTRUCTIONS = """Create ONE new post for the content pillar: "{pillar}".
{topic_line}
Avoid repeating these recent headlines:
{recent}

Return JSON with exactly these keys:
- "topic": short topic label (max 8 words)
- "format": "checklist" | "numbered" | "quote" | "questions"
- "headline": image headline, max 12 words (for "quote" format: the quote itself, max 25 words)
- "items": array of 4-8 short lines for the image, max 8 words each (empty array for "quote")
- "footer_cta": short line under the list, max 8 words (e.g. "Save this for later")
- "illustration": one sentence describing a small, gentle illustration or background (no people's faces in close-up, no logos, no text)
- "fb_caption": Facebook caption, 60-110 words, warm, ends with one question that invites comments, no hashtags
- "ig_caption": Instagram caption, 40-80 words, ends with "Save this for later." then a blank line and 5 relevant hashtags
- "threads_text": Threads post, 1-3 short conversational sentences, max 400 characters, no hashtags"""

CAPTION_INSTRUCTIONS = """Here is an image prompt that was already written for a post:
---
{image_prompt}
---
Write the matching captions. Return JSON with exactly these keys:
- "topic": short topic label (max 8 words)
- "headline": the image headline from the prompt
- "fb_caption": Facebook caption, 60-110 words, warm, ends with one question that invites comments, no hashtags
- "ig_caption": Instagram caption, 40-80 words, ends with "Save this for later." then a blank line and 5 relevant hashtags
- "threads_text": Threads post, 1-3 short conversational sentences, max 400 characters, no hashtags"""


def build_image_prompt(idea: dict) -> str:
    """Menyusun prompt gambar lengkap dari ide + gaya brand."""
    fmt = idea.get("format", "checklist")
    lines = ["Create a portrait social media image (2:3). Keep all text inside the central area with wide margins on every side."]
    if fmt == "quote":
        lines.append(f'Quote in large elegant serif, centered:\n"{idea["headline"]}"')
    else:
        lines.append(f'Headline (large, top): "{idea["headline"]}"')
        items = idea.get("items") or []
        if fmt == "numbered" or fmt == "questions":
            lines.append("Numbered list:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)))
        else:
            lines.append("Checklist with empty checkbox icons, each item on its own line:\n" + "\n".join(f"- {t}" for t in items))
    footer = idea.get("footer_cta", "").strip()
    footer_text = f'"{footer}" and "{config.BRAND_HANDLE}"' if footer else f'"{config.BRAND_HANDLE}"'
    lines.append(f"Footer (small): {footer_text}")
    if idea.get("illustration"):
        lines.append(f"Illustration: {idea['illustration']}")
    lines.append(config.BRAND_STYLE)
    return "\n\n".join(lines)


def _chat_json(user_msg: str) -> dict:
    resp = client().chat.completions.create(
        model=config.OPENAI_TEXT_MODEL,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_msg}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def _recent_headlines(n: int = 40) -> str:
    rows = db.list_posts(limit=n)
    heads = [r["headline"] for r in rows if r["headline"]]
    return "\n".join(f"- {h}" for h in heads) or "- (none yet)"


def generate_idea(pillar: str, topic: str | None = None) -> dict:
    if config.FAKE_AI:
        return _fake_idea(pillar, topic)
    topic_line = f'Topic requested by the owner: "{topic}"' if topic else "Pick a fresh, specific topic."
    return _chat_json(
        IDEA_INSTRUCTIONS.format(pillar=pillar, topic_line=topic_line, recent=_recent_headlines())
    )


def captions_for_prompt(image_prompt: str) -> dict:
    if config.FAKE_AI:
        return {
            "topic": "Custom prompt",
            "headline": image_prompt.split("\n")[0][:80],
            "fb_caption": "Test caption for Facebook. What would you add?",
            "ig_caption": "Test caption for Instagram. Save this for later.\n\n#family #caregiving #retirement #organized #legacy",
            "threads_text": "Test post for Threads.",
        }
    return _chat_json(CAPTION_INSTRUCTIONS.format(image_prompt=image_prompt))


def generate_image(image_prompt: str, label: str = "") -> str:
    """Membuat gambar dan mengembalikan nama file JPEG 1080x1350 di folder media."""
    filename = f"{uuid.uuid4().hex}.jpg"
    out = config.MEDIA_DIR / filename
    if config.FAKE_AI:
        imaging.placeholder(out, label or image_prompt[:120])
        return filename
    low = image_prompt.lower()
    square = "square" in low or "1080x1080" in low or "1:1" in low
    resp = client().images.generate(
        model=config.OPENAI_IMAGE_MODEL,
        prompt=image_prompt,
        size="1024x1024" if square else "1024x1536",
        quality=config.OPENAI_IMAGE_QUALITY,
        n=1,
    )
    data = resp.data[0]
    if getattr(data, "b64_json", None):
        raw = base64.b64decode(data.b64_json)
    else:  # beberapa model mengembalikan URL
        import httpx

        raw = httpx.get(data.url, timeout=120).content
    imaging.to_feed_jpeg(raw, out, square=square)
    return filename


def create_post(pillar: str | None = None, topic: str | None = None, image_prompt: str | None = None,
                with_image: bool = True) -> int:
    """Membuat satu draf lengkap (caption + gambar) dan menyimpannya ke database."""
    pillar = pillar or random.choice(config.PILLARS)
    if image_prompt:
        idea = captions_for_prompt(image_prompt)
        prompt = image_prompt
    else:
        idea = generate_idea(pillar, topic)
        prompt = build_image_prompt(idea)
    post_id = db.insert_post(
        pillar=pillar,
        topic=idea.get("topic", topic or ""),
        headline=idea.get("headline", ""),
        image_prompt=prompt,
        fb_caption=idea.get("fb_caption", ""),
        ig_caption=idea.get("ig_caption", ""),
        threads_text=clip(idea.get("threads_text", "")),
        status="draft",
    )
    if with_image:
        try:
            fname = generate_image(prompt, label=idea.get("headline", ""))
            db.update_post(post_id, image_file=fname, error=None)
        except Exception as e:  # draf tetap disimpan agar bisa di-generate ulang
            log.exception("Gagal membuat gambar")
            db.update_post(post_id, error=f"Gagal membuat gambar: {e}")
    return post_id


def next_pillar() -> str:
    """Rotasi pilar secara bergiliran."""
    idx = int(db.get_setting("pillar_idx", "0") or 0)
    db.set_setting("pillar_idx", str((idx + 1) % len(config.PILLARS)))
    return config.PILLARS[idx % len(config.PILLARS)]


def _fake_idea(pillar: str, topic: str | None) -> dict:
    n = random.randint(100, 999)
    return {
        "topic": topic or f"Test topic {n}",
        "format": "checklist",
        "headline": f"Test Headline {n} for {pillar}",
        "items": ["First item", "Second item", "Third item", "Fourth item"],
        "footer_cta": "Save this for later",
        "illustration": "a small sage green folder",
        "fb_caption": f"Test Facebook caption {n}. What would you add?",
        "ig_caption": f"Test Instagram caption {n}. Save this for later.\n\n#family #caregiving #retirement #organized #legacy",
        "threads_text": f"Test Threads post {n}.",
    }
