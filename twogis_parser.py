"""
2GIS Parser — core logic using official 2GIS Catalog API v3.
"""
from __future__ import annotations

import logging
import os
import random
import re
import sys
import time
from collections import OrderedDict
from datetime import date
from urllib.parse import quote

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import MultiCellRange
from openpyxl.worksheet.datavalidation import DataValidation

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
_SEARCH_API  = "https://catalog.api.2gis.com/3.0/items"
_BYID_API    = "https://catalog.api.2gis.com/3.0/items/byid"
_REVIEWS_API = "https://catalog.api.2gis.com/3.0/items/{id}/reviews"

def _get_api_key() -> str:
    return os.environ.get("TWOGIS_API_KEY", "ruMkgx3275")

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent":      _UA,
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer":         "https://2gis.ru/",
})

# ---------------------------------------------------------------------------
# Column definitions (16 fields)
# ---------------------------------------------------------------------------
ALL_FIELD_DEFS: OrderedDict = OrderedDict([
    ("name",        ("Название",         30)),
    ("phone",       ("Телефон",          22)),
    ("site",        ("Сайт",             28)),
    ("social",      ("Соцсети",          30)),
    ("address",     ("Адрес",            40)),
    ("lat",         ("Широта",           14)),
    ("lon",         ("Долгота",          14)),
    ("category",    ("Категория",        25)),
    ("rating",      ("Рейтинг",          10)),
    ("reviews",     ("Кол-во отзывов",   16)),
    ("hours",       ("Часы работы",      30)),
    ("description", ("Описание",         45)),
    ("services",    ("Услуги",           50)),
    ("features",    ("Особенности",      45)),
    ("has_site",    ("Есть сайт",        12)),
    ("map_url",     ("Ссылка на 2ГИС",   36)),
])
DEFAULT_FIELDS = list(ALL_FIELD_DEFS.keys())

# ---------------------------------------------------------------------------
# Social media detection
# ---------------------------------------------------------------------------
_SOCIAL_CONTACT_TYPES = frozenset([
    "vkontakte", "instagram", "facebook", "twitter", "odnoklassniki",
    "youtube", "telegram", "tiktok", "ok", "vk", "whatsapp",
])
_SOCIAL_DOMAINS = (
    "vk.com", "vkontakte.ru", "instagram.com", "t.me", "telegram.me",
    "ok.ru", "facebook.com", "fb.com", "youtube.com", "tiktok.com",
    "twitter.com", "x.com", "whatsapp.com",
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

def _is_social_url(url: str) -> bool:
    return any(d in url for d in _SOCIAL_DOMAINS)

def _dedup_key(c: dict) -> tuple:
    return (c.get("name", "").lower(), c.get("address", "").lower())

def _build_map_url(org_id: str, name: str = "", address: str = "") -> str:
    numeric_id = re.sub(r"[^0-9]", "", org_id)
    if numeric_id:
        return f"https://2gis.ru/firm/{numeric_id}"
    return f"https://2gis.ru/search/?q={quote(name + ' ' + address)}"

# ---------------------------------------------------------------------------
# Contact extraction from contact_groups
# ---------------------------------------------------------------------------
def _extract_contacts(contact_groups: list) -> tuple[str, str, str]:
    """Return (phones_str, site_str, socials_str) from 2GIS contact_groups."""
    phones:  list[str] = []
    site:    str       = ""
    socials: list[str] = []

    for group in (contact_groups or []):
        for contact in (group.get("contacts") or []):
            ctype    = (contact.get("type")    or "").lower()
            csubtype = (contact.get("subtype") or "").lower()
            url      = contact.get("url") or contact.get("href") or ""
            value    = contact.get("value") or contact.get("text") or ""

            if ctype == "phone":
                if value:
                    phones.append(_fmt_phone(value))
                continue

            # Social network contact
            if (ctype in _SOCIAL_CONTACT_TYPES or
                    csubtype in _SOCIAL_CONTACT_TYPES or
                    (url and _is_social_url(url))):
                if url:
                    c = _clean_site(url)
                    if c not in socials:
                        socials.append(c)
                continue

            # Website
            if ctype in ("website", "link") and url and not _is_social_url(url):
                if not site:
                    site = _clean_site(url)

    return (
        ", ".join(phones)  if phones  else "—",
        site               if site    else "—",
        ", ".join(socials) if socials else "—",
    )

# ---------------------------------------------------------------------------
# Schedule parser
# ---------------------------------------------------------------------------
_DAY_MAP = {
    "Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт",
    "Fri": "Пт", "Sat": "Сб", "Sun": "Вс",
}
_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def _parse_schedule(schedule: dict) -> str:
    if not schedule:
        return ""
    if schedule.get("is_24h"):
        return "Круглосуточно"

    # Format 1: {"items": [{"days": {"from": "Mon", "to": "Fri"}, "working_hours": [...]}]}
    items = schedule.get("items")
    if items:
        parts = []
        for item in items:
            days_range = item.get("days") or {}
            d_from = _DAY_MAP.get(days_range.get("from", ""), "")
            d_to   = _DAY_MAP.get(days_range.get("to",   ""), "")
            wh = item.get("working_hours") or []
            h_str = f"{wh[0]['from']}–{wh[0]['to']}" if wh else ""
            if not h_str:
                continue  # closed or no hours — skip
            if d_from and d_to and d_from != d_to:
                parts.append(f"{d_from}-{d_to} {h_str}")
            elif d_from:
                parts.append(f"{d_from} {h_str}")
        return ", ".join(parts)

    # Format 2: {"Mon": {"working_hours": [...]}, "Tue": {...}, ...}
    parts: list[str] = []
    for day_key in _DAY_ORDER:
        day_data = schedule.get(day_key)
        if not isinstance(day_data, dict):
            continue
        wh = day_data.get("working_hours") or []
        h_str = f"{wh[0]['from']}–{wh[0]['to']}" if wh else ""
        if h_str:
            parts.append(f"{_DAY_MAP[day_key]} {h_str}")
    return ", ".join(parts)

# ---------------------------------------------------------------------------
# Attribute groups → features string
# ---------------------------------------------------------------------------
def _extract_attribute_features(attr_groups: list) -> str:
    feats: list[str] = []
    _skip_values = {"нет", "no", "false", "0", "недоступно", "unavailable", ""}
    for group in (attr_groups or []):
        for attr in (group.get("attributes") or []):
            name = (attr.get("name") or "").strip()
            val  = str(attr.get("value") or "").strip().lower()
            if not name or val in _skip_values:
                continue
            feats.append(name)
    return ", ".join(feats[:25])

# ---------------------------------------------------------------------------
# Search API
# ---------------------------------------------------------------------------
class _ApiError(Exception):
    """Raised when the API returns an unrecoverable error (e.g. 401, 403)."""
    def __init__(self, status_code: int, msg: str):
        super().__init__(msg)
        self.status_code = status_code


def _search_page(query: str, page: int) -> list[dict]:
    params = {
        "q":         query,
        "page":      page,
        "page_size": 10,   # API v3 max is 10
        "fields":    ("items.point,items.contact_groups,items.rubrics,"
                      "items.reviews,items.photos,items.name_ex"),
        "key":       _get_api_key(),
        "locale":    "ru_RU",
    }
    try:
        resp = SESSION.get(_SEARCH_API, params=params, timeout=15)
        if resp.status_code in (401, 403):
            raise _ApiError(
                resp.status_code,
                f"2ГИС API вернул {resp.status_code}. "
                f"Проверьте API-ключ (TWOGIS_API_KEY) или зарегистрируйтесь на dev.2gis.ru"
            )
        if not resp.ok:
            log.warning("2GIS search page=%d HTTP %d: %s", page, resp.status_code, resp.text[:300])
            raise _ApiError(resp.status_code,
                            f"2ГИС API вернул {resp.status_code} (страница {page})")
        data  = resp.json()
        items = data.get("result", {}).get("items") or data.get("items") or []
        log.debug("2GIS search page=%d total=%s items=%d",
                  page, data.get("result", {}).get("total", "?"), len(items))
        return items
    except _ApiError:
        raise
    except Exception as exc:
        log.warning("2GIS search page=%d exception: %s", page, exc)
        return []

# ---------------------------------------------------------------------------
# Parse one API item → company dict
# ---------------------------------------------------------------------------
def _parse_item(item: dict) -> dict:
    org_id  = str(item.get("id", ""))
    name    = (item.get("name")
               or (item.get("name_ex") or {}).get("primary")
               or "—")
    address = item.get("address_name") or (item.get("address") or {}).get("name", "—") or "—"

    point = item.get("point") or {}
    lat   = round(float(point["lat"]), 6) if point.get("lat") else "—"
    lon   = round(float(point["lon"]), 6) if point.get("lon") else "—"

    rubrics  = item.get("rubrics") or []
    category = rubrics[0].get("name", "—") if rubrics else "—"

    reviews_obj = item.get("reviews") or {}
    try:
        # API v3 uses general_rating / org_rating; older responses used rating
        raw_rating = (reviews_obj.get("general_rating")
                      or reviews_obj.get("org_rating")
                      or reviews_obj.get("rating")
                      or 0)
        rating = round(float(raw_rating), 1)
        rating = rating if rating > 0 else "—"
    except (TypeError, ValueError):
        rating = "—"
    try:
        # API v3 uses general_review_count; older responses used count
        n_reviews = int(
            reviews_obj.get("general_review_count")
            or reviews_obj.get("org_review_count")
            or reviews_obj.get("count")
            or 0
        )
    except (TypeError, ValueError):
        n_reviews = 0

    phone_str, site, social = _extract_contacts(item.get("contact_groups") or [])

    # Services: all rubric names joined
    all_rubrics = item.get("rubric_list") or rubrics
    services = ", ".join(r.get("name", "") for r in all_rubrics if r.get("name"))

    map_url = _build_map_url(org_id, name, address)

    return {
        "id":          org_id,
        "name":        name,
        "phone":       phone_str,
        "site":        site,
        "social":      social,
        "address":     address,
        "lat":         lat,
        "lon":         lon,
        "category":    category,
        "rating":      rating,
        "reviews":     n_reviews,
        "hours":       "",
        "description": "",
        "services":    services,
        "features":    "",
        "has_site":    "Да" if site != "—" else "Нет",
        "map_url":     map_url,
    }

# ---------------------------------------------------------------------------
# Enrich via byid
# ---------------------------------------------------------------------------
def _enrich_byid(company: dict) -> dict:
    org_id = company.get("id", "")
    if not org_id:
        return company
    if company.get("hours") and company.get("description") and company.get("features"):
        return company

    params = {
        "id":     org_id,
        "fields": ("items.schedule,items.description_short,"
                   "items.rubric_list,items.attribute_groups,items.photos,"
                   "items.contact_groups"),
        "key":    _get_api_key(),
        "locale": "ru_RU",
    }
    try:
        resp = SESSION.get(_BYID_API, params=params, timeout=15)
        if resp.status_code not in (401, 403):
            resp.raise_for_status()
        data  = resp.json()
        items = data.get("result", {}).get("items") or data.get("items") or []
        if not items:
            return company
        detail = items[0]
    except Exception as exc:
        log.debug("byid %s: %s", org_id, exc)
        return company

    # Fill contacts from byid if search didn't return them
    if company.get("phone") == "—" or company.get("site") == "—":
        cg = detail.get("contact_groups") or []
        if cg:
            phone_str, site, social = _extract_contacts(cg)
            if company.get("phone") == "—" and phone_str != "—":
                company["phone"] = phone_str
            if company.get("site") == "—" and site != "—":
                company["site"]     = site
                company["has_site"] = "Да"
            if company.get("social") == "—" and social != "—":
                company["social"] = social

    if not company.get("hours"):
        schedule = detail.get("schedule")
        if schedule:
            company["hours"] = _parse_schedule(schedule)

    if not company.get("description"):
        desc = detail.get("description_short") or ""
        company["description"] = str(desc)[:200]

    if not company.get("features"):
        company["features"] = _extract_attribute_features(
            detail.get("attribute_groups") or [])

    # Always prefer byid rubric_list (more detailed than search rubrics)
    rubric_list = detail.get("rubric_list") or []
    if rubric_list:
        company["services"] = ", ".join(
            r.get("name", "") for r in rubric_list if r.get("name"))

    return company


# ---------------------------------------------------------------------------
# Browser-based contact extraction (fallback when API lacks contact_groups)
# ---------------------------------------------------------------------------
_SKIP_DOMAINS = frozenset([
    "2gis.ru", "flamp.ru", "api.", "disk.", "yandex.", "google.",
    "apple.", "maps.", "schema.org", "w3.org",
])

def _extract_contacts_html(html: str) -> tuple[str, str, str]:
    """Extract phone/site/social from a rendered 2GIS org page."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return "—", "—", "—"

    soup   = BeautifulSoup(html, "html.parser")
    phones: list[str] = []
    sites:  list[str] = []
    socials: list[str] = []

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if href.startswith("tel:"):
            raw = href[4:].strip()
            if raw:
                fmt = _fmt_phone(raw)
                if fmt not in phones:
                    phones.append(fmt)
        elif href.startswith(("http://", "https://")):
            if any(d in href for d in _SKIP_DOMAINS):
                continue
            if _is_social_url(href):
                c = _clean_site(href)
                if c not in socials:
                    socials.append(c)
            else:
                c = _clean_site(href)
                if c not in sites:
                    sites.append(c)

    return (
        ", ".join(phones[:3])  if phones  else "—",
        sites[0]               if sites   else "—",
        ", ".join(socials[:5]) if socials else "—",
    )


# ---------------------------------------------------------------------------
# Main collect()
# ---------------------------------------------------------------------------
def collect(query: str, max_companies: int = 100,
            emit=None, filter_fn=None) -> list[dict]:
    def _emit(event, **kw):
        if emit:
            emit(event, **kw)

    # Start browser session for contact enrichment (API free tier lacks contacts)
    try:
        from browser_fetch import BrowserSession
        bs = BrowserSession()
        bs.start()
        if bs.available:
            log.debug("BrowserSession ready for contact enrichment")
    except Exception:
        bs = None

    hard_cap  = min(max_companies * 8, 1000) if filter_fn else max_companies
    all_seen:    set[tuple] = set()
    all_fetched: list[dict] = []
    passed:      list[dict] = []
    streak = 0

    def _target_reached():
        return (len(passed) >= max_companies if filter_fn
                else len(all_fetched) >= max_companies)

    try:
        for page in range(1, 150):  # 10 per page → up to 1500 results
            if _target_reached() or len(all_fetched) >= hard_cap:
                break

            _emit("progress",
                  found=len(passed) if filter_fn else len(all_fetched),
                  total=max_companies,
                  message=f"Загружаем страницу {page}…"
                          + (f" (проверено: {len(all_fetched)})" if filter_fn else ""))

            try:
                items = _search_page(query, page)
            except _ApiError as exc:
                _emit("error", message=str(exc))
                return []
            except Exception as exc:
                _emit("error", message=f"2ГИС: ошибка запроса страницы {page}: {exc}")
                return []
            if not items:
                streak += 1
                _emit("progress",
                      found=len(passed) if filter_fn else len(all_fetched),
                      total=max_companies,
                      message=f"Страница {page}: нет результатов (попытка {streak}/3)…")
                if streak >= 3:
                    break
                _sleep(0.8, 2.0)
                continue
            streak = 0

            for item in items:
                c   = _parse_item(item)
                key = _dedup_key(c)
                if key in all_seen:
                    continue
                all_seen.add(key)

                _emit("progress",
                      found=len(passed) if filter_fn else len(all_fetched),
                      total=max_companies,
                      message=f"Обогащаем: {c['name'][:40]}…")

                c = _enrich_byid(c)

                # Browser fallback for contacts (free API tier omits contact_groups)
                needs_contacts = c.get("phone") == "—" or c.get("site") == "—"
                if needs_contacts and bs and bs.available:
                    map_url = c.get("map_url", "")
                    if map_url:
                        html = bs.fetch(map_url, scroll_px=300, wait_ms=2_000,
                                        timeout_ms=15_000)
                        if html:
                            ph, st, sc = _extract_contacts_html(html)
                            if c.get("phone") == "—" and ph != "—":
                                c["phone"] = ph
                            if c.get("site") == "—" and st != "—":
                                c["site"]     = st
                                c["has_site"] = "Да"
                            if c.get("social") == "—" and sc != "—":
                                c["social"] = sc

                _sleep(0.4, 1.0)
                all_fetched.append(c)

                if filter_fn and filter_fn(c):
                    passed.append(c)

                if _target_reached() or len(all_fetched) >= hard_cap:
                    break

            _sleep(0.6, 1.5)

    finally:
        if bs:
            bs.stop()

    return passed if filter_fn else all_fetched

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def apply_filters(companies: list, no_site=False, no_social=False, no_phone=False) -> list:
    if no_site:   companies = [c for c in companies if c["has_site"] == "Нет"]
    if no_social: companies = [c for c in companies if c["social"]   == "—"]
    if no_phone:  companies = [c for c in companies if c["phone"]    == "—"]
    return companies

# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------
HEADER_FILL = PatternFill("solid", fgColor="D9EAD3")
HEADER_FONT = Font(bold=True)


def _build_columns(selected: list[str]) -> list[tuple]:
    return [(key, ALL_FIELD_DEFS[key][0], ALL_FIELD_DEFS[key][1])
            for key in selected if key in ALL_FIELD_DEFS]


def export_excel(companies: list, query: str,
                 selected_fields: list[str] | None = None) -> str:
    cols = _build_columns(selected_fields or DEFAULT_FIELDS)
    wb   = Workbook()

    def _write_sheet(ws, rows):
        for ci, (_, header, _) in enumerate(cols, 1):
            cell = ws.cell(1, ci, header)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for ri, c in enumerate(rows, 2):
            for ci, (key, _, _) in enumerate(cols, 1):
                val = c.get(key)
                if key in ("lat", "lon")  and not isinstance(val, float): val = None
                if key == "rating"        and not isinstance(val, float): val = None
                cell = ws.cell(ri, ci, val)
                if key in ("lat", "lon") and val is not None: cell.number_format = "0.000000"
                elif key == "rating"     and val is not None: cell.number_format = "0.0"
                elif key == "reviews":                        cell.number_format = "0"
        for ci, (_, _, width) in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(ci)].width = width
        n = len(rows)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{n + 1}"
        if n > 0:
            for ci, (key, _, _) in enumerate(cols, 1):
                if key == "has_site":
                    dv = DataValidation(type="list", formula1='"Да,Нет"', allow_blank=False)
                    dv.sqref = MultiCellRange(
                        f"{get_column_letter(ci)}2:{get_column_letter(ci)}{n + 1}")
                    ws.add_data_validation(dv)
                    break

    ws1 = wb.active
    ws1.title = "Компании"
    _write_sheet(ws1, companies)
    ws2 = wb.create_sheet("Без сайта")
    _write_sheet(ws2, [c for c in companies if c.get("has_site") == "Нет"])

    safe     = re.sub(r"[^\w\-а-яА-Я]", "_", query)[:40]
    filename = f"2gis_{safe}_{date.today()}.xlsx"
    wb.save(filename)
    return filename


def print_stats(companies: list, filename: str):
    total      = len(companies)
    with_site  = sum(1 for c in companies if c["has_site"] == "Да")
    with_phone = sum(1 for c in companies if c["phone"]    != "—")
    ratings    = [c["rating"] for c in companies if isinstance(c["rating"], float)]
    avg        = round(sum(ratings) / len(ratings), 1) if ratings else 0.0
    pct        = lambda n: f"{round(n / total * 100)}%" if total else "0%"
    print(f"\n{'='*46}")
    print(f"  Файл: {filename}")
    print(f"{'='*46}")
    print(f"  Всего          : {total}")
    print(f"  С сайтом       : {with_site} ({pct(with_site)})")
    print(f"  Без сайта      : {total - with_site} ({pct(total - with_site)})")
    print(f"  С телефоном    : {with_phone} ({pct(with_phone)})")
    print(f"  Средний рейтинг: {avg}")
    print(f"{'='*46}")
