# YandexMapsParser — контекст для Claude Code

## Что это

Flask-приложение: парсит Яндекс Карты → Excel + ZIP-архивы данных компаний.
Рабочая ветка: `claude/yandex-maps-parser-JFTlY`
Репозиторий: `rauf-ai/yandexmapsparser`

## Запуск

```bash
pip install -r requirements.txt
python3 app.py          # http://localhost:5000
```

## Архитектура

```
app.py              — Flask-сервер, SSE-стриминг, фоновые джобы
yandex_parser.py    — поиск по API Яндекс Карт + обогащение страниц
org_collector.py    — сбор данных одной орг (отзывы/фото/акции → ZIP)
templates/index.html
static/app.js
static/style.css
downloads/          — Excel и ZIP файлы, history.json
```

## Ключевые концепции

- **Search API**: `search-maps.yandex.ru/v1/` с ключом `dda3ddba-c9ea-4ead-9010-f43fbc15c6e3`
- **Обогащение**: GET `yandex.ru/maps/org/{oid}/` → JSON-LD + HTML fallback
- **SSE**: `/api/stream/<job_id>` — real-time прогресс через EventSource
- **Джобы**: `JOBS` dict, фоновые `threading.Thread`, `queue.Queue`
- **История**: `downloads/history.json`, последние 50 записей

## Поля компании

```python
ALL_FIELD_DEFS = OrderedDict([
    ("name", "services", "features", "price_range",  # + phone, site, social,
     "address", "lat", "lon", "category", "rating",  # address, lat, lon,
     "reviews", "has_site", "map_url")               # category, rating и т.д.
])
```

## Парсинг услуг / особенностей

Основной источник — JSON-LD:
- `hasOfferCatalog.itemListElement[].name` → `services`
- `amenityFeature[{name, value}]` → `features` (value falsy/недоступно — пропускаем)
- `priceRange` или диапазон из `makesOffer[].price` → `price_range`

CSS-fallback только если JSON-LD пуст. Важные blacklist-слова для services:
`"колл-центр", "позвонить", "обзор", "записаться"` и т.д.

Фильтрация features: исключать элементы с `₽`/`руб`, слова-значения (`доступно`, `нет`),
элементы длиннее 70 символов.

## org_collector.py

`collect_org_zip(company, out_dir, emit)` — собирает:
1. Главная страница `/maps/org/{oid}/`
2. Страница отзывов `/maps/org/{oid}/reviews/`
3. Страница акций `/maps/org/{oid}/actions/`
4. Страница фото `/maps/org/{oid}/photos/`

ZIP содержит: `info.json`, `reviews.json`, `news.json`, `summary.txt`, `PROMPT.md`
(готовый промт для создания лендинга Claude Code), `README.txt`, `photos/photo_NN.jpg`

Фото ищутся: regex на CDN `avatars.mds.yandex.net/get-{bizdir|sprav|...}`, JSON-LD
`image`/`photo` поля, `<meta og:image>`, страница `/photos/`.

`emit` принимает `(event: str, **kwargs)` — например `emit("progress", message="...")`.

## Маршруты Flask

| Метод | URL | Назначение |
|-------|-----|-----------|
| GET | `/` | Главная страница |
| POST | `/api/start` | Запуск поиска → `{job_id}` |
| GET | `/api/stream/<job_id>` | SSE прогресс |
| GET | `/api/download/<filename>` | Скачать Excel или ZIP |
| GET | `/api/results/<job_id>` | JSON результатов |
| GET | `/api/history` | История |
| DELETE | `/api/history/<id>` | Удалить запись |
| POST | `/api/start-org` | Запуск сбора данных орг → `{job_id}` |

## Фронтенд (app.js)

- `allCompanies` / `filteredRows` / `currentPage` — состояние таблицы
- `selectedFeatureTags`, `selectedServiceTags` — мультиселект AND-фильтр
- `activeQuickFilters` — быстрые фильтры (парковка, wifi, онлайн-запись, коляски)
- `buildCloud(companies, field, cloudEl, selectedSet, chipClass)` — таг-облако
- `renderTablePage()` — рендерит текущую страницу таблицы (25 строк)
- `renderTags(raw, type)` — показывает ≤3 чипа + «+N ещё» с раскрытием по клику
- `startOrgCollect(company)` → POST `/api/start-org` → `startCollectSSE(jobId)`
- `renderHistory(entries)` — тип `org_collect` показывает 📦 + ZIP-кнопку

## CSS-классы (style.css)

- `.tag-chip--srv` (синий), `.tag-chip--ftr` (зелёный), `.tag-chip--more` (серый)
- `.cloud-tag.srv-tag.active` / `.cloud-tag.ftr-tag.active`
- `.quick-filter-btn` + `.active` — быстрые фильтры
- `.collect-overlay` — всплывающая панель прогресса выгрузки
- `.btn-collect` — кнопка 📦 в строке таблицы
- `.history-item--org` — запись в истории для org_collect

## Известные ограничения

- Яндекс Карты — React SPA: большинство данных (услуги, фото, акции)
  **не рендерится в статическом HTML**. Основной источник — JSON-LD (SEO-данные).
- Сторис/акции загружаются динамически — CSS-селекторы часто пустые.
  В PROMPT.md добавлена подсказка: пользователь может добавить скриншоты в `promo/`.
- Search API ограничен: `skip` работает до ~100 результатов, потом пустые ответы.

## Что планировалось следующим

1. **Лендинг через Claude API** — кнопка «Создать лендинг» рядом с ZIP,
   отправляет PROMPT.md + данные в Claude API и возвращает `index.html`
2. **Computer Use тестирование** — автоматический прогон UI через Docker +
   `ghcr.io/anthropics/anthropic-quickstarts:computer-use-demo-latest`
   (нужен `ANTHROPIC_API_KEY` в окружении)

## Git

```bash
git push -u origin claude/yandex-maps-parser-JFTlY
```

Коммиты заканчиваются ссылкой на сессию Claude Code.
