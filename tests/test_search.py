"""Flow 1: Поиск — форма, прогресс, результаты, скачивание."""

import pytest
from tests.conftest import do_search


def test_search_basic(page):
    """Поиск → прогресс → таблица с результатами."""
    do_search(page, query="кофейни Москва", max_n=3)

    rows = page.locator("#mainTableBody tr")
    assert rows.count() == 3, f"Ожидали 3 строки, получили {rows.count()}"


def test_search_shows_progress_bar(page):
    """Во время поиска виден прогресс-бар."""
    page.fill("#queryInput", "рестораны")
    page.fill("#maxInput", "3")
    page.click("#startBtn")

    # Мок быстрый — достаточно убедиться что progressSection появляется и cancelBtn виден
    page.wait_for_selector("#progressSection:not(.hidden)", timeout=5000)
    # progressSection видна → cancelBtn внутри неё тоже виден
    page.wait_for_selector("#progressSection:not(.hidden) #cancelBtn", timeout=5000)

    # Дожидаемся результатов
    page.wait_for_selector("#page-results:not([style*='none'])", timeout=20000)


def test_search_empty_query_shows_error(page):
    """Пустой запрос — кнопка не запускает поиск (браузерная валидация)."""
    page.click("#startBtn")
    # Прогресс не должен появиться
    assert not page.is_visible("#progressSection")


def test_search_with_query_input(page):
    """Поиск по произвольному запросу без категории."""
    page.fill("#queryInput", "барбершоп")
    page.fill("#maxInput", "3")
    page.click("#startBtn")
    page.wait_for_selector("#page-results:not([style*='none'])", timeout=20000)

    rows = page.locator("#mainTableBody tr")
    assert rows.count() > 0


def test_download_button_visible_after_search(page):
    """После поиска появляется кнопка скачивания Excel."""
    do_search(page, max_n=3)

    btn = page.locator("#downloadBtn")
    assert btn.is_visible()
    href = btn.get_attribute("href") or ""
    assert ".xlsx" in href or "/api/download/" in href


def test_stats_cards_visible(page):
    """После поиска видны карточки статистики."""
    do_search(page, max_n=3)

    assert page.is_visible("#statsGrid")
    # Хотя бы одна карточка со значением
    cards = page.locator("#statsGrid .stat-value")
    assert cards.count() > 0


def test_cancel_button_works(page):
    """Кнопка отмены скрывает прогресс-бар."""
    page.fill("#queryInput", "тест")
    page.fill("#maxInput", "3")
    page.click("#startBtn")
    page.wait_for_selector("#progressSection:not(.hidden)", timeout=5000)

    page.click("#cancelBtn")
    # progressSection получает класс hidden — ждём пока станет невидимым
    page.wait_for_selector("#progressSection", state="hidden", timeout=5000)
    assert page.is_visible("#startBtn")
    assert not page.locator("#startBtn").is_disabled()
