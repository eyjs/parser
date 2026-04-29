"""개발용 Flask 실행 스크립트.

사용법:
    python -m docforge.web.run
    또는
    .venv/Scripts/python -m docforge.web.run
"""

from __future__ import annotations

from docforge.web.app import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="127.0.0.1", port=5000)
