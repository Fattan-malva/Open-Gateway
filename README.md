# open-masking

Gateway **OpenAI-compatible** untuk **chat.deepseek.com**, dengan web UI lokal untuk chat, API key, dan sesi browser. Semua berjalan lewat Playwright + Chrome di mesin kamu.

Satu perintah menjalankan semuanya:

```powershell
py main.py
```

Web UI terbuka otomatis di `http://127.0.0.1:8787`. Tidak ada CLI, subcommand, atau terminal chat.

## Prasyarat

- Python 3.9+
- Google Chrome terpasang (default `DS_CHANNEL=chrome`)

## Instalasi

```powershell
pip install -r requirements.txt
```

Hanya jika Chrome tidak terpasang / ingin memakai Chromium bawaan Playwright:

```powershell
playwright install chromium
```

lalu set `DS_CHANNEL=chromium` di `.env`.

## Menjalankan

```powershell
py main.py
```

- Server: `http://127.0.0.1:8787`
- API base: `http://127.0.0.1:8787/v1`
- Web UI: `http://127.0.0.1:8787`

Ubah host/port lewat `.env` (`HOST`, `PORT`). Set `DS_OPEN_BROWSER=0` bila tidak ingin browser dibuka otomatis.

## Tab di web UI

### Playground

Chat langsung ke DeepSeek lewat browser yang dikelola server. Playground memakai satu model (`deepseek-chat`) dengan dua mode terpisah: **Deep Think** (`auto` / aktif / nonaktif) dan **Search** (aktif / nonaktif). Playground tidak memerlukan API key karena hanya bisa diakses dari localhost.

### API Keys

Buat, lihat, dan revoke key untuk OpenCode/VS Code/klien lain. Key hanya ditampilkan **sekali** saat dibuat; yang disimpan di `data/api_keys.json` hanya hash SHA-256.

### Browser

Kelola profil Chrome dan sesi DeepSeek:

- **Login** — membuka jendela Chrome terlihat, lalu menunggu login selesai.
- **Restart** — tutup lalu buka ulang browser.
- **Close** — hentikan browser.
- **Reset profil** — hapus `data/chrome-profile` dan `data/cookies.json` (semua sesi login hilang).

Login butuh jendela terlihat; setelah login, chat bisa tetap berjalan headless (`DS_HEADLESS=1`).

### Connect

Base URL, daftar model, dan snippet siap salin untuk OpenCode, VS Code, dan cURL.

## Model

Gateway menampilkan satu model kanonis: `deepseek-chat`. Mode tidak dibuat sebagai model terpisah, tetapi dikirim sebagai opsi per permintaan.

| Opsi | Default | Keterangan |
| --- | --- | --- |
| `thinking` | `false` untuk `deepseek-chat` | Mengaktifkan Deep Think pada UI DeepSeek |
| `search` | `false` | Mengaktifkan tombol Search/pencarian web di UI DeepSeek |

Contoh request:

```json
{
  "model": "deepseek-chat",
  "thinking": true,
  "search": false,
  "messages": [{"role": "user", "content": "Jelaskan topik ini"}]
}
```

Alias `web_search`, `enable_search`, serta `modelOptions.thinking/search` juga diterima untuk menyesuaikan format konfigurasi klien. ID lama `deepseek-reasoner` dan `deepseek-chat-thinking` tetap dipahami sebagai alias kompatibilitas jika masih tersimpan di konfigurasi klien, tetapi tidak lagi ditampilkan sebagai model terpisah.

## Endpoint

| Method | Path | Auth | Keterangan |
| --- | --- | --- | --- |
| `GET` | `/` | tidak | Web UI |
| `GET` | `/health` | tidak | Status gateway |
| `GET` | `/v1/models` | ya | Daftar model |
| `POST` | `/v1/chat/completions` | ya | Chat completion (stream & non-stream) |
| `GET` | `/api/status` | lokal | Status gateway + browser |
| `GET/POST` | `/api/keys` | lokal | Daftar / buat API key |
| `POST` | `/api/keys/{id}/revoke` | lokal | Revoke key |
| `GET` | `/api/browser` | lokal | Status browser |
| `POST` | `/api/browser/{login,restart,close,reset}` | lokal | Kontrol browser |
| `POST` | `/api/playground/chat` | lokal | Chat SSE untuk UI |
| `POST` | `/api/playground/reset` | lokal | Reset percakapan |
| `GET` | `/api/connect` | lokal | Info koneksi |

Auth `/v1/*` menerima salah satu:

```http
Authorization: Bearer sk-ds-...
```

```http
X-API-Key: sk-ds-...
```

Endpoint `/api/*` hanya menerima request dari localhost.

## Connect ke klien

Ambil key dari tab **API Keys**, lalu gunakan:

### OpenCode

Salin `opencode.example.jsonc` ke `opencode.jsonc` proyek (atau config global), ganti `sk-ds-GANTI_DENGAN_KEY`, lalu set environment:

```powershell
$env:OPEN_MASKING_KEY = "sk-ds-..."
opencode
```

Pilih satu model `open-masking/deepseek-chat` lewat `/models`. Gunakan variant `#deepthink`, `#search`, atau `#deepthink-search` untuk mengatur mode tanpa membuat model terpisah.

### VS Code

Buka Command Palette → **Chat: Manage Language Models**, atau salin `vscode.example.json` ke `chatLanguageModels.json`. Ganti `sk-ds-GANTI_DENGAN_KEY` dengan key kamu, lalu reload window dan buat chat baru. Konfigurasi contoh memakai satu model; ubah `modelOptions.thinking` atau `modelOptions.search` menjadi `true` untuk mengaktifkan mode tersebut.

### cURL

```powershell
curl http://127.0.0.1:8787/v1/chat/completions `
  -H "Authorization: Bearer sk-ds-..." `
  -H "Content-Type: application/json" `
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Halo"}]}'
```

## Streaming, tools, dan thinking

- stream=true mengirim SSE berformat Chat Completions (data: {...} dan penutup data: [DONE]).
- Balasan teks biasa dapat dialirkan langsung. Permintaan yang menyertakan tools di-buffer sampai jawaban selesai agar blok internal tool-call tidak bocor sebagai teks; setelah itu gateway mengirim delta tool_calls mengikuti format streaming OpenAI. Jadi streaming teks biasa real-time, sedangkan tool-call baru dikirim setelah parsing selesai.
- Mode berpikir dikirim melalui reasoning_content ketika tersedia dari UI DeepSeek. usage merupakan estimasi berbasis karakter, bukan tokenizer resmi.

### Tool calling

Gateway menerima definisi fungsi modern tools, tool_choice (auto, none, required atau pilihan fungsi tertentu), dan parallel_tool_calls. Format lama functions / function_call juga dipetakan untuk client yang belum memakai tools. Respons modern berisi message.tool_calls dengan ID unik dan finish_reason: "tool_calls". Client harus menjalankan fungsi lokal/eksternal, lalu mengirim hasilnya sebagai pesan role=tool beserta tool_call_id; gateway tidak mengeksekusi tools milik client.

Contoh definisi tool:

```json
{
  "model": "deepseek-chat",
  "messages": [{"role": "user", "content": "Cari waktu sekarang di Jakarta."}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_time",
      "description": "Ambil waktu saat ini untuk timezone tertentu.",
      "parameters": {
        "type": "object",
        "properties": {"timezone": {"type": "string"}},
        "required": ["timezone"]
      }
    }
  }],
  "tool_choice": "auto"
}
```

**Batasan penting:** project ini mengendalikan chat.deepseek.com lewat browser, bukan memanggil endpoint inference resmi DeepSeek. Karena UI web tersebut tidak menyediakan kanal tool-call native yang dipakai API, gateway meminta model menulis blok tool-call lalu mem-parsenya menjadi format OpenAI. Parser memvalidasi nama tool dan sintaks JSON object, tetapi tidak menjamin kepatuhan penuh terhadap setiap JSON Schema atau strict mode; hasilnya tidak bisa dijamin identik dengan tool calling native API. Gunakan client yang mendukung endpoint POST /v1/chat/completions; endpoint Responses API belum diimplementasikan. Gateway hanya mengeluarkan satu choice (n=1).

## Konfigurasi `.env`

```env
# Server
HOST=127.0.0.1
PORT=8787
DS_OPEN_BROWSER=1
DS_LOG_LEVEL=info

# Data persisten
DS_DATA_DIR=./data
DS_PROFILE_DIR=./data/chrome-profile
DS_API_KEYS_FILE=./data/api_keys.json

# Browser
DS_BASE_URL=https://chat.deepseek.com
DS_CHANNEL=chrome
DS_HEADLESS=0

# Timeout dan batas prompt
DS_LOGIN_TIMEOUT=300
DS_REPLY_TIMEOUT=180
DS_MAX_PROMPT_CHARS=60000

# Opsional: cookie sesi DeepSeek
DS_COOKIE=

# Debug: simpan screenshot + HTML saat error
DS_DEBUG=0
```

`DS_HEADLESS=1` menjalankan Chrome tanpa jendela, tetapi login lewat tab **Browser** tetap membuka jendela terlihat.

Selector UI DeepSeek bisa berubah. Override lewat env, dipisahkan `||`:

```env
DS_INPUT_SELECTOR=textarea[placeholder]||#chat-input
DS_SEND_SELECTOR=button[aria-label*='send' i]
DS_STOP_SELECTOR=button[aria-label*='stop' i]
DS_THINKING_SELECTOR=div[class*='ds-toggle-button']:has-text('Deep thinking')
DS_REASONING_SELECTOR=.ds-think-content
```

## Troubleshooting

### Chrome gagal dibuka

- Pastikan Chrome terpasang.
- Tutup Chrome lain yang memakai profil yang sama (`data/chrome-profile`).
- Untuk Chromium Playwright: `playwright install chromium`, lalu `DS_CHANNEL=chromium`.

### Diminta login terus / sesi berakhir

- Buka tab **Browser** → **Login**, selesaikan login di jendela Chrome.
- Jika masih gagal, **Reset profil** lalu login ulang.
- `DS_HEADLESS=1` tidak menghalangi login dari web UI; server memaksa jendela terlihat.

### `401` dari gateway

- Pastikan header `Authorization: Bearer <key>` atau `X-API-Key`.
- Cek key masih aktif di tab **API Keys**.
- Key hanya tampil sekali; jika hilang, buat key baru dan revoke yang lama.

### `502 No BYOK model found` di VS Code

- Pastikan konfigurasi hanya mendaftarkan satu model `deepseek-chat`, URL berakhir dengan `/v1/chat/completions`, dan API key masih aktif. Reload window lalu buat chat baru. Untuk mode, atur `modelOptions.thinking` atau `modelOptions.search` di konfigurasi model.
- Jangan mengganti `name` provider setelah dipakai.

### Deep Think atau Search tidak aktif

- Pastikan UI akun DeepSeek menampilkan tombol Deep Think/Search.
- Set `DS_DEBUG=1` untuk menyimpan screenshot/HTML ke `data/debug`.
- Override `DS_THINKING_SELECTOR` atau `DS_SEARCH_SELECTOR` bila label tombol UI berubah.

### VS Code menampilkan `No response was returned`

- Gunakan konfigurasi terbaru dengan satu model `deepseek-chat`, lalu reload window VS Code.
- Pastikan sesi Browser masih login dan tidak ada captcha/dialog di DeepSeek.
- Pada request streaming, error otomasi dikirim sebagai chunk assistant yang valid dan ditutup dengan `finish_reason`, bukan objek JSON error mentah di tengah stream yang dapat dianggap klien sebagai respons kosong.

### Balasan timeout

- Naikkan `DS_REPLY_TIMEOUT`.
- Pastikan tab DeepSeek tidak menampilkan dialog/captcha.
- Request dengan tools menunggu hasil lengkap agar `tool_call` dapat diparsing.

## Tes

```powershell
py -m unittest discover -s tests -v
```

Tes tidak membuka browser; automation diuji manual lewat web UI.

## Keamanan

- Default bind `127.0.0.1`. Jangan bind ke `0.0.0.0` tanpa menambahkan autentikasi admin.
- `data/chrome-profile`, `data/cookies.json`, dan `data/api_keys.json` berisi data sensitif — jangan dibagikan atau di-commit.
- Satu sesi browser dipakai bersama oleh playground dan API; request diproses berurutan.

Lisensi MIT.
