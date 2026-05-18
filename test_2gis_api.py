"""Quick diagnostic — run once to check the 2GIS API key works."""
import os, sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

key = os.environ.get("TWOGIS_API_KEY", "")
if not key:
    print("❌  TWOGIS_API_KEY не задан. Создайте .env с ключом:")
    print("    echo 'TWOGIS_API_KEY=ваш_ключ' > .env")
    sys.exit(1)

print(f"🔑  Ключ: {key[:6]}…{key[-4:]}")

url    = "https://catalog.api.2gis.com/3.0/items"
params = {"q": "кафе", "page_size": 3, "key": key, "locale": "ru_RU"}

try:
    resp = requests.get(url, params=params, timeout=10)
except Exception as e:
    print(f"❌  Сетевая ошибка: {e}")
    sys.exit(1)

if resp.status_code == 200:
    items = resp.json().get("result", {}).get("items", [])
    print(f"✅  API работает! Первые результаты по «кафе»:")
    for c in items:
        print(f"   • {c.get('name', '?')}  —  {c.get('address_name', '')}")
elif resp.status_code in (401, 403):
    print(f"❌  Ключ отклонён ({resp.status_code}). Проверьте ключ на dev.2gis.ru")
    try:
        print("   Ответ API:", resp.json())
    except Exception:
        pass
    sys.exit(1)
else:
    print(f"❌  Неожиданный ответ: {resp.status_code}")
    print("   Тело:", resp.text[:300])
    sys.exit(1)
