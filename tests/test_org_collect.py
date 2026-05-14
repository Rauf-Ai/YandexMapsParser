"""Flow 3: Выгрузка данных компании → ZIP через оверлей."""

import pytest
from tests.conftest import do_search


def test_collect_overlay_opens(page):
    """Клик 📦 открывает оверлей с именем компании."""
    do_search(page, max_n=3)

    btn = page.locator(".btn-collect").first
    company_row = page.locator("#mainTableBody tr").first
    company_name = company_row.locator("td").first.inner_text().strip()

    btn.click()
    page.wait_for_selector("#collectOverlay:not(.hidden)", timeout=5000)

    assert page.is_visible("#collectOverlay")
    overlay_name = page.locator("#collectOrgName").inner_text()
    assert len(overlay_name) > 0


def test_collect_progress_bar_fills(page):
    """Прогресс-бар заполняется во время сбора."""
    do_search(page, max_n=3)
    page.locator(".btn-collect").first.click()
    page.wait_for_selector("#collectOverlay:not(.hidden)", timeout=5000)

    # Ждём когда бар начнёт заполняться
    page.wait_for_function(
        "() => parseFloat(document.getElementById('collectBar').style.width) > 0",
        timeout=10000,
    )
    width = page.evaluate(
        "() => parseFloat(document.getElementById('collectBar').style.width)"
    )
    assert width > 0


def test_collect_log_has_messages(page):
    """В лог-боксе появляются сообщения о прогрессе."""
    do_search(page, max_n=3)
    page.locator(".btn-collect").first.click()
    page.wait_for_selector("#collectOverlay:not(.hidden)", timeout=5000)

    page.wait_for_selector("#collectLog .log-line", timeout=10000)
    lines = page.locator("#collectLog .log-line")
    assert lines.count() > 0


def test_collect_download_button_appears(page):
    """После завершения появляется кнопка скачивания ZIP."""
    do_search(page, max_n=3)
    page.locator(".btn-collect").first.click()
    page.wait_for_selector("#collectOverlay:not(.hidden)", timeout=5000)

    page.wait_for_selector("#collectDownloadBtn:not(.hidden)", timeout=15000)

    dl_btn = page.locator("#collectDownloadBtn")
    assert dl_btn.is_visible()
    href = dl_btn.get_attribute("href") or ""
    assert ".zip" in href


def test_collect_cancel_closes_overlay(page):
    """Кнопка ✕ закрывает оверлей."""
    do_search(page, max_n=3)
    page.locator(".btn-collect").first.click()
    page.wait_for_selector("#collectOverlay:not(.hidden)", timeout=5000)

    page.click("#collectCancelBtn")
    page.wait_for_selector("#collectOverlay", state="hidden", timeout=5000)
    assert not page.is_visible("#collectOverlay")


def test_collect_each_row_has_button(page):
    """У каждой строки таблицы есть кнопка 📦."""
    do_search(page, max_n=3)

    rows = page.locator("#mainTableBody tr")
    btns = page.locator(".btn-collect")
    assert btns.count() == rows.count()
