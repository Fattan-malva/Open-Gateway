# Open Gateway

Jembatan lokal antara **model AI OpenAI-compatible** dan **bot Discord**. Pilih model dari provider, atur cara bot merespons, lalu jalankan semuanya dari satu web UI.

```
AI Provider  →  Open Gateway  →  Bot Discord
                        ├─ chat + riwayat
                        ├─ internet (search & fetch)
                        ├─ gambar
                        └─ voice (dengar & ngobrol)
```

Satu perintah menjalankan server, web UI, dan bot:

```powershell
py main.py
```

- Web UI: `http://127.0.0.1:8787` (aplikasi: `/app`)
- Health check: `http://127.0.0.1:8787/health`
- API admin: `http://127.0.0.1:8787/api/*` (hanya localhost)

## Prasyarat

- Python 3.9+
- Bot Discord + token dari [Discord Developer Portal](https://discord.com/developers/applications)
- **ffmpeg** untuk memutar suara di voice channel (`winget install Gyan.FFmpeg` di Windows). Tanpa ffmpeg, chat dan gambar tetap jalan.

## Instalasi

```powershell
pip install -r requirements.txt
```

Semua pengaturan disimpan di `data/config.json` (dibuat otomatis). Tidak ada file `.env`.

## Menjalankan

```powershell
py main.py
```

Browser terbuka otomatis. Set `OG_OPEN_BROWSER=0` bila tidak ingin browser dibuka.

## Menu di web UI

### AI Provider

Menghubungkan gateway ke provider OpenAI-compatible.

1. Isi **Base URL** (contoh: `http://localhost:20128`) dan **API key**.
2. Klik **Simpan & muat model**.
3. Gateway memuat semua jenis model: chat, image, TTS, STT, web, dan embedding. Tabel utama menampilkan model chat; model lain otomatis tersedia di pilihan tab Gateway.
4. Gunakan kolom pencarian untuk menyaring model chat, lalu klik **Pakai** pada model yang diinginkan.

Model disimpan sebagai cache di `data/config.json`, jadi tetap tampil saat aplikasi dibuka kembali. Tombol **Muat ulang model** mengambil ulang daftar terbaru dari provider.

### Gateway

Menghubungkan model terpilih ke bot Discord.

1. Tempel **Discord bot token**.
2. Pilih **Model AI** (daftar berasal dari tab AI Provider).
3. Atur **Pemicu balasan**:
   - **Saat di-mention atau DM** — bot menjawab mention dan pesan langsung.
   - **Prefix di semua channel** — bot menjawab pesan yang diawali prefix (default `!ai`).
   - **Semua pesan di satu channel** — bot menjawab semua pesan di channel yang Channel ID-nya diisi.
4. Atur **Riwayat pesan**, **Maks. token jawaban**, dan **System prompt** bila perlu.
5. Atur fitur tambahan (bisa dinonaktifkan kapan saja):
   - **Internet** — model memakai tool `web_search` dan `web_fetch` saat butuh info terbaru. Bila provider punya model search/fetch, isi kolomnya; jika tidak, gateway memakai DuckDuckGo Lite/Bing dan pembaca halaman bawaan.
   - **Gambar** — model memakai tool `generate_image`. Ukuran bisa dipilih; bila model gambar provider gagal (kuota/entitlement), gateway otomatis memakai pollinations.ai selama **Cadangan gratis** aktif. Tanpa token, pollinations membatasi **1 gambar per 45 detik per IP**; isi `OG_POLLINATIONS_TOKEN` (gratis di [enter.pollinations.ai](https://enter.pollinations.ai)) untuk menghilangkan batas itu.
   - **Suara** — bot bisa masuk voice channel, mendengarkan lawan bicara, dan menjawab dengan TTS. Isi **Voice Channel ID** atau gunakan `!ai join`. **TTS** memakai model text-to-speech provider; **STT** dipakai untuk transkripsi suara.
6. Klik **Simpan konfigurasi**, lalu **Start**.

Tombol **Restart** memuat ulang bot dengan konfigurasi terbaru. **Stop** mematikan bot tanpa menutup server. Bot juga menyala otomatis saat aplikasi dibuka jika token sudah tersimpan.

Panel **Aktivitas** menampilkan log koneksi, pesan yang diproses, tool yang dipakai, dan error dari provider.

## Setup bot Discord

1. Buka [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. Tab **Bot** → aktifkan **Message Content Intent** (wajib; tanpa ini bot gagal start).
3. Salin **Bot Token**.
4. Tab **OAuth2 → URL Generator** → scope `bot`, izin minimal:
   - Send Messages
   - Read Message History
   - Attach Files (untuk gambar)
   - Connect + Speak (untuk voice channel)
5. Buka URL hasil generator untuk mengundang bot ke server.

## Perintah di Discord

| Perintah | Fungsi |
| --- | --- |
| `@bot halo` | Mengobrol dengan model (atau DM langsung ke bot) |
| `@bot cari harga bitcoin` | Model otomatis memakai internet bila perlu |
| `@bot buatkan gambar kucing astronot` | Model otomatis memakai tool gambar |
| `!ai gambar <deskripsi>` | Membuat gambar langsung tanpa lewat model chat |
| `!ai cari <kata kunci>` | Mencari di internet |
| `!ai buka <url>` | Membaca isi sebuah halaman |
| `!ai join [id]` | Masuk voice channel (atau channel tempat pengirim berada) |
| `!ai leave` | Keluar dari voice channel |
| `!ai dengar on\|off` | Mengaktifkan/mematikan mode mendengarkan |
| `!ai voice` | Status voice bot |
| `!ai reset` | Menghapus riwayat percakapan di channel |
| `!ai model` | Menampilkan model dan fitur yang aktif |
| `!ai ping` | Cek latensi bot |
| `!ai help` | Menampilkan bantuan |

Prefix mengikuti pengaturan di tab Gateway. Riwayat disimpan terpisah per channel dan dibatasi oleh **Riwayat pesan**. Di voice channel, bot memakai segmentasi diam ~1,2 detik, lalu transkripsi, jawaban model, dan TTS.

## Konfigurasi

Semua konfigurasi disimpan di `data/config.json` dan dibuat otomatis bila belum ada. Host/port server juga ada di bagian `server` pada file tersebut. Token pollinations bisa diisi per gateway pada kolom "Token pollinations (opsional)". Folder `data/` diabaikan git.

## Struktur

```
main.py                 Launcher server + web UI
gateway/
  config.py             Env, path, default
  store.py              Persistensi data/config.json
  provider.py           Klien chat, model, image, audio, web provider
  agent.py              Loop tool-calling (search, fetch, gambar)
  webtools.py           Pencarian & pembaca halaman tanpa API key
  media.py              Gambar (dengan fallback), TTS, STT
  voice.py              Voice channel: join, dengar, bicara
  discord_bot.py        Manajer bot, perintah, riwayat, log
  admin.py              API admin (localhost)
  server.py             FastAPI + static UI
  web/                  index.html, app.js, style.css, landing.html
tests/                  Unit test
data/                   config.json (gitignored)
```

## API admin

Semua endpoint hanya bisa diakses dari localhost.

| Method | Path | Fungsi |
| --- | --- | --- |
| `GET` | `/api/status` | Status aplikasi, provider, bot, dan voice |
| `GET` | `/api/provider` | Konfigurasi provider + katalog model per jenis |
| `POST` | `/api/provider` | Simpan provider + muat katalog (`catalog_errors` berisi jenis yang gagal) |
| `POST` | `/api/provider/refresh` | Muat ulang katalog model |
| `GET` | `/api/gateway` | Pengaturan gateway, status, log |
| `POST` | `/api/gateway` | Simpan pengaturan gateway |
| `POST` | `/api/gateway/start` | Jalankan bot |
| `POST` | `/api/gateway/stop` | Hentikan bot |
| `POST` | `/api/gateway/restart` | Jalankan ulang bot |
| `GET` | `/api/gateway/logs` | Log aktivitas |
| `POST` | `/api/gateway/logs/clear` | Bersihkan log |

Token Discord tidak pernah dikirim kembali ke browser; UI hanya menerima petunjuk 4 karakter terakhir.

## Troubleshooting

- **Bot gagal start / `PrivilegedIntentsRequired`** — aktifkan **Message Content Intent** di Developer Portal, lalu Restart.
- **`LoginFailure`** — token salah atau sudah di-reset. Simpan token baru lalu Restart.
- **Provider 429 / FreeUsageLimitError** — kuota model gratis habis. Pilih model lain dari daftar.
- **Provider 503 / 504** — model atau upstream sedang tidak tersedia. Coba lagi atau pilih model lain.
- **Model tidak muncul** — cek Base URL berakhiran `/v1`, API key valid, lalu klik **Muat ulang model**.
- **Bot tidak membalas** — pastikan bot sudah diundang, pemicu sesuai, dan model sudah dipilih.
- **Gambar gagal di provider** — biasanya kuota model gambar habis. Biarkan **Cadangan gratis** aktif agar memakai pollinations.ai; tanpa token, pollinations hanya melayani 1 gambar / 45 detik per IP (isi `OG_POLLINATIONS_TOKEN` untuk menghapusnya).
- **Suara tidak keluar** — pastikan `ffmpeg` ada di `PATH`, bot punya izin Connect + Speak, dan model TTS dipilih. Cek `!ai voice`.
- **Bot tidak mendengar** — pastikan `discord-ext-voice-recv` dan `PyNaCl` terpasang, model STT dipilih, dan mode dengar aktif (`!ai dengar on`).
- **Port dipakai** — ubah `PORT` di `.env`, lalu jalankan ulang.

## Test

```powershell
py -m unittest discover -s tests -v
```
