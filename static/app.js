"use strict";

// ── DOM refs ──────────────────────────────────────────────────
const searchSection   = document.getElementById("searchSection");
const progressSection = document.getElementById("progressSection");
const resultSection   = document.getElementById("resultSection");
const errorSection    = document.getElementById("errorSection");

const searchForm   = document.getElementById("searchForm");
const queryInput   = document.getElementById("queryInput");
const maxInput     = document.getElementById("maxInput");
const startBtn     = document.getElementById("startBtn");
const cancelBtn    = document.getElementById("cancelBtn");
const newSearchBtn = document.getElementById("newSearchBtn");
const errorRetryBtn= document.getElementById("errorRetryBtn");
const downloadBtn  = document.getElementById("downloadBtn");

const progressBar    = document.getElementById("progressBar");
const progressFound  = document.getElementById("progressFound");
const progressPct    = document.getElementById("progressPct");
const progressTitle  = document.getElementById("progressTitle");
const pulseDot       = document.getElementById("pulseDot");
const logBox         = document.getElementById("logBox");
const statsGrid      = document.getElementById("statsGrid");
const previewBody    = document.getElementById("previewBody");
const resultSub      = document.getElementById("resultSub");
const errorMsg       = document.getElementById("errorMsg");

let currentSSE  = null;
let currentMax  = 50;

// ── Helpers ───────────────────────────────────────────────────
function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

function addLog(text, type = "") {
  const span = document.createElement("span");
  span.className = "log-line" + (type ? ` log-line--${type}` : "");
  const ts = new Date().toLocaleTimeString("ru", { hour12: false });
  span.textContent = `[${ts}] ${text}`;
  logBox.appendChild(span);
  logBox.scrollTop = logBox.scrollHeight;
}

function setProgress(found, total) {
  const pct = total > 0 ? Math.min(100, Math.round(found / total * 100)) : 0;
  progressBar.style.width = pct + "%";
  progressFound.textContent = `Найдено: ${found}`;
  progressPct.textContent   = pct + "%";
}

function resetLog() {
  logBox.innerHTML = "";
}

function showSection(name) {
  hide(searchSection);
  hide(progressSection);
  hide(resultSection);
  hide(errorSection);
  if (name === "search")   show(searchSection);
  if (name === "progress") show(progressSection);
  if (name === "result")   show(resultSection);
  if (name === "error")    show(errorSection);
}

function starsHtml(rating) {
  if (!rating || rating === "—") return '<span style="color:#94a3b8">—</span>';
  const n = parseFloat(rating);
  const full  = Math.round(n);
  return `<span class="stars">${"★".repeat(full)}${"☆".repeat(5 - full)}</span> ${n.toFixed(1)}`;
}

// ── Example chips ─────────────────────────────────────────────
document.querySelectorAll(".chip").forEach(chip => {
  chip.addEventListener("click", () => {
    queryInput.value = chip.dataset.q;
    queryInput.focus();
  });
});

// ── Form submit ───────────────────────────────────────────────
searchForm.addEventListener("submit", async e => {
  e.preventDefault();
  const query = queryInput.value.trim();
  const max   = parseInt(maxInput.value, 10) || 50;
  if (!query) return;

  currentMax = max;
  startBtn.disabled = true;

  try {
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, max }),
    });
    const json = await res.json();

    if (!res.ok || json.error) {
      throw new Error(json.error || "Не удалось запустить задачу");
    }

    resetLog();
    setProgress(0, max);
    progressTitle.textContent = `Сбор: «${query}»`;
    showSection("progress");
    startSSE(json.job_id, max);

  } catch (err) {
    errorMsg.textContent = err.message;
    showSection("error");
  } finally {
    startBtn.disabled = false;
  }
});

// ── SSE ───────────────────────────────────────────────────────
function startSSE(jobId, max) {
  if (currentSSE) { currentSSE.close(); currentSSE = null; }

  const sse = new EventSource(`/api/stream/${jobId}`);
  currentSSE = sse;

  sse.addEventListener("start", e => {
    const d = JSON.parse(e.data);
    addLog(d.message, "ok");
  });

  sse.addEventListener("progress", e => {
    const d = JSON.parse(e.data);
    setProgress(d.found, max);
    addLog(d.message);
  });

  sse.addEventListener("done", e => {
    const d = JSON.parse(e.data);
    sse.close(); currentSSE = null;
    renderResult(d);
    showSection("result");
  });

  sse.addEventListener("error", e => {
    let msg = "Неизвестная ошибка";
    try { msg = JSON.parse(e.data).message; } catch {}
    addLog(msg, "err");
    sse.close(); currentSSE = null;
    errorMsg.textContent = msg;
    showSection("error");
  });

  sse.addEventListener("close", () => {
    sse.close(); currentSSE = null;
  });

  sse.onerror = () => {
    if (sse.readyState === EventSource.CLOSED) return;
    addLog("Соединение прервано", "warn");
  };
}

// ── Result renderer ───────────────────────────────────────────
function renderResult(d) {
  resultSub.textContent = `Файл: ${d.filename}`;
  downloadBtn.href = `/api/download/${encodeURIComponent(d.filename)}`;

  // Stats
  statsGrid.innerHTML = `
    <div class="stat-card">
      <div class="stat-value">${d.total}</div>
      <div class="stat-label">Всего компаний</div>
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
      <div class="stat-value">${d.with_phone}</div>
      <div class="stat-label">С телефоном (${d.pct_phone}%)</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${d.avg_rating || "—"}</div>
      <div class="stat-label">Средний рейтинг</div>
    </div>
  `;

  // Preview table
  previewBody.innerHTML = (d.preview || []).map(c => `
    <tr>
      <td title="${escHtml(c.name)}">${escHtml(truncate(c.name, 36))}</td>
      <td>${escHtml(c.phone)}</td>
      <td title="${escHtml(c.address)}">${escHtml(truncate(c.address, 38))}</td>
      <td>${starsHtml(c.rating)}</td>
      <td><span class="badge ${c.has_site === "Да" ? "badge--yes" : "badge--no"}">${c.has_site}</span></td>
    </tr>
  `).join("");
}

function escHtml(s) {
  return String(s ?? "—")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function truncate(s, n) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// ── Cancel / reset ────────────────────────────────────────────
cancelBtn.addEventListener("click", () => {
  if (currentSSE) { currentSSE.close(); currentSSE = null; }
  showSection("search");
});

newSearchBtn.addEventListener("click", () => showSection("search"));
errorRetryBtn.addEventListener("click", () => showSection("search"));
