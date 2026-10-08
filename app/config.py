"""Semua pengaturan dibaca dari file .env (lihat .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- Dashboard ---
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "ganti-password-ini")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Jakarta")
# URL publik aplikasi (tanpa / di akhir). Wajib agar Meta bisa mengambil gambar.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# --- Penyimpanan ---
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "autopost.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.4-mini")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")  # low | medium | high
# FAKE_AI=true -> tidak memanggil OpenAI (untuk uji coba tanpa biaya)
FAKE_AI = _bool("FAKE_AI", False)

# --- Brand ---
BRAND_HANDLE = os.getenv("BRAND_HANDLE", "@familyreadybinder")
BRAND_AUDIENCE = os.getenv(
    "BRAND_AUDIENCE",
    "Americans aged 45-65, mostly women, who care for aging parents, plan for retirement, "
    "and want to get their family's affairs in order.",
)
BRAND_STYLE = os.getenv(
    "BRAND_STYLE",
    "Style: warm, calm and trustworthy. Color palette: sage green (#8FA98C), cream background "
    "(#F6F1E7), soft terracotta accents (#C97B5A), charcoal text (#2F2F2F). Large bold serif "
    "headline, clean sans-serif body text, high contrast, easy to read for adults 45+, generous "
    "white space, no clutter, no tiny text. Spell every word exactly as written and do not add "
    "any other text.",
)
PILLARS = [
    p.strip()
    for p in os.getenv(
        "PILLARS",
        "Caring for Aging Parents|Be Ready (legacy & emergency documents)|"
        "Retirement & Next Chapter|Family Stories & Nostalgia",
    ).split("|")
    if p.strip()
]

# --- Jadwal ---
# Jam posting default (zona TIMEZONE). 19:30 & 07:30 WIB = pagi & malam waktu AS bagian timur.
POST_TIMES = [t.strip() for t in os.getenv("POST_TIMES", "19:30,07:30").split(",") if t.strip()]
# Buat draf otomatis setiap hari (masuk antrean approval, tidak langsung diposting)
AUTO_GENERATE_DAILY = _bool("AUTO_GENERATE_DAILY", True)
AUTO_GENERATE_TIME = os.getenv("AUTO_GENERATE_TIME", "09:00")
AUTO_GENERATE_COUNT = int(os.getenv("AUTO_GENERATE_COUNT", "2"))

# --- Meta (Facebook Page + Instagram) ---
# App ID & Secret dipakai halaman Pengaturan untuk menukar token jadi permanen
META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v23.0")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
IG_USER_ID = os.getenv("IG_USER_ID", "")

# --- Threads ---
# Threads punya App ID & Secret sendiri (lihat di use case Threads pada Meta App)
THREADS_APP_ID = os.getenv("THREADS_APP_ID", "")
THREADS_APP_SECRET = os.getenv("THREADS_APP_SECRET", "")
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "")
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "")

# DRY_RUN=true -> tidak benar-benar memposting, hanya mencatat (untuk uji coba)
DRY_RUN = _bool("DRY_RUN", False)
