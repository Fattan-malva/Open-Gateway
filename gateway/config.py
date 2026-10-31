"""Konfigurasi bersama untuk Open Gateway.

Semua pengaturan aplikasi disimpan di ``data/config.json``. Tidak ada file
``.env`` dan tidak ada variabel lingkungan yang wajib diisi.
"""
from pathlib import Path

APP_NAME = "Open Gateway"
VERSION = "2.0.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
CONFIG_FILE = DATA_DIR / "config.json"
WEB_DIR = PROJECT_ROOT / "public"

# Default server; nilai aktif dibaca dari data/config.json (bagian "server").
HOST = "127.0.0.1"
PORT = 8787
OPEN_BROWSER = True
LOG_LEVEL = "info"

PROVIDER_TIMEOUT = 180
MODELS_TIMEOUT = 45
