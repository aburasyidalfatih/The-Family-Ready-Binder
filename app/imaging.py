"""Mengubah gambar ke format feed 4:5 (1080x1350 JPEG) yang diterima FB, IG, dan Threads."""
import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FEED_W, FEED_H = 1080, 1350
BG = (246, 241, 231)  # cream #F6F1E7


def to_feed_jpeg(raw: bytes, out: Path, square: bool = False):
    """Skala gambar agar muat di 1080x1350 (atau 1080x1080) tanpa memotong teks, sisanya diisi warna latar."""
    W, H = (FEED_W, FEED_W) if square else (FEED_W, FEED_H)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    scale = min(W / img.width, H / img.height)
    new = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    # warna pengisi diambil dari piksel pojok agar menyatu dengan latar gambar
    corner = img.getpixel((5, 5))
    canvas = Image.new("RGB", (W, H), corner if isinstance(corner, tuple) else BG)
    canvas.paste(new, ((W - new.width) // 2, (H - new.height) // 2))
    canvas.save(out, "JPEG", quality=92, optimize=True)


def placeholder(out: Path, text: str):
    """Gambar contoh untuk mode uji coba (FAKE_AI)."""
    canvas = Image.new("RGB", (FEED_W, FEED_H), BG)
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("DejaVuSerif-Bold.ttf", 56)
    except OSError:
        font = ImageFont.load_default()
    y = 200
    for line in textwrap.wrap(text, 28):
        d.text((90, y), line, fill=(47, 47, 47), font=font)
        y += 80
    d.rectangle([60, 60, FEED_W - 60, FEED_H - 60], outline=(143, 169, 140), width=8)
    canvas.save(out, "JPEG", quality=90)
