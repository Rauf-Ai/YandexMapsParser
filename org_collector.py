"""
org_collector.py — Per-organisation deep data collector.

Fetches reviews, photos, working hours, description, and news/promotions
for a single Yandex Maps organisation, then packages everything into a
ZIP archive containing:

  info.json      — structured company data
  reviews.json   — customer reviews
  news.json      — promotions / news
  summary.txt    — human-readable summary
  PROMPT.md      — ready-to-use Claude Code prompt for landing page creation
  README.txt     — usage instructions
  photos/        — downloaded photos (if found)
"""

import io
import json
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from yandex_parser import SESSION, _sleep, _extract_vitrina
from browser_fetch import fetch_rendered

log = logging.getLogger(__name__)

# Yandex avatars CDN: match business/org photo URLs.
# Match any avatars.mds.yandex.net/get-* namespace.
# Excludes template URLs containing { } or %s placeholders.
_PHOTO_RE = re.compile(
    r"https://avatars\.mds\.yandex\.net/get-[a-z][a-z\-]+"
    r"/\d+/[a-zA-Z0-9_\-]+"
    r"(?:/[a-zA-Z0-9_\-\.]+)?",
    re.IGNORECASE,
)

# ── Low-level helpers ─────────────────────────────────────────────────────

def _fetch(url: str):
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code == 200:
            return r
    except Exception as exc:
        log.debug("fetch %s: %s", url, exc)
    return None


def _parse_jsonld(soup) -> list:
    blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            blocks.append(json.loads(tag.string or ""))
        except Exception:
            pass
    return blocks


# ── Data extractors ───────────────────────────────────────────────────────

def _extract_reviews(soup, jsonld_blocks: list) -> list[dict]:
    reviews: list[dict] = []
    seen: set[str] = set()

    def _add(text, author="", rating="", date=""):
        t = text.strip()
        if t and t not in seen and len(t) >= 15:
            seen.add(t)
            reviews.append({"author": author, "rating": rating,
                             "date": date, "text": t})

    # JSON-LD review entries (SEO-rendered on some org pages)
    for block in jsonld_blocks:
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue
            for entity in ([item] + (item.get("@graph") or [])):
                if not isinstance(entity, dict):
                    continue
                for rev in (entity.get("review") or entity.get("reviews") or []):
                    if not isinstance(rev, dict):
                        continue
                    text = rev.get("reviewBody") or rev.get("description") or ""
                    a = rev.get("author")
                    author = (a.get("name", "") if isinstance(a, dict) else str(a or ""))
                    ar = rev.get("reviewRating") or {}
                    _add(text, author=author,
                         rating=str(ar.get("ratingValue", "")),
                         date=str(rev.get("datePublished", "")))

    # HTML selectors (work when Yandex pre-renders review text for SEO)
    for sel in [
        ".business-review-view__body-text",
        "[class*='review-view__body-text']",
        "[itemprop='reviewBody']",
    ]:
        for el in soup.select(sel):
            _add(el.get_text(strip=True))

    return reviews


def _extract_org_details(soup, jsonld_blocks: list) -> dict:
    info = {"hours": "", "description": ""}

    for block in jsonld_blocks:
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue
            for entity in ([item] + (item.get("@graph") or [])):
                if not isinstance(entity, dict):
                    continue
                if not info["description"]:
                    desc = entity.get("description") or ""
                    if isinstance(desc, str) and len(desc) > 10:
                        info["description"] = desc[:2000]
                if not info["hours"]:
                    oh = (entity.get("openingHours")
                          or entity.get("openingHoursSpecification"))
                    if oh:
                        info["hours"] = (
                            "; ".join(str(h) for h in oh)
                            if isinstance(oh, list) else str(oh)
                        )[:400]

    if not info["description"]:
        for sel in [
            ".business-card-description-view__text",
            "[itemprop='description']",
        ]:
            el = soup.select_one(sel)
            if el:
                info["description"] = el.get_text(strip=True)[:2000]
                break

    if not info["hours"]:
        for sel in [
            "[class*='business-working-status']",
            "[itemprop='openingHours']",
        ]:
            els = soup.select(sel)
            if els:
                info["hours"] = "; ".join(e.get_text(strip=True) for e in els)[:400]
                break

    return info


def _extract_from_js_state(html: str) -> dict:
    """
    Yandex Maps embeds org data in script tags as JSON (SSR state).
    Try to extract photo URLs, services, and features from those scripts.
    Returns dict with keys: photos, services, features.
    """
    result: dict = {"photos": [], "services": [], "features": []}
    seen_photos: set[str] = set()

    photo_re = re.compile(
        r"https://avatars\.mds\.yandex\.net/get-[a-z][a-z\-]+"
        r"/\d+/[a-zA-Z0-9_\-]+"
        r"(?:/[a-zA-Z0-9_\-\.]+)?",
        re.IGNORECASE,
    )

    for sc_match in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
        t = sc_match.group(1)
        if len(t) < 200:
            continue

        # Photos: grab all CDN URLs from script content
        for m in photo_re.finditer(t):
            url = m.group(0)
            key = url.rstrip("/").split("/")[-2] if "/" in url else url
            if key not in seen_photos:
                seen_photos.add(key)
                # Normalise to /orig
                url = re.sub(r"/(?:L|XL|XXL)(/|$)", "/orig\\1", url)
                if not url.endswith("/orig"):
                    url = url.rstrip("/") + "/orig"
                result["photos"].append(url)

        # Services: look for showcase / catalog item titles in JSON
        if not result["services"] and (
            "showcase" in t.lower() or "catalog" in t.lower() or "offer" in t.lower()
        ):
            # Grab title strings near price values (витрина pattern)
            for m in re.finditer(
                r'"(?:title|name)"\s*:\s*"([^"]{3,80})"[^}]{0,200}?"price"\s*:\s*(\d+)',
                t
            ):
                item_name = m.group(1)
                price = m.group(2)
                if item_name and item_name not in result["services"]:
                    result["services"].append(f"{item_name} {int(price):,} ₽".replace(",", " "))
            # Also try plain title list without prices
            if not result["services"]:
                for m in re.finditer(r'"title"\s*:\s*"([^"]{5,80})"', t):
                    item_name = m.group(1)
                    skip_words = {"обзор", "фото", "отзывы", "контакты", "услуги",
                                  "информация", "маршрут", "акции"}
                    if item_name.lower() not in skip_words:
                        result["services"].append(item_name)
                result["services"] = result["services"][:25]

        if len(result["photos"]) >= 20:
            break

    return result


def _extract_photo_urls(html: str, max_photos: int = 20) -> list[str]:
    seen_keys: set[str] = set()
    urls: list[str] = []

    def _add(raw: str):
        if len(urls) >= max_photos:
            return
        # Skip template URLs (placeholders like {size}, %s, {namespace})
        if "{" in raw or "%" in raw:
            return
        # Strip any trailing size suffix, then append best quality
        # Covers: orig, L, XL, XXL, L_height, XL_height, XXL_height, 426x240.jpeg
        url = re.sub(
            r"/(?:orig|[SMLX]+(?:_height)?|[0-9]+x[0-9]+(?:\.[a-z]+)?)$",
            "",
            raw.rstrip("/"),
        ) + "/XXL_height"
        # Dedup by the hash segment (3rd segment: /get-ns/{id}/{hash}/...)
        parts = url.split("/")
        key = parts[5] if len(parts) > 5 else url  # hash is at index 5
        if key not in seen_keys:
            seen_keys.add(key)
            urls.append(url)

    for m in _PHOTO_RE.finditer(html):
        _add(m.group(0))
        if len(urls) >= max_photos:
            break
    return urls


def _extract_photo_urls_from_jsonld(jsonld_blocks: list) -> list[str]:
    """Pull image URLs out of JSON-LD image / photo fields."""
    urls: list[str] = []
    seen: set[str] = set()

    def _collect(val):
        if isinstance(val, str) and val.startswith("http") and val not in seen:
            seen.add(val)
            urls.append(val)
        elif isinstance(val, dict):
            _collect(val.get("url") or val.get("contentUrl", ""))
        elif isinstance(val, list):
            for v in val:
                _collect(v)

    for block in jsonld_blocks:
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue
            for entity in ([item] + (item.get("@graph") or [])):
                if not isinstance(entity, dict):
                    continue
                _collect(entity.get("image"))
                _collect(entity.get("photo"))
                _collect(entity.get("primaryImageOfPage"))
    return urls


def _extract_news(soup, extra_html: str = "") -> list[dict]:
    news: list[dict] = []
    seen: set[str] = set()
    soups = [soup]
    if extra_html:
        soups.append(BeautifulSoup(extra_html, "html.parser"))
    for s in soups:
        # Narrow selectors only — broad [class*='promo'] matches entire page body
        for sel in [
            "[class*='story-view__title']",
            "[class*='promotion-view__title']",
            "[class*='action-view__title']",
            "[class*='news-view__title']",
            "[class*='story-snippet__title']",
            "[class*='action-snippet__title']",
            "[class*='promo-item__title']",
            "[class*='promo-card__title']",
        ]:
            for el in s.select(sel):
                text = el.get_text(strip=True)
                if text and len(text) > 5 and len(text) < 500 and text not in seen:
                    seen.add(text)
                    news.append({"text": text[:500]})

        # Extract promo items from page plain text using known patterns.
        # Yandex renders promos as React components — no CSS class is reliable.
        page_text = s.get_text(strip=True)
        # Pattern: text with price/discount markers (₽, скидка, вместо, %)
        for m in re.finditer(
            r'([А-ЯЁа-яёA-Za-z0-9][^₽\n]{5,200}(?:₽|скидк|акци|вместо|%)[^₽\n]{0,100})',
            page_text,
        ):
            candidate = m.group(1).strip()
            # Skip navigation blobs (they contain multiple tab names)
            skip_markers = ("Обзор", "ПоискМаршруты", "Товары и услуги", "Витрина")
            if any(mk in candidate for mk in skip_markers):
                continue
            # Strip leading "Акция"/"Реклама" labels and trailing "Реклама"
            candidate = re.sub(r'^(Акция|Новость|Скидка)\s*', '', candidate).strip()
            candidate = re.sub(r'\s*Реклама\s*$', '', candidate).strip()
            if len(candidate) > 10 and candidate not in seen:
                seen.add(candidate)
                news.append({"text": candidate[:500]})
            if len(news) >= 20:
                break

    return news[:20]


# ── Main collection entry point ───────────────────────────────────────────

def collect_org_zip(
    company: dict,
    out_dir: Path,
    emit=None,
    max_reviews: int = 100,
    max_photos: int = 20,
) -> str:
    """
    Collect all available data for one org and package as ZIP.
    Returns the ZIP filename (relative to out_dir).

    emit — optional callable(event, **kwargs) for SSE progress.
    """
    def _emit(msg: str):
        if emit:
            emit("progress", message=msg)

    oid  = company.get("oid", "")
    name = company.get("name", "company")

    # 1 ── Main org page ───────────────────────────────────────────────
    # Try browser-rendered version first (needed for photos and JS content).
    # Falls back to plain requests when Playwright is unavailable or blocked.
    _emit(f"Загружаем страницу: {name}…")
    org_url = f"https://yandex.ru/maps/org/{oid}/" if oid else ""
    html = ""
    used_browser = False
    if org_url:
        _emit("Открываем браузер для загрузки фото…")
        html = fetch_rendered(
            org_url,
            wait_for_selector="img[src*='avatars.mds.yandex.net']",
            scroll_px=600,
            extra_wait_ms=2_000,
        )
        if html:
            used_browser = True
    if not html and org_url:
        if not used_browser:
            _emit("Браузер недоступен, загружаем стандартным способом…")
        resp = _fetch(org_url)
        html = resp.text if resp else ""
    soup   = BeautifulSoup(html, "html.parser")
    jsonld = _parse_jsonld(soup)

    # 2 ── Reviews page ────────────────────────────────────────────────
    _sleep(0.5, 1.0)
    _emit("Загружаем страницу отзывов…")
    rev_resp = _fetch(f"https://yandex.ru/maps/org/{oid}/reviews/") if oid else None
    if rev_resp:
        rsoup  = BeautifulSoup(rev_resp.text, "html.parser")
        rjsonld = _parse_jsonld(rsoup)
        reviews = _extract_reviews(rsoup, rjsonld)
    else:
        reviews = []
    if not reviews:                         # supplement from main page
        reviews = _extract_reviews(soup, jsonld)
    reviews = reviews[:max_reviews]
    _emit(f"Отзывов: {len(reviews)}")

    # 3 ── Extended org details ────────────────────────────────────────
    _sleep(0.3, 0.5)
    details = _extract_org_details(soup, jsonld)

    # 4 ── Photo URLs ──────────────────────────────────────────────────
    _emit("Ищем фотографии…")

    # Primary: extract from embedded JS state (richest source in React SPA)
    js_data = _extract_from_js_state(html)
    photo_urls: list[str] = js_data["photos"][:max_photos]

    # Supplement with regex scan of raw HTML for CDN URLs
    if len(photo_urls) < max_photos:
        extra = _extract_photo_urls(html, max_photos - len(photo_urls))
        seen = set(photo_urls)
        for u in extra:
            if u not in seen:
                photo_urls.append(u)
                seen.add(u)

    # Supplement from JSON-LD image fields
    if len(photo_urls) < max_photos:
        seen = set(photo_urls)
        for url in _extract_photo_urls_from_jsonld(jsonld):
            if url not in seen:
                photo_urls.append(url)
                seen.add(url)
                if len(photo_urls) >= max_photos:
                    break

    # Try og:image meta tag
    if len(photo_urls) < max_photos:
        og = soup.find("meta", property="og:image")
        if og:
            og_url = og.get("content", "")
            if og_url.startswith("http") and og_url not in photo_urls:
                photo_urls.append(og_url)

    photo_urls = photo_urls[:max_photos]
    _emit(f"Фотографий найдено: {len(photo_urls)}")

    # 5 ── News / promotions ───────────────────────────────────────────
    _sleep(0.3, 0.5)
    _emit("Ищем акции…")
    actions_html = ""
    if oid:
        ar = _fetch(f"https://yandex.ru/maps/org/{oid}/actions/")
        if ar:
            actions_html = ar.text
    news = _extract_news(soup, actions_html)
    _emit(f"Акций/новостей: {len(news)}")

    # 6 ── Build full info dict ────────────────────────────────────────
    # Use витрина parser then JS-state as fallbacks for services
    services = company.get("services", "")
    price_range = company.get("price_range", "")
    if not services:
        vitrina_svcs, vitrina_pr = _extract_vitrina(soup)
        if vitrina_svcs:
            services = ", ".join(vitrina_svcs[:25])
        if vitrina_pr and not price_range:
            price_range = vitrina_pr
    if not services and js_data.get("services"):
        services = ", ".join(js_data["services"][:25])

    info = {
        "name":          name,
        "category":      company.get("category",    ""),
        "address":       company.get("address",     ""),
        "phone":         company.get("phone",       ""),
        "site":          company.get("site",        ""),
        "social":        company.get("social",      ""),
        "rating":        company.get("rating",      ""),
        "reviews_count": company.get("reviews",     0),
        "services":      services,
        "features":      company.get("features",    ""),
        "price_range":   price_range,
        "map_url":       company.get("map_url",     ""),
        "lat":           company.get("lat",         ""),
        "lon":           company.get("lon",         ""),
        "hours":         details.get("hours",       ""),
        "description":   details.get("description", ""),
    }

    # 7 ── Build ZIP (text files first) ───────────────────────────────
    _emit("Формируем архив…")
    safe  = re.sub(r"[^\w\-а-яА-Я]", "_", name)[:40]
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"org_{safe}_{ts}.zip"
    fpath = out_dir / fname

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("info.json",    json.dumps(info,    ensure_ascii=False, indent=2))
        zf.writestr("reviews.json", json.dumps(reviews, ensure_ascii=False, indent=2))
        zf.writestr("news.json",    json.dumps(news,    ensure_ascii=False, indent=2))
        zf.writestr("summary.txt",  _make_summary(info, reviews, news))
        zf.writestr("PROMPT.md",    _make_prompt(info, reviews, news))
        zf.writestr("README.txt",   _make_readme())
    fpath.write_bytes(buf.getvalue())

    # 8 ── Download photos and append to ZIP ──────────────────────────
    ok = 0
    if photo_urls:
        _emit(f"Скачиваем фото (0/{len(photo_urls)})…")
        buf2 = io.BytesIO(fpath.read_bytes())
        with zipfile.ZipFile(buf2, "a", zipfile.ZIP_STORED) as zf:
            for i, url in enumerate(photo_urls, 1):
                ext = ("webp" if ".webp" in url.lower()
                       else "png" if ".png" in url.lower() else "jpg")
                try:
                    r = SESSION.get(url, timeout=20)
                    ct = r.headers.get("content-type", "")
                    if r.status_code == 200 and "image" in ct:
                        zf.writestr(f"photos/photo_{i:02d}.{ext}", r.content)
                        ok += 1
                        _emit(f"Скачиваем фото ({ok}/{len(photo_urls)})…")
                except Exception as exc:
                    log.debug("photo %d: %s", i, exc)
                _sleep(0.2, 0.5)
        fpath.write_bytes(buf2.getvalue())
        _emit(f"Фото загружено: {ok}/{len(photo_urls)}")

    return fname


# ── Text generators ───────────────────────────────────────────────────────

def _make_summary(info: dict, reviews: list, news: list) -> str:
    lines = [
        f"# {info['name']}", "",
        f"Категория:    {info['category']}",
        f"Адрес:        {info['address']}",
        f"Телефон:      {info['phone']}",
        f"Сайт:         {info['site']}",
        f"Соцсети:      {info['social']}",
        f"Рейтинг:      {info['rating']} ({info['reviews_count']} отзывов)",
        f"Часы работы:  {info['hours'] or '—'}",
        f"Цены:         {info['price_range'] or '—'}",
        f"Карты:        {info['map_url']}", "",
        "Описание:",
        info["description"] or "(нет)", "",
        f"Услуги:       {info['services'] or '—'}",
        f"Особенности:  {info['features'] or '—'}",
    ]
    if reviews:
        lines += ["", "─" * 60, f"ОТЗЫВЫ ({len(reviews)} шт.)", "─" * 60]
        for i, r in enumerate(reviews, 1):
            a = r.get("author") or "Аноним"
            rt = r.get("rating") or ""
            d  = r.get("date") or ""
            hdr = f"[{i}] {a}" + (f"  ★{rt}" if rt else "") + (f"  ({d})" if d else "")
            lines += ["", hdr, r.get("text", "")]
    if news:
        lines += ["", "─" * 60, f"АКЦИИ И НОВОСТИ ({len(news)} шт.)", "─" * 60]
        for i, n in enumerate(news, 1):
            lines += [f"\n{i}. {n.get('text', '')}"]
    return "\n".join(lines)


def _make_prompt(info: dict, reviews: list, news: list) -> str:
    """Generate a Claude Code prompt for one-shot landing page creation."""

    # Format reviews as Markdown blockquotes
    if reviews:
        rev_parts = []
        for r in reviews[:25]:
            text = (r.get("text") or "").strip()
            if not text:
                continue
            a  = r.get("author") or "Аноним"
            rt = r.get("rating") or ""
            d  = r.get("date")   or ""
            meta = a + (f" ★{rt}" if rt else "") + (f" · {d}" if d else "")
            rev_parts.append(f"> **{meta}**  \n> {text}")
        rev_block = "\n\n".join(rev_parts) if rev_parts else "_Отзывы не найдены._"
    else:
        rev_block = "_Отзывы не найдены._"

    news_block = (
        "\n".join(f"- {n.get('text', '')}" for n in news if n.get("text"))
        or "_Акции не найдены._"
    )

    try:
        stars = "⭐" * round(float(str(info["rating"]).replace(",", ".")))
    except Exception:
        stars = ""

    phone_clean = re.sub(r"[^+\d]", "", str(info.get("phone", "")))

    return f"""\
# Создай лендинг для «{info['name']}»

Ты — опытный фронтенд-разработчик и дизайнер. На основе реальных данных ниже создай
профессиональный одностраничный сайт-лендинг.

---

## 📋 Данные компании (источник — Яндекс Карты)

| Поле | Значение |
|---|---|
| **Название** | {info['name']} |
| **Категория** | {info['category']} |
| **Адрес** | {info['address']} |
| **Телефон** | {info['phone']} |
| **Сайт** | {info['site']} |
| **Соцсети** | {info['social']} |
| **Рейтинг** | {info['rating']} {stars} ({info['reviews_count']} отзывов) |
| **Часы работы** | {info['hours'] or '—'} |
| **Диапазон цен** | {info['price_range'] or '—'} |
| **Услуги** | {info['services'] or '—'} |
| **Особенности** | {info['features'] or '—'} |
| **Описание** | {(info['description'] or '—')[:300]} |
| **Яндекс Карты** | {info['map_url']} |

---

## 💬 Реальные отзывы клиентов

{rev_block}

---

## 🎯 Акции и новости

{news_block}

---

## 🛠 Техническое задание

### Технологии
- Один файл **`index.html`** — открывается без сервера через `file://`
- **Tailwind CSS** через CDN (`https://cdn.tailwindcss.com`)
- **Font Awesome 6** через CDN для иконок
- Ванильный JavaScript (без фреймворков и сборщиков)

### Структура страницы (сверху вниз)

**1. Sticky header** — логотип/название, навигация-якоря, кнопка «Позвонить»

**2. Hero** (полный экран):
- Заголовок — название компании
- Слоган — придумай яркий, релевантный тематике «{info['category']}»
- Рейтинг: {info['rating']} {stars} ({info['reviews_count']} отзывов)
- CTA: «📞 Позвонить» → `tel:{phone_clean}` · «📍 На карте» → `{info['map_url']}`
- Фон: `photos/photo_01.jpg` если есть, иначе градиент под тематику

**3. О нас** — описание + 3–4 иконки-преимущества из «Особенностей»:
`{info['features'] or 'Профессионализм, Качество, Опыт'}`

**4. Услуги** — карточки из поля «Услуги»:
`{info['services'] or '(добавь ключевые услуги из категории)'}`
Цены: {info['price_range'] or 'уточняйте'}

**5. Галерея** *(только если папка `photos/` не пустая)*
```html
<img src="photos/photo_01.jpg" alt="Фото">
<img src="photos/photo_02.jpg" alt="Фото">
```
Grid 2–4 колонки, все фото из папки `photos/`.

**6. Отзывы** — карточки с реальными отзывами из раздела выше.
⚠️ Показывай только реальные — не выдумывай.

**7. Акции** *(только если данные есть в разделе «Акции» выше)*
> 💡 Сторис/акции на Яндекс Картах загружаются динамически и не всегда попадают в архив.
> Если пользователь добавил скриншоты сторисов в папку `promo/` — учти их при создании секции акций.
> Иначе используй только данные из `news.json`.

**8. Контакты**
- Адрес: {info['address']}
- Телефон: {info['phone']}
- Соцсети: {info['social']}
- Часы: {info['hours'] or 'уточняйте'}
- Кнопка «Открыть в Яндекс Картах» → `{info['map_url']}`

**9. Footer** — название, год, соцсети-иконки

### Дизайн

- Цветовую палитру подбери под тематику **«{info['category']}»**
  (медицина → синий/белый; красота → пастель; еда → тёплые тона; авто → тёмный/серый)
- Современный вид, не шаблонный
- Mobile-first, полностью адаптивный
- Плавное появление секций при скролле (`IntersectionObserver`)

### Правила контента

| ⛔ Нельзя | ✅ Можно |
|---|---|
| Выдумывать отзывы | Придумывать слоган |
| Добавлять несуществующие услуги | Дописывать текст преимуществ |
| Изменять контакты | Выбирать цветовую схему |
| Придумывать цены | Придумывать заголовки секций |

---

Создай файл `index.html`. Если папка `photos/` не пустая — используй фото.
Если данных (отзывов, акций) нет — скрывай соответствующие секции.
"""


def _make_readme() -> str:
    return """\
Содержимое архива
=================

  info.json     — данные компании (JSON)
  reviews.json  — отзывы клиентов с Яндекс Карт (JSON)
  news.json     — акции и новости (JSON)
  summary.txt   — читаемая сводка всех данных
  PROMPT.md     — готовый промт для Claude Code (создание лендинга)
  README.txt    — этот файл
  photos/       — фотографии компании (если найдены)

Как использовать PROMPT.md
--------------------------

Вариант 1 — Claude Code CLI (рекомендуется):
  1. Распакуй архив: unzip org_*.zip -d landing/
  2. cd landing/
  3. Запусти: claude
  4. Вставь содержимое PROMPT.md в чат Claude Code

Вариант 2 — Через аргумент CLI:
  cd landing/ && claude "$(cat PROMPT.md)"

Вариант 3 — claude.ai/code (веб):
  1. Распакуй архив
  2. Открой claude.ai/code, добавь папку как проект
  3. Вставь PROMPT.md в чат

После генерации index.html:
  - Открой в браузере: open index.html
  - Фото из photos/ подключены автоматически
  - Отредактируй под финальные требования клиента
"""
