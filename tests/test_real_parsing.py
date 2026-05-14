"""
Тесты с реальными запросами к Яндекс Картам.

ЗАПУСКАТЬ ТОЛЬКО ЛОКАЛЬНО (нужен интернет):
    pytest tests/test_real_parsing.py -v -s --no-header

Тесты намеренно МЯГКИЕ: они печатают что нашли и помечают
проблемные поля как warnings, не падают. Цель — показать
что реально возвращает парсер, чтобы можно было починить.

После прогона создаётся файл: tests/real_parse_report.json
"""

import json
import re
import time
from pathlib import Path
from io import BytesIO
import zipfile

import pytest
import requests as _req

# Пропустить если нет интернета / Яндекс недоступен
def _yandex_available():
    try:
        from yandex_parser import SESSION, SEARCH_API, SEARCH_API_KEY
        r = SESSION.get(SEARCH_API, params={
            "apikey": SEARCH_API_KEY, "text": "тест", "lang": "ru_RU",
            "type": "biz", "results": 1,
        }, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(
    not _yandex_available(),
    reason="Яндекс Карты недоступны — запускай локально"
)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from yandex_parser import SESSION, SEARCH_API, SEARCH_API_KEY, _parse_feature, _enrich

# Компании для тестирования — разные категории
TEST_QUERIES = [
    ("стоматология Москва",    3),
    ("кофейня Москва",         2),
    ("автосервис Москва",      2),
]

REPORT = {}   # глобальный отчёт, сохраняется в конце


def _search(query, n=3):
    r = SESSION.get(SEARCH_API, params={
        "apikey": SEARCH_API_KEY, "text": query, "lang": "ru_RU",
        "type": "biz", "results": n,
    }, timeout=15)
    r.raise_for_status()
    return [_parse_feature(f) for f in r.json().get("features", [])]


# ── helpers ───────────────────────────────────────────────────────────────────

def _check_field(company, field, label):
    v = company.get(field, "")
    ok = bool(v and v != "—" and str(v).strip())
    status = "✓" if ok else "✗ ПУСТО"
    print(f"    {status:10s} {label}: {str(v)[:120]}")
    return ok


# ── 1. Услуги ─────────────────────────────────────────────────────────────────

class TestServices:

    def test_services_parsed_for_clinic(self):
        """Стоматология должна иметь услуги (удаление, имплантация и т.д.)."""
        companies = _search("стоматология Москва", 5)
        assert companies, "Search API вернул 0 результатов"

        results = []
        for c in companies:
            enriched = _enrich(c.copy())
            has = bool(enriched.get("services") and enriched["services"] != "—")
            print(f"\n  {c['name']}")
            _check_field(enriched, "services", "Услуги")

            # Проверяем что не попали навигационные слова
            svc = enriched.get("services", "")
            bad_words = ["обзор", "колл-центр", "позвонить", "фото", "отзывы"]
            bad_found = [w for w in bad_words if w in svc.lower()]
            if bad_found:
                print(f"    ⚠ МУСОР В УСЛУГАХ: {bad_found}")
            results.append({"name": c["name"], "services": svc, "bad": bad_found})
            time.sleep(0.5)

        REPORT["services_clinic"] = results
        filled = sum(1 for r in results if r["services"])
        print(f"\n  Итог: {filled}/{len(results)} компаний с услугами")
        # Предупреждение если совсем ничего нет (не падаем)
        if filled == 0:
            pytest.warns(UserWarning, match="no services")

    def test_services_not_navigation_junk(self):
        """Услуги не должны содержать навигационные кнопки интерфейса."""
        companies = _search("кофейня Москва", 3)
        bad_words = {"обзор", "колл-центр", "позвонить", "маршрут",
                     "написать", "забронировать"}
        problems = []
        for c in companies:
            enriched = _enrich(c.copy())
            svc = enriched.get("services", "").lower()
            found = [w for w in bad_words if w in svc]
            if found:
                problems.append(f"{c['name']}: {found}")
                print(f"  ⚠ Мусор в услугах [{c['name']}]: {found}")
            time.sleep(0.5)

        REPORT["services_junk"] = problems
        assert not problems, f"Навигационный мусор в услугах: {problems}"


# ── 2. Особенности ────────────────────────────────────────────────────────────

class TestFeatures:

    def test_features_no_price_items(self):
        """Особенности не должны содержать строки с ценами (₽)."""
        companies = _search("клиника Москва", 4)
        problems = []
        for c in companies:
            enriched = _enrich(c.copy())
            ftr = enriched.get("features", "")
            print(f"\n  {c['name']}")
            _check_field(enriched, "features", "Особенности")
            if "₽" in ftr or "руб" in ftr.lower():
                problems.append(f"{c['name']}: {ftr[:100]}")
                print(f"    ⚠ ЦЕНЫ В ОСОБЕННОСТЯХ: {ftr[:100]}")
            time.sleep(0.5)

        REPORT["features_price_leak"] = problems
        assert not problems, f"Цены попали в особенности: {problems}"

    def test_features_no_value_words(self):
        """Особенности не должны содержать слова-значения ('доступно', 'нет')."""
        companies = _search("медицинский центр Москва", 4)
        value_words = {"доступно", "недоступно", "да", "нет"}
        problems = []
        for c in companies:
            enriched = _enrich(c.copy())
            ftr = enriched.get("features", "")
            parts = [p.strip().lower() for p in ftr.split(",")]
            bad = [p for p in parts if p in value_words]
            if bad:
                problems.append(f"{c['name']}: {bad}")
                print(f"  ⚠ Слова-значения в особенностях [{c['name']}]: {bad}")
            time.sleep(0.5)

        REPORT["features_value_words"] = problems
        assert not problems, f"Слова-значения в особенностях: {problems}"

    def test_features_reasonable_length(self):
        """Каждая особенность — не длиннее 70 символов."""
        companies = _search("спортзал Москва", 3)
        problems = []
        for c in companies:
            enriched = _enrich(c.copy())
            ftr = enriched.get("features", "")
            long_items = [p for p in ftr.split(",") if len(p.strip()) > 70]
            if long_items:
                problems.append(f"{c['name']}: {long_items}")
                print(f"  ⚠ Длинные элементы в особенностях [{c['name']}]: {long_items}")
            time.sleep(0.5)

        REPORT["features_too_long"] = problems
        assert not problems, f"Слишком длинные элементы в особенностях: {problems}"


# ── 3. Цены ───────────────────────────────────────────────────────────────────

class TestPriceRange:

    def test_price_found_for_clinic(self):
        """Клиника должна иметь диапазон цен."""
        companies = _search("частная клиника Москва", 5)
        results = []
        for c in companies:
            enriched = _enrich(c.copy())
            pr = enriched.get("price_range", "")
            ok = bool(pr and pr != "—")
            print(f"  {'✓' if ok else '✗':3s} {c['name'][:40]:40s} цены: {pr[:50]}")
            results.append({"name": c["name"], "price_range": pr})
            time.sleep(0.5)

        REPORT["price_clinic"] = results
        filled = sum(1 for r in results if r["price_range"])
        print(f"\n  Итог: {filled}/{len(results)} компаний с ценами")
        # Мягкое предупреждение
        if filled == 0:
            print("  ⚠ Ни у одной клиники не нашли цены — проблема в парсере")

    def test_price_not_empty_for_beauty(self):
        """Салон красоты / барбершоп часто имеет priceRange."""
        companies = _search("барбершоп Москва", 4)
        results = []
        for c in companies:
            enriched = _enrich(c.copy())
            pr = enriched.get("price_range", "")
            print(f"  {c['name'][:40]:40s} → {pr or '(пусто)'}")
            results.append(pr)
            time.sleep(0.5)

        REPORT["price_beauty"] = results
        filled = sum(1 for p in results if p)
        print(f"  Итог: {filled}/{len(results)} с ценами")


# ── 4. Фото ───────────────────────────────────────────────────────────────────

class TestPhotos:

    def test_photos_found_in_html(self):
        """Хотя бы несколько фото должны найтись через regex в HTML."""
        from org_collector import _extract_photo_urls, _extract_photo_urls_from_jsonld, _parse_jsonld

        companies = _search("ресторан Москва", 3)
        results = []
        for c in companies:
            oid = c.get("oid", "")
            if not oid:
                continue
            resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/", timeout=15)
            if resp.status_code != 200:
                continue

            html = resp.text
            soup_obj = __import__("bs4").BeautifulSoup(html, "html.parser")
            jsonld = _parse_jsonld(soup_obj)

            regex_urls  = _extract_photo_urls(html)
            jsonld_urls = _extract_photo_urls_from_jsonld(jsonld)
            og = soup_obj.find("meta", property="og:image")
            og_url = (og.get("content", "") if og else "")

            total = len(set(regex_urls + jsonld_urls + ([og_url] if og_url else [])))
            print(f"\n  {c['name']}")
            print(f"    regex: {len(regex_urls)}  jsonld: {len(jsonld_urls)}  og: {bool(og_url)}")
            for u in (regex_urls + jsonld_urls)[:3]:
                print(f"    {u}")

            results.append({
                "name": c["name"],
                "regex": len(regex_urls),
                "jsonld": len(jsonld_urls),
                "og": bool(og_url),
                "total": total,
            })
            time.sleep(0.8)

        REPORT["photos"] = results
        has_any = any(r["total"] > 0 for r in results)
        if not has_any:
            print("  ⚠ Ни одного фото не найдено ни одним методом!")
        assert results, "Нет компаний для проверки"

    def test_photo_downloadable(self):
        """Найденные URL фото должны скачиваться (200 + content-type: image)."""
        from org_collector import _extract_photo_urls, _extract_photo_urls_from_jsonld, _parse_jsonld

        companies = _search("гостиница Москва", 2)
        for c in companies:
            oid = c.get("oid", "")
            if not oid:
                continue
            resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/", timeout=15)
            if resp.status_code != 200:
                continue

            html = resp.text
            bs4 = __import__("bs4")
            soup_obj = bs4.BeautifulSoup(html, "html.parser")
            jsonld = _parse_jsonld(soup_obj)

            urls = (_extract_photo_urls(html) +
                    _extract_photo_urls_from_jsonld(jsonld))

            if not urls:
                print(f"  {c['name']}: фото не найдены — пропускаем")
                continue

            print(f"\n  {c['name']}: проверяем {min(3, len(urls))} фото")
            errors = []
            for url in urls[:3]:
                try:
                    r = SESSION.get(url, timeout=10)
                    ct = r.headers.get("content-type", "")
                    ok = r.status_code == 200 and "image" in ct
                    print(f"    {'✓' if ok else '✗'} {r.status_code} {ct} {len(r.content)}b  {url[:60]}")
                    if not ok:
                        errors.append(url)
                except Exception as e:
                    print(f"    ✗ Error: {e}")
                    errors.append(url)
                time.sleep(0.3)

            REPORT.setdefault("photo_download", []).extend(errors)

        if REPORT.get("photo_download"):
            pytest.fail(f"Не скачались фото: {REPORT['photo_download']}")


# ── 5. Новости / акции ────────────────────────────────────────────────────────

class TestNews:

    def test_news_extraction_not_ui_junk(self):
        """
        Новости не должны содержать навигационные строки интерфейса.
        Типичный мусор: 'Обзор', 'Фото', 'Отзывы', 'Условия'.
        """
        from org_collector import _extract_news, _parse_jsonld
        bs4 = __import__("bs4")

        ui_junk = {"обзор", "фото", "отзывы", "условия", "контакты",
                   "информация", "маршрут", "позвонить", "написать"}

        companies = _search("ресторан Москва", 3)
        problems = []
        for c in companies:
            oid = c.get("oid", "")
            if not oid:
                continue
            resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/", timeout=15)
            if resp.status_code != 200:
                continue

            soup = bs4.BeautifulSoup(resp.text, "html.parser")
            news = _extract_news(soup)

            print(f"\n  {c['name']}: {len(news)} новостей")
            for n in news[:5]:
                t = n.get("text", "")
                is_junk = t.strip().lower() in ui_junk
                mark = "⚠ МУСОР" if is_junk else "✓"
                print(f"    {mark}: {t[:80]!r}")
                if is_junk:
                    problems.append(f"{c['name']}: {t!r}")
            time.sleep(0.5)

        REPORT["news_junk"] = problems
        assert not problems, f"UI-мусор в новостях: {problems}"

    def test_actions_page_fetched(self):
        """Страница /actions/ должна возвращать 200 или 404 (не 500)."""
        companies = _search("кафе Москва", 2)
        for c in companies:
            oid = c.get("oid", "")
            if not oid:
                continue
            resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/actions/", timeout=10)
            print(f"  {c['name']}: /actions/ → HTTP {resp.status_code}")
            assert resp.status_code in (200, 404, 302, 301), \
                f"Неожиданный статус {resp.status_code} для /actions/"
            time.sleep(0.5)


# ── 6. org_collect ZIP ────────────────────────────────────────────────────────

class TestOrgCollectZip:

    def test_zip_contains_required_files(self, tmp_path):
        """ZIP должен содержать info.json, reviews.json, PROMPT.md, README.txt."""
        from org_collector import collect_org_zip

        companies = _search("ресторан Москва", 1)
        assert companies
        c = companies[0]
        print(f"\n  Сбор данных: {c['name']}")

        fname = collect_org_zip(c, tmp_path, emit=lambda ev, **kw: print(f"    [{ev}] {kw.get('message','')}"))
        zpath = tmp_path / fname

        assert zpath.exists(), f"ZIP не создан: {fname}"
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            print(f"  Файлы в ZIP: {names}")
            for required in ("info.json", "reviews.json", "PROMPT.md", "README.txt"):
                assert required in names, f"Нет {required} в ZIP"

            info = json.loads(zf.read("info.json"))
            reviews = json.loads(zf.read("reviews.json"))
            print(f"  Отзывов: {len(reviews)}")
            print(f"  Фото: {sum(1 for n in names if n.startswith('photos/'))}")
            print(f"  Услуги: {info.get('services','(нет)')[:80]}")
            print(f"  Особенности: {info.get('features','(нет)')[:80]}")

        REPORT["zip"] = {
            "name": c["name"],
            "files": names,
            "reviews": len(reviews),
            "services": info.get("services", ""),
            "features": info.get("features", ""),
        }

    def test_zip_photos_downloadable(self, tmp_path):
        """Если фото найдены — они должны реально скачаться в ZIP."""
        from org_collector import collect_org_zip

        companies = _search("отель Москва", 1)
        assert companies
        c = companies[0]
        print(f"\n  Сбор данных (фото): {c['name']}")

        fname = collect_org_zip(c, tmp_path, max_photos=5,
                                emit=lambda ev, **kw: print(f"    [{ev}] {kw.get('message','')}"))
        with zipfile.ZipFile(tmp_path / fname) as zf:
            photo_files = [n for n in zf.namelist() if n.startswith("photos/")]
            print(f"  Фото в ZIP: {len(photo_files)}")
            for pf in photo_files:
                data = zf.read(pf)
                print(f"    {pf}: {len(data)} bytes")
                assert len(data) > 1000, f"Фото {pf} слишком маленькое ({len(data)} bytes)"

        REPORT["zip_photos"] = {"name": c["name"], "count": len(photo_files)}


# ── Сохранение отчёта ─────────────────────────────────────────────────────────

def pytest_sessionfinish(session, exitstatus):
    report_path = Path(__file__).parent / "real_parse_report.json"
    try:
        report_path.write_text(
            json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  📄 Отчёт сохранён: {report_path}")
    except Exception as e:
        print(f"  Не удалось сохранить отчёт: {e}")
