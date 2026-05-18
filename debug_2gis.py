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
    "Referer": "https://2gis.ru/",
})

# ── Тест 1: сырой ответ API ──────────────────────────────────────────────────
print("=" * 60)
print("ТЕСТ 1 — сырой ответ /3.0/items")
print("=" * 60)

params = {
    "q":         "Рестораны Самара",
    "page":      1,
    "page_size": 5,
    "fields":    "items.point,items.contact_groups,items.rubrics,items.reviews",
    "key":       KEY,
    "locale":    "ru_RU",
}

try:
    resp = session.get("https://catalog.api.2gis.com/3.0/items", params=params, timeout=15)
    print(f"HTTP статус: {resp.status_code}")
    print(f"URL: {resp.url}\n")
except Exception as e:
    print(f"❌ Сетевая ошибка: {e}")
    sys.exit(1)

if resp.status_code != 200:
    print(f"❌ Ошибка API. Тело ответа:")
    print(resp.text[:1000])
    sys.exit(1)

data = resp.json()

# Показываем структуру верхнего уровня
print("Ключи верхнего уровня:", list(data.keys()))

# Проверяем разные возможные структуры
items = (
    data.get("result", {}).get("items")         # ожидаемое
    or data.get("items")                         # альтернатива
    or data.get("result", {}).get("result", {}).get("items")  # вложенное
    or []
)

total = (data.get("result", {}).get("total")
         or data.get("total")
         or "?")

print(f"Всего результатов (total): {total}")
print(f"Элементов в items: {len(items)}")

if not items:
    print("\n⚠️  items пустой! Полный ответ API:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
else:
    print(f"\n✅ Найдено {len(items)} организаций. Первая:")
    first = items[0]
    print(json.dumps(first, ensure_ascii=False, indent=2)[:1500])

# ── Тест 2: проверяем byid ───────────────────────────────────────────────────
if items:
    print("\n" + "=" * 60)
    print("ТЕСТ 2 — /3.0/items/byid для первой найденной орг")
    print("=" * 60)

    org_id = str(items[0].get("id", ""))
    print(f"org_id: {org_id}")

    p2 = {
        "id":     org_id,
        "fields": "items.schedule,items.description_short,items.rubric_list,items.attribute_groups",
        "key":    KEY,
        "locale": "ru_RU",
    }
    r2 = session.get("https://catalog.api.2gis.com/3.0/items/byid", params=p2, timeout=15)
    print(f"HTTP статус: {r2.status_code}")

    d2 = r2.json()
    items2 = d2.get("result", {}).get("items") or d2.get("items") or []
    if items2:
        detail = items2[0]
        print("Поля детальной карточки:", list(detail.keys()))
        sched = detail.get("schedule")
        print("schedule:", json.dumps(sched, ensure_ascii=False)[:300] if sched else "нет")
        desc = detail.get("description_short")
        print("description_short:", desc[:100] if desc else "нет")
        rl = detail.get("rubric_list")
        print("rubric_list:", json.dumps(rl, ensure_ascii=False)[:200] if rl else "нет")
    else:
        print("⚠️  byid вернул пустой items. Полный ответ:")
        print(json.dumps(d2, ensure_ascii=False, indent=2)[:1000])
