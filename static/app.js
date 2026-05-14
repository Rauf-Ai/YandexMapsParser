"use strict";

// ── Field definitions (mirrors yandex_parser.ALL_FIELD_DEFS) ─────────────
const FIELD_DEFS = {
  name:     "Название",
  phone:    "Телефон",
  site:     "Сайт",
  social:   "Социальные сети",
  address:  "Адрес",
  lat:      "Широта",
  lon:      "Долгота",
  category: "Категория",
  rating:   "Рейтинг",
  reviews:  "Кол-во отзывов",
  has_site: "Есть сайт",
  map_url:  "Ссылка на карты",
};
const ALWAYS_ON = new Set(["name"]);

// ── State ────────────────────────────────────────────────────────────────
let selectedFields = new Set(Object.keys(FIELD_DEFS));
let currentSSE     = null;

// ── DOM refs ─────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const searchSection   = $("searchSection");
const progressSection = $("progressSection");
const resultSection   = $("resultSection");
const errorSection    = $("errorSection");
const historySection  = $("historySection");

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  buildFieldsGrid();
  loadHistory();
});

// ── Field selection grid ─────────────────────────────────────────────────
function buildFieldsGrid() {
  const grid = $("fieldsGrid");
  grid.innerHTML = "";
  for (const [key, label] of Object.entries(FIELD_DEFS)) {
    if (ALWAYS_ON.has(key)) continue;
    const wrap = document.createElement("label");
    wrap.className = "check-item";
    wrap.innerHTML = `
      <input type="checkbox" data-field="${key}" ${selectedFields.has(key) ? "checked" : ""}/>
      <span class="check-box"></span>
      <span>${label}</span>`;
    wrap.querySelector("input").addEventListener("change", e => {
      if (e.target.checked) selectedFields.add(key);
      else selectedFields.delete(key);
      updateFieldsCount();
    });
    grid.appendChild(wrap);
  }
  updateFieldsCount();
}

function updateFieldsCount() {
  const total = Object.keys(FIELD_DEFS).length - ALWAYS_ON.size;
  const sel   = [...selectedFields].filter(k => !ALWAYS_ON.has(k)).length;
  $("fieldsCount").textContent =
    sel === total ? "все выбраны" : `выбрано: ${sel + 1}/${total + 1}`;
}

$("selectAllFields").addEventListener("click", () => {
  selectedFields = new Set(Object.keys(FIELD_DEFS));
  $("fieldsGrid").querySelectorAll("input[type=checkbox]")
    .forEach(cb => { cb.checked = true; });
  updateFieldsCount();
});

$("selectNoneFields").addEventListener("click", () => {
  selectedFields = new Set(["name"]);
  $("fieldsGrid").querySelectorAll("input[type=checkbox]")
    .forEach(cb => { cb.checked = false; });
  updateFieldsCount();
});

// ── Collapsible ───────────────────────────────────────────────────────────
$("fieldsToggleBtn").addEventListener("click", () => {
  const body = $("fieldsBody");
  const btn  = $("fieldsToggleBtn");
  body.classList.toggle("open");
  btn.classList.toggle("open");
});

// ── Example chips ─────────────────────────────────────────────────────────
document.querySelectorAll(".chip").forEach(chip => {
  chip.addEventListener("click", () => {
    const q    = chip.dataset.q    || "";
    const city = chip.dataset.city || "";
    if (q) {
      $("categoryInput").value = q;
      $("queryInput").value    = "";
    }
    if (city) $("cityInput").value = city;
  });
});

// ── Form submit ───────────────────────────────────────────────────────────
$("searchForm").addEventListener("submit", async e => {
  e.preventDefault();

  const category = $("categoryInput").value.trim();
  const city     = $("cityInput").value.trim();
  const query    = $("queryInput").value.trim();
  const max      = parseInt($("maxInput").value, 10) || 50;

  if (!category && !city && !query) {
    alert("Введите запрос или выберите категорию");
    return;
  }

  const filters = {
    no_site:   $("filterNoSite").checked,
    no_social: $("filterNoSocial").checked,
    no_phone:  $("filterNoPhone").checked,
  };

  $("startBtn").disabled = true;
  try {
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category, city, query, max,
        ...filters,
        fields: [...selectedFields],
      }),
    });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Ошибка запуска");

    $("progressBar").style.width = "0%";
    $("progressFound").textContent = "Найдено: 0";
    $("progressPct").textContent   = "0%";
    $("progressTitle").textContent = `Сбор: «${json.query}»`;
    $("logBox").innerHTML = "";
    showSection("progress");
    startSSE(json.job_id, max);
  } catch (err) {
    $("errorMsg").textContent = err.message;
    showSection("error");
  } finally {
    $("startBtn").disabled = false;
  }
});

// ── SSE ───────────────────────────────────────────────────────────────────
function startSSE(jobId, max) {
  if (currentSSE) { currentSSE.close(); currentSSE = null; }
  const sse = new EventSource(`/api/stream/${jobId}`);
  currentSSE = sse;

  sse.addEventListener("start", e => {
    addLog(JSON.parse(e.data).message, "ok");
  });
  sse.addEventListener("progress", e => {
    const d = JSON.parse(e.data);
    setProgress(d.found, max);
    addLog(d.message);
  });
  sse.addEventListener("done", e => {
    sse.close(); currentSSE = null;
    renderResult(JSON.parse(e.data));
    showSection("result");
    loadHistory();
  });
  sse.addEventListener("error", e => {
    let msg = "Неизвестная ошибка";
    try { msg = JSON.parse(e.data).message; } catch {}
    addLog(msg, "err");
    sse.close(); currentSSE = null;
    $("errorMsg").textContent = msg;
    showSection("error");
  });
  sse.addEventListener("close", () => { sse.close(); currentSSE = null; });
  sse.onerror = () => {
    if (sse.readyState !== EventSource.CLOSED)
      addLog("Соединение прервано", "warn");
  };
}

// ── Progress helpers ──────────────────────────────────────────────────────
function setProgress(found, total) {
  const pct = total > 0 ? Math.min(100, Math.round(found / total * 100)) : 0;
  $("progressBar").style.width     = pct + "%";
  $("progressFound").textContent   = `Найдено: ${found}`;
  $("progressPct").textContent     = pct + "%";
}

function addLog(text, type = "") {
  const span = document.createElement("span");
  span.className = "log-line" + (type ? ` log-line--${type}` : "");
  const ts = new Date().toLocaleTimeString("ru", { hour12: false });
  span.textContent = `[${ts}] ${text}`;
  const box = $("logBox");
  box.appendChild(span);
  box.scrollTop = box.scrollHeight;
}

// ── Result renderer ───────────────────────────────────────────────────────
function renderResult(d) {
  $("resultSub").textContent = `Файл: ${d.filename}`;
  $("downloadBtn").href      = `/api/download/${encodeURIComponent(d.filename)}`;
  $("downloadBtn").setAttribute("download", d.filename);

  // Show soft warning when filter limited results
  const warnEl = $("resultWarning");
  if (d.warning) {
    warnEl.textContent = "⚠ " + d.warning;
    warnEl.style.display = "";
  } else {
    warnEl.style.display = "none";
  }

  $("statsGrid").innerHTML = `
    <div class="stat-card">
      <div class="stat-value">${d.total}</div>
      <div class="stat-label">Всего</div>
    </div>
    <div class="stat-card">
      <div class="stat-value stat-value--green">${d.with_site}</div>
      <div class="stat-label">С сайтом (${d.pct_site}%)</div>
    </div>
    <div class="stat-card">
      <div class="stat-value stat-value--accent">${d.without_site}</div>
      <div class="stat-label">Без сайта</div>
    </div>
    <div class="stat-card">
      <div class="stat-value stat-value--blue">${d.with_social ?? "—"}</div>
      <div class="stat-label">С соцсетями</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${d.with_phone}</div>
      <div class="stat-label">С телефоном (${d.pct_phone}%)</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${d.avg_rating || "—"}</div>
      <div class="stat-label">Средний рейтинг</div>
    </div>`;

  const tbody = $("previewBody");
  tbody.innerHTML = (d.preview || []).map(c => `
    <tr>
      <td title="${esc(c.name)}">${esc(trunc(c.name, 32))}</td>
      <td>${esc(c.phone)}</td>
      <td>${c.site !== "—"
        ? `<a href="https://${esc(c.site)}" target="_blank" class="link-map">${esc(trunc(c.site, 28))}</a>`
        : '<span style="color:var(--muted)">—</span>'}</td>
      <td>${c.social !== "—"
        ? `<span class="badge badge--val" title="${esc(c.social)}">${esc(trunc(c.social, 26))}</span>`
        : '<span style="color:var(--muted)">—</span>'}</td>
      <td>${starsHtml(c.rating)}</td>
      <td title="${esc(c.address)}">${esc(trunc(c.address, 32))}</td>
      <td>${c.map_url
        ? `<a href="${esc(c.map_url)}" target="_blank" class="link-map">🗺 открыть</a>`
        : "—"}</td>
    </tr>`).join("");

  resultSection.className = "card result-card";
}

// ── History ───────────────────────────────────────────────────────────────
async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    const entries = await res.json();
    renderHistory(entries);
  } catch { /* ignore */ }
}

function renderHistory(entries) {
  if (!entries || entries.length === 0) {
    historySection.style.display = "none";
    return;
  }
  historySection.style.display = "";
  const list = $("historyList");
  list.innerHTML = "";
  list.className = "history-list";

  entries.forEach(e => {
    const item = document.createElement("div");
    item.className = "history-item";
    const filterTags = [];
    if (e.filters?.no_site)   filterTags.push("без сайта");
    if (e.filters?.no_social) filterTags.push("без соцсетей");
    if (e.filters?.no_phone)  filterTags.push("без телефона");
    const filterStr = filterTags.length ? ` · ${filterTags.join(", ")}` : "";

    item.innerHTML = `
      <div style="flex:1;min-width:0">
        <div class="history-query">${esc(e.query)}</div>
        <div class="history-meta">${e.date} в ${e.time}${filterStr}</div>
      </div>
      <span class="history-count">${e.total} орг.</span>
      <div class="history-actions">
        <a class="btn-secondary" style="padding:6px 12px;font-size:.78rem"
           href="/api/download/${encodeURIComponent(e.filename)}"
           download="${esc(e.filename)}" title="Скачать">↓ Excel</a>
        <button class="btn-ghost btn-sm" data-del="${esc(e.id)}" title="Удалить">✕</button>
      </div>`;
    list.appendChild(item);
  });

  list.querySelectorAll("[data-del]").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/history/${btn.dataset.del}`, { method: "DELETE" });
      loadHistory();
    });
  });
}

$("clearHistoryBtn").addEventListener("click", async () => {
  const hist = await (await fetch("/api/history")).json();
  for (const e of hist)
    await fetch(`/api/history/${e.id}`, { method: "DELETE" });
  loadHistory();
});

// ── Section visibility ────────────────────────────────────────────────────
function showSection(name) {
  searchSection.classList.add("hidden");
  progressSection.classList.add("hidden");
  resultSection.classList.add("hidden");
  errorSection.classList.add("hidden");

  progressSection.className = "card hidden";
  resultSection.className   = "card hidden";
  errorSection.className    = "card hidden";

  if (name === "search")   searchSection.classList.remove("hidden");
  if (name === "progress") { progressSection.className = "card"; }
  if (name === "result")   { /* set by renderResult */ }
  if (name === "error")    { errorSection.className = "card error-card"; }
}

$("cancelBtn").addEventListener("click", () => {
  if (currentSSE) { currentSSE.close(); currentSSE = null; }
  showSection("search");
  searchSection.classList.remove("hidden");
});
$("newSearchBtn").addEventListener("click",  () => showSection("search"));
$("errorRetryBtn").addEventListener("click", () => showSection("search"));

// ── Utilities ─────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? "—")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function trunc(s, n) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n) + "…" : s;
}
function starsHtml(r) {
  if (!r || r === "—") return '<span style="color:var(--muted)">—</span>';
  const n = parseFloat(r);
  const full = Math.round(n);
  return `<span class="stars">${"★".repeat(full)}${"☆".repeat(Math.max(0,5-full))}</span> ${n.toFixed(1)}`;
}
