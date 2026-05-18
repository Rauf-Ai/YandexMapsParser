"""
Диагностика 2GIS API — запустите на своей машине:
  python3 debug_2gis.py
"""
import os, json, sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

KEY = os.environ.get("TWOGIS_API_KEY", "")
if not KEY:
    print("❌ TWOGIS_API_KEY не задан в .env")
    sys.exit(1)

print(f"🔑 Ключ: {KEY[:6]}…{KEY[-4:]}\n")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": "https://2gis.ru/",
})

# ── Тест 1: те же параметры что использует _search_page ──────────────────────
print("=" * 60)
print("ТЕСТ 1 — точные параметры из _search_page()")
print("=" * 60)

params = {
    "q":         "Рестораны Самара",
    "page":      1,
    "page_size": 50,
    "fields":    ("items.point,items.contact_groups,items.rubrics,"
                  "items.reviews,items.photos,items.name_ex"),
    "key":       KEY,
    "locale":    "ru_RU",
}

try:
    resp = session.get("https://catalog.api.2gis.com/3.0/items", params=params, timeout=15)
    print(f"HTTP статус: {resp.status_code}")
except Exception as e:
    print(f"❌ Сетевая ошибка: {e}")
    sys.exit(1)

if not resp.ok:
    print(f"❌ Ошибка. Тело ответа:")
    print(resp.text[:1000])
    sys.exit(1)

data  = resp.json()
items = data.get("result", {}).get("items") or data.get("items") or []
total = data.get("result", {}).get("total", "?")

print(f"Ключи верхнего уровня: {list(data.keys())}")
print(f"Всего: {total}, получено: {len(items)}")

if not items:
    print("\n⚠️  items пустой! Полный ответ:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    sys.exit(1)

first = items[0]
print(f"\n✅ Первая компания: {first.get('name')}")
print(f"   id: {first.get('id')}")

# Проверяем поля reviews
rev = first.get("reviews", {})
print(f"\n   reviews keys: {list(rev.keys())}")
print(f"   general_rating: {rev.get('general_rating')} | org_rating: {rev.get('org_rating')} | rating: {rev.get('rating')}")
print(f"   general_review_count: {rev.get('general_review_count')} | count: {rev.get('count')}")

# Проверяем contacts
cg = first.get("contact_groups")
print(f"\n   contact_groups: {'есть' if cg else 'нет'}")
if cg:
    print(f"   {json.dumps(cg, ensure_ascii=False)[:300]}")

# ── Тест 2: byid ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("ТЕСТ 2 — /3.0/items/byid")
print("=" * 60)

org_id = str(first.get("id", ""))
p2 = {
    "id":     org_id,
    "fields": "items.schedule,items.description_short,items.rubric_list,items.attribute_groups",
    "key":    KEY,
    "locale": "ru_RU",
}
r2 = session.get("https://catalog.api.2gis.com/3.0/items/byid", params=p2, timeout=15)
print(f"HTTP: {r2.status_code}")

d2     = r2.json()
items2 = d2.get("result", {}).get("items") or d2.get("items") or []
if items2:
    det = items2[0]
    print(f"Поля детальной карточки: {list(det.keys())}")
    print(f"schedule: {'есть' if det.get('schedule') else 'нет'}")
    print(f"description_short: {str(det.get('description_short', 'нет'))[:80]}")
    print(f"rubric_list: {json.dumps(det.get('rubric_list'), ensure_ascii=False)[:200] if det.get('rubric_list') else 'нет'}")
    attr = det.get("attribute_groups")
    if attr:
        print(f"attribute_groups (первая группа): {json.dumps(attr[0], ensure_ascii=False)[:300]}")
    else:
        print("attribute_groups: нет")
else:
    print("⚠️  byid вернул пустой items")
    print(json.dumps(d2, ensure_ascii=False, indent=2)[:500])

print("\n✅ Диагностика завершена.")
