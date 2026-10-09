# Auto Post: generate konten + posting otomatis ke Facebook Page, Instagram & Threads

Alur kerja:

1. **Generate:** OpenAI membuat ide, gambar 4:5, dan caption untuk tiap platform.
2. **Review:** Anda cek gambar di dashboard, edit caption bila perlu, lalu klik **Setujui**.
3. **Posting otomatis:** pada jam yang dijadwalkan, aplikasi memposting ke FB Page, Instagram, dan Threads.

Setiap hari aplikasi juga bisa membuat draf baru secara otomatis (default 2 draf, jam 09:00 WIB).

Gaya konten: campuran checklist, daftar bernomor, pertanyaan, kutipan, dan cerita singkat yang relatable. AI diarahkan agar tidak memakai *engagement bait* ("Comment YES", "Tag a friend") yang diturunkan jangkauannya oleh Meta, tidak mengarang pengalaman pribadi palsu, dan hanya sesekali menyebut produk. Draf otomatis **tidak** langsung diposting dan tetap menunggu persetujuan Anda.

---

## 1. Coba di komputer sendiri (5 menit, gratis)

Butuh Python 3.11+ ([unduh di sini](https://www.python.org/downloads/); di Windows centang **Add Python to PATH** saat memasang).

### Cara cepat: satu perintah

- **Windows:** klik dua kali `run-local.bat`
- **Mac/Linux:** buka terminal di folder ini, lalu jalankan `./run-local.sh`

Skrip ini otomatis membuat virtual environment, memasang dependency, dan (bila belum ada) membuat file `.env` dalam **mode uji coba** (`FAKE_AI=true`, `DRY_RUN=true`) dengan password dashboard acak. Alamat dan login dashboard ditampilkan di layar. Port bisa diganti dengan `PORT=8080 ./run-local.sh` (Windows: `set PORT=8080` lalu jalankan `run-local.bat`). Tekan Ctrl+C untuk berhenti.

File `.env` yang sudah ada tidak diubah, jadi setelah Anda mengisi `OPENAI_API_KEY` dan mengubah `FAKE_AI=false`, cukup jalankan skrip yang sama lagi.

### Cara manual

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

Untuk uji coba tanpa biaya, buka `.env` dan isi:

```
FAKE_AI=true
DRY_RUN=true
PUBLIC_BASE_URL=http://localhost:8000
```

Lalu jalankan:

```bash
uvicorn app.main:app --port 8000
```

Buka http://localhost:8000 dan login dengan `DASHBOARD_USER` / `DASHBOARD_PASSWORD`.

Setelah tampilannya oke, isi `OPENAI_API_KEY` dan ubah `FAKE_AI=false` untuk mencoba generate gambar sungguhan. Posting sungguhan **harus** dari server publik (langkah 3), karena Meta perlu mengambil gambar dari URL publik.

---

## 2. Siapkan Meta App (sekali saja)

Prasyarat: Instagram sudah **Business/Creator** dan terhubung ke Facebook Page Anda.

1. Buka **developers.facebook.com → My Apps → Create App**.
2. Pilih use case untuk **mengelola Page** dan **Instagram (dengan Facebook Login)**, lalu tambahkan juga **Access the Threads API**.
3. Catat:
   - **App ID** dan **App Secret** (App settings → Basic) → isi `META_APP_ID`, `META_APP_SECRET`.
   - **Threads App ID** dan **Threads App Secret** (di pengaturan use case Threads) → isi `THREADS_APP_ID`, `THREADS_APP_SECRET`.
4. Di pengaturan Threads, daftarkan **Redirect Callback URL**: `https://DOMAIN-ANDA/threads/callback`.
5. Di **App roles → Roles**, tambahkan akun Threads Anda sebagai **Threads Tester**. Lalu di aplikasi Threads buka *Settings → Account → Website permissions → Invites* dan terima undangannya.

Karena aplikasi hanya memposting ke akun Anda sendiri (Anda admin App dan Page), biasanya **tidak perlu App Review**. Tampilan menu Meta sering berubah; kalau namanya sedikit berbeda, cari menu yang paling mirip.

---

## 3. Deploy ke server (contoh: Railway)

Aplikasi harus jalan terus 24 jam supaya jadwal posting berjalan.

1. Upload folder ini ke repo GitHub (private).
2. Di **railway.app**: New Project → Deploy from GitHub repo. Dockerfile terdeteksi otomatis.
3. Tambahkan **Volume** dengan mount path `/data`, supaya database dan gambar tidak hilang saat redeploy.
4. Di tab **Variables**, masukkan semua isi `.env` (tanpa `FAKE_AI`/`DRY_RUN`, atau set `false`) dan tambahkan `DATA_DIR=/data`.
5. Di **Settings → Networking → Generate Domain**, lalu isi `PUBLIC_BASE_URL` dengan domain tersebut (contoh `https://autopost-production.up.railway.app`).
6. Pastikan hanya **1 instance/replica** berjalan, supaya posting tidak dobel.

Alternatif lain: Render (paket berbayar dengan persistent disk), VPS (DigitalOcean/Contabo/IDCloudHost) dengan Docker, atau platform apa pun yang bisa menjalankan Docker dan menyediakan disk permanen.

---

## 4. Hubungkan akun (di dashboard → Pengaturan)

**Facebook Page + Instagram**

1. Buka **Graph API Explorer** (developers.facebook.com/tools/explorer) dan pilih App Anda.
2. Klik **Generate Access Token** dengan izin:
   `pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic, instagram_content_publish, business_management`
3. Tempel token di halaman Pengaturan → **Ambil daftar Page** → pilih Page → **Simpan**.
   Aplikasi menukarnya menjadi token Page yang tidak kedaluwarsa, sekaligus menemukan akun Instagram yang terhubung.

**Threads**

Klik **Hubungkan Threads** lalu setujui. Token berlaku 60 hari dan diperpanjang otomatis setiap minggu (dicek tiap hari, tanggal perpanjangan terakhir disimpan di database sehingga aman walau sering redeploy). Kalau perpanjangan gagal, peringatannya muncul di halaman Pengaturan.

Klik **Tes koneksi**. Ketiganya harus bertanda ✓.

---

## 5. Pemakaian harian

- **+ Buat konten → A:** AI membuat ide sendiri (pilih pilar/topik, jumlah draf).
- **+ Buat konten → B:** tempel prompt dari dokumen 30 prompt. Untuk beberapa sekaligus, pisahkan dengan baris `===`.
- **Menunggu review:** buka draf, cek ejaan di gambar, edit caption, lalu:
  - **Setujui → slot berikutnya:** otomatis mengisi jam posting kosong berikutnya (`POST_TIMES`).
  - **Setujui di waktu ini:** pilih tanggal dan jam sendiri.
  - **Posting sekarang:** langsung tayang.
  - **Buat ulang gambar / caption** kalau hasilnya kurang bagus.
- **Sebagian gagal / Gagal:** lihat pesan error, perbaiki, lalu **Coba posting lagi**. Platform yang sudah sukses tidak diposting ulang.
- Kalau server restart saat sedang memposting, post tersebut otomatis dipindah ke **Gagal** agar bisa dicoba lagi.
- Kalau server mati sampai jadwal terlewat lebih dari `MAX_LATE_HOURS` jam, post tidak diposting otomatis (supaya tidak ada banyak post basi terbit sekaligus). Post masuk **Gagal**; setujui ulang dengan jadwal baru atau klik **Coba posting lagi**.

Saran ritme: setujui konten untuk 1–2 minggu sekaligus di akhir pekan.

---

## Pengaturan penting (`.env`)

| Variabel | Fungsi |
|---|---|
| `POST_TIMEZONE`, `POST_TIMES` | Jam posting menurut zona audiens. Disarankan `America/New_York` + `08:30,20:30`, sehingga otomatis mengikuti DST di AS. Dashboard tetap menampilkan waktu `TIMEZONE` (WIB) beserta jam New York-nya |
| `PRODUCT_NAME`, `PRODUCT_DESCRIPTION`, `PRODUCT_URL`, `CTA_EVERY` | CTA produk yang halus di 1 dari setiap `CTA_EVERY` post (default 5). Kosongkan `PRODUCT_NAME` untuk mematikan |
| `AUTO_GENERATE_DAILY`, `AUTO_GENERATE_TIME`, `AUTO_GENERATE_COUNT` | Draf otomatis harian |
| `OPENAI_IMAGE_QUALITY` | `low` / `medium` / `high`. Kualitas lebih tinggi = biaya lebih tinggi |
| `OPENAI_TEXT_MODEL`, `OPENAI_IMAGE_MODEL` | Ganti model jika OpenAI merilis yang baru |
| `BRAND_HANDLE`, `PILLARS`, `BRAND_STYLE`, `BRAND_AUDIENCE` | Identitas brand |
| `MAX_LATE_HOURS` | Batas keterlambatan posting terjadwal (default 6 jam, `0` = tanpa batas) |
| `MEDIA_RETENTION_DAYS` | Gambar post yang ditolak dihapus setelah sekian hari (default 30). File gambar yang tidak dipakai post mana pun dihapus otomatis tiap malam |

---

## Batasan & catatan

- **Gambar AI kadang salah eja.** Selalu cek sebelum menyetujui; itulah gunanya tahap review.
- **Batas API:** Instagram dan Threads membatasi jumlah posting via API per 24 jam. Untuk 1–3 post per hari, batas ini tidak akan tercapai.
- **Biaya OpenAI:** dihitung per gambar dan per caption. Cek harga terbaru di openai.com/pricing dan pasang batas pemakaian (usage limit) di akun OpenAI.
- **Keamanan:** jangan bagikan file `.env`. `DASHBOARD_PASSWORD` wajib diganti (min. 8 karakter); aplikasi menolak start dengan password bawaan. Form dashboard menolak permintaan POST dari situs lain (perlindungan CSRF). Gambar di `/media/...` sengaja bisa diakses publik karena Meta perlu mengambilnya.
- **Konten:** hindari klaim medis, hukum, dan keuangan yang spesifik. Prompt AI sudah diarahkan ke sana, tapi tetap periksa.
- API Meta dan OpenAI bisa berubah. Jika muncul error versi, coba naikkan `GRAPH_VERSION` atau ganti nama model di `.env`.

## Struktur kode

```
app/
  main.py        dashboard & rute
  generator.py   OpenAI: ide, caption, gambar
  imaging.py     ubah gambar ke 1080x1350 JPEG
  publishers.py  posting ke FB Page, Instagram, Threads
  connect.py     hubungkan akun (token Page, OAuth Threads)
  scheduler.py   jadwal posting, draf harian, perpanjang token
  db.py          SQLite
  templates/     halaman HTML
tests/           tes otomatis (mode uji coba, tanpa OpenAI/Meta)
```

Menjalankan tes:

```bash
pip install -r requirements-dev.txt
pytest
```
