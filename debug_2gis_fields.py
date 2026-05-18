"""
Перебирает разные варианты полей 2GIS API чтобы найти телефоны.
Запуск: python3 debug_2gis_fields.py
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
    print("❌ TWOGIS_API_KEY не задан"); sys.exit(1)

print(f"🔑 Ключ: {KEY[:6]}…{KEY[-4:]}\n")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
    "Referer": "https://2gis.ru/",
})

ORG_ID = "1267165676908780"   # Bukowski grill

print("=" * 60)
print("ТЕСТ: byid с разными наборами полей")
print("=" * 60)

field_variants = [
    "items.contact_groups",
    "items.contacts",
    "items.links",
    "items.phone_number",
    "items.org",
    "items.contact_groups,items.links,items.org",
    # Запросить ВСЁ
    ("items.schedule,items.description_short,items.rubric_list,"
     "items.attribute_groups,items.photos,items.contact_groups,"
     "items.links,items.org,items.external_content,items.flags"),
]

for fields in field_variants:
    params = {"id": ORG_ID, "fields": fields, "key": KEY, "locale": "ru_RU"}
    r = session.get("https://catalog.api.2gis.com/3.0/items/byid",
                    params=params, timeout=15)
    items = r.json().get("result", {}).get("items", [])
    detail = items[0] if items else {}
    keys = sorted(detail.keys())
    has_contact = any("contact" in k or "phone" in k or "link" in k for k in keys)
    marker = "✅" if has_contact else "  "
    print(f"{marker} fields={fields[:60]}")
    print(f"     → ключи: {keys}")
    if has_contact:
        for k in keys:
            if "contact" in k or "phone" in k or "link" in k:
                print(f"     → {k}: {json.dumps(detail[k], ensure_ascii=False)[:300]}")
    print()

print("=" * 60)
print("ТЕСТ: поиск через /3.0/items с расширенными полями")
print("=" * 60)

params = {
    "q": "Bukowski grill Екатеринбург",
    "page": 1,
    "page_size": 3,
    "fields": ("items.point,items.contact_groups,items.rubrics,items.reviews,"
               "items.links,items.org,items.external_content"),
    "key": KEY,
    "locale": "ru_RU",
}
r = session.get("https://catalog.api.2gis.com/3.0/items", params=params, timeout=15)
items = r.json().get("result", {}).get("items", [])
if items:
    first = items[0]
    print(f"Найдено: {first.get('name')}")
    print(f"Все ключи: {sorted(first.keys())}")
    for k in sorted(first.keys()):
        if "contact" in k or "phone" in k or "link" in k or "org" in k:
            print(f"  {k}: {json.dumps(first[k], ensure_ascii=False)[:200]}")
else:
    print("Ничего не найдено")
