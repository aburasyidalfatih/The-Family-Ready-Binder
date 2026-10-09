"""Menyiapkan file .env untuk uji coba lokal (dipanggil oleh run-local.sh / run-local.bat).

- Bila .env belum ada: disalin dari .env.example dalam mode uji coba (FAKE_AI & DRY_RUN aktif),
  dengan password dashboard acak.
- Bila .env sudah ada: tidak diubah, hanya dicek apakah password-nya masih bawaan.
"""
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV, EXAMPLE = ROOT / ".env", ROOT / ".env.example"
WEAK = {"", "ganti-password-ini", "ganti-dengan-password-kuat", "admin", "password"}


def read_value(text: str, key: str) -> str:
    # baris terakhir yang aktif (bukan komentar) yang berlaku, sama seperti python-dotenv
    found = re.findall(rf"^{key}=(.*)$", text, flags=re.M)
    return found[-1].strip() if found else ""


def set_value(text: str, key: str, value: str) -> str:
    """Ganti semua baris aktif KEY=...; baris komentar tidak disentuh."""
    line = f"{key}={value}"
    pattern = rf"^{key}=.*$"
    if re.search(pattern, text, flags=re.M):
        return re.sub(pattern, lambda _: line, text, flags=re.M)
    return text.rstrip("\n") + f"\n{line}\n"


def main(port: str) -> int:
    if not ENV.exists():
        text = EXAMPLE.read_text(encoding="utf-8")
        password = secrets.token_urlsafe(9)
        for key, value in {
            "DASHBOARD_PASSWORD": password,
            "FAKE_AI": "true",
            "DRY_RUN": "true",
            "PUBLIC_BASE_URL": f"http://localhost:{port}",
        }.items():
            text = set_value(text, key, value)
        ENV.write_text(text, encoding="utf-8")
        print("File .env dibuat dalam MODE UJI COBA (tidak memanggil OpenAI, tidak memposting).")
    text = ENV.read_text(encoding="utf-8")
    user = read_value(text, "DASHBOARD_USER") or "admin"
    password = read_value(text, "DASHBOARD_PASSWORD")
    if password in WEAK or len(password) < 8:
        print("\n[!] DASHBOARD_PASSWORD di file .env masih bawaan atau kurang dari 8 karakter.")
        print("    Buka file .env, isi password yang kuat, lalu jalankan skrip ini lagi.")
        return 1
    print()
    print("=" * 56)
    print(f"  Buka di browser : http://localhost:{port}")
    print(f"  Login           : {user} / {password}")
    print(f"  Mode uji coba   : FAKE_AI={read_value(text, 'FAKE_AI') or 'false'}, "
          f"DRY_RUN={read_value(text, 'DRY_RUN') or 'false'}")
    print("  Hentikan server : tekan Ctrl+C")
    print("=" * 56)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "8000"))
