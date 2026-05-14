"""Тестовые данные — 3 компании без реальных запросов к Яндексу."""

COMPANIES = [
    {
        "oid": "111111111",
        "name": "Кофейня Аромат",
        "phone": "+7 (999) 111-11-11",
        "site": "aroma-cafe.ru",
        "social": "vk.com/aroma_cafe",
        "address": "Москва, ул. Ленина, 1",
        "lat": 55.751244,
        "lon": 37.618423,
        "category": "Кофейня",
        "rating": 4.7,
        "reviews": 312,
        "has_site": "Да",
        "map_url": "https://yandex.ru/maps/org/111111111/",
        "services": "Эспрессо, Капучино, Латте, Десерты, Завтраки",
        "features": "Wi-Fi, Парковка, Оплата картой, Доставка",
        "price_range": "200–500 ₽",
    },
    {
        "oid": "222222222",
        "name": "Барбершоп Чёткий",
        "phone": "+7 (999) 222-22-22",
        "site": "—",
        "social": "—",
        "address": "Москва, ул. Пушкина, 5",
        "lat": 55.760000,
        "lon": 37.620000,
        "category": "Барбершоп",
        "rating": 4.2,
        "reviews": 87,
        "has_site": "Нет",
        "map_url": "https://yandex.ru/maps/org/222222222/",
        "services": "Стрижка, Бритьё, Укладка",
        "features": "Онлайн-запись, Оплата картой",
        "price_range": "800–2000 ₽",
    },
    {
        "oid": "333333333",
        "name": "Стоматология Улыбка",
        "phone": "+7 (999) 333-33-33",
        "site": "smile-dental.ru",
        "social": "—",
        "address": "Москва, пр. Мира, 10",
        "lat": 55.770000,
        "lon": 37.630000,
        "category": "Стоматология",
        "rating": 4.9,
        "reviews": 524,
        "has_site": "Да",
        "map_url": "https://yandex.ru/maps/org/333333333/",
        "services": "Отбеливание, Имплантация, Брекеты, Чистка",
        "features": "Парковка, Туалет, Оплата картой, Предварительная запись",
        "price_range": "1500–25000 ₽",
    },
]

# 26 компаний для теста пагинации
COMPANIES_26 = [
    {**COMPANIES[i % 3],
     "oid": str(400000000 + i),
     "name": f"Компания {i + 1}",
     "rating": round(3.5 + (i % 15) * 0.1, 1)}
    for i in range(26)
]
