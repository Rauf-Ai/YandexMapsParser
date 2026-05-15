"""
Диагностический скрипт — запускать ЛОКАЛЬНО (нужен интернет).

Делает реальные запросы к Яндекс Картам и печатает что реально
возвращает парсер: услуги, особенности, цены, фото, новости.

Запуск:
    cd /path/to/YandexMapsParser
    python3 tests/diagnose.py
    python3 tests/diagnose.py --query "стоматология Москва" --org-name "Улыбка"
"""

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup
from yandex_parser import SESSION, SEARCH_API, SEARCH_API_KEY, _parse_feature, _enrich, _apply_jsonld

SEP  = "─" * 70
SEP2 = "═" * 70

# ── helpers ───────────────────────────────────────────────────────────────────

def _color(text, code): return f"\033[{code}m{text}\033[0m"
def red(t):    return _color(t, 31)
def green(t):  return _color(t, 32)
def yellow(t): return _color(t, 33)
def cyan(t):   return _color(t, 36)
def bold(t):   return _color(t, 1)

def _val(v, ok_fn=None):
    if not v or v == "—":
        return red("(пусто)")
    if ok_fn and not ok_fn(v):
        return yellow(str(v))
    return green(str(v))


# ── 1. Search ─────────────────────────────────────────────────────────────────

def search_companies(query: str, n: int = 5) -> list[dict]:
    print(f"\n{SEP2}")
    print(bold(f"  ПОИСК: «{query}» (первые {n})"))
    print(SEP2)
    try:
        r = SESSION.get(SEARCH_API, params={
            "apikey": SEARCH_API_KEY, "text": query,
            "lang": "ru_RU", "type": "biz", "results": n,
        }, timeout=15)
        r.raise_for_status()
        feats = r.json().get("features", [])
    except Exception as e:
        print(red(f"  Search API error: {e}"))
        return []
    companies = [_parse_feature(f) for f in feats]
    print(f"  Найдено: {len(companies)}")
    return companies


# ── 2. Enrich + show raw JSON-LD ──────────────────────────────────────────────

def diagnose_company(company: dict, show_jsonld: bool = False):
    oid  = company.get("oid", "")
    name = company.get("name", "—")
    print(f"\n{SEP}")
    print(bold(f"  {name}"))
    print(f"  OID: {oid}  |  {company.get('address','')}")
    print(SEP)

    # — Before enrich
    print(cyan("  [До обогащения]"))
    print(f"  rating:   {_val(company['rating'])}")
    print(f"  reviews:  {_val(company['reviews'], lambda v: int(v) > 0)}")
    print(f"  site:     {_val(company['site'])}")

    # — Fetch raw HTML
    if not oid:
        print(red("  Нет OID — пропускаем"))
        return company

    url = f"https://yandex.ru/maps/org/{oid}/"
    try:
        resp = SESSION.get(url, timeout=15)
    except Exception as e:
        print(red(f"  HTTP error: {e}"))
        return company

    print(f"  HTTP status: {resp.status_code}")
    if resp.status_code != 200:
        print(red("  Страница недоступна"))
        return company

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    # — Raw JSON-LD
    jsonld_blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            jsonld_blocks.append(json.loads(tag.string or ""))
        except Exception:
            pass

    print(f"\n  JSON-LD блоков: {len(jsonld_blocks)}")

    # Show key JSON-LD fields
    for i, block in enumerate(jsonld_blocks):
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "?")
            has_catalog  = bool(item.get("hasOfferCatalog"))
            has_offers   = bool(item.get("makesOffer"))
            has_amenity  = bool(item.get("amenityFeature"))
            has_price    = bool(item.get("priceRange"))
            has_image    = bool(item.get("image"))
            graph        = item.get("@graph", [])
            if any([has_catalog, has_offers, has_amenity, has_price, has_image, graph]):
                print(f"    [{i}] @type={t}  catalog={has_catalog}  offers={has_offers}  "
                      f"amenity={has_amenity}  price={has_price}  image={has_image}  "
                      f"@graph={len(graph) if graph else 0}")

            if has_catalog and show_jsonld:
                cat = item["hasOfferCatalog"]
                items_list = (cat.get("itemListElement") or []) if isinstance(cat, dict) else []
                print(f"      hasOfferCatalog items ({len(items_list)}):")
                for it in items_list[:5]:
                    print(f"        - {it.get('name', '?')}")

            if has_amenity and show_jsonld:
                ams = item["amenityFeature"]
                print(f"      amenityFeature ({len(ams)}):")
                for a in ams[:10]:
                    print(f"        name={a.get('name','?')!r:40s}  value={a.get('value','?')!r}")

            if has_image and show_jsonld:
                img = item["image"]
                imgs = img if isinstance(img, list) else [img]
                print(f"      image ({len(imgs)}): {str(imgs[0])[:80]}")

    # — Поиск элементов по ключевым словам (находит правильные CSS классы) ──────
    print(f"\n  {cyan('Поиск элементов по ключевым словам:')}")
    keywords = ["Витрин", "showcase", "Имплант", "Стрижк", "Кофе",
                "Акци", "story", "promo", "action", "news"]
    for kw in keywords:
        for el in soup.find_all(True):
            text = el.get_text(strip=True)
            cls  = " ".join(el.get("class") or [])
            if kw.lower() in text[:80].lower() and len(text) < 200 and cls:
                print(f"    [{kw}] <{el.name} class={cls!r:.60s}> → {text[:60]!r}")
                break  # только первое совпадение на ключевое слово

    # Dump all unique class names containing 'showcase','service','feature','story','promo'
    print(f"\n  {cyan('Уникальные CSS классы (showcase/service/feature/story/promo):')}")
    interesting = set()
    for el in soup.find_all(True):
        for cls in (el.get("class") or []):
            if any(p in cls.lower() for p in
                   ["showcase", "service", "feature", "story", "promo",
                    "action", "news", "price", "vitrin"]):
                interesting.add(cls)
    for cls in sorted(interesting):
        print(f"    .{cls}")

    # Найти скрипты с JSON-данными (начальный стейт страницы — там могут быть фото)
    print(f"\n  {cyan('Script-теги с JSON (ищем фото/услуги):')} ")
    for sc in soup.find_all("script"):
        t = sc.string or ""
        if len(t) > 200 and any(w in t for w in
                                 ["avatars.mds", "photo", "showcase", "amenity"]):
            print(f"    <script> len={len(t)}  preview={t[50:130]!r}")

    # — CSS selectors: что реально есть в HTML ────────────────────────────────
    print(f"\n  {cyan('CSS-селекторы (что реально в HTML):')} ")

    checks = {
        "services": [
            ".business-services-item-view__name",
            "[class*='services-item-view'] [class*='name']",
            ".card-feature-view__title",
        ],
        "features": [
            ".business-features-view__feature",
            "[class*='features-view__feature']",
        ],
        "price": [
            ".business-prices-view__text",
            "[class*='price-view__text']",
            "[itemprop='priceRange']",
        ],
        "news": [
            "[class*='story-view__title']",
            "[class*='promotion-view__title']",
            "[class*='action-view__title']",
            "[class*='news-view__title']",
            "[class*='story-snippet__title']",
            "[class*='action-snippet__title']",
        ],
    }

    for group, sels in checks.items():
        found_any = False
        for sel in sels:
            els = soup.select(sel)
            if els:
                texts = [e.get_text(strip=True)[:60] for e in els[:5]]
                print(f"    {green('✓')} [{group}] {sel!r}")
                for t in texts:
                    print(f"        → {t!r}")
                found_any = True
        if not found_any:
            print(f"    {red('✗')} [{group}] все селекторы пустые")

    # — Photo URLs in raw HTML ─────────────────────────────────────────────────
    from org_collector import _PHOTO_RE, _extract_photo_urls, _extract_photo_urls_from_jsonld
    photo_urls = _extract_photo_urls(html)
    jld_photos = _extract_photo_urls_from_jsonld(jsonld_blocks)
    og = soup.find("meta", property="og:image")
    og_url = og.get("content", "") if og else ""

    print(f"\n  {cyan('Фото:')}")
    print(f"    regex в HTML:  {len(photo_urls)}  URL")
    for u in photo_urls[:3]:
        print(f"      {u}")
    print(f"    JSON-LD image: {len(jld_photos)}  URL")
    for u in jld_photos[:3]:
        print(f"      {u}")
    print(f"    og:image:      {og_url[:80] if og_url else red('нет')}")

    # Try photos sub-page
    try:
        pr = SESSION.get(f"https://yandex.ru/maps/org/{oid}/photos/", timeout=10)
        if pr.status_code == 200:
            extra = _extract_photo_urls(pr.text)
            print(f"    /photos/ page: {len(extra)}  URL  (HTTP {pr.status_code})")
            for u in extra[:3]:
                print(f"      {u}")
        else:
            print(f"    /photos/ page: HTTP {pr.status_code}")
    except Exception as e:
        print(f"    /photos/ page: {red(str(e))}")

    # — Run full _enrich ───────────────────────────────────────────────────────
    enriched = _enrich(company.copy())

    print(f"\n  {cyan('[После обогащения]')}")
    for field in ["rating", "reviews", "site", "social", "services", "features", "price_range"]:
        v = enriched.get(field, "")
        label = f"{field}:"
        if not v or v == "—":
            print(f"    {label:14s} {red('(пусто)')}")
        else:
            val_str = str(v)
            if len(val_str) > 100:
                val_str = val_str[:100] + "…"
            print(f"    {label:14s} {green(val_str)}")

    return enriched


# ── 3. org_collector diagnostics ──────────────────────────────────────────────

def diagnose_org_collect(company: dict):
    oid  = company.get("oid", "")
    name = company.get("name", "—")

    print(f"\n{SEP2}")
    print(bold(f"  ORG COLLECT: {name}"))
    print(SEP2)

    if not oid:
        print(red("  Нет OID"))
        return

    # Fetch main page
    resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/", timeout=15)
    print(f"  Главная страница: HTTP {resp.status_code}")
    if resp.status_code != 200:
        return
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    # Reviews page
    rev_resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/reviews/", timeout=15)
    print(f"  Страница отзывов: HTTP {rev_resp.status_code}")

    from org_collector import (
        _parse_jsonld, _extract_reviews, _extract_org_details,
        _extract_photo_urls, _extract_photo_urls_from_jsonld,
        _extract_news,
    )

    jsonld = _parse_jsonld(soup)

    # Reviews
    rsoup  = BeautifulSoup(rev_resp.text, "html.parser") if rev_resp.status_code == 200 else soup
    rjsonld = _parse_jsonld(rsoup)
    reviews = _extract_reviews(rsoup, rjsonld)
    if not reviews:
        reviews = _extract_reviews(soup, jsonld)

    print(f"\n  {cyan('Отзывы:')} найдено {len(reviews)}")
    for r in reviews[:3]:
        print(f"    [{r.get('rating','?')}★] {r.get('author','?')}: {r.get('text','')[:80]!r}")

    # Details
    details = _extract_org_details(soup, jsonld)
    print(f"\n  {cyan('Часы работы:')} {details['hours'][:80] or red('(нет)')}")
    print(f"  {cyan('Описание:')}     {details['description'][:100] or red('(нет)')}")

    # Photos
    from org_collector import _PHOTO_RE
    photo_urls = _extract_photo_urls(html)
    jld_photos = _extract_photo_urls_from_jsonld(jsonld)
    og = soup.find("meta", property="og:image")
    og_url = og.get("content", "") if og else ""

    print(f"\n  {cyan('Фото (regex HTML):')}")
    if photo_urls:
        for u in photo_urls[:5]:
            print(f"    {u}")
        # Try downloading first photo
        try:
            r = SESSION.get(photo_urls[0], timeout=10)
            ct = r.headers.get("content-type", "")
            print(f"    Тест загрузки фото[0]: HTTP {r.status_code} / {ct} / {len(r.content)} bytes")
        except Exception as e:
            print(f"    Тест загрузки: {red(str(e))}")
    else:
        print(f"    {red('Нет URL в HTML')}")

    if jld_photos:
        print(f"  {cyan('Фото (JSON-LD):')}")
        for u in jld_photos[:5]:
            print(f"    {u}")
    if og_url:
        print(f"  {cyan('og:image:')} {og_url[:80]}")

    # News
    print(f"\n  {cyan('Новости/акции:')}")
    news = _extract_news(soup)

    # Also try /actions/ page
    actions_resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/actions/", timeout=10)
    print(f"  /actions/ HTTP: {actions_resp.status_code}")
    if actions_resp.status_code == 200:
        news2 = _extract_news(BeautifulSoup(actions_resp.text, "html.parser"))
        news = list({n["text"]: n for n in news + news2}.values())

    if news:
        for n in news[:10]:
            print(f"    → {n['text']!r}")
    else:
        print(f"    {red('Ничего не найдено')}")
        # Покажем что вообще есть похожее в HTML
        promo_like = []
        for cls_pattern in ["story", "promo", "action", "news", "акци"]:
            for tag in soup.find_all(class_=re.compile(cls_pattern, re.I)):
                t = tag.get_text(strip=True)
                if 5 < len(t) < 200:
                    promo_like.append((tag.get("class"), t[:80]))
        if promo_like:
            print(f"    {yellow('Похожие элементы в HTML (для отладки):')} ")
            for cls, t in promo_like[:5]:
                print(f"      class={cls}: {t!r}")
        else:
            print(f"    {yellow('Нет похожих элементов — скорее всего JS-рендеринг')}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Диагностика парсера Яндекс Карт")
    ap.add_argument("--query",    default="стоматология Москва", help="Поисковый запрос")
    ap.add_argument("--n",        type=int, default=3,           help="Число компаний")
    ap.add_argument("--org-name", default="",                    help="Имя компании для org_collect")
    ap.add_argument("--jsonld",   action="store_true",           help="Показать детали JSON-LD")
    args = ap.parse_args()

    companies = search_companies(args.query, args.n)
    if not companies:
        print(red("Нет результатов — проверь интернет-соединение"))
        sys.exit(1)

    enriched = []
    for c in companies:
        e = diagnose_company(c, show_jsonld=args.jsonld)
        enriched.append(e)

    # Org collect для первой компании или по имени
    target = enriched[0]
    if args.org_name:
        for c in enriched:
            if args.org_name.lower() in c.get("name", "").lower():
                target = c
                break
    diagnose_org_collect(target)

    print(f"\n{SEP2}")
    print(bold("  ИТОГ"))
    print(SEP2)
    print(f"  {'Компания':30s} {'Услуги':6s} {'Особен':6s} {'Цены':6s} {'Рейтинг':8s}")
    print(f"  {'-'*30} {'-'*6} {'-'*6} {'-'*6} {'-'*8}")
    for c in enriched:
        ok = lambda v: bool(v and v != "—")
        sv = green("Да") if ok(c.get("services"))    else red("Нет")
        fv = green("Да") if ok(c.get("features"))    else red("Нет")
        pv = green("Да") if ok(c.get("price_range")) else red("Нет")
        rv = green(str(c["rating"])) if ok(str(c.get("rating",""))) else red("—")
        print(f"  {c['name'][:30]:30s} {sv:15s} {fv:15s} {pv:15s} {rv}")
    print()


if __name__ == "__main__":
    main()
