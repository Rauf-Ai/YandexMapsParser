"use strict";

// ── Field definitions per source ────────────────────────────────────────
const FIELD_DEFS_YANDEX = {
  name: "Название", phone: "Телефон", site: "Сайт",
  social: "Социальные сети", address: "Адрес",
  lat: "Широта", lon: "Долгота", category: "Категория",
  rating: "Рейтинг", reviews: "Кол-во отзывов",
  has_site: "Есть сайт", map_url: "Ссылка на карты",
  services: "Товары и услуги", features: "Особенности",
  price_range: "Цены",
};
const FIELD_DEFS_2GIS = {
  name: "Название", phone: "Телефон", site: "Сайт",
  social: "Соцсети", address: "Адрес",
  lat: "Широта", lon: "Долгота", category: "Категория",
  rating: "Рейтинг", reviews: "Кол-во отзывов",
  hours: "Часы работы", description: "Описание",
  services: "Услуги", features: "Особенности",
  has_site: "Есть сайт", map_url: "Ссылка на 2ГИС",
};
const FIELD_DEFS_BOTH = {
  name: "Название", source_name: "Источник", phone: "Телефон", site: "Сайт",
  social: "Соцсети", address: "Адрес",
  lat: "Широта", lon: "Долгота", category: "Категория",
  rating: "Рейтинг", reviews: "Кол-во отзывов",
  hours: "Часы работы", description: "Описание",
  services: "Услуги / Товары", features: "Особенности",
  price_range: "Цены", has_site: "Есть сайт", map_url: "Ссылка на карты",
};

// Active field defs (switches with source)
let FIELD_DEFS = FIELD_DEFS_YANDEX;
const ALWAYS_ON = new Set(["name"]);

// ── App state ────────────────────────────────────────────────────────────
let currentSource       = "yandex";
let selectedFields      = new Set(Object.keys(FIELD_DEFS));
let currentSSE          = null;
let currentJobId        = null;
let allCompanies        = [];
let filteredRows        = [];
let sortCol             = "";
let sortDir             = 1;
let currentPage         = 1;
const PAGE_SIZE         = 25;

// Multi-select filter state
let selectedFeatureTags = new Set();
let selectedServiceTags = new Set();
let activeQuickFilters  = new Set();   // each entry = "kw1,kw2,..." string from data-kws

// Org-collect state
let currentCollectSSE   = null;

// ── DOM helpers ──────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ── Navigation ───────────────────────────────────────────────────────────
const PAGES = ["search", "results", "history"];

function showPage(name) {
  PAGES.forEach(p => {
    $(`page-${p}`).style.display = p === name ? "" : "none";
  });
  document.querySelectorAll(".nav-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.section === name);
  });
}

document.querySelectorAll(".nav-tab").forEach(tab => {
  tab.addEventListener("click", e => {
    e.preventDefault();
    const sec = tab.dataset.section;
    if (sec === "results" && allCompanies.length === 0) return;
    if (sec === "history") loadHistory();
    showPage(sec);
  });
});

$("logoLink").addEventListener("click", e => {
  e.preventDefault();
  showPage("search");
});

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  buildFieldsGrid();
  loadHistory();

  // Source toggle
  document.querySelectorAll(".source-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".source-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentSource = btn.dataset.src;
      if (currentSource === "yandex") {
        FIELD_DEFS = FIELD_DEFS_YANDEX;
        $("cardTitle").textContent = "Сбор компаний с Яндекс Карт";
        $("cardSub").textContent   = "Выберите категорию и город или введите запрос вручную";
      } else if (currentSource === "2gis") {
        FIELD_DEFS = FIELD_DEFS_2GIS;
        $("cardTitle").textContent = "Сбор компаний с 2ГИС";
        $("cardSub").textContent   = "Выберите категорию и город или введите запрос вручную";
      } else {
        FIELD_DEFS = FIELD_DEFS_BOTH;
        $("cardTitle").textContent = "Сбор компаний с Яндекс Карт + 2ГИС";
        $("cardSub").textContent   = "Запрос отправляется в оба источника, результаты объединяются";
      }
      selectedFields = new Set(Object.keys(FIELD_DEFS));
      buildFieldsGrid();
    });
  });

  // Quick-filter buttons (preset, always in HTML)
  document.querySelectorAll(".quick-filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const kws = btn.dataset.kws;
      if (activeQuickFilters.has(kws)) {
        activeQuickFilters.delete(kws);
        btn.classList.remove("active");
      } else {
        activeQuickFilters.add(kws);
        btn.classList.add("active");
      }
      applyTableFilters();
    });
  });
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
      <span class="check-box"></span><span>${label}</span>`;
    wrap.querySelector("input").addEventListener("change", e => {
      e.target.checked ? selectedFields.add(key) : selectedFields.delete(key);
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
  $("fieldsGrid").querySelectorAll("input").forEach(cb => cb.checked = true);
  updateFieldsCount();
});
$("selectNoneFields").addEventListener("click", () => {
  selectedFields = new Set(["name"]);
  $("fieldsGrid").querySelectorAll("input").forEach(cb => cb.checked = false);
  updateFieldsCount();
});

// ── Collapsible ───────────────────────────────────────────────────────────
$("fieldsToggleBtn").addEventListener("click", () => {
  $("fieldsBody").classList.toggle("open");
  $("fieldsToggleBtn").classList.toggle("open");
});

// ── Example chips ─────────────────────────────────────────────────────────
document.querySelectorAll(".chip").forEach(chip => {
  chip.addEventListener("click", () => {
    if (chip.dataset.q)    $("categoryInput").value = chip.dataset.q;
    if (chip.dataset.city) $("cityInput").value     = chip.dataset.city;
    if (chip.dataset.q)    $("queryInput").value    = "";
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

  $("startBtn").disabled = true;
  $("progressSection").classList.remove("hidden");
  $("errorSection").classList.add("hidden");
  $("progressBar").style.width = "0%";
  $("progressFound").textContent = "Найдено: 0";
  $("progressPct").textContent   = "0%";
  $("logBox").innerHTML = "";

  try {
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category, city, query, max,
        source:    currentSource,
        no_site:   $("filterNoSite").checked,
        no_social: $("filterNoSocial").checked,
        no_phone:  $("filterNoPhone").checked,
        fields: [...selectedFields],
      }),
    });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Ошибка запуска");

    currentJobId = json.job_id;
    $("progressTitle").textContent = `Сбор: «${json.query}»`;
    startSSE(json.job_id, max);
  } catch (err) {
    $("progressSection").classList.add("hidden");
    $("errorMsg").textContent = err.message;
    $("errorSection").classList.remove("hidden");
    $("startBtn").disabled = false;
  }
});

// ── SSE ───────────────────────────────────────────────────────────────────
function startSSE(jobId, max) {
  if (currentSSE) { currentSSE.close(); currentSSE = null; }
  const sse = new EventSource(`/api/stream/${jobId}`);
  currentSSE = sse;

  sse.addEventListener("start", e => addLog(JSON.parse(e.data).message, "ok"));
  sse.addEventListener("progress", e => {
    const d = JSON.parse(e.data);
    setProgress(d.found, max);
    addLog(d.message);
  });
  sse.addEventListener("done", e => {
    sse.close(); currentSSE = null;
    $("startBtn").disabled = false;
    renderSummary(JSON.parse(e.data));
    fetchAndRenderTable(jobId);
    showNavTab("results");
    showPage("results");
    loadHistory();
  });
  sse.addEventListener("error", e => {
    let msg = "Неизвестная ошибка";
    try { msg = JSON.parse(e.data).message; } catch {}
    addLog(msg, "err");
    sse.close(); currentSSE = null;
    $("startBtn").disabled = false;
    $("progressSection").classList.add("hidden");
    $("errorMsg").textContent = msg;
    $("errorSection").classList.remove("hidden");
  });
  sse.addEventListener("close", () => { sse.close(); currentSSE = null; });
}

function showNavTab(name) {
  const tab = document.querySelector(`.nav-tab[data-section="${name}"]`);
  if (tab) tab.style.display = "";
}

// ── Progress helpers ──────────────────────────────────────────────────────
function setProgress(found, total) {
  const pct = total > 0 ? Math.min(100, Math.round(found / total * 100)) : 0;
  $("progressBar").style.width   = pct + "%";
  $("progressFound").textContent = `Найдено: ${found}`;
  $("progressPct").textContent   = pct + "%";
}
function addLog(text, type = "") {
  const span = document.createElement("span");
  span.className = "log-line" + (type ? ` log-line--${type}` : "");
  span.textContent = `[${new Date().toLocaleTimeString("ru")}] ${text}`;
  const box = $("logBox");
  box.appendChild(span);
  box.scrollTop = box.scrollHeight;
}

// ── Summary card ─────────────────────────────────────────────────────────
function renderSummary(d) {
  $("resultSub").textContent    = `Файл: ${d.filename}`;
  $("downloadBtn").href         = `/api/download/${encodeURIComponent(d.filename)}`;
  $("downloadBtn").setAttribute("download", d.filename);

  const warn = $("resultWarning");
  if (d.warning) { warn.textContent = "⚠ " + d.warning; warn.classList.remove("hidden"); }
  else warn.classList.add("hidden");

  $("statsGrid").innerHTML = `
    <div class="stat-card"><div class="stat-value">${d.total}</div><div class="stat-label">Всего</div></div>
    <div class="stat-card"><div class="stat-value stat-value--green">${d.with_site}</div><div class="stat-label">С сайтом (${d.pct_site}%)</div></div>
    <div class="stat-card"><div class="stat-value stat-value--accent">${d.without_site}</div><div class="stat-label">Без сайта</div></div>
    <div class="stat-card"><div class="stat-value stat-value--blue">${d.with_social ?? "—"}</div><div class="stat-label">С соцсетями</div></div>
    <div class="stat-card"><div class="stat-value">${d.with_phone}</div><div class="stat-label">С телефоном (${d.pct_phone}%)</div></div>
    <div class="stat-card"><div class="stat-value">${d.avg_rating || "—"}</div><div class="stat-label">Средний рейтинг</div></div>`;
}

// ── Full table ────────────────────────────────────────────────────────────
async function fetchAndRenderTable(jobId) {
  try {
    const res = await fetch(`/api/results/${jobId}`);
    if (!res.ok) return;
    allCompanies = await res.json();

    // Reset all multi-select state when new results arrive
    selectedFeatureTags.clear();
    selectedServiceTags.clear();
    activeQuickFilters.clear();
    document.querySelectorAll(".quick-filter-btn").forEach(b => b.classList.remove("active"));

    const hasAnyTags = allCompanies.some(c => c.services || c.features);
    $("tagFiltersRow").style.display = hasAnyTags ? "" : "none";

    buildCloud(allCompanies, "features", $("featureTags"), selectedFeatureTags, "ftr-tag");
    buildCloud(allCompanies, "services", $("serviceTags"), selectedServiceTags, "srv-tag");

    // Hide services bar if no services data at all
    const hasSrv = allCompanies.some(c => c.services);
    $("servicesBar").style.display = hasSrv ? "" : "none";

    applyTableFilters();
  } catch { /* silently ignore */ }
}

// ── Tag cloud (multi-select, AND logic) ──────────────────────────────────
function buildCloud(companies, field, cloudEl, selectedSet, chipClass) {
  const counts = {};
  companies.forEach(c => {
    (c[field] || "").split(", ").filter(Boolean).forEach(tag => {
      counts[tag] = (counts[tag] || 0) + 1;
    });
  });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 30);
  if (!sorted.length) { cloudEl.innerHTML = ""; return; }

  function render() {
    cloudEl.innerHTML = sorted.map(([tag, n]) =>
      `<button class="cloud-tag ${chipClass} ${selectedSet.has(tag) ? "active" : ""}"
               data-tag="${esc(tag)}" title="${n} компаний">
        ${esc(trunc(tag, 24))}<sup>${n}</sup>
      </button>`
    ).join("");
    cloudEl.querySelectorAll(".cloud-tag").forEach(btn => {
      btn.addEventListener("click", () => {
        const t = btn.dataset.tag;
        selectedSet.has(t) ? selectedSet.delete(t) : selectedSet.add(t);
        render();
        applyTableFilters();
      });
    });
  }
  render();
}

// ── Table filters ─────────────────────────────────────────────────────────
function applyTableFilters() {
  const q       = ($("tableSearch").value   || "").toLowerCase();
  const hasSite = $("filterHasSite").value;
  const hasSoc  = $("filterHasSocial").value;
  const hasSrv  = $("filterHasServices").value;
  const hasFtr  = $("filterHasFeatures").value;
  const srvKw   = ($("filterServiceKw")?.value || "").toLowerCase();
  const ftrKw   = ($("filterFeatureKw")?.value || "").toLowerCase();

  filteredRows = allCompanies.filter(c => {
    const feats = (c.features || "").toLowerCase();
    const srvs  = (c.services  || "").toLowerCase();

    if (hasSite && c.has_site !== hasSite) return false;
    if (hasSoc === "yes" && c.social === "—") return false;
    if (hasSoc === "no"  && c.social !== "—") return false;
    if (hasSrv === "yes" && !c.services) return false;
    if (hasSrv === "no"  &&  c.services) return false;
    if (hasFtr === "yes" && !c.features) return false;
    if (hasFtr === "no"  &&  c.features) return false;

    // Quick feature presets: each active button = AND, keywords within a button = OR
    for (const kwStr of activeQuickFilters) {
      const kws = kwStr.split(",").map(s => s.trim()).filter(Boolean);
      if (!kws.some(kw => feats.includes(kw))) return false;
    }

    // Multi-select tag clouds: AND between selected tags
    for (const tag of selectedFeatureTags) {
      if (!feats.includes(tag.toLowerCase())) return false;
    }
    for (const tag of selectedServiceTags) {
      if (!srvs.includes(tag.toLowerCase())) return false;
    }

    // Keyword text inputs
    if (ftrKw && !feats.includes(ftrKw)) return false;
    if (srvKw && !srvs.includes(srvKw))  return false;

    // General search covers all text fields
    if (q) {
      const hay = [c.name, c.address, c.phone, c.category, c.services, c.features,
                   c.hours, c.description, c.source_name]
        .join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  // Sort
  if (sortCol) {
    filteredRows.sort((a, b) => {
      let av = a[sortCol], bv = b[sortCol];
      if (sortCol === "price_range") {
        return (priceToNum(av) - priceToNum(bv)) * sortDir;
      }
      if (typeof av === "number" && typeof bv === "number")
        return (av - bv) * sortDir;
      if (av === "—" || av == null || av === "") av = sortDir > 0 ? "￿" : "";
      if (bv === "—" || bv == null || bv === "") bv = sortDir > 0 ? "￿" : "";
      return String(av).localeCompare(String(bv), "ru") * sortDir;
    });
  }

  currentPage = 1;
  renderTablePage();
}

function priceToNum(s) {
  if (!s) return Infinity;
  const nums = String(s).match(/\d[\d\s]*/g);
  if (!nums) return Infinity;
  return parseInt(nums[0].replace(/\s/g, ""), 10);
}

function renderTablePage() {
  const start = (currentPage - 1) * PAGE_SIZE;
  const slice = filteredRows.slice(start, start + PAGE_SIZE);
  const tbody = $("mainTableBody");

  $("tableCount").textContent =
    `${filteredRows.length} из ${allCompanies.length} компаний`;

  const show2gis   = currentSource === "2gis" || currentSource === "both";
  const showYandex = currentSource === "yandex" || currentSource === "both";
  const showBoth   = currentSource === "both";
  const mapTitle   = currentSource === "2gis" ? "2ГИС" : "Яндекс Карты";

  // Sync table header
  const thead = $("mainTable").querySelector("thead tr");
  if (thead) {
    thead.innerHTML = `
      <th class="sortable" data-col="name">Название <span class="sort-arrow">↕</span></th>
      <th>Телефон</th>
      <th>Сайт</th>
      <th>Соцсети</th>
      <th class="sortable" data-col="rating">Рейтинг <span class="sort-arrow">↕</span></th>
      <th class="sortable" data-col="reviews">Отзывы <span class="sort-arrow">↕</span></th>
      <th>Адрес</th>
      <th>Категория</th>
      ${show2gis   ? '<th>Часы</th>' : ''}
      ${showYandex ? `<th class="sortable" data-col="price_range">Цены <span class="sort-arrow">↕</span></th>` : ''}
      <th>Услуги</th>
      <th>Особенности</th>
      ${showBoth   ? '<th>Источник</th>' : ''}
      <th>Карты</th>`;
    // Re-attach sort listeners
    thead.querySelectorAll(".sortable").forEach(th => {
      th.addEventListener("click", () => {
        const col = th.dataset.col;
        if (sortCol === col) sortDir *= -1;
        else { sortCol = col; sortDir = -1; }
        thead.querySelectorAll(".sort-arrow").forEach(a => a.textContent = "↕");
        th.querySelector(".sort-arrow").textContent = sortDir > 0 ? "↑" : "↓";
        applyTableFilters();
      });
    });
  }

  tbody.innerHTML = slice.map((c, idx) => `
    <tr>
      <td class="td-name" title="${esc(c.name)}">${esc(trunc(c.name, 35))}</td>
      <td class="td-mono">${esc(c.phone)}</td>
      <td>${c.site !== "—"
        ? `<a href="https://${esc(c.site)}" target="_blank" rel="noopener" class="link-ext">${esc(trunc(c.site, 28))}</a>`
        : '<span class="td-muted">—</span>'}</td>
      <td>${renderSocials(c.social)}</td>
      <td class="td-center">${starsHtml(c.rating)}</td>
      <td class="td-center">${c.reviews > 0 ? c.reviews : '<span class="td-muted">—</span>'}</td>
      <td class="td-addr" title="${esc(c.address)}">${esc(trunc(c.address, 35))}</td>
      <td class="td-muted">${esc(trunc(c.category, 20))}</td>
      ${show2gis   ? `<td class="td-hours">${c.hours ? esc(trunc(c.hours, 30)) : '<span class="td-muted">—</span>'}</td>` : ''}
      ${showYandex ? `<td class="td-price">${c.price_range ? esc(c.price_range) : '<span class="td-muted">—</span>'}</td>` : ''}
      <td>${renderTags(c.services, "srv")}</td>
      <td>${renderTags(c.features, "ftr")}</td>
      ${showBoth   ? `<td><span class="source-badge source-badge--${c.source_name === '2ГИС' ? 'tg' : 'ya'}">${esc(c.source_name || '')}</span></td>` : ''}
      <td class="td-actions">
        ${c.map_url ? `<a href="${esc(c.map_url)}" target="_blank" rel="noopener" class="link-maps" title="${mapTitle}">🗺</a>` : ""}
        <button class="btn-collect" data-idx="${start + idx}"
                title="Выгрузить отзывы, фото, акции → ZIP + промт для Claude">📦</button>
      </td>
    </tr>`).join("");

  renderPagination();
}

// ── Tag chips with +N expand/collapse ────────────────────────────────────
const TAG_MAX = 3;

function renderTags(raw, type) {
  if (!raw) return '<span class="td-muted">—</span>';
  const items = raw.split(", ").filter(Boolean);
  if (items.length <= TAG_MAX) {
    return `<div class="tag-cell">${items.map(t =>
      `<span class="tag-chip tag-chip--${type}">${esc(trunc(t, 22))}</span>`
    ).join("")}</div>`;
  }
  const rest = items.length - TAG_MAX;
  return `<div class="tag-cell" data-raw="${esc(raw)}" data-type="${type}" data-expanded="0">${
    items.slice(0, TAG_MAX).map(t =>
      `<span class="tag-chip tag-chip--${type}">${esc(trunc(t, 22))}</span>`
    ).join("")
  }<span class="tag-chip tag-chip--more">+${rest} ещё</span></div>`;
}

// Delegated click: collect button OR expand tag
$("mainTableBody").addEventListener("click", e => {
  // 📦 Collect button
  const collectBtn = e.target.closest(".btn-collect");
  if (collectBtn) {
    const idx = parseInt(collectBtn.dataset.idx, 10);
    startOrgCollect(filteredRows[idx]);
    return;
  }

  // +N tag expand / collapse
  const chip = e.target.closest(".tag-chip--more");
  if (!chip) return;
  const cell = chip.closest(".tag-cell[data-raw]");
  if (!cell) return;
  const expanded = cell.dataset.expanded === "1";
  const items = cell.dataset.raw.split(", ").filter(Boolean);
  const type  = cell.dataset.type;
  if (expanded) {
    cell.dataset.expanded = "0";
    cell.innerHTML = items.slice(0, TAG_MAX).map(t =>
      `<span class="tag-chip tag-chip--${type}">${esc(trunc(t, 22))}</span>`
    ).join("") + `<span class="tag-chip tag-chip--more">+${items.length - TAG_MAX} ещё</span>`;
  } else {
    cell.dataset.expanded = "1";
    cell.innerHTML = items.map(t =>
      `<span class="tag-chip tag-chip--${type}">${esc(trunc(t, 22))}</span>`
    ).join("") + `<span class="tag-chip tag-chip--more">↑ свернуть</span>`;
  }
});

// ── Social chips ──────────────────────────────────────────────────────────
function renderSocials(social) {
  if (!social || social === "—") return '<span class="td-muted">—</span>';
  const links = social.split(", ").filter(Boolean).map(s => {
    const icon = s.includes("vk.com") ? "VK" :
                 s.includes("t.me") || s.includes("telegram") ? "TG" :
                 s.includes("instagram") ? "IG" :
                 s.includes("ok.ru") ? "OK" :
                 s.includes("youtube") ? "YT" :
                 s.includes("whatsapp") ? "WA" :
                 s.includes("tiktok") ? "TK" : "🔗";
    return `<a href="https://${esc(s)}" target="_blank" rel="noopener"
               class="social-chip" title="${esc(s)}">${icon}</a>`;
  });
  return links.join(" ");
}

// ── Pagination ────────────────────────────────────────────────────────────
function renderPagination() {
  const total = Math.ceil(filteredRows.length / PAGE_SIZE);
  const el    = $("pagination");
  if (total <= 1) { el.innerHTML = ""; return; }

  let html = `<button class="page-btn" ${currentPage === 1 ? "disabled" : ""}
                data-page="${currentPage - 1}">‹ Пред</button>`;
  const delta = 2;
  for (let i = 1; i <= total; i++) {
    if (i === 1 || i === total || Math.abs(i - currentPage) <= delta) {
      html += `<button class="page-btn ${i === currentPage ? "active" : ""}"
                 data-page="${i}">${i}</button>`;
    } else if (Math.abs(i - currentPage) === delta + 1) {
      html += `<span class="page-ellipsis">…</span>`;
    }
  }
  html += `<button class="page-btn" ${currentPage === total ? "disabled" : ""}
             data-page="${currentPage + 1}">След ›</button>`;
  el.innerHTML = html;

  el.querySelectorAll(".page-btn:not([disabled])").forEach(btn => {
    btn.addEventListener("click", () => {
      currentPage = parseInt(btn.dataset.page);
      renderTablePage();
      $("mainTableScroll").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

// Column sort — delegated to thead rows rebuilt in renderTablePage

// Table filter inputs
["tableSearch", "filterHasSite", "filterHasSocial",
 "filterHasServices", "filterHasFeatures",
 "filterServiceKw", "filterFeatureKw"].forEach(id => {
  const el = $(id);
  if (el) el.addEventListener("input", applyTableFilters);
});

// ── Org collect (📦) ─────────────────────────────────────────────────────

async function startOrgCollect(company) {
  if (!company) return;
  $("collectOrgName").textContent = company.name || "…";
  $("collectBar").style.width = "0%";
  $("collectLog").innerHTML = "";
  $("collectDownloadBtn").classList.add("hidden");
  $("collectOverlay").classList.remove("hidden");

  try {
    const res  = await fetch("/api/start-org", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ company }),
    });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Ошибка запуска");
    startCollectSSE(json.job_id);
  } catch (err) {
    addCollectLog("Ошибка: " + err.message, "err");
  }
}

function startCollectSSE(jobId) {
  if (currentCollectSSE) { currentCollectSSE.close(); }
  const sse = new EventSource(`/api/stream/${jobId}`);
  currentCollectSSE = sse;
  let step = 0;
  const STEPS = 10;

  sse.addEventListener("start",    e => addCollectLog(JSON.parse(e.data).message, "ok"));
  sse.addEventListener("progress", e => {
    step = Math.min(step + 1, STEPS - 1);
    $("collectBar").style.width = Math.round(step / STEPS * 90) + "%";
    addCollectLog(JSON.parse(e.data).message);
  });
  sse.addEventListener("done", e => {
    sse.close(); currentCollectSSE = null;
    $("collectBar").style.width = "100%";
    const d = JSON.parse(e.data);
    const dl = $("collectDownloadBtn");
    dl.href = `/api/download/${encodeURIComponent(d.zip_filename)}`;
    dl.setAttribute("download", d.zip_filename);
    dl.classList.remove("hidden");
    addCollectLog(
      `✓ Готово! Отзывов: ${d.reviews_count}, фото: ${d.photos_count}`, "ok"
    );
    loadHistory();
  });
  sse.addEventListener("error", e => {
    sse.close(); currentCollectSSE = null;
    let msg = "Ошибка";
    try { msg = JSON.parse(e.data).message; } catch {}
    addCollectLog(msg, "err");
  });
  sse.addEventListener("close", () => { sse.close(); currentCollectSSE = null; });
}

function addCollectLog(text, type = "") {
  const span = document.createElement("span");
  span.className = "log-line" + (type ? ` log-line--${type}` : "");
  span.textContent = `[${new Date().toLocaleTimeString("ru")}] ${text}`;
  const box = $("collectLog");
  box.appendChild(span);
  box.scrollTop = box.scrollHeight;
}

$("collectCancelBtn").addEventListener("click", () => {
  if (currentCollectSSE) { currentCollectSSE.close(); currentCollectSSE = null; }
  $("collectOverlay").classList.add("hidden");
});

// ── History ───────────────────────────────────────────────────────────────
async function loadHistory() {
  try {
    const res   = await fetch("/api/history");
    const items = await res.json();
    renderHistory(items);
    if (items.length > 0) showNavTab("history");
  } catch { /* ignore */ }
}

function renderHistory(entries) {
  const list = $("historyList");
  if (!entries || entries.length === 0) {
    list.innerHTML = '<p class="history-empty">История пустая</p>';
    return;
  }
  list.className = "history-list";
  list.innerHTML = entries.map(e => {
    if (e.type === "org_collect") {
      const meta = [];
      if (e.reviews_count) meta.push(`отзывов: ${e.reviews_count}`);
      if (e.photos_count)  meta.push(`фото: ${e.photos_count}`);
      return `
        <div class="history-item history-item--org">
          <span class="history-org-icon">📦</span>
          <div style="flex:1;min-width:0">
            <div class="history-query">${esc(e.company || e.query)}</div>
            <div class="history-meta">${e.date} в ${e.time} · выгрузка данных${meta.length ? " · " + meta.join(", ") : ""}</div>
          </div>
          <div class="history-actions">
            <a class="btn-secondary" style="padding:5px 12px;font-size:.78rem"
               href="/api/download/${encodeURIComponent(e.filename)}"
               download="${esc(e.filename)}">↓ ZIP</a>
            <button class="btn-ghost btn-sm" data-del="${esc(e.id)}">✕</button>
          </div>
        </div>`;
    }
    const tags = [];
    if (e.filters?.no_site)   tags.push("без сайта");
    if (e.filters?.no_social) tags.push("без соцсетей");
    if (e.filters?.no_phone)  tags.push("без телефона");
    return `
      <div class="history-item">
        <div style="flex:1;min-width:0">
          <div class="history-query">${esc(e.query)}</div>
          <div class="history-meta">${e.date} в ${e.time}${tags.length ? " · " + tags.join(", ") : ""}</div>
        </div>
        <span class="history-count">${e.total} орг.</span>
        <div class="history-actions">
          <a class="btn-secondary" style="padding:5px 12px;font-size:.78rem"
             href="/api/download/${encodeURIComponent(e.filename)}"
             download="${esc(e.filename)}">↓ Excel</a>
          <button class="btn-ghost btn-sm" data-del="${esc(e.id)}">✕</button>
        </div>
      </div>`;
  }).join("");

  list.querySelectorAll("[data-del]").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/history/${btn.dataset.del}`, { method: "DELETE" });
      loadHistory();
    });
  });
}

$("clearHistoryBtn").addEventListener("click", async () => {
  const items = await (await fetch("/api/history")).json();
  for (const e of items)
    await fetch(`/api/history/${e.id}`, { method: "DELETE" });
  loadHistory();
});

// ── Cancel / retry ────────────────────────────────────────────────────────
$("cancelBtn").addEventListener("click", () => {
  if (currentSSE) { currentSSE.close(); currentSSE = null; }
  $("startBtn").disabled = false;
  $("progressSection").classList.add("hidden");
});
$("errorRetryBtn").addEventListener("click", () => {
  $("errorSection").classList.add("hidden");
});

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
  if (!r || r === "—") return '<span class="td-muted">—</span>';
  const n    = parseFloat(r);
  const full = Math.round(n);
  return `<span class="stars">${"★".repeat(full)}${"☆".repeat(Math.max(0, 5 - full))}</span>${n.toFixed(1)}`;
}
