"""
org_collector.py — Per-organisation deep data collector for 2GIS.

Collects reviews (API), photos (API), news/promotions (Playwright),
then packages everything into a ZIP archive:

  info.json      — structured company data
  reviews.json   — customer reviews
  news.json      — promotions / news
  summary.txt    — human-readable summary
  PROMPT.md      — ready-to-use Claude Code prompt for landing page creation
  README.txt     — usage instructions
  photos/        — downloaded photos
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from twogis_parser import SESSION, _sleep, _get_api_key, _BYID_API, _REVIEWS_API
from browser_fetch import fetch_rendered

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, params: dict | None = None):
    try:
        r = SESSION.get(url, params=params or {}, timeout=15)
        if r.status_code == 200:
            return r
    except Exception as exc:
        log.debug("fetch %s: %s", url, exc)
    return None


# ---------------------------------------------------------------------------
# Reviews via API
# ---------------------------------------------------------------------------

def _fetch_reviews_api(org_id: str, max_reviews: int = 100) -> list[dict]:
    reviews: list[dict] = []
    seen: set[str] = set()
    page = 1

    while len(reviews) < max_reviews:
        url = _REVIEWS_API.format(id=org_id)
        params = {
            "key":         _get_api_key(),
            "locale":      "ru_RU",
            "page_size":   50,
            "page":        page,
            "fields":      "reviews.user,reviews.rating,reviews.date_created,reviews.text",
        }
        try:
            resp = SESSION.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data  = resp.json()
            items = (data.get("reviews") or {}).get("items") or []
        except Exception as exc:
            log.debug("reviews API %s p%d: %s", org_id, page, exc)
            break

        if not items:
            break

        for item in items:
            text = (item.get("text") or "").strip()
            if not text or len(text) < 5 or text in seen:
                continue
            seen.add(text)
            user   = item.get("user") or {}
            author = user.get("name") or user.get("first_name") or "Аноним"
            rating = str(item.get("rating") or "")
            date   = str((item.get("date_created") or "")[:10])
            reviews.append({"author": author, "rating": rating,
                             "date": date, "text": text})
            if len(reviews) >= max_reviews:
                break

        if len(items) < 50:
            break
        page += 1
        _sleep(0.3, 0.6)

    return reviews


# ---------------------------------------------------------------------------
# Photos via byid API
# ---------------------------------------------------------------------------

def _fetch_photos_api(org_id: str, max_photos: int = 20) -> list[str]:
    params = {
        "id":     org_id,
        "fields": "items.photos",
        "key":    _get_api_key(),
        "locale": "ru_RU",
    }
    try:
        resp = SESSION.get(_BYID_API, params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("result", {}).get("items", [])
        if not items:
            return []
        photos = (items[0].get("photos") or {}).get("items") or []
        urls: list[str] = []
        seen: set[str] = set()
        for p in photos[:max_photos]:
            url = p.get("filename_v1") or ""
            if url and url not in seen:
                seen.add(url)
                # Ensure we request the large size
                if not any(sz in url for sz in ("/p/", "/hd", "/large")):
                    url = url.rstrip("/") + "/p/200x150"  # 2GIS CDN size token
                urls.append(p.get("filename_v1") or "")  # use original URL
        # Return original URLs (no size manipulation - 2GIS CDN resizes automatically)
        return [p.get("filename_v1", "") for p in photos[:max_photos]
                if p.get("filename_v1")]
    except Exception as exc:
        log.debug("photos API %s: %s", org_id, exc)
        return []


# ---------------------------------------------------------------------------
# News/promotions via Playwright
# ---------------------------------------------------------------------------

def _fetch_news_playwright(map_url: str) -> list[dict]:
    if not map_url:
        return []

    html = fetch_rendered(
        map_url,
        scroll_px=600,
        extra_wait_ms=2_000,
        timeout_ms=20_000,
    )
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    news: list[dict] = []
    seen: set[str] = set()

    for sel in [
        "[class*='promo-card__title']",
        "[class*='promotion-card__title']",
        "[class*='story-card__title']",
        "[class*='action-card__title']",
        "[class*='promo__title']",
        "[class*='story__title']",
        "[class*='news__title']",
        "[class*='promotions__title']",
    ]:
        for el in soup.select(sel):
            text = el.get_text(strip=True)
            if text and 5 < len(text) < 500 and text not in seen:
                seen.add(text)
                news.append({"text": text[:500]})

    # Regex: text blocks with price/discount markers
    page_text = soup.get_text(strip=True)
    for m in re.finditer(
        r'([А-ЯЁа-яёA-Za-z0-9][^₽\n]{5,200}(?:₽|скидк|акци|вместо|%)[^₽\n]{0,100})',
        page_text,
    ):
        candidate = m.group(1).strip()
        skip_markers = ("Рейтинг", "отзыв", "Часы работы", "Проложить маршрут")
        if any(mk in candidate for mk in skip_markers):
            continue
        promo_words = {"скидк", "акци", "вместо", "бесплатн", "подарок", "предложени"}
        has_promo = any(w in candidate.lower() for w in promo_words)
        has_rub   = "₽" in candidate
        if not (has_promo or (has_rub and len(candidate) < 120)):
            continue
        candidate = re.sub(r'^(Акция|Скидка|Новость)\s*', '', candidate).strip()
        if len(candidate) > 10 and candidate not in seen:
            seen.add(candidate)
            news.append({"text": candidate[:500]})
        if len(news) >= 20:
            break

    return news[:20]


# ---------------------------------------------------------------------------
# Main collect entry point
# ---------------------------------------------------------------------------

def collect_org_zip(
    company: dict,
    out_dir: Path,
    emit=None,
    max_reviews: int = 100,
    max_photos: int = 20,
) -> str:
    def _emit(msg: str):
        if emit:
            emit("progress", message=msg)

    org_id  = company.get("id", "")
    name    = company.get("name", "company")
    map_url = company.get("map_url", "")

    # 1 ── Reviews via API ────────────────────────────────────────────
    _emit(f"Загружаем отзывы: {name}…")
    reviews: list[dict] = []
    if org_id:
        reviews = _fetch_reviews_api(org_id, max_reviews)
    _emit(f"Отзывов: {len(reviews)}")

    # 2 ── Photos via byid API ────────────────────────────────────────
    _sleep(0.3, 0.6)
    _emit("Загружаем фотографии…")
    photo_urls: list[str] = []
    if org_id:
        photo_urls = _fetch_photos_api(org_id, max_photos)
    _emit(f"Фотографий найдено: {len(photo_urls)}")

    # 3 ── News/promotions via Playwright ────────────────────────────
    _sleep(0.3, 0.6)
    _emit("Ищем акции…")
    news: list[dict] = []
    if map_url:
        news = _fetch_news_playwright(map_url)
    _emit(f"Акций/новостей: {len(news)}")

    # 4 ── Build info dict ────────────────────────────────────────────
    info = {
        "name":          name,
        "category":      company.get("category",    ""),
        "address":       company.get("address",     ""),
        "phone":         company.get("phone",       ""),
        "site":          company.get("site",        ""),
        "social":        company.get("social",      ""),
        "rating":        company.get("rating",      ""),
        "reviews_count": company.get("reviews",     0),
        "hours":         company.get("hours",       ""),
        "description":   company.get("description", ""),
        "services":      company.get("services",    ""),
        "features":      company.get("features",    ""),
        "map_url":       map_url,
        "lat":           company.get("lat",         ""),
        "lon":           company.get("lon",         ""),
    }

    # 5 ── Build ZIP ──────────────────────────────────────────────────
    _emit("Формируем архив…")
    safe  = re.sub(r"[^\w\-а-яА-Я]", "_", name)[:40]
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"2gis_{safe}_{ts}.zip"
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

    # 6 ── Download photos and append ────────────────────────────────
    ok = 0
    if photo_urls:
        _emit(f"Скачиваем фото (0/{len(photo_urls)})…")
        buf2 = io.BytesIO(fpath.read_bytes())
        with zipfile.ZipFile(buf2, "a", zipfile.ZIP_STORED) as zf:
            for i, url in enumerate(photo_urls, 1):
                if not url:
                    continue
                url_lower = url.lower()
                ext = ("webp" if ".webp" in url_lower
                       else "png" if ".png" in url_lower else "jpg")
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


# ---------------------------------------------------------------------------
# Text generators
# ---------------------------------------------------------------------------

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
        f"2ГИС:         {info['map_url']}", "",
        "Описание:",
        info["description"] or "(нет)", "",
        f"Услуги:       {info['services'] or '—'}",
        f"Особенности:  {info['features'] or '—'}",
    ]
    if reviews:
        lines += ["", "─" * 60, f"ОТЗЫВЫ ({len(reviews)} шт.)", "─" * 60]
        for i, r in enumerate(reviews, 1):
            a  = r.get("author") or "Аноним"
            rt = r.get("rating") or ""
            d  = r.get("date")   or ""
            hdr = f"[{i}] {a}" + (f"  ★{rt}" if rt else "") + (f"  ({d})" if d else "")
            lines += ["", hdr, r.get("text", "")]
    if news:
        lines += ["", "─" * 60, f"АКЦИИ И НОВОСТИ ({len(news)} шт.)", "─" * 60]
        for i, n in enumerate(news, 1):
            lines += [f"\n{i}. {n.get('text', '')}"]
    return "\n".join(lines)


def _make_prompt(info: dict, reviews: list, news: list) -> str:
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

    first_phone = str(info.get("phone", "")).split(",")[0].strip()
    phone_clean = re.sub(r"[^+\d]", "", first_phone)

    return f"""\
# Создай лендинг для «{info['name']}»

Ты — опытный фронтенд-разработчик и дизайнер. На основе реальных данных ниже создай
профессиональный одностраничный сайт-лендинг.

---

## 📋 Данные компании (источник — 2ГИС)

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
| **Услуги** | {info['services'] or '—'} |
| **Особенности** | {info['features'] or '—'} |
| **Описание** | {(info['description'] or '—')[:300]} |
| **2ГИС** | {info['map_url']} |

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

**5. Галерея** *(только если папка `photos/` не пустая)*
```html
<img src="photos/photo_01.jpg" alt="Фото">
```
Grid 2–4 колонки.

**6. Отзывы** — карточки с реальными отзывами выше.
⚠️ Показывай только реальные — не выдумывай.

**7. Акции** *(только если данные есть в разделе «Акции» выше)*

**8. Контакты**
- Адрес: {info['address']}
- Телефон: {info['phone']}
- Соцсети: {info['social']}
- Часы: {info['hours'] or 'уточняйте'}
- Кнопка «Открыть в 2ГИС» → `{info['map_url']}`

**9. Footer** — название, год, соцсети-иконки

### Дизайн
- Цветовую палитру подбери под тематику **«{info['category']}»**
- Mobile-first, полностью адаптивный
- Плавное появление секций (`IntersectionObserver`)

### Правила контента
| ⛔ Нельзя | ✅ Можно |
|---|---|
| Выдумывать отзывы | Придумывать слоган |
| Добавлять несуществующие услуги | Дописывать текст преимуществ |
| Изменять контакты | Выбирать цветовую схему |

---

Создай файл `index.html`. Если папки `photos/` не пустая — используй фото.
Если данных (отзывов, акций) нет — скрывай соответствующие секции.
"""


def _make_readme() -> str:
    return """\
Содержимое архива
=================

  info.json     — данные компании (JSON)
  reviews.json  — отзывы клиентов из 2ГИС (JSON)
  news.json     — акции и новости (JSON)
  summary.txt   — читаемая сводка всех данных
  PROMPT.md     — готовый промт для Claude Code (создание лендинга)
  README.txt    — этот файл
  photos/       — фотографии компании (если найдены)

Как использовать PROMPT.md
--------------------------

Вариант 1 — Claude Code CLI (рекомендуется):
  1. Распакуй архив: unzip 2gis_*.zip -d landing/
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
"""
