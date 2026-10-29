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

Chat langsung ke DeepSeek lewat browser yang dikelola server. Pilih model, atur thinking (`auto` / aktif / nonaktif), lihat panel **Thinking**, dan mulai percakapan baru dengan **New chat**. Playground tidak memerlukan API key karena hanya bisa diakses dari localhost.

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

| ID | Thinking |
| --- | --- |
| `deepseek-chat` | off secara default |
| `deepseek-reasoner` | on |
| `deepseek-chat-thinking` | on |

Thinking juga bisa di-override per request:

```json
{"thinking": true}
{"ds_thinking": true}
{"enable_thinking": true}
{"reasoning_effort": "high"}
```

`reasoning_effort: "none"`, `"off"`, `false`, atau `"thinking": false` mematikan thinking. Nama model yang mengandung `reasoner`, `thinking`, `deepseek-r1`, atau `-r1` otomatis memakai thinking.

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

Pilih model `open-masking/deepseek-chat` lewat `/models`. Variant thinking bisa dipakai dengan `#`:

```powershell
opencode run --model open-masking/deepseek-chat#thinking "Jelaskan HTTP/2"
opencode run --model open-masking/deepseek-reasoner#none "Jawab singkat"
```

### VS Code

Buka Command Palette → **Chat: Manage Language Models**, atau salin `vscode.example.json` ke `chatLanguageModels.json`. Ganti `sk-ds-GANTI_DENGAN_KEY` dengan key kamu, lalu reload window dan buat chat baru.

### cURL

```powershell
curl http://127.0.0.1:8787/v1/chat/completions `
  -H "Authorization: Bearer sk-ds-..." `
  -H "Content-Type: application/json" `
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Halo"}]}'
```

## Streaming, tool, dan thinking

- `stream: true` mengirim SSE OpenAI (`data: {...}`, lalu `data: [DONE]`).
- Bila request membawa `tools`, respons di-buffer dulu agar blok `tool_call` tidak bocor sebagai teks. Klien tetap menerima SSE yang valid.
- Reasoning dikirim sebagai `reasoning_content` pada message dan chunk SSE.

### Tool calling

Definisi tool datang dari request klien. Gateway mengirimkannya ke model lewat prompt, lalu mem-parse balasan berbentuk:

````text
```tool_call
{"name":"get_time","arguments":{"timezone":"Asia/Jakarta"}}
```
````

Hasil tool dikirim klien pada pesan berikutnya (`role: "tool"`, `tool_call_id`). Blok yang didukung: pagar `tool_call` / `tool-call` / `tool_calls` / `tool` / `function_call`, tag `<tool_call>...</tool_call>`, atau JSON polos.

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

- Nama provider di `chatLanguageModels.json` berubah setelah chat lama dibuat. Reload window, buat chat baru, lalu pilih model dari provider yang benar.
- Jangan mengganti `name` provider setelah dipakai.

### DeepThink tidak aktif

- Pastikan akun menampilkan toggle `Deep thinking`.
- Set `DS_DEBUG=1` untuk menyimpan screenshot/HTML ke `data/debug`.
- Override `DS_THINKING_SELECTOR` bila teks UI berubah.

### Balasan timeout

- Naikkan `DS_REPLY_TIMEOUT`.
- Pastikan tab DeepSeek tidak menampilkan dialog/captcha.
- Request dengan tools memang menunggu model selesai sebelum SSE dikirim.

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
