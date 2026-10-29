"""Konfigurasi bersama untuk server web open-masking."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

APP_NAME = "open-masking"
VERSION = "0.2.1"
MODEL_IDS = ("deepseek-chat",)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def project_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def data_dir() -> Path:
    raw = os.getenv("DS_DATA_DIR", "").strip()
    if not raw or raw == ".":
        return PROJECT_ROOT / "data"
    return project_path(raw)


DATA_DIR = data_dir()
HOST = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = env_int("PORT", 8787)
OPEN_BROWSER = env_bool("DS_OPEN_BROWSER", True)
BASE_URL = os.getenv("DS_BASE_URL", "https://chat.deepseek.com").rstrip("/")
CHROME_CHANNEL = os.getenv("DS_CHANNEL", "chrome")
HEADLESS = env_bool("DS_HEADLESS", False)
LOGIN_TIMEOUT = env_int("DS_LOGIN_TIMEOUT", 300)
REPLY_TIMEOUT = env_int("DS_REPLY_TIMEOUT", 180)
MAX_PROMPT_CHARS = env_int("DS_MAX_PROMPT_CHARS", 60000)
DEBUG = env_bool("DS_DEBUG", False)

COOKIES_FILE = DATA_DIR / "cookies.json"
PROFILE_DIR = project_path(os.getenv("DS_PROFILE_DIR", "") or str(DATA_DIR / "chrome-profile"))
API_KEYS_FILE = project_path(os.getenv("DS_API_KEYS_FILE", "") or str(DATA_DIR / "api_keys.json"))
DEBUG_DIR = DATA_DIR / "debug"
WEB_DIR = Path(__file__).resolve().parent / "web"

# Nama env yang bukan cookie bila memakai format DS_<NAMA_COOKIE>.
BROWSER_CONFIG_KEYS = {
    "HOST",
    "PORT",
    "DS_CDP",
    "DS_DATA_DIR",
    "DS_POW_JS",
    "DS_BASE_URL",
    "DS_DEBUG",
    "DS_COOKIE",
    "DS_TOKEN",
    "DS_CHANNEL",
    "DS_HEADLESS",
    "DS_LOGIN_TIMEOUT",
    "DS_REPLY_TIMEOUT",
    "DS_MAX_PROMPT_CHARS",
    "DS_PROFILE_DIR",
    "DS_API_KEYS_FILE",
    "DS_OPEN_BROWSER",
    "DS_LOG_LEVEL",
    "DS_INPUT_SELECTOR",
    "DS_SEND_SELECTOR",
    "DS_STOP_SELECTOR",
    "DS_THINKING_SELECTOR",
    "DS_SEARCH_SELECTOR",
    "DS_REASONING_SELECTOR",
}
