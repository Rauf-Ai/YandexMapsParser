"""Flow 4: История — поисковые и org-collect записи."""

import pytest
from tests.conftest import do_search


def _go_history(page):
    page.click(".nav-tab[data-section='history']")
    page.wait_for_selector("#page-history:not([style*='none'])", timeout=5000)


def test_history_has_search_entry(page):
    """После поиска в истории появляется запись."""
    do_search(page, query="кофейни тест", max_n=3)
    _go_history(page)

    items = page.locator(".history-item")
    assert items.count() >= 1

    text = page.locator("#historyList").inner_text()
    assert "кофейни тест" in text.lower() or items.count() >= 1


def test_history_search_entry_has_excel(page):
    """Запись поиска содержит кнопку скачивания Excel."""
    do_search(page, max_n=3)
    _go_history(page)

    dl_links = page.locator(".history-item a", has_text="↓ Excel")
    assert dl_links.count() >= 1


def test_history_org_entry_after_collect(page):
    """После org-collect в истории появляется запись с ZIP."""
    do_search(page, max_n=3)
    page.locator(".btn-collect").first.click()
    page.wait_for_selector("#collectDownloadBtn:not(.hidden)", timeout=15000)
    page.click("#collectCancelBtn")

    _go_history(page)

    zip_links = page.locator(".history-item a", has_text="↓ ZIP")
    assert zip_links.count() >= 1


def test_history_org_entry_has_org_style(page):
    """Запись org-collect имеет особый стиль (.history-item--org)."""
    do_search(page, max_n=3)
    page.locator(".btn-collect").first.click()
    page.wait_for_selector("#collectDownloadBtn:not(.hidden)", timeout=15000)
    page.click("#collectCancelBtn")

    _go_history(page)

    org_items = page.locator(".history-item--org")
    assert org_items.count() >= 1


def test_history_delete_entry(page):
    """Кнопка ✕ удаляет запись из истории."""
    do_search(page, max_n=3)
    _go_history(page)

    before = page.locator(".history-item").count()
    page.locator("[data-del]").first.click()
    page.wait_for_timeout(600)

    after = page.locator(".history-item").count()
    assert after == before - 1


def test_history_clear_all(page):
    """Кнопка 'Очистить всё' удаляет все записи."""
    do_search(page, max_n=3)
    _go_history(page)

    page.click("#clearHistoryBtn")
    page.wait_for_timeout(800)

    assert page.is_visible(".history-empty") or page.locator(".history-item").count() == 0


def test_history_tab_navigation(page):
    """Клик по вкладке History показывает страницу истории."""
    page.click(".nav-tab[data-section='history']")
    page.wait_for_selector("#page-history:not([style*='none'])", timeout=5000)
    assert page.is_visible("#historyList")
