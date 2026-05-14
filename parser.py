"""
Yandex Maps Parser
Extracts company data from Yandex Maps search results and exports to Excel.
"""

import argparse
import json
import logging
import math
import random
import re
import sys
import time
from datetime import date
from urllib.parse import quote, urlencode, urlparse

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://yandex.ru/maps/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sleep():
    time.sleep(random.uniform(1.0, 3.0))


def _fmt_phone(raw: str) -> str:
    """Normalise phone to +7 (XXX) XXX-XX-XX."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return raw.strip()


def _clean_site(url: str) -> str:
    """Strip scheme and trailing slash."""
    url = re.sub(r"^https?://", "", url.strip())
    return url.rstrip("/")


def _dedup_key(company: dict) -> tuple:
    return (company.get("name", "").lower(), company.get("address", "").lower())


# ---------------------------------------------------------------------------
# Yandex Maps API
# ---------------------------------------------------------------------------

SEARCH_API = "https://search-maps.yandex.ru/v1/"
GEOCODE_API = "https://geocode-maps.yandex.ru/1.x/"

# Public demo API key (works for moderate volume; replace with your own).
SEARCH_API_KEY = "dda3ddba-c9ea-4ead-9010-f43fbc15c6e3"


def _search_page(query: str, skip: int, lang: str = "ru_RU") -> list[dict]:
    """Fetch one page of search results (up to 10 items) from Yandex Search API."""
    params = {
        "apikey": SEARCH_API_KEY,
        "text": query,
        "lang": lang,
        "type": "biz",
        "results": 10,
        "skip": skip,
    }
    try:
        resp = SESSION.get(SEARCH_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("API request failed (skip=%d): %s", skip, exc)
        return []

    features = data.get("features", [])
    companies = []
    for feat in features:
        props = feat.get("properties", {})
        geo = feat.get("geometry", {})
        companies.append(_parse_feature(props, geo))
    return companies


def _parse_feature(props: dict, geo: dict) -> dict:
    """Extract company fields from a GeoJSON feature."""
    name = props.get("name", "—")
    name = re.sub(r'^["\'«»]+|["\'»«]+$', "", name).strip() or "—"

    # Coordinates: GeoJSON is [lon, lat]
    coords = geo.get("coordinates", [None, None])
    lon = round(coords[0], 6) if coords[0] is not None else "—"
    lat = round(coords[1], 6) if len(coords) > 1 and coords[1] is not None else "—"

    # CompanyMetaData
    meta = props.get("CompanyMetaData", {})
    address = meta.get("address", "—")
    categories = meta.get("Categories", [])
    category = categories[0].get("name", "—") if categories else "—"
    rating_info = meta.get("Siren", {}).get("Reviews", {})
    rating = rating_info.get("rating", None)
    reviews = rating_info.get("count", 0)

    # Phones
    phones = [_fmt_phone(p.get("formatted", "")) for p in meta.get("Phones", []) if p.get("formatted")]
    phone_str = ", ".join(phones) if phones else "—"

    # URLs / social
    site = "—"
    socials = []
    for url_item in meta.get("Links", []):
        href = url_item.get("href", "")
        tag = url_item.get("tag", "")
        if tag == "official" or (not tag and href):
            if any(s in href for s in ("vk.com", "instagram.com", "t.me", "telegram", "ok.ru", "facebook.com")):
                socials.append(_clean_site(href))
            elif site == "—":
                site = _clean_site(href)
        elif href:
            socials.append(_clean_site(href))

    social_str = ", ".join(socials) if socials else "—"
    has_site = "Да" if site != "—" else "Нет"

    return {
        "name": name,
        "phone": phone_str,
        "site": site,
        "social": social_str,
        "address": address,
        "lat": lat,
        "lon": lon,
        "category": category,
        "rating": round(float(rating), 1) if rating else "—",
        "reviews": int(reviews) if reviews else 0,
        "has_site": has_site,
    }


# ---------------------------------------------------------------------------
# Scraping fallback (HTML)
# ---------------------------------------------------------------------------

def _scrape_page(query: str, page: int) -> list[dict]:
    """Fallback HTML scraper for a single search results page."""
    url = f"https://yandex.ru/maps/?text={quote(query)}&page={page}"
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("HTML page %d failed: %s", page, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select("li.search-list-item")
    companies = []
    for item in items:
        name_el = item.select_one(".search-business-snippet-view__title")
        addr_el = item.select_one(".search-business-snippet-view__address")
        cat_el = item.select_one(".search-business-snippet-view__category")
        rating_el = item.select_one(".business-rating-badge-view__rating")
        review_el = item.select_one(".business-rating-badge-view__count")

        name = name_el.get_text(strip=True) if name_el else "—"
        address = addr_el.get_text(strip=True) if addr_el else "—"
        category = cat_el.get_text(strip=True) if cat_el else "—"
        try:
            rating = round(float(rating_el.get_text(strip=True).replace(",", ".")), 1) if rating_el else "—"
        except ValueError:
            rating = "—"
        try:
            reviews = int(re.sub(r"\D", "", review_el.get_text(strip=True))) if review_el else 0
        except ValueError:
            reviews = 0

        companies.append({
            "name": name,
            "phone": "—",
            "site": "—",
            "social": "—",
            "address": address,
            "lat": "—",
            "lon": "—",
            "category": category,
            "rating": rating,
            "reviews": reviews,
            "has_site": "Нет",
        })
    return companies


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def collect(query: str, max_companies: int = 100) -> list[dict]:
    companies = []
    seen: set[tuple] = set()
    skip = 0
    page = 1
    empty_streak = 0

    while len(companies) < max_companies:
        log.info("Fetching results %d–%d for '%s'…", skip + 1, skip + 10, query)
        batch = _search_page(query, skip)

        if not batch:
            log.warning("API returned no results at skip=%d, trying HTML fallback…", skip)
            batch = _scrape_page(query, page)

        if not batch:
            empty_streak += 1
            if empty_streak >= 3:
                log.info("No more results found after %d items.", len(companies))
                break
        else:
            empty_streak = 0

        for c in batch:
            key = _dedup_key(c)
            if key in seen:
                continue
            seen.add(key)
            companies.append(c)
            if len(companies) >= max_companies:
                break

        skip += 10
        page += 1
        _sleep()

    return companies


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

COLUMNS = [
    ("Название", 30),
    ("Телефон", 22),
    ("Сайт", 28),
    ("Социальные сети", 30),
    ("Адрес", 40),
    ("Широта", 14),
    ("Долгота", 14),
    ("Категория", 25),
    ("Рейтинг", 10),
    ("Кол-во отзывов", 16),
    ("Есть сайт", 12),
]

HEADER_FILL = PatternFill("solid", fgColor="D9EAD3")
HEADER_FONT = Font(bold=True)


def _write_header(ws, row: int = 1):
    for col_idx, (title, _) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=row, column=col_idx, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _write_row(ws, row_idx: int, company: dict):
    values = [
        company["name"],
        company["phone"],
        company["site"],
        company["social"],
        company["address"],
        company["lat"] if isinstance(company["lat"], float) else None,
        company["lon"] if isinstance(company["lon"], float) else None,
        company["category"],
        company["rating"] if isinstance(company["rating"], float) else None,
        company["reviews"],
        company["has_site"],
    ]
    for col_idx, val in enumerate(values, start=1):
        cell = ws.cell(row=row_idx, column=col_idx, value=val)
        # Numeric format for coordinate / numeric columns
        if col_idx in (6, 7) and val is not None:
            cell.number_format = "0.000000"
        elif col_idx == 9 and val is not None:
            cell.number_format = "0.0"
        elif col_idx == 10:
            cell.number_format = "0"


def _set_column_widths(ws):
    for col_idx, (_, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def _add_autofilter(ws, num_rows: int):
    last_col = get_column_letter(len(COLUMNS))
    ws.auto_filter.ref = f"A1:{last_col}{num_rows + 1}"


def _add_site_validation(ws, num_rows: int):
    dv = DataValidation(
        type="list",
        formula1='"Да,Нет"',
        allow_blank=False,
        showDropDown=False,
    )
    dv.sqref = f"K2:K{num_rows + 1}"
    ws.add_data_validation(dv)


def export_excel(companies: list[dict], query: str) -> str:
    wb = Workbook()
    # ---- Sheet 1: all companies ----
    ws_all = wb.active
    ws_all.title = "Компании"
    ws_all.row_dimensions[1].height = 20
    _write_header(ws_all)

    for i, company in enumerate(companies, start=2):
        _write_row(ws_all, i, company)

    _set_column_widths(ws_all)
    _add_autofilter(ws_all, len(companies))
    _add_site_validation(ws_all, len(companies))

    # ---- Sheet 2: companies without website ----
    ws_no = wb.create_sheet("Без сайта")
    _write_header(ws_no)
    no_site = [c for c in companies if c["has_site"] == "Нет"]
    for i, company in enumerate(no_site, start=2):
        _write_row(ws_no, i, company)
    _set_column_widths(ws_no)
    _add_autofilter(ws_no, len(no_site))
    _add_site_validation(ws_no, len(no_site))

    # ---- Save ----
    safe_query = re.sub(r"[^\w\-а-яА-Я]", "_", query)[:40]
    filename = f"yandex_maps_{safe_query}_{date.today()}.xlsx"
    wb.save(filename)
    return filename


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def print_stats(companies: list[dict], filename: str):
    total = len(companies)
    with_site = sum(1 for c in companies if c["has_site"] == "Да")
    without_site = total - with_site
    with_phone = sum(1 for c in companies if c["phone"] != "—")
    ratings = [c["rating"] for c in companies if isinstance(c["rating"], float)]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0.0

    pct = lambda n: f"{round(n / total * 100)}%" if total else "0%"

    print()
    print("=" * 46)
    print(f"  Файл сохранён: {filename}")
    print("=" * 46)
    print(f"  Всего компаний собрано : {total}")
    print(f"  С сайтом               : {with_site} ({pct(with_site)})")
    print(f"  Без сайта              : {without_site} ({pct(without_site)})")
    print(f"  С телефоном            : {with_phone} ({pct(with_phone)})")
    print(f"  Средний рейтинг        : {avg_rating}")
    print("=" * 46)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Парсер Яндекс Карт — выгрузка компаний в Excel",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("query", help='Поисковый запрос, например: "рестораны Казань"')
    parser.add_argument(
        "-n", "--max",
        type=int,
        default=100,
        metavar="N",
        help="Максимальное количество компаний (по умолчанию: 100)",
    )
    args = parser.parse_args()

    log.info("Запрос: '%s' | Лимит: %d", args.query, args.max)
    companies = collect(args.query, args.max)

    if not companies:
        log.error("Не удалось собрать ни одной компании. Проверьте запрос или доступность API.")
        sys.exit(1)

    filename = export_excel(companies, args.query)
    print_stats(companies, filename)


if __name__ == "__main__":
    main()
