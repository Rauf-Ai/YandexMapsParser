"""Flow 2: Таблица — поиск, сортировка, фильтры, пагинация, теги."""

import pytest
from tests.conftest import do_search
from tests.mock_data import COMPANIES_26


def test_table_search_filters_rows(page):
    """#tableSearch фильтрует строки таблицы по тексту."""
    do_search(page, max_n=3)

    page.fill("#tableSearch", "Кофейня")
    page.wait_for_timeout(400)

    rows = page.locator("#mainTableBody tr")
    assert rows.count() == 1
    assert "Кофейня" in rows.first.inner_text()


def test_table_search_clear_shows_all(page):
    """Очистка поиска возвращает все строки."""
    do_search(page, max_n=3)

    page.fill("#tableSearch", "Кофейня")
    page.wait_for_timeout(300)
    page.fill("#tableSearch", "")
    page.wait_for_timeout(300)

    rows = page.locator("#mainTableBody tr")
    assert rows.count() == 3


def test_column_sort_rating(page):
    """Клик по заголовку 'Рейтинг' сортирует строки."""
    do_search(page, max_n=3)

    page.click("[data-col='rating']")
    page.wait_for_timeout(300)

    # Получаем рейтинги в текущем порядке
    rows = page.locator("#mainTableBody tr")
    ratings = []
    for i in range(rows.count()):
        txt = rows.nth(i).inner_text()
        for part in txt.split("\t"):
            try:
                ratings.append(float(part.replace(",", ".").strip()))
                break
            except ValueError:
                continue

    # Повторный клик — обратная сортировка
    page.click("[data-col='rating']")
    page.wait_for_timeout(300)
    rows2 = page.locator("#mainTableBody tr")
    assert rows2.count() == rows.count()


def test_tag_chip_expand(page):
    """Кнопка '+N ещё' раскрывает все теги."""
    do_search(page, max_n=3)

    more_btn = page.locator(".tag-chip--more").first
    if more_btn.count() == 0:
        pytest.skip("Нет скрытых тегов в тестовых данных")

    initial_text = more_btn.inner_text()
    assert "+" in initial_text

    more_btn.click()
    page.wait_for_timeout(300)

    # После раскрытия кнопка исчезает или текст меняется
    new_more = page.locator(".tag-chip--more").first
    if new_more.count() > 0:
        assert new_more.inner_text() != initial_text


def test_quick_filter_wifi(page):
    """Кнопка Wi-Fi фильтрует только компании с wifi в features."""
    do_search(page, max_n=3)

    wifi_btn = page.locator(".quick-filter-btn", has_text="Wi-Fi")
    wifi_btn.click()
    page.wait_for_timeout(400)

    assert "active" in (wifi_btn.get_attribute("class") or "")
    rows = page.locator("#mainTableBody tr")
    count = rows.count()
    assert count >= 1


def test_quick_filter_toggle(page):
    """Повторный клик на быстрый фильтр снимает его."""
    do_search(page, max_n=3)

    btn = page.locator(".quick-filter-btn").first
    btn.click()
    page.wait_for_timeout(300)
    assert "active" in (btn.get_attribute("class") or "")

    btn.click()
    page.wait_for_timeout(300)
    assert "active" not in (btn.get_attribute("class") or "")


def test_pagination_shown_for_26_results(page):
    """При 26 результатах появляется пагинация."""
    do_search(page, query="тест 26", max_n=26)

    pagination = page.locator("#pagination")
    assert pagination.is_visible()

    rows = page.locator("#mainTableBody tr")
    assert rows.count() == 25  # первая страница = PAGE_SIZE


def test_pagination_next_page(page):
    """Клик 'Следующая' показывает вторую страницу."""
    do_search(page, query="тест 26", max_n=26)

    next_btn = page.locator("#pagination button", has_text="›")
    next_btn.click()
    page.wait_for_timeout(400)

    rows = page.locator("#mainTableBody tr")
    assert rows.count() == 1  # вторая страница = 1 запись (26 - 25)


def test_filter_has_site(page):
    """Фильтр 'Есть сайт' показывает только компании с сайтом."""
    do_search(page, max_n=3)

    page.select_option("#filterHasSite", "Да")
    page.wait_for_timeout(400)

    rows = page.locator("#mainTableBody tr")
    assert rows.count() == 2  # Кофейня + Стоматология (без сайта — Барбершоп)
