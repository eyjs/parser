"""DocForge desktop launcher — starts Flask server and opens browser.

This is the PyInstaller entry point for the packaged .exe.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def find_free_port(start: int = 8000, end: int = 8100) -> int:
    """Find a free port in the given range."""
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port found in range {start}-{end}")


def get_base_dir() -> Path:
    """Return base directory (handles PyInstaller frozen mode)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent


def main() -> None:
    """Launch DocForge server and open browser."""
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    print(f"DocForge 서버 시작 중... ({url})")

    from docforge.web.app import create_app

    app = create_app()

    def open_browser() -> None:
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        from werkzeug.serving import make_server

        server = make_server("127.0.0.1", port, app)
        print(f"DocForge 서버 실행 중: {url}")
        print("종료하려면 Ctrl+C를 누르세요.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDocForge 서버 종료")
        sys.exit(0)


if __name__ == "__main__":
    main()
