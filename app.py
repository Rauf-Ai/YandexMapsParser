"""
Flask web interface for the Yandex Maps Parser.
Streams progress via Server-Sent Events (SSE).
"""

import json
import logging
import os
import queue
import re
import threading
import uuid
from datetime import date
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
)

# Reuse core logic from parser.py
from parser import (
    COLUMNS,
    _dedup_key,
    _fmt_phone,
    _clean_site,
    _parse_feature,
    _scrape_page,
    _sleep,
    export_excel,
    SEARCH_API,
    SEARCH_API_KEY,
    SESSION,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

JOBS: dict[str, dict] = {}   # job_id → { status, companies, filename, error, queue }
DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", "downloads"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job runner (background thread)
# ---------------------------------------------------------------------------

def _search_page_with_progress(query: str, skip: int) -> list[dict]:
    from urllib.parse import quote
    params = {
        "apikey": SEARCH_API_KEY,
        "text": query,
        "lang": "ru_RU",
        "type": "biz",
        "results": 10,
        "skip": skip,
    }
    try:
        resp = SESSION.get(SEARCH_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("API skip=%d: %s", skip, exc)
        return []
    features = data.get("features", [])
    return [_parse_feature(f.get("properties", {}), f.get("geometry", {})) for f in features]


def _run_job(job_id: str, query: str, max_companies: int):
    job = JOBS[job_id]
    q: queue.Queue = job["queue"]

    def emit(event: str, **data):
        q.put({"event": event, "data": data})

    try:
        emit("start", message=f"Начинаем сбор: «{query}»")
        companies = []
        seen: set[tuple] = set()
        skip = 0
        page = 1
        empty_streak = 0

        while len(companies) < max_companies:
            emit("progress",
                 found=len(companies),
                 total=max_companies,
                 message=f"Запрос {skip + 1}–{skip + 10}…")

            batch = _search_page_with_progress(query, skip)
            if not batch:
                emit("progress",
                     found=len(companies),
                     total=max_companies,
                     message=f"API пустой, HTML-fallback (стр. {page})…")
                batch = _scrape_page(query, page)

            if not batch:
                empty_streak += 1
                if empty_streak >= 3:
                    emit("progress",
                         found=len(companies),
                         total=max_companies,
                         message="Результатов больше нет.")
                    break
            else:
                empty_streak = 0

            for c in batch:
                key = _dedup_key(c)
                if key in seen:
                    continue
                seen.add(key)
                companies.append(c)
                if len(companies) >= max_companies:
                    break

            skip += 10
            page += 1
            _sleep()

        if not companies:
            emit("error", message="Не удалось собрать ни одной компании.")
            job["status"] = "error"
            job["error"] = "Нет результатов"
            return

        # Export
        emit("progress",
             found=len(companies),
             total=max_companies,
             message="Формируем Excel-файл…")

        safe_q = re.sub(r"[^\w\-а-яА-Я]", "_", query)[:40]
        filename = f"yandex_maps_{safe_q}_{date.today()}.xlsx"
        filepath = DOWNLOADS_DIR / filename
        _export_to_path(companies, filepath)

        # Stats
        total = len(companies)
        with_site = sum(1 for c in companies if c["has_site"] == "Да")
        with_phone = sum(1 for c in companies if c["phone"] != "—")
        ratings = [c["rating"] for c in companies if isinstance(c["rating"], float)]
        avg = round(sum(ratings) / len(ratings), 1) if ratings else 0.0

        job["status"] = "done"
        job["filename"] = filename
        job["companies"] = companies[:5]  # preview

        emit("done",
             filename=filename,
             total=total,
             with_site=with_site,
             without_site=total - with_site,
             with_phone=with_phone,
             avg_rating=avg,
             pct_site=round(with_site / total * 100) if total else 0,
             pct_phone=round(with_phone / total * 100) if total else 0,
             preview=[{
                 "name": c["name"],
                 "address": c["address"],
                 "phone": c["phone"],
                 "rating": c["rating"],
                 "has_site": c["has_site"],
             } for c in companies[:8]])

    except Exception as exc:
        log.exception("Job %s crashed", job_id)
        job["status"] = "error"
        job["error"] = str(exc)
        q.put({"event": "error", "data": {"message": str(exc)}})
    finally:
        q.put(None)  # sentinel


def _export_to_path(companies, filepath):
    """Like export_excel but saves to an explicit path."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    HEADER_FILL = PatternFill("solid", fgColor="D9EAD3")
    HEADER_FONT = Font(bold=True)

    def write_header(ws):
        for ci, (title, _) in enumerate(COLUMNS, 1):
            cell = ws.cell(1, ci, title)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")

    def write_row(ws, ri, c):
        vals = [c["name"], c["phone"], c["site"], c["social"], c["address"],
                c["lat"] if isinstance(c["lat"], float) else None,
                c["lon"] if isinstance(c["lon"], float) else None,
                c["category"],
                c["rating"] if isinstance(c["rating"], float) else None,
                c["reviews"], c["has_site"]]
        for ci, v in enumerate(vals, 1):
            cell = ws.cell(ri, ci, v)
            if ci in (6, 7) and v is not None:
                cell.number_format = "0.000000"
            elif ci == 9 and v is not None:
                cell.number_format = "0.0"
            elif ci == 10:
                cell.number_format = "0"

    def set_widths(ws):
        for ci, (_, w) in enumerate(COLUMNS, 1):
            ws.column_dimensions[get_column_letter(ci)].width = w

    def add_filter(ws, n):
        ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{n + 1}"

    def add_dv(ws, n):
        dv = DataValidation(type="list", formula1='"Да,Нет"', allow_blank=False)
        dv.sqref = f"K2:K{n + 1}"
        ws.add_data_validation(dv)

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Компании"
    write_header(ws1)
    for i, c in enumerate(companies, 2):
        write_row(ws1, i, c)
    set_widths(ws1)
    add_filter(ws1, len(companies))
    add_dv(ws1, len(companies))

    ws2 = wb.create_sheet("Без сайта")
    write_header(ws2)
    no_site = [c for c in companies if c["has_site"] == "Нет"]
    for i, c in enumerate(no_site, 2):
        write_row(ws2, i, c)
    set_widths(ws2)
    add_filter(ws2, len(no_site))
    add_dv(ws2, len(no_site))

    wb.save(filepath)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/start")
def api_start():
    body = request.get_json(force=True)
    query = (body.get("query") or "").strip()
    try:
        max_n = max(1, min(int(body.get("max", 100)), 500))
    except (TypeError, ValueError):
        max_n = 100

    if not query:
        return jsonify(error="Введите поисковый запрос"), 400

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "running",
        "query": query,
        "max": max_n,
        "filename": None,
        "error": None,
        "companies": [],
        "queue": queue.Queue(),
    }
    threading.Thread(target=_run_job, args=(job_id, query, max_n), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/stream/<job_id>")
def api_stream(job_id):
    if job_id not in JOBS:
        return jsonify(error="Job not found"), 404

    def generate():
        q: queue.Queue = JOBS[job_id]["queue"]
        while True:
            msg = q.get()
            if msg is None:
                yield "event: close\ndata: {}\n\n"
                break
            event = msg["event"]
            data = json.dumps(msg["data"], ensure_ascii=False)
            yield f"event: {event}\ndata: {data}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/download/<filename>")
def api_download(filename):
    # Sanitise: only allow safe filenames from downloads dir
    safe = re.sub(r"[^\w\-а-яА-Я.]", "_", filename)
    path = DOWNLOADS_DIR / safe
    if not path.exists():
        return jsonify(error="File not found"), 404
    return send_file(path, as_attachment=True, download_name=safe)


if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000)
