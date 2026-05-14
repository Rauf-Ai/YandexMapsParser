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
    ("name",        ("Название",        30)),
    ("phone",       ("Телефон",         22)),
    ("site",        ("Сайт",            28)),
    ("social",      ("Социальные сети", 30)),
    ("address",     ("Адрес",           40)),
    ("lat",         ("Широта",          14)),
    ("lon",         ("Долгота",         14)),
    ("category",    ("Категория",       25)),
    ("rating",      ("Рейтинг",         10)),
    ("reviews",     ("Кол-во отзывов",  16)),
    ("has_site",    ("Есть сайт",       12)),
    ("map_url",     ("Ссылка на карты", 36)),
    ("services",    ("Товары и услуги", 50)),
    ("features",    ("Особенности",     45)),
    ("price_range", ("Цены",            22)),
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

# Яндексовские соцсети и навигационные ссылки, которые попадают на каждую страницу
SOCIAL_BLACKLIST = frozenset([
    "vk.com/yandex.maps", "vk.com/yandexmaps", "vk.com/yandex",
    "t.me/mapsyandex", "t.me/yandex", "t.me/yandexmaps",
    "instagram.com/yandex", "ok.ru/yandex",
    "facebook.com/yandex", "youtube.com/yandex",
    "twitter.com/yandex", "x.com/yandex",
])

# JSON-LD @type значения, которые соответствуют бизнесу
_BUSINESS_TYPES = frozenset([
    "LocalBusiness", "MedicalBusiness", "HealthAndBeautyBusiness",
    "FoodEstablishment", "Restaurant", "CafeOrCoffeeShop", "BarOrPub",
    "Store", "AutoRepair", "Hotel", "Lodging", "BeautySalon",
    "Dentist", "MedicalOrganization", "HealthClub", "SportsActivityLocation",
    "TouristAttraction", "Organization",
])

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

def _is_valid_social(href: str) -> bool:
    """True только для настоящих соцсетей бизнеса, не Яндексовских страниц."""
    if not href:
        return False
    if "yandex" in href.lower():   # убираем все ссылки на yandex.*
        return False
    if not _is_social(href):
        return False
    cleaned = _clean_site(href).split("?")[0].rstrip("/")
    # Убираем известные Яндексовские аккаунты (footer каждой страницы)
    return not any(
        cleaned == b or cleaned.startswith(b + "/")
        for b in SOCIAL_BLACKLIST
    )

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
        if _is_valid_social(href):          # строгая проверка — без мусора
            c = _clean_site(href)
            if c not in socials:
                socials.append(c)
        elif not site and "yandex" not in href:
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
        "services":    "",
        "features":    "",
        "price_range": "",
    }

# ---------------------------------------------------------------------------
# Step 2 — Per-org enrichment (JSON-LD + itemprop + HTML fallback)
# ---------------------------------------------------------------------------
def _enrich(company: dict) -> dict:
    oid = company.get("oid", "")
    if not oid:
        return company

    ns  = company["site"]        == "—"
    nso = company["social"]      == "—"
    nr  = company["rating"]      == "—"
    nrv = company["reviews"]     == 0
    nsv = not company.get("services")
    nft = not company.get("features")
    no_pr = not company.get("price_range")

    if not any([ns, nso, nr, nrv, nsv, nft, no_pr]):
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

    # ── 3. Соцсети: сначала ищем в блоке контактов, потом по всей странице ──
    if nso:
        found: list[str] = []
        # Приоритет: специфичные блоки для контактов бизнеса
        contact_sections = (
            soup.select(".business-contacts__social a") or
            soup.select(".business-contacts a") or
            soup.select("[class*='contacts'] a") or
            soup.select("[class*='link-source'] a") or
            []
        )
        for a in contact_sections:
            href = a.get("href", "").strip()
            if _is_valid_social(href):
                c = _clean_site(href)
                if c not in found:
                    found.append(c)

        # Если контактный блок пустой — сканируем всю страницу, но строго
        if not found:
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if _is_valid_social(href):     # строгая проверка без мусора
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

    # ── 5. Товары и услуги ─────────────────────────────────────────────
    # NOTE: Yandex Maps renders services via React; static CSS selectors may
    # be empty — rely primarily on JSON-LD hasOfferCatalog (in _apply_jsonld).
    _SVC_BLACKLIST = {
        "обзор", "фото", "отзывы", "условия", "информация",
        "товары и услуги", "услуги", "контакты",
        "колл-центр", "позвонить", "написать", "маршрут",
        "поделиться", "записаться", "забронировать", "заказать",
    }
    if not company.get("services"):
        for sel in [
            ".business-services-item-view__name",
            "[class*='services-item-view'] [class*='name']",
            ".card-feature-view__title",
        ]:
            items = [el.get_text(strip=True) for el in soup.select(sel)
                     if el.get_text(strip=True) and len(el.get_text(strip=True)) < 100]
            items = [x for x in items if x.lower() not in _SVC_BLACKLIST]
            if items:
                company["services"] = ", ".join(items[:25])
                break

    # ── 6. Особенности ────────────────────────────────────────────────
    # Only use narrow, proven selectors.  The broad [class*='business-feature']
    # span selector matched price tables, category chips, and value spans
    # ("доступно") from the accessibility block — removed entirely.
    _FTR_VALUE_WORDS = {"доступно", "недоступно", "да", "нет", "yes", "no", "true", "false"}
    if not company.get("features"):
        for sel in [
            ".business-features-view__feature",
            "[class*='features-view__feature']",
        ]:
            items = []
            for el in soup.select(sel):
                t = el.get_text(strip=True)
                if not t:
                    continue
                if "₽" in t or "руб" in t.lower():   # price item
                    continue
                if t.lower() in _FTR_VALUE_WORDS:      # bare value word
                    continue
                if len(t) > 70:                         # too long
                    continue
                items.append(t)
            if items:
                company["features"] = ", ".join(items[:25])
                break

    # ── 7. Цены ───────────────────────────────────────────────────────
    if not company.get("price_range"):
        for sel in [
            ".business-prices-view__text",
            "[class*='price-view__text']",
            "[itemprop='priceRange']",
            "[class*='business-price'] [class*='value']",
        ]:
            el = soup.select_one(sel)
            if el:
                txt = el.get_text(strip=True)
                if txt and len(txt) < 60:
                    company["price_range"] = txt
                    break

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

    # Определяем тип сущности — обрабатываем sameAs только для бизнесов
    entity_type = data.get("@type", "")
    if isinstance(entity_type, list):
        entity_type = " ".join(entity_type)
    is_business = any(t in entity_type for t in _BUSINESS_TYPES)
    is_website  = entity_type in ("WebSite", "WebPage")

    # url — только если это не Яндекс и не WebSite сам по себе
    if not is_website and company["site"] == "—":
        url = data.get("url") or data.get("website") or ""
        if (isinstance(url, str) and url.startswith("http")
                and not _is_valid_social(url) and "yandex" not in url):
            company["site"]     = _clean_site(url)
            company["has_site"] = "Да"

    # sameAs → соцсети, только от бизнес-сущностей
    if is_business or (not is_website and not entity_type):
        same = data.get("sameAs") or []
        if isinstance(same, str):
            same = [same]
        new_s: list[str] = []
        for link in same:
            if isinstance(link, str) and _is_valid_social(link):
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

    # Товары и услуги: hasOfferCatalog / makesOffer
    if not company.get("services"):
        catalog = data.get("hasOfferCatalog") or {}
        if isinstance(catalog, dict):
            items = catalog.get("itemListElement") or []
            if isinstance(items, list):
                names = [i.get("name", "") for i in items if isinstance(i, dict) and i.get("name")]
                if names:
                    company["services"] = ", ".join(names[:25])
        if not company.get("services"):
            offers = data.get("makesOffer") or []
            if isinstance(offers, list):
                names = []
                for o in offers:
                    if not isinstance(o, dict):
                        continue
                    n = o.get("name") or (o.get("itemOffered") or {}).get("name", "")
                    if n:
                        names.append(n)
                if names:
                    company["services"] = ", ".join(names[:25])

    # Цены: priceRange или offers
    if not company.get("price_range"):
        pr = data.get("priceRange")
        if isinstance(pr, str) and pr.strip():
            company["price_range"] = pr.strip()[:50]
        else:
            # Try to derive a range from individual offer prices
            offers_list = data.get("makesOffer") or []
            if not offers_list:
                cat = data.get("hasOfferCatalog") or {}
                if isinstance(cat, dict):
                    offers_list = cat.get("itemListElement") or []
            if isinstance(offers_list, list) and offers_list:
                prices = []
                for o in offers_list:
                    if not isinstance(o, dict):
                        continue
                    p = o.get("price") or (o.get("offers") or {}).get("price")
                    if p is not None:
                        try:
                            prices.append(float(str(p).replace(",", ".")))
                        except (ValueError, TypeError):
                            pass
                if prices:
                    mn, mx = int(min(prices)), int(max(prices))
                    cur = data.get("priceCurrency") or "₽"
                    if cur == "RUB":
                        cur = "₽"
                    company["price_range"] = (
                        f"{mn} {cur}" if mn == mx else f"{mn}–{mx} {cur}"
                    )

    # Особенности: amenityFeature
    if not company.get("features"):
        amenity = data.get("amenityFeature") or []
        if isinstance(amenity, list):
            _ftr_skip = {"false", "0", "no", "none", "", "недоступно"}
            values = []
            for a in amenity:
                if not isinstance(a, dict):
                    continue
                val  = a.get("value")
                name = a.get("name", "").strip()
                if str(val).lower() in _ftr_skip:
                    continue
                if not name or "₽" in name or "руб" in name.lower():
                    continue
                if len(name) > 70:
                    continue
                values.append(name)
            if values:
                company["features"] = ", ".join(values[:25])

    # Рекурсия только в @graph (явный граф сущностей), не во все поля
    graph = data.get("@graph")
    if graph:
        _apply_jsonld(company, graph)

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
            "services": "", "features": "", "price_range": "",
        })
    return companies

# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------
def collect(query: str, max_companies: int = 100,
            emit=None, filter_fn=None) -> list[dict]:
    """
    Collect companies for query.

    filter_fn — optional callable(company) → bool.
    When provided, keeps searching until max_companies PASS the filter,
    up to a hard cap of min(max_companies * 8, 400) total fetched.
    Returns only companies that pass the filter (up to max_companies).
    Without filter_fn returns all collected companies.
    """
    def _emit(event, **kw):
        if emit:
            emit(event, **kw)

    # When filter active we may need to scan many more raw results
    hard_cap = min(max_companies * 8, 400) if filter_fn else max_companies

    all_seen:    set[tuple] = set()
    all_fetched: list[dict] = []   # every company before filter
    passed:      list[dict] = []   # companies that pass filter_fn
    skip, page, streak = 0, 1, 0

    def _target_reached():
        if filter_fn:
            return len(passed) >= max_companies
        return len(all_fetched) >= max_companies

    while not _target_reached():
        if len(all_fetched) >= hard_cap:
            _emit("progress",
                  found=len(passed) if filter_fn else len(all_fetched),
                  total=max_companies,
                  message="Достигнут лимит проверенных компаний.")
            break

        _emit("progress",
              found=len(passed) if filter_fn else len(all_fetched),
              total=max_companies,
              message=f"Поиск результатов {skip + 1}–{skip + 10}…"
                      + (f" (проверено: {len(all_fetched)})" if filter_fn else ""))

        feats = _search_page(query, skip)
        batch = [_parse_feature(f) for f in feats]

        if not batch:
            _emit("progress",
                  found=len(passed) if filter_fn else len(all_fetched),
                  total=max_companies,
                  message=f"API пустой, HTML fallback (стр. {page})…")
            batch = _scrape_page(query, page)

        if not batch:
            streak += 1
            if streak >= 3:
                _emit("progress",
                      found=len(passed) if filter_fn else len(all_fetched),
                      total=max_companies,
                      message="Яндекс больше не отдаёт результаты.")
                break
        else:
            streak = 0

        for c in batch:
            key = _dedup_key(c)
            if key in all_seen:
                continue
            all_seen.add(key)

            _emit("progress",
                  found=len(passed) if filter_fn else len(all_fetched),
                  total=max_companies,
                  message=f"Обогащаем: {c['name'][:40]}…")
            c = _enrich(c)
            _sleep(0.4, 1.2)
            all_fetched.append(c)

            if filter_fn:
                if filter_fn(c):
                    passed.append(c)
            if _target_reached() or len(all_fetched) >= hard_cap:
                break

        skip += 10
        page += 1
        _sleep(0.8, 2.0)

    return passed if filter_fn else all_fetched

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
