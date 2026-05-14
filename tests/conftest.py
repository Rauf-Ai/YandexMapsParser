"""Pytest fixtures: Flask server + mocks для Playwright тестов."""

import io
import json
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
import requests as _requests

# Добавляем корень проекта в путь
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.mock_data import COMPANIES, COMPANIES_26

PORT = 5001
BASE_URL = f"http://localhost:{PORT}"


def _make_fake_zip(company_name: str) -> str:
    """Создаёт реальный ZIP-файл в downloads/ и возвращает имя файла."""
    import app as flask_app

    info = {"name": company_name, "reviews_count": 5, "photos_count": 2}
    reviews = [{"author": "Иван", "rating": "5", "text": "Отличное место!", "date": "2024-01-01"}]
    news = [{"text": "Скидка 20% в январе"}]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("info.json",    json.dumps(info,    ensure_ascii=False))
        zf.writestr("reviews.json", json.dumps(reviews, ensure_ascii=False))
        zf.writestr("news.json",    json.dumps(news,    ensure_ascii=False))
        zf.writestr("PROMPT.md",    "# Test prompt")
        zf.writestr("README.txt",   "Test archive")

    fname = f"org_test_{company_name[:10].replace(' ', '_')}.zip"
    fpath = flask_app.DOWNLOADS_DIR / fname
    fpath.write_bytes(buf.getvalue())
    return fname


@pytest.fixture(scope="session")
def flask_server():
    """Запускает Flask на порту 5001 с замоканными внешними вызовами."""
    import app as flask_app

    def fake_collect(query, max_companies=50, emit=None, filter_fn=None):
        companies = COMPANIES_26 if max_companies > 25 else COMPANIES
        if emit:
            emit("progress", found=0, total=max_companies, message="Тест: загружаем данные…")
        time.sleep(0.1)
        result = [c for c in companies if filter_fn is None or filter_fn(c)]
        return result[:max_companies]

    def fake_collect_org_zip(company, out_dir, emit=None, **_):
        if emit:
            emit("progress", message="Тест: собираем данные…")
        time.sleep(0.15)
        if emit:
            emit("progress", message="Тест: формируем архив…")
        return _make_fake_zip(company.get("name", "test"))

    # app.py делает `from yandex_parser import collect` — патчим в пространстве app
    patches = [
        patch("app.collect", side_effect=fake_collect),
        patch("app.collect_org_zip", side_effect=fake_collect_org_zip),
    ]
    for p in patches:
        p.start()

    server = threading.Thread(
        target=lambda: flask_app.app.run(port=PORT, threaded=True, use_reloader=False),
        daemon=True,
    )
    server.start()

    # Ждём запуска
    for _ in range(30):
        try:
            _requests.get(BASE_URL, timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    yield BASE_URL

    for p in patches:
        p.stop()


CHROMIUM_PATH = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


@pytest.fixture(scope="session")
def browser(playwright):
    """Браузер с явным путём к бинарнику (sandbox не имеет интернета для скачивания)."""
    browser = playwright.chromium.launch(
        executable_path=CHROMIUM_PATH,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    yield browser
    browser.close()


@pytest.fixture
def page(flask_server, browser):
    """Playwright page, открытая на главной."""
    context = browser.new_context()
    pg = context.new_page()
    pg.goto(flask_server)
    pg.wait_for_load_state("networkidle")
    yield pg
    context.close()


def do_search(page, query="кофейни Москва", max_n=3):
    """Вспомогательная функция: запускает поиск и ждёт результатов."""
    page.fill("#queryInput", query)
    page.fill("#maxInput", str(max_n))
    page.click("#startBtn")
    page.wait_for_selector("#progressSection:not(.hidden)", timeout=5000)
    page.wait_for_selector("#page-results:not([style*='none'])", timeout=20000)
    page.wait_for_selector("#mainTableBody tr", timeout=10000)
