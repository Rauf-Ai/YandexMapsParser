"""
Проверяет что видит Playwright на странице 2GIS.
Запуск: python3 debug_playwright_2gis.py
"""
import sys, re

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("❌ Playwright не установлен:")
    print("   python3 -m pip install playwright")
    print("   python3 -m playwright install chromium")
    sys.exit(1)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

ORG_URL = "https://2gis.ru/firm/1267165676908780"  # Bukowski grill

print(f"Открываем: {ORG_URL}\n")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent=UA,
        locale="ru-RU",
        ignore_https_errors=True,
        extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
    )
    page = ctx.new_page()

    # Загружаем страницу
    try:
        page.goto(ORG_URL, wait_until="domcontentloaded", timeout=25_000)
        print("✅ Страница загружена (domcontentloaded)")
    except PWTimeout:
        print("⚠️  Timeout на domcontentloaded, продолжаем...")

    page.wait_for_timeout(3_000)

    print(f"📄 Title: {page.title()}")
    print(f"🔗 URL после загрузки: {page.url}")
    print(f"📏 HTML size: {len(page.content())} bytes\n")

    # Телефоны через JS — ищем все tel: ссылки
    phones = page.evaluate("""
        () => [...document.querySelectorAll('a[href^="tel:"]')]
             .map(a => a.href + ' | text: ' + a.innerText.trim())
    """)
    print("=" * 50)
    print(f"Телефонные ссылки tel: ({len(phones)} шт.):")
    for p in phones[:10]:
        print(f"  {p}")
    if not phones:
        print("  Нет!")

    # Внешние ссылки
    ext_links = page.evaluate("""
        () => [...document.querySelectorAll('a[href^="http"]')]
             .map(a => a.href)
             .filter(h => !h.includes('2gis.ru') && !h.includes('flamp.ru'))
    """)
    print(f"\nВнешние ссылки ({len(ext_links)} шт.):")
    seen = set()
    for lnk in ext_links[:20]:
        if lnk not in seen:
            seen.add(lnk)
            print(f"  {lnk[:100]}")

    # Кнопки "показать" / "Позвонить"
    btns = page.evaluate("""
        () => [...document.querySelectorAll('button')]
             .map(b => b.innerText.trim())
             .filter(t => t.length > 0 && t.length < 50)
    """)
    print(f"\nТекст кнопок на странице:")
    for b in btns[:20]:
        print(f"  [{b}]")

    # JSON-LD
    jsonld = page.evaluate("""
        () => [...document.querySelectorAll('script[type="application/ld+json"]')]
             .map(s => s.textContent)
    """)
    print(f"\nJSON-LD блоков: {len(jsonld)}")
    for i, block in enumerate(jsonld, 1):
        print(f"  Блок {i}: {block[:300]}")

    # Сохраним скриншот для визуальной проверки
    try:
        page.screenshot(path="debug_2gis_screenshot.png", full_page=False)
        print("\n📸 Скриншот: debug_2gis_screenshot.png")
    except Exception as e:
        print(f"\nСкриншот не удался: {e}")

    browser.close()

print("\n✅ Готово.")
