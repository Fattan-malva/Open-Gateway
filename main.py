"""open-masking: jalankan gateway dan web UI dalam satu perintah.

    py main.py

Semua pengaturan (API key, browser, playground) dilakukan dari web UI.
"""
import logging
import os
import sys
import threading
import webbrowser

import uvicorn

from gateway.config import APP_NAME, HOST, OPEN_BROWSER, PORT, VERSION

# Hindari crash saat stdout memakai encoding non-UTF-8 (mis. pipe PowerShell cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    log_level = os.getenv("DS_LOG_LEVEL", "info").strip().lower() or "info"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    display_host = "127.0.0.1" if HOST in ("0.0.0.0", "::", "") else HOST
    url = f"http://{display_host}:{PORT}"

    print()
    print(f"  {APP_NAME} v{VERSION}")
    print(f"  Web UI     : {url}")
    print(f"  API base   : {url}/v1")
    print(f"  Bind       : {HOST}:{PORT}")
    print()

    if OPEN_BROWSER:
        threading.Timer(1.2, _open_browser, args=(url,)).start()

    uvicorn.run("gateway.server:app", host=HOST, port=PORT, log_level=log_level)


if __name__ == "__main__":
    main()
