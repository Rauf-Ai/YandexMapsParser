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

BRANCH_ID = "1267165676908780"   # Bukowski grill — ID филиала

# ── Шаг 1: получить org.id из byid ──────────────────────────────────────────
print("=" * 60)
print("Шаг 1: получаем org.id для филиала")
print("=" * 60)

r = session.get("https://catalog.api.2gis.com/3.0/items/byid",
                params={"id": BRANCH_ID, "fields": "items.org", "key": KEY, "locale": "ru_RU"},
                timeout=15)
items = r.json().get("result", {}).get("items", [])
detail = items[0] if items else {}
org    = detail.get("org", {})
ORG_ID = org.get("id", "")
print(f"  branch_id : {BRANCH_ID}")
print(f"  org.id    : {ORG_ID}")
print(f"  org       : {json.dumps(org, ensure_ascii=False)}")

if not ORG_ID:
    print("\n❌ org.id не найден"); sys.exit(1)

# ── Шаг 2: запросить contact_groups по org.id ────────────────────────────────
print("\n" + "=" * 60)
print(f"Шаг 2: byid с org.id={ORG_ID}")
print("=" * 60)

for fields in [
    "items.contact_groups",
    "items.contact_groups,items.links",
    "items.contact_groups,items.description_short,items.rubric_list",
]:
    r2 = session.get("https://catalog.api.2gis.com/3.0/items/byid",
                     params={"id": ORG_ID, "fields": fields, "key": KEY, "locale": "ru_RU"},
                     timeout=15)
    items2 = r2.json().get("result", {}).get("items", [])
    det2   = items2[0] if items2 else {}
    keys2  = sorted(det2.keys())
    has_cg = "contact_groups" in keys2
    marker = "✅" if has_cg else "  "
    print(f"{marker} fields={fields[:70]}")
    print(f"     ключи: {keys2}")
    if has_cg:
        print(f"     contact_groups: {json.dumps(det2['contact_groups'], ensure_ascii=False)[:500]}")
    print()
