"""
Проверяет что реально находится в HTML страницы 2GIS org.
Запуск: python3 debug_2gis_contacts.py
"""
import re, sys, json

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

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

# Bukowski grill из вашего Excel
ORG_ID  = "1267165676908780"
ORG_URL = f"https://2gis.ru/firm/{ORG_ID}"

print(f"Fetching: {ORG_URL}\n")
resp = session.get(ORG_URL, timeout=15)
print(f"HTTP: {resp.status_code}  |  Content-Length: {len(resp.text)} bytes")
print(f"Final URL: {resp.url}\n")

html = resp.text

# ── 1. JSON-LD ──────────────────────────────────────────────────────────────
print("=" * 60)
print("JSON-LD блоки на странице:")
print("=" * 60)
ld_blocks = re.findall(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    html, re.DOTALL | re.IGNORECASE)
if ld_blocks:
    for i, block in enumerate(ld_blocks, 1):
        block = block.strip()
        print(f"\n--- Блок {i} ---")
        try:
            data = json.loads(block)
            print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
        except Exception:
            print(block[:500])
else:
    print("JSON-LD не найден!")

# ── 2. tel: links ───────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("tel: ссылки в HTML:")
print("=" * 60)
phones = re.findall(r'tel:([+\d\-\(\)\s]{7,20})', html)
if phones:
    for p in phones[:10]:
        print(" ", p.strip())
else:
    print("Нет tel: ссылок")

# ── 3. External links ────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Внешние ссылки (href):")
print("=" * 60)
SKIP = {"2gis.ru", "flamp.ru", "yandex.", "google.", "apple.", "schema.org", "w3.org", "disk.2gis"}
hrefs = re.findall(r'href=["\']?(https?://[^\s"\'<>]+)', html)
seen = set()
for h in hrefs:
    if any(d in h for d in SKIP):
        continue
    if h in seen:
        continue
    seen.add(h)
    print(" ", h[:120])
    if len(seen) >= 20:
        break
if not seen:
    print("Нет внешних ссылок")

# ── 4. window.__data / initial state ──────────────────────────────────────
print("\n" + "=" * 60)
print("Поиск данных в script-тегах (window.__*, __NEXT_DATA__, etc.):")
print("=" * 60)
for pattern in [r'__NEXT_DATA__\s*=\s*(\{.{0,500})',
                r'window\.__data\s*=\s*(\{.{0,500})',
                r'"telephone"\s*:\s*"([^"]+)"',
                r'"phone"\s*:\s*"([^"]+)"',
                r'"website"\s*:\s*"([^"]+)"']:
    m = re.search(pattern, html)
    if m:
        print(f"  Найдено [{pattern[:40]}...]: {m.group(1)[:200]}")

# ── 5. Size check ────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"Размер HTML: {len(html)} байт")
if len(html) < 5000:
    print("⚠️  Очень маленький HTML — вероятно React shell без SSR данных")
    print("\nПервые 2000 символов HTML:")
    print(html[:2000])
