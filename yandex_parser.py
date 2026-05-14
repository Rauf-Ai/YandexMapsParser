"""
Yandex Maps Parser — core logic
"""

import argparse
import json
import logging
import random
import re
import sys
import time
from collections import OrderedDict
from datetime import date
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import MultiCellRange
from openpyxl.worksheet.datavalidation import DataValidation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://yandex.ru/maps/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ---------------------------------------------------------------------------
# Column definitions  (key → (header, width))
# ---------------------------------------------------------------------------
ALL_FIELD_DEFS: OrderedDict = OrderedDict([
    ("name",     ("Название",        30)),
    ("phone",    ("Телефон",         22)),
    ("site",     ("Сайт",            28)),
    ("social",   ("Социальные сети", 30)),
    ("address",  ("Адрес",           40)),
    ("lat",      ("Широта",          14)),
    ("lon",      ("Долгота",         14)),
    ("category", ("Категория",       25)),
    ("rating",   ("Рейтинг",         10)),
    ("reviews",  ("Кол-во отзывов",  16)),
    ("has_site", ("Есть сайт",       12)),
    ("map_url",  ("Ссылка на карты", 36)),
])

DEFAULT_FIELDS = list(ALL_FIELD_DEFS.keys())

# Legacy alias expected by app.py
COLUMNS = [(v[0], v[1]) for v in ALL_FIELD_DEFS.values()]

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
SEARCH_API     = "https://search-maps.yandex.ru/v1/"
SEARCH_API_KEY = "dda3ddba-c9ea-4ead-9010-f43fbc15c6e3"

SOCIAL_DOMAINS = (
    "vk.com", "vkontakte.ru",
    "instagram.com",
    "t.me", "telegram.me", "telegram.org",
    "ok.ru",
    "facebook.com", "fb.com",
    "youtube.com",
    "tiktok.com",
    "twitter.com", "x.com",
    "whatsapp.com",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sleep(lo=0.8, hi=2.0):
    time.sleep(random.uniform(lo, hi))

def _fmt_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return raw.strip()

def _clean_site(url: str) -> str:
    url = re.sub(r"^https?://", "", url.strip())
    return url.rstrip("/")

def _is_social(url: str) -> bool:
    return any(d in url for d in SOCIAL_DOMAINS)

def _dedup_key(c: dict) -> tuple:
    return (c.get("name", "").lower(), c.get("address", "").lower())

def _extract_oid(uri: str) -> str:
    m = re.search(r"oid=(\d+)", uri or "")
    return m.group(1) if m else ""

# ---------------------------------------------------------------------------
# Step 1 — Search API
# ---------------------------------------------------------------------------
def _search_page(query: str, skip: int) -> list[dict]:
    params = {
        "apikey": SEARCH_API_KEY,
        "text": query,
        "lang": "ru_RU",
        "type": "biz",
        "results": 10,
        "skip": skip,
    }
    try:
        resp = SESSION.get(SEARCH_API, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("features", [])
    except Exception as exc:
        log.warning("Search API skip=%d: %s", skip, exc)
        return []

def _parse_feature(feat: dict) -> dict:
    props = feat.get("properties", {})
    geo   = feat.get("geometry", {})

    name = props.get("name", "—")
    name = re.sub(r'^["\'«»]+|["\'»«]+$', "", name).strip() or "—"

    coords = geo.get("coordinates", [])
    lon = round(coords[0], 6) if len(coords) > 0 and coords[0] is not None else "—"
    lat = round(coords[1], 6) if len(coords) > 1 and coords[1] is not None else "—"

    meta     = props.get("CompanyMetaData", {})
    address  = meta.get("address", "—")
    cats     = meta.get("Categories", [])
    category = cats[0].get("name", "—") if cats else "—"
    oid      = _extract_oid(props.get("uri", "")) or str(meta.get("id", ""))

    phones = [_fmt_phone(p["formatted"]) for p in meta.get("Phones", [])
              if p.get("formatted")]
    phone_str = ", ".join(phones) if phones else "—"

    # Rating — try several known API field paths
    rating, reviews = "—", 0
    for path in [
        lambda m: m.get("rating") or {},
        lambda m: m.get("Siren", {}).get("Reviews", {}),
    ]:
        obj = path(meta)
        r_val = (obj.get("ratings") or obj.get("score")
                 or obj.get("value") or obj.get("rating"))
        rv_val = obj.get("reviews") or obj.get("count") or 0
        if r_val:
            try:
                rating  = round(float(r_val), 1)
                reviews = int(rv_val) if rv_val else 0
                break
            except (TypeError, ValueError):
                pass

    # Also check top-level properties
    if rating == "—":
        top_r = props.get("rating") or {}
        r_val = top_r.get("ratings") or top_r.get("score")
        if r_val:
            try:
                rating  = round(float(r_val), 1)
                reviews = int(top_r.get("reviews") or top_r.get("count") or 0)
            except (TypeError, ValueError):
                pass

    # Website & socials
    site    = meta.get("url", "") or ""
    socials : list[str] = []
    for link in meta.get("Links", []):
        href = (link.get("href") or link.get("url") or "").strip()
        if not href:
            continue
        if _is_social(href):
            c = _clean_site(href)
            if c not in socials:
                socials.append(c)
        elif not site:
            site = href

    site = _clean_site(site) if site else "—"

    map_url = (f"https://yandex.ru/maps/org/{oid}/" if oid
               else f"https://yandex.ru/maps/?text={quote(name + ' ' + address)}")

    return {
        "oid":      oid,
        "name":     name,
        "phone":    phone_str,
        "site":     site,
        "social":   ", ".join(socials) if socials else "—",
        "address":  address,
        "lat":      lat,
        "lon":      lon,
        "category": category,
        "rating":   rating,
        "reviews":  reviews,
        "has_site": "Да" if site != "—" else "Нет",
        "map_url":  map_url,
    }

# ---------------------------------------------------------------------------
# Step 2 — Per-org enrichment (JSON-LD + itemprop + HTML fallback)
# ---------------------------------------------------------------------------
def _enrich(company: dict) -> dict:
    oid = company.get("oid", "")
    if not oid:
        return company

    ns  = company["site"]    == "—"
    nso = company["social"]  == "—"
    nr  = company["rating"]  == "—"
    nrv = company["reviews"] == 0

    if not any([ns, nso, nr, nrv]):
        return company

    try:
        resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/", timeout=15)
        if resp.status_code != 200:
            return company
        html = resp.text
    except Exception as exc:
        log.debug("Enrich %s: %s", oid, exc)
        return company

    soup = BeautifulSoup(html, "html.parser")

    # ── 1. JSON-LD  (most reliable — served for SEO) ──────────────────────
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, AttributeError):
            continue
        _apply_jsonld(company, data)
        ns  = company["site"]    == "—"
        nso = company["social"]  == "—"
        nr  = company["rating"]  == "—"
        nrv = company["reviews"] == 0

    # ── 2. itemprop / schema.org attributes ───────────────────────────────
    if nr:
        el = soup.find(itemprop="ratingValue")
        if el:
            try:
                company["rating"] = round(float(
                    (el.get("content") or el.get_text()).replace(",", ".")), 1)
                nr = False
            except ValueError:
                pass

    if nrv:
        el = (soup.find(itemprop="reviewCount") or
              soup.find(itemprop="ratingCount"))
        if el:
            try:
                v = int(re.sub(r"\D", "",
                               el.get("content") or el.get_text()))
                if v > 0:
                    company["reviews"] = v
                    nrv = False
            except ValueError:
                pass

    if ns:
        el = soup.find(itemprop="url")
        if el:
            href = el.get("href", "") or el.get("content", "")
            if href and not _is_social(href) and "yandex" not in href:
                company["site"]     = _clean_site(href)
                company["has_site"] = "Да"
                ns = False

    # ── 3. All <a href> for socials ───────────────────────────────────────
    if nso:
        found: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if _is_social(href):
                c = _clean_site(href)
                if c not in found:
                    found.append(c)
        if found:
            company["social"] = ", ".join(found)
            nso = False

    # ── 4. CSS class selectors as last resort ─────────────────────────────
    if nr:
        for sel in [".business-rating-badge-view__rating",
                    "[class*='rating__value']", ".business-rating__value"]:
            el = soup.select_one(sel)
            if el:
                try:
                    company["rating"] = round(float(
                        el.get_text(strip=True).replace(",", ".")), 1)
                    break
                except ValueError:
                    pass

    if nrv:
        for sel in [".business-rating-badge-view__count",
                    "[class*='rating__count']"]:
            el = soup.select_one(sel)
            if el:
                try:
                    v = int(re.sub(r"\D", "", el.get_text()))
                    if v > 0:
                        company["reviews"] = v
                        break
                except ValueError:
                    pass

    return company


def _apply_jsonld(company: dict, data):
    """Recursively apply JSON-LD data to company dict."""
    if isinstance(data, list):
        for item in data:
            _apply_jsonld(company, item)
        return
    if not isinstance(data, dict):
        return

    # rating
    ar = data.get("aggregateRating") or {}
    if isinstance(ar, dict):
        rv = ar.get("ratingValue")
        rc = ar.get("reviewCount") or ar.get("ratingCount")
        if rv and company["rating"] == "—":
            try:
                company["rating"] = round(float(str(rv).replace(",", ".")), 1)
            except ValueError:
                pass
        if rc and company["reviews"] == 0:
            try:
                v = int(re.sub(r"\D", "", str(rc)))
                if v > 0:
                    company["reviews"] = v
            except ValueError:
                pass

    # url
    if company["site"] == "—":
        url = data.get("url") or data.get("website") or ""
        if (isinstance(url, str) and url.startswith("http")
                and not _is_social(url) and "yandex" not in url):
            company["site"]     = _clean_site(url)
            company["has_site"] = "Да"

    # sameAs → socials
    same = data.get("sameAs") or []
    if isinstance(same, str):
        same = [same]
    new_s: list[str] = []
    for link in same:
        if isinstance(link, str) and _is_social(link):
            c = _clean_site(link)
            if c not in new_s:
                new_s.append(c)
    if new_s:
        existing = company["social"]
        if existing == "—":
            company["social"] = ", ".join(new_s)
        else:
            ex_list = existing.split(", ")
            for s in new_s:
                if s not in ex_list:
                    ex_list.append(s)
            company["social"] = ", ".join(ex_list)

    # recurse into nested @graph
    for v in data.values():
        if isinstance(v, (dict, list)):
            _apply_jsonld(company, v)

# ---------------------------------------------------------------------------
# HTML fallback scraper
# ---------------------------------------------------------------------------
def _scrape_page(query: str, page: int) -> list[dict]:
    url = f"https://yandex.ru/maps/?text={quote(query)}&page={page}"
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("HTML page %d: %s", page, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    companies = []
    for item in soup.select("li.search-list-item"):
        def txt(sel):
            el = item.select_one(sel)
            return el.get_text(strip=True) if el else "—"

        name     = txt(".search-business-snippet-view__title")
        address  = txt(".search-business-snippet-view__address")
        category = txt(".search-business-snippet-view__category")

        try:
            rating = round(float(
                txt(".business-rating-badge-view__rating").replace(",", ".")), 1)
        except ValueError:
            rating = "—"
        try:
            reviews = int(re.sub(r"\D", "",
                                 txt(".business-rating-badge-view__count")))
        except ValueError:
            reviews = 0

        oid = ""
        for attr in ("data-permalink", "data-uri"):
            m = re.search(r"(\d{10,})", item.get(attr, ""))
            if m:
                oid = m.group(1)
                break

        map_url = (f"https://yandex.ru/maps/org/{oid}/" if oid
                   else f"https://yandex.ru/maps/?text={quote(name + ' ' + address)}")

        companies.append({
            "oid": oid, "name": name, "phone": "—",
            "site": "—", "social": "—", "address": address,
            "lat": "—", "lon": "—", "category": category,
            "rating": rating, "reviews": reviews,
            "has_site": "Нет", "map_url": map_url,
        })
    return companies

# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------
def collect(query: str, max_companies: int = 100,
            emit=None) -> list[dict]:
    def _emit(event, **kw):
        if emit:
            emit(event, **kw)

    companies: list[dict] = []
    seen: set[tuple]      = set()
    skip, page, streak    = 0, 1, 0

    while len(companies) < max_companies:
        _emit("progress", found=len(companies), total=max_companies,
              message=f"Поиск результатов {skip + 1}–{skip + 10}…")

        feats = _search_page(query, skip)
        batch = [_parse_feature(f) for f in feats]

        if not batch:
            _emit("progress", found=len(companies), total=max_companies,
                  message=f"API пустой, HTML fallback (стр. {page})…")
            batch = _scrape_page(query, page)

        if not batch:
            streak += 1
            if streak >= 3:
                _emit("progress", found=len(companies), total=max_companies,
                      message="Результаты закончились.")
                break
        else:
            streak = 0

        for c in batch:
            key = _dedup_key(c)
            if key in seen:
                continue
            seen.add(key)
            _emit("progress", found=len(companies), total=max_companies,
                  message=f"Обогащаем: {c['name'][:40]}…")
            c = _enrich(c)
            _sleep(0.4, 1.2)
            companies.append(c)
            if len(companies) >= max_companies:
                break

        skip += 10
        page += 1
        _sleep(0.8, 2.0)

    return companies

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def apply_filters(companies: list[dict],
                  no_site=False, no_social=False, no_phone=False) -> list[dict]:
    result = companies
    if no_site:
        result = [c for c in result if c["has_site"] == "Нет"]
    if no_social:
        result = [c for c in result if c["social"] == "—"]
    if no_phone:
        result = [c for c in result if c["phone"] == "—"]
    return result

# ---------------------------------------------------------------------------
# Excel export  (dynamic columns)
# ---------------------------------------------------------------------------
HEADER_FILL = PatternFill("solid", fgColor="D9EAD3")
HEADER_FONT = Font(bold=True)


def _build_columns(selected: list[str]) -> list[tuple]:
    """Return [(key, header, width), ...] for the selected fields."""
    cols = []
    for key in selected:
        if key in ALL_FIELD_DEFS:
            header, width = ALL_FIELD_DEFS[key]
            cols.append((key, header, width))
    return cols


def export_excel(companies: list[dict], query: str,
                 selected_fields: list[str] | None = None) -> str:
    cols = _build_columns(selected_fields or DEFAULT_FIELDS)
    wb = Workbook()

    def _write_sheet(ws, rows):
        # Header
        for ci, (_, header, _) in enumerate(cols, 1):
            cell = ws.cell(1, ci, header)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
        # Rows
        for ri, c in enumerate(rows, 2):
            for ci, (key, _, _) in enumerate(cols, 1):
                val = c.get(key)
                if key in ("lat", "lon") and not isinstance(val, float):
                    val = None
                if key == "rating" and not isinstance(val, float):
                    val = None
                cell = ws.cell(ri, ci, val)
                if key in ("lat", "lon") and val is not None:
                    cell.number_format = "0.000000"
                elif key == "rating" and val is not None:
                    cell.number_format = "0.0"
                elif key == "reviews":
                    cell.number_format = "0"
        # Widths
        for ci, (_, _, width) in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(ci)].width = width
        # Autofilter
        n = len(rows)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{n + 1}"
        # Dropdown for "has_site" column if present
        if n > 0:
            for ci, (key, _, _) in enumerate(cols, 1):
                if key == "has_site":
                    dv = DataValidation(
                        type="list", formula1='"Да,Нет"', allow_blank=False)
                    dv.sqref = MultiCellRange(f"{get_column_letter(ci)}2:"
                                              f"{get_column_letter(ci)}{n + 1}")
                    ws.add_data_validation(dv)
                    break

    ws1 = wb.active
    ws1.title = "Компании"
    _write_sheet(ws1, companies)

    ws2 = wb.create_sheet("Без сайта")
    _write_sheet(ws2, [c for c in companies if c.get("has_site") == "Нет"])

    safe = re.sub(r"[^\w\-а-яА-Я]", "_", query)[:40]
    filename = f"yandex_maps_{safe}_{date.today()}.xlsx"
    wb.save(filename)
    return filename

# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def print_stats(companies: list[dict], filename: str):
    total      = len(companies)
    with_site  = sum(1 for c in companies if c["has_site"] == "Да")
    with_phone = sum(1 for c in companies if c["phone"]    != "—")
    ratings    = [c["rating"] for c in companies if isinstance(c["rating"], float)]
    avg        = round(sum(ratings) / len(ratings), 1) if ratings else 0.0
    pct        = lambda n: f"{round(n / total * 100)}%" if total else "0%"

    print()
    print("=" * 46)
    print(f"  Файл: {filename}")
    print("=" * 46)
    print(f"  Всего          : {total}")
    print(f"  С сайтом       : {with_site} ({pct(with_site)})")
    print(f"  Без сайта      : {total - with_site} ({pct(total - with_site)})")
    print(f"  С телефоном    : {with_phone} ({pct(with_phone)})")
    print(f"  Средний рейтинг: {avg}")
    print("=" * 46)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Парсер Яндекс Карт → Excel")
    ap.add_argument("query")
    ap.add_argument("-n", "--max", type=int, default=100, metavar="N")
    args = ap.parse_args()

    log.info("Запрос: «%s» | Лимит: %d", args.query, args.max)
    companies = collect(args.query, args.max)

    if not companies:
        log.error("Ничего не найдено.")
        sys.exit(1)

    filename = export_excel(companies, args.query)
    print_stats(companies, filename)


if __name__ == "__main__":
    main()
