"""Open Gateway: jalankan web UI dan bot Discord dari satu perintah.

    py main.py

Semua pengaturan (provider AI, model, token Discord) dilakukan dari web UI.
"""
import logging
import sys
import threading
import webbrowser

import uvicorn

from gateway.config import APP_NAME, VERSION
from gateway.store import get_store

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
    store = get_store()
    server = store.get_server()
    host = server.get("host") or "127.0.0.1"
    port = int(server.get("port") or 8787)
    open_browser = bool(server.get("open_browser", True))

    log_level = "info"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    display_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{display_host}:{port}"

    print()
    print(f"  {APP_NAME} v{VERSION}")
    print(f"  Web UI     : {url}")
    print(f"  Bind       : {host}:{port}")
    print()

    if open_browser:
        threading.Timer(1.2, _open_browser, args=(url,)).start()

    uvicorn.run("gateway.server:app", host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
